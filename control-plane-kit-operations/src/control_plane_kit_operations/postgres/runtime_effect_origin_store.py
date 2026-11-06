"""Caller-transactional original selection with bounded immutable owner joins."""

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import ActivityEventKind, EffectAttemptIdentity
from control_plane_kit_operations.records import OperationsRecordError, ExecutionCommandReceiptStatus
from control_plane_kit_operations.runtime_effect_origins import (
    RuntimeEffectOriginRecord, unprepared_runtime_origin_supported,
)
from .effect_attempt_intent_store import EffectAttemptIntentStore
from .effect_attempt_store import EffectAttemptStore
from .runtime_authority_store import RuntimeAuthorityStore

_COLUMNS = ("run_id", "activity_id", "attempt", "workspace_id", "command_key", "parent_fingerprint",
    "request_fingerprint", "original_event_id", "runtime_registration_id", "descriptor")
_SELECT = "SELECT " + ", ".join(name if name == "attempt" else
    ("CASE WHEN octet_length(descriptor::text)<=8192 THEN descriptor ELSE NULL END" if name == "descriptor" else
     f"CASE WHEN octet_length({name}) BETWEEN 1 AND 2048 THEN {name} ELSE NULL END")
    for name in _COLUMNS) + " FROM cpk_runtime_effect_origins"


class RuntimeEffectOriginStore:
    def __init__(self, connection):
        self._connection = connection

    def get(self, identity):
        if type(identity) is not EffectAttemptIdentity:
            raise OperationsRecordError("original runtime identity is invalid")
        identity.__post_init__()
        rows = self._connection.execute(_SELECT + " WHERE run_id=%s AND activity_id=%s AND attempt=%s LIMIT 2",
            (identity.run_id.value, identity.activity_id, identity.attempt)).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise OperationsRecordError("original runtime selection is ambiguous")
        return self._read(rows[0])

    def insert_absent(self, record):
        self._require_owners(record, fresh=True)
        values = _values(record)
        row = self._connection.execute("INSERT INTO cpk_runtime_effect_origins (" + ", ".join(_COLUMNS)
            + ") VALUES (" + ", ".join("%s" for _ in _COLUMNS) + ") "
            "ON CONFLICT (run_id, activity_id, attempt) DO NOTHING RETURNING attempt",
            values[:-1] + (Jsonb(values[-1]),)).fetchone()
        if row != (record.identity.attempt,):
            raise OperationsRecordError("original runtime selection already exists")
        return record

    def _read(self, row):
        record = RuntimeEffectOriginRecord.from_descriptor(row[-1])
        if row != _values(record):
            raise OperationsRecordError("original runtime selection is incongruent")
        self._require_owners(record)
        return record

    def _require_owners(self, record, *, fresh=False):
        from .execution import PostgresExecutionStore
        if type(record) is not RuntimeEffectOriginRecord:
            raise OperationsRecordError("original runtime selection is invalid")
        record.__post_init__()
        execution = PostgresExecutionStore(self._connection)
        original = EffectAttemptIntentStore(self._connection).get(record.identity)
        attempt = EffectAttemptStore(self._connection).get(record.identity)
        parent = execution.command_receipt_for_idempotency(record.command.run_id, record.command.idempotency_key)
        run = execution.get_run(record.command.run_id)
        request = execution.get_request(run.admission.request_id)
        runtime = RuntimeAuthorityStore(self._connection).get_registration(record.workspace_id,
            original.intent.authority_ref, record.runtime_registration_id)
        if (parent is None or parent.intent_fingerprint != record.command.intent_fingerprint
                or parent.managed_intent is None or parent.managed_intent.workspace_id != record.workspace_id
                or (fresh and parent.status is not ExecutionCommandReceiptStatus.INCOMPLETE)
                or parent.initial_run.admission.request_id != original.request_id
                or parent.initial_run.plan_id != run.plan_id or original.intent.source.plan_id != run.plan_id
                or request.identity.request_id != original.request_id
                or request.identity.workspace_id != record.workspace_id
                or original.workspace_id != record.workspace_id
                or original.request_fingerprint != record.request_fingerprint
                or original.original_start_event != attempt.original_start_event
                or original.original_start_event.event_id != record.original_event_id
                or original.original_start_event.occurred_at != record.recorded_at
                or original.original_start_event.kind is not ActivityEventKind.STEP_STARTED
                or not unprepared_runtime_origin_supported(original.intent, runtime)):
            raise OperationsRecordError("original runtime selection owners disagree")
        markers = self._connection.execute("SELECT invocation_id FROM cpk_execution_command_invocations "
            "WHERE run_id=%s AND activity_id=%s AND attempt=%s "
            "AND kind IN ('prepared-runtime-dispatch','ordinary-verification-acquisition') LIMIT 1",
            (record.command.run_id, record.activity_id, record.attempt)).fetchall()
        if markers:
            raise OperationsRecordError("original runtime selection has mixed sources")


def _values(record):
    return (record.command.run_id, record.activity_id, record.attempt, record.workspace_id,
        record.command.idempotency_key, record.command.intent_fingerprint, record.request_fingerprint,
        record.original_event_id, record.runtime_registration_id, record.descriptor())


def validate_runtime_effect_origin_rows(connection):
    from .current_data_validation import CurrentRowDrift
    store, after = RuntimeEffectOriginStore(connection), None
    try:
        while True:
            rows = connection.execute(_SELECT + ("" if after is None else
                ' WHERE (run_id COLLATE "C",activity_id COLLATE "C",attempt)>(%s,%s,%s)')
                + ' ORDER BY run_id COLLATE "C",activity_id COLLATE "C",attempt LIMIT 100',
                () if after is None else after).fetchall()
            for row in rows:
                store._read(row)
                after = row[:3]
            if len(rows) < 100:
                return
    except (KeyError, ValueError, TypeError, AttributeError):
        raise CurrentRowDrift from None
