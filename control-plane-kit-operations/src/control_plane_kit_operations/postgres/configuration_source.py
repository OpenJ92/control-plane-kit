"""Compact original-intent proof over the existing exact-byte commitment."""
from __future__ import annotations

import json

from psycopg import DataError

from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelectionCodec
from control_plane_kit_core.operations import ActivityEventKind, RunId
from control_plane_kit_core.planning import activity_operation_from_descriptor
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntentSource
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationSourceEvidence, ConfigurationSourceProjection, _identity, _ref,
)
from control_plane_kit_operations.records import ActivityEventRecord, BoundedEvidence
from .configuration_evidence import _Capacity, _EvidenceRead, _Unavailable


# Hash the retained canonical bytes before decoding JSON. Products are never
# transported by this read; insertion/current verification retain full decoding.
_SOURCE = """
WITH original AS MATERIALIZED (
 SELECT * FROM cpk_effect_attempt_intents
 WHERE run_id=%s AND activity_id=%s AND attempt=%s
), checked AS MATERIALIZED (
 SELECT original.*, CASE WHEN octet_length(preimage) BETWEEN 1 AND 1048576
   THEN CASE WHEN encode(sha256(convert_to('control-plane-kit.runtime-effect-intent.v1','UTF8')
       || decode('00','hex') || preimage),'hex')=request_fingerprint
     THEN convert_from(preimage,'UTF8')::jsonb END END AS doc
 FROM original
), compact AS MATERIALIZED (
 SELECT c.*, e.event_type, e.occurred_at::text AS event_time, e.payload::text AS event_payload,
   c.doc->'source' AS source, c.doc->'operation' AS operation,
   c.doc->'configuration_instances' AS selection,
   (a.run_id IS NOT NULL AND a.request_fingerprint=c.request_fingerprint
    AND a.original_event_id=c.original_event_id
    AND a.original_event_run_id=c.original_event_run_id
    AND a.original_event_ordinal=c.original_event_ordinal) AS linked
 FROM checked c
 LEFT JOIN cpk_activity_events e ON e.event_id=c.original_event_id
   AND e.run_id=c.original_event_run_id AND e.ordinal=c.original_event_ordinal
 LEFT JOIN cpk_effect_attempts a ON (a.run_id,a.activity_id,a.attempt)=(c.run_id,c.activity_id,c.attempt)
), bounded AS MATERIALIZED (
 SELECT *, (doc->>'kind'='configuration-activity.v1'
   AND doc->>'activity_id'=activity_id AND original_event_run_id=run_id
   AND jsonb_typeof(doc)='object'
   AND doc - ARRAY['kind','runtime_kind','authority_ref','authority_deliveries','source',
     'activity_id','operation','products','configuration_instances']::text[] = '{}'::jsonb
   AND octet_length(workspace_id) BETWEEN 1 AND 128
   AND octet_length(request_id) BETWEEN 1 AND 2048
   AND octet_length(original_event_id) BETWEEN 1 AND 2048
   AND octet_length(request_fingerprint)=64
   AND octet_length(source::text)<=8192 AND octet_length(operation::text)<=4096
   AND octet_length(selection::text)<=65536 AND octet_length(event_payload)<=16384
   AND octet_length(event_time)<=64 AND octet_length(event_type)<=64
   AND octet_length(source::text)+octet_length(operation::text)+octet_length(selection::text)
     +octet_length(event_payload)+octet_length(workspace_id)+octet_length(request_id)
     +octet_length(original_event_id)+octet_length(request_fingerprint)
     +octet_length(event_time)+octet_length(event_type) <= 80000) AS valid
 FROM compact
)
SELECT valid AND linked,
 CASE WHEN valid AND linked THEN workspace_id END,
 CASE WHEN valid AND linked THEN request_id END,
 CASE WHEN valid AND linked THEN request_fingerprint END,
 CASE WHEN valid AND linked THEN original_event_id END,
 CASE WHEN valid AND linked THEN original_event_ordinal END,
 CASE WHEN valid AND linked THEN source::text END,
 CASE WHEN valid AND linked THEN operation::text END,
 CASE WHEN valid AND linked THEN selection::text END,
 CASE WHEN valid AND linked THEN event_type END,
 CASE WHEN valid AND linked THEN event_time END,
 CASE WHEN valid AND linked THEN event_payload END
FROM bounded
"""


def read_source(connection, identity, exact_ref, *, read=None):
    _identity(identity)
    _ref(exact_ref)
    read = _EvidenceRead(connection, standalone=True) if read is None else read
    key = ("cpk_effect_attempt_intents", identity)
    try:
        if key not in read.sources:
            # A savepoint contains malformed UTF8/JSON without poisoning the
            # caller's read transaction. Its SQL overhead is explicitly charged.
            read.query("SAVEPOINT cpk_configuration_source_read", (), records=0, octets=0, cells=0)
            try:
                rows = read.query(_SOURCE, (identity.run_id.value, identity.activity_id, identity.attempt),
                    records=1, octets=80032, cells=12, identities=3)
            except DataError:
                read.query("ROLLBACK TO SAVEPOINT cpk_configuration_source_read", (), records=0, octets=0, cells=0)
                raise _Unavailable from None
            finally:
                read.query("RELEASE SAVEPOINT cpk_configuration_source_read", (), records=0, octets=0, cells=0)
            if len(rows) != 1 or rows[0][0] is not True:
                raise _Unavailable
            row = rows[0]
            source_doc, operation_doc, selection_doc, payload = (json.loads(row[i]) for i in (6, 7, 8, 11))
            if set(source_doc) != {"workspace_id", "request_id", "run_id", "plan_id", "base_graph_id", "desired_graph_id"}:
                raise _Unavailable
            source = RuntimeEffectIntentSource(**(source_doc | {"run_id": RunId(source_doc["run_id"])}))
            operation = activity_operation_from_descriptor(operation_doc)
            selection = ConfigurationInstanceSelectionCodec().decode(selection_doc)
            if (source.workspace_id != row[1] or source.request_id != row[2]
                    or source.run_id != identity.run_id
                    or type(payload) is not dict
                    or set(payload) != {"activity_id", "evidence", "failure", "recovery"}
                    or payload["failure"] is not None or payload["recovery"] is not None
                    or payload["activity_id"] != identity.activity_id):
                raise _Unavailable
            # The timestamp was transported as text to retain exact octet accounting.
            from datetime import datetime, timezone
            instant = datetime.fromisoformat(row[10]).astimezone(timezone.utc)
            occurred = instant.isoformat(timespec="microseconds" if instant.microsecond else "seconds").replace("+00:00", "Z")
            event = ActivityEventRecord(row[4], identity.run_id.value, row[5], ActivityEventKind(row[9]),
                occurred, activity_id=payload["activity_id"], evidence=BoundedEvidence.from_mapping(payload["evidence"]))
            if event.kind is not ActivityEventKind.STEP_STARTED:
                raise _Unavailable
            # Validate every ref before selecting the requested point. Foreign
            # siblings, duplicate slots and unknown selection fields cannot hide.
            projections = tuple(ConfigurationSourceProjection(identity, row[3], row[4], row[5],
                RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, source, operation, ref) for ref in selection.instances)
            read.sources[key] = projections
        found = tuple(value for value in read.sources[key] if value.ref == exact_ref)
        if len(found) != 1:
            raise _Unavailable
        return ConfigurationSourceEvidence("complete", found[0])
    except _Capacity:
        return ConfigurationSourceEvidence("capacity")
    except (_Unavailable, ValueError, TypeError, KeyError, AttributeError):
        return ConfigurationSourceEvidence("unavailable")
