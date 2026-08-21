"""Postgres representation for immutable effect-attempt start intent."""

from __future__ import annotations

from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptIdentity,
    RunId,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
    _decode_runtime_effect_intent,
    _encode_runtime_effect_intent,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    OperationsRecordError,
)


_INPUT_ERROR = "effect attempt intent store input is invalid"
_ROW_ERROR = "effect attempt intent row is invalid"
_MISS_ERROR = "effect attempt intent evidence was not found"
_MAX_PREIMAGE_BYTES = 1_048_576
_BATCH_SIZE = 8
_COLUMN_NAMES = (
    "run_id",
    "activity_id",
    "attempt",
    "workspace_id",
    "request_id",
    "request_fingerprint",
    "original_event_id",
    "original_event_run_id",
    "original_event_ordinal",
    "preimage",
)
_COLUMNS = ", ".join(_COLUMN_NAMES)
_VALUES = ", ".join("%s" for _ in _COLUMN_NAMES)
_SELECT = """
SELECT intent.run_id, intent.activity_id, intent.attempt,
       intent.workspace_id, intent.request_id, intent.request_fingerprint,
       intent.original_event_id, intent.original_event_run_id,
       intent.original_event_ordinal,
       CASE WHEN octet_length(intent.preimage) BETWEEN 1 AND 1048576
            THEN intent.preimage ELSE NULL END AS preimage,
       event.event_type,
       event.occurred_at AT TIME ZONE 'UTC',
       event.payload
FROM cpk_effect_attempt_intents AS intent
LEFT JOIN cpk_activity_events AS event
  ON event.event_id = intent.original_event_id
 AND event.run_id = intent.original_event_run_id
 AND event.ordinal = intent.original_event_ordinal
"""
_CURRENT_QUERY = _SELECT + """
WHERE (intent.run_id, intent.activity_id, intent.attempt) > (%s, %s, %s)
ORDER BY intent.run_id, intent.activity_id, intent.attempt
LIMIT %s
"""
_ORPHAN_QUERY = """
SELECT NOT EXISTS (
  SELECT 1
  FROM cpk_effect_attempt_intents AS intent
  LEFT JOIN cpk_effect_attempts AS attempt
    ON attempt.run_id = intent.run_id
   AND attempt.activity_id = intent.activity_id
   AND attempt.attempt = intent.attempt
   AND attempt.request_fingerprint = intent.request_fingerprint
   AND attempt.original_event_id = intent.original_event_id
  WHERE attempt.run_id IS NULL
  LIMIT 1
)
"""


class EffectAttemptIntentStore:
    """Caller-transactional immutable start-intent evidence."""

    def __init__(self, connection: object) -> None:
        self._connection = connection

    def insert(
        self,
        record: EffectAttemptIntentRecord,
    ) -> EffectAttemptIntentRecord:
        admitted, preimage = _require_record(record)
        identity = admitted.identity
        event = admitted.original_start_event
        self._connection.execute(
            f"INSERT INTO cpk_effect_attempt_intents ({_COLUMNS}) "
            f"VALUES ({_VALUES})",
            (
                identity.run_id.value,
                identity.activity_id,
                identity.attempt,
                admitted.workspace_id,
                admitted.request_id,
                admitted.request_fingerprint,
                event.event_id,
                event.run_id,
                event.ordinal,
                preimage,
            ),
        )
        return admitted

    def get(self, identity: EffectAttemptIdentity) -> EffectAttemptIntentRecord:
        admitted = _require_identity(identity)
        row = self._connection.execute(
            _SELECT
            + """
WHERE intent.run_id = %s
  AND intent.activity_id = %s
  AND intent.attempt = %s
""",
            (
                admitted.run_id.value,
                admitted.activity_id,
                admitted.attempt,
            ),
        ).fetchone()
        if row is None:
            raise KeyError(_MISS_ERROR)
        return _decode_row(row)


def _require_identity(value: object) -> EffectAttemptIdentity:
    if type(value) is not EffectAttemptIdentity:
        raise OperationsRecordError(_INPUT_ERROR)
    parts = None
    try:
        parts = (value.run_id.value, value.activity_id, value.attempt)
    except AttributeError:
        pass
    admitted = None
    if parts is not None:
        try:
            admitted = EffectAttemptIdentity(RunId(parts[0]), parts[1], parts[2])
        except (TypeError, ValueError):
            pass
    if admitted is None or admitted != value:
        raise OperationsRecordError(_INPUT_ERROR)
    return admitted


def _require_record(
    value: object,
) -> tuple[EffectAttemptIntentRecord, bytes]:
    if type(value) is not EffectAttemptIntentRecord:
        raise OperationsRecordError(_INPUT_ERROR)
    parts = None
    try:
        parts = (value.identity, value.original_start_event, value.intent)
    except AttributeError:
        pass
    admitted = None
    preimage = b""
    if parts is not None:
        try:
            admitted = EffectAttemptIntentRecord(*parts)
            preimage = _encode_runtime_effect_intent(admitted.intent)
        except (OperationsRecordError, ValueError):
            pass
    if (
        admitted is None
        or admitted != value
        or not 1 <= len(preimage) <= _MAX_PREIMAGE_BYTES
    ):
        raise OperationsRecordError(_INPUT_ERROR)
    return admitted, preimage


def _decode_row(row: object) -> EffectAttemptIntentRecord:
    if type(row) not in (tuple, list) or len(row) != len(_COLUMN_NAMES) + 3:
        raise OperationsRecordError(_ROW_ERROR)
    preimage = row[9]
    if type(preimage) is not bytes or not 1 <= len(preimage) <= _MAX_PREIMAGE_BYTES:
        raise OperationsRecordError(_ROW_ERROR)
    invalid = False
    try:
        intent = _decode_runtime_effect_intent(preimage)
    except OperationsRecordError:
        invalid = True
    if invalid:
        raise OperationsRecordError(_ROW_ERROR)
    invalid = False
    try:
        payload = row[12]
        if (
            type(payload) is not dict
            or set(payload) != {"activity_id", "evidence", "failure", "recovery"}
            or payload["failure"] is not None
            or payload["recovery"] is not None
        ):
            raise ValueError
        occurred_at = _utc_text(row[11])
        event = ActivityEventRecord(
            row[6],
            row[7],
            row[8],
            ActivityEventKind(row[10]),
            occurred_at,
            activity_id=payload["activity_id"],
            evidence=BoundedEvidence.from_mapping(payload["evidence"]),
        )
        identity = EffectAttemptIdentity(RunId(row[0]), row[1], row[2])
        record = EffectAttemptIntentRecord(identity, event, intent)
        if (
            record.workspace_id != row[3]
            or record.request_id != row[4]
            or record.request_fingerprint != row[5]
            or (event.event_id, event.run_id, event.ordinal) != (row[6], row[7], row[8])
        ):
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError, OperationsRecordError):
        invalid = True
    if invalid:
        raise OperationsRecordError(_ROW_ERROR)
    return record


def _utc_text(value: object) -> str:
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec) + "Z"


def _validate_current_rows(connection: object) -> None:
    cursor = ("", "", 0)
    while True:
        rows = connection.execute(
            _CURRENT_QUERY,
            (*cursor, _BATCH_SIZE),
        ).fetchall()
        if type(rows) not in (tuple, list) or len(rows) > _BATCH_SIZE:
            raise OperationsRecordError(_ROW_ERROR)
        if not rows:
            break
        for row in rows:
            _decode_row(row)
            cursor = (row[0], row[1], row[2])
        if len(rows) < _BATCH_SIZE:
            break
    orphan = connection.execute(_ORPHAN_QUERY).fetchone()
    if orphan != (True,):
        raise OperationsRecordError(_ROW_ERROR)


__all__ = ["EffectAttemptIntentStore"]
