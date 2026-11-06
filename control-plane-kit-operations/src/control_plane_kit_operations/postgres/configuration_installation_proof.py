"""Read only original installation fragments under their retained commitment.

The source owner establishes the complete invocation first. SQL guards transport;
Core interprets deliveries, selections and receipts. These values grant no power.
"""
from dataclasses import dataclass, replace
import json

from psycopg import DataError

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceSelection, ConfigurationInstanceSelectionCodec,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.planning import activity_operation_descriptor
from control_plane_kit_core.secret_installation import SecretInstallationCorrelation
from control_plane_kit_core.secret_selections import SecretVersionSelection, secret_selections_from_descriptor
from control_plane_kit_core.secrets import secret_delivery_from_descriptor
from control_plane_kit_operations._configuration_preparation import _OrdinarySuffixBudget
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationSourceProjection, ConfigurationEvidenceFootprint as F,
    ConfigurationCapacityDecision, configuration_evidence_capacity,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    _encode_runtime_effect_intent, _object_without_duplicate_keys, _reject_nonfinite,
)
from .configuration_evidence import _Capacity, _Unavailable


@dataclass(frozen=True, slots=True, repr=False)
class _InstallationShape:
    identity: object
    request_fingerprint: str
    original_event_id: str | None
    original_event_ordinal: int | None
    source: object
    kind: object
    operation: object
    configuration_instances: object
    widths: tuple[int, int, int, int]
    empty: bool


@dataclass(frozen=True, slots=True, repr=False)
class _InstallationProof:
    shape: _InstallationShape
    fragments: tuple[str, str, str, str]
    correlation: SecretInstallationCorrelation


def installation_key(source):
    return ("configuration-installation-proof", source.identity, source.request_fingerprint,
        source.original_event_id, source.original_event_ordinal)


def _decode_probe(row):
    if (type(row) is not tuple or len(row) != 5
            or any(type(value) is not int for value in row)
            or row[0] not in (1, 2) or any(not 1 <= value < 2**31 for value in row[1:])
            or min(row[3:]) < 2 or row[0] == 2 and row[3:] != (2, 2)):
        raise _Unavailable
    return row[0] == 2, row[1:]


def installation_budget(shape):
    if (type(shape) is not _InstallationShape or type(shape.empty) is not bool
            or type(shape.widths) is not tuple):
        raise _Unavailable
    _decode_probe((2 if shape.empty else 1, *shape.widths))
    if shape.empty:
        return _OrdinarySuffixBudget(F(1, 41, 5, 3), F(1, 41, 5, 4))
    return _OrdinarySuffixBudget(F(2, sum(shape.widths) + 42, 10, 4),
        F(2, sum(shape.widths) + 42, 10, 5))


def _selection(read, source):
    valid = False
    selection = None
    try:
        if type(source) is not ConfigurationSourceProjection:
            raise ValueError
        ConfigurationSourceProjection.__post_init__(source)
        members = read.sources[("cpk_effect_attempt_intents", source.identity)]
        if type(members) is not tuple or not members or members.count(source) != 1:
            raise ValueError
        for member in members:
            if type(member) is not ConfigurationSourceProjection:
                raise ValueError
            ConfigurationSourceProjection.__post_init__(member)
            if replace(member, ref=source.ref) != source:
                raise ValueError
        refs = tuple(member.ref for member in members)
        selection = ConfigurationInstanceSelection(refs)
        if selection.instances != refs:
            raise ValueError
        valid = True
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
        pass
    if not valid:
        raise _Unavailable
    return selection


def _shape(source, selection, empty, widths):
    return _InstallationShape(source.identity, source.request_fingerprint,
        source.original_event_id, source.original_event_ordinal, source.source,
        source.kind, source.operation, selection, widths, empty)


def _correlation(shape, fragments):
    result = None
    try:
        installation_budget(shape)
        if (type(fragments) is not tuple or len(fragments) != 4
                or any(type(value) is not str for value in fragments)
                or tuple(len(value.encode("utf-8")) for value in fragments) != shape.widths
                or shape.empty != (fragments[2:] == ("[]", "[]"))):
            raise ValueError
        deliveries, selections = (json.loads(value, object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite) for value in fragments[2:])
        if type(deliveries) is not list:
            raise ValueError
        selections = secret_selections_from_descriptor(selections)
        if any(type(value) is not SecretVersionSelection for value in selections):
            raise ValueError
        result = SecretInstallationCorrelation(shape.request_fingerprint, shape.original_event_id,
            shape.kind, shape.source, shape.operation, *fragments[:2],
            tuple(secret_delivery_from_descriptor(value) for value in deliveries),
            selections, shape.configuration_instances)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
        pass
    if result is None:
        raise _Unavailable
    return result


# Each arm is repeated by the fetch. In particular, no conversion precedes the
# raw-size/hash/link checks. Native decoding errors are contained below.
_ORIGINAL = """
WITH installation_original AS MATERIALIZED (
 SELECT i.*, (i.request_fingerprint=%s AND i.original_event_id=%s
   AND i.original_event_ordinal=%s AND i.original_event_run_id=i.run_id
   AND a.request_fingerprint=i.request_fingerprint
   AND (a.original_event_id,a.original_event_run_id,a.original_event_ordinal)=
       (i.original_event_id,i.original_event_run_id,i.original_event_ordinal)
   AND e.event_type='step_started'
   AND q.workspace_id=i.workspace_id AND r.request_id=i.request_id) AS linked
 FROM cpk_effect_attempt_intents i
 JOIN cpk_effect_attempts a USING (run_id,activity_id,attempt)
 JOIN cpk_activity_events e ON (e.event_id,e.run_id,e.ordinal)=
   (i.original_event_id,i.original_event_run_id,i.original_event_ordinal)
 JOIN cpk_activity_runs r ON r.run_id=i.run_id
 JOIN cpk_execution_requests q ON q.request_id=r.request_id
 WHERE i.run_id=%s AND i.activity_id=%s AND i.attempt=%s
), decoded AS MATERIALIZED (
 SELECT *, CASE WHEN linked THEN CASE WHEN octet_length(preimage) BETWEEN 1 AND 1048576
   THEN CASE WHEN encode(sha256(convert_to('control-plane-kit.runtime-effect-intent.v1','UTF8')
     || decode('00','hex') || preimage),'hex')=request_fingerprint
     THEN convert_from(preimage,'UTF8')::jsonb END END END AS doc FROM installation_original
), context AS MATERIALIZED (
 SELECT doc, (doc->>'kind'='configuration-activity.v1'
   AND doc->>'activity_id'=activity_id AND doc->'source'=%s::jsonb
   AND doc->'operation'=%s::jsonb AND doc->'configuration_instances'=%s::jsonb
   AND doc->'source'->>'workspace_id'=workspace_id
   AND doc->'source'->>'request_id'=request_id
   AND CASE WHEN jsonb_typeof(doc->'products')='array'
       THEN jsonb_array_length(doc->'products')=1 ELSE false END) AS valid
 FROM decoded
), material AS MATERIALIZED (
 SELECT CASE WHEN valid THEN doc->'products'->0 END AS product FROM context
), fragments AS MATERIALIZED (
 SELECT product->>'runtime_id' AS runtime, product->>'node_id' AS node,
   product->'product'->'runtime_contract'->'secret_deliveries' AS deliveries,
   product->'secret_selections' AS selections,
   (jsonb_typeof(product->'runtime_id')='string' AND product->>'runtime_id'=%s
    AND jsonb_typeof(product->'node_id')='string' AND product->>'node_id'=%s
    AND jsonb_typeof(product->'product'->'runtime_contract'->'secret_deliveries')='array'
    AND jsonb_typeof(product->'secret_selections')='array') AS valid
 FROM material
), measured AS MATERIALIZED (
 SELECT *, octet_length(runtime) AS r, octet_length(node) AS n,
   octet_length(deliveries::text) AS d, octet_length(selections::text) AS s
 FROM fragments
)
"""
_PROBE = _ORIGINAL + """
SELECT CASE WHEN valid THEN CASE WHEN deliveries='[]'::jsonb AND selections='[]'::jsonb
    THEN 2 ELSE 1 END ELSE 0 END,
 CASE WHEN valid THEN r ELSE 0 END, CASE WHEN valid THEN n ELSE 0 END,
 CASE WHEN valid THEN d ELSE 0 END, CASE WHEN valid THEN s ELSE 0 END FROM measured
"""
_FETCH = _ORIGINAL + """
, bounded AS MATERIALIZED (
 SELECT *, (valid AND (r,n,d,s)=(%s,%s,%s,%s)) AS fits FROM measured
)
SELECT CASE WHEN fits THEN 1 ELSE 0 END,
 CASE WHEN fits THEN runtime END, CASE WHEN fits THEN node END,
 CASE WHEN fits THEN deliveries::text END, CASE WHEN fits THEN selections::text END FROM bounded
"""


def _params(source, selection):
    ref = selection.instances[0]
    if any((value.runtime_id, value.node_id) != (ref.runtime_id, ref.node_id)
            for value in selection.instances):
        raise _Unavailable
    return (source.request_fingerprint, source.original_event_id, source.original_event_ordinal,
        source.identity.run_id.value, source.identity.activity_id, source.identity.attempt,
        json.dumps(source.source.descriptor()), json.dumps(activity_operation_descriptor(source.operation)),
        json.dumps(ConfigurationInstanceSelectionCodec().encode(selection)), ref.runtime_id, ref.node_id)


def _capacity(read, future):
    if configuration_evidence_capacity(read.used.plus(future)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
        raise _Capacity


def _read_material(read, source, selection, *, fetch):
    # Initial savepoint, probe, worst rollback/release. After settlement only
    # the remaining fetch/closure is reserved; the probe is never counted twice.
    _capacity(read, F(1, 41, 5, 4))
    read.query("SAVEPOINT cpk_configuration_installation_read", (), records=0, octets=0, cells=0)
    native_error = False
    result = None
    try:
        params = _params(source, selection)
        rows = read.query(_PROBE, params, records=1, octets=41, cells=5)
        if len(rows) != 1:
            raise _Unavailable
        empty, widths = _decode_probe(rows[0])
        shape = _shape(source, selection, empty, widths)
        from .configuration_cleanup_phase_read_bounds import _installation_shape
        _installation_shape(read, shape)
        if not fetch:
            result = shape
        else:
            if empty:
                ref = selection.instances[0]
                fragments = (ref.runtime_id, ref.node_id, "[]", "[]")
            else:
                _capacity(read, F(1, sum(widths) + 1, 5, 3))
                rows = read.query(_FETCH, (*params, *widths), records=1, octets=sum(widths) + 1, cells=5)
                if len(rows) != 1 or type(rows[0]) is not tuple or len(rows[0]) != 5 or type(rows[0][0]) is not int or rows[0][0] != 1:
                    raise _Unavailable
                fragments = rows[0][1:]
            result = _InstallationProof(shape, fragments, _correlation(shape, fragments))
    except DataError:
        native_error = True
        read.query("ROLLBACK TO SAVEPOINT cpk_configuration_installation_read", (), records=0, octets=0, cells=0)
    finally:
        read.query("RELEASE SAVEPOINT cpk_configuration_installation_read", (), records=0, octets=0, cells=0)
    if native_error:
        raise _Unavailable
    return result


def _warm(read, source, selection, key):
    proof = read.sources[key]
    if type(proof) is not _InstallationProof or type(proof.shape) is not _InstallationShape:
        raise _Unavailable
    shape = proof.shape
    if shape != _shape(source, selection, shape.empty, shape.widths):
        raise _Unavailable
    from .configuration_cleanup_phase_read_bounds import _installation_shape
    _installation_shape(read, shape)
    correlation = _correlation(shape, proof.fragments)
    if type(proof.correlation) is not SecretInstallationCorrelation or correlation != proof.correlation:
        raise _Unavailable
    return proof


def read_installation_proof(read, source):
    selection = _selection(read, source)
    from .configuration_cleanup_phase_read_bounds import _installation_shape
    _installation_shape(read, source, selection)
    key = installation_key(source)
    from control_plane_kit_operations._configuration_acceptance import _require_publication_proof
    _require_publication_proof(read, "sources", key)
    if key in read.sources:
        return _warm(read, source, selection, key)
    proof = _read_material(read, source, selection, fetch=True)
    read.sources[key] = proof
    return proof


def measure_installation_shape(read, source):
    selection = _selection(read, source)
    from .configuration_cleanup_phase_read_bounds import _installation_shape
    _installation_shape(read, source, selection)
    key = installation_key(source)
    if key in read.sources:
        return _warm(read, source, selection, key).shape
    return _read_material(read, source, selection, fetch=False)


def measure_prospective_installation(read, identity, intent):
    canonical = _encode_runtime_effect_intent(intent)
    fingerprint = runtime_effect_intent_fingerprint(intent)
    # The owning preparation validated the complete canonical value. Measure
    # the same PostgreSQL representations as the future retained protocol.
    rows = read.query("""
        WITH proposed AS (SELECT convert_from(%s,'UTF8')::jsonb AS doc), material AS (
          SELECT doc->'products'->0 AS p FROM proposed)
        SELECT CASE WHEN p->'product'->'runtime_contract'->'secret_deliveries'='[]'::jsonb
          AND p->'secret_selections'='[]'::jsonb THEN 2 ELSE 1 END,
          octet_length(p->>'runtime_id'), octet_length(p->>'node_id'),
          octet_length((p->'product'->'runtime_contract'->'secret_deliveries')::text),
          octet_length((p->'secret_selections')::text) FROM material
        """, (canonical,), records=1, octets=41, cells=5)
    if len(rows) != 1 or len(intent.products) != 1:
        raise _Unavailable
    empty, widths = _decode_probe(rows[0])
    return _InstallationShape(identity, fingerprint, None, None, intent.source, intent.kind,
        intent.operation, intent.configuration_instances, widths, empty)
