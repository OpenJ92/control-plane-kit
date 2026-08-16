"""Postgres representation for exact effect-attempt current truth."""

from __future__ import annotations

from typing import Any, Protocol

from control_plane_kit_core.operations import (
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    RunId,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.records import OperationsRecordError


class _Connection(Protocol):
    def execute(self, query: str, params: object = ...) -> Any: ...


_COLUMN_NAMES = (
    "run_id",
    "activity_id",
    "attempt",
    "request_fingerprint",
    "fence_worker_id",
    "fence_generation",
    "status",
    "outcome_fingerprint",
    "prior_run_id",
    "prior_activity_id",
    "prior_attempt",
    "recovery_decision_id",
    "recovery_resolution",
    "recovery_uncertain_fingerprint",
    "recovery_evidence_fingerprint",
    "original_event_id",
    "original_event_run_id",
    "original_event_ordinal",
    "latest_event_id",
    "latest_event_run_id",
    "latest_event_ordinal",
)
_COLUMNS = ", ".join(_COLUMN_NAMES)
_VALUES = ", ".join("%s" for _ in _COLUMN_NAMES)
_SELECT = f"SELECT {_COLUMNS} FROM cpk_effect_attempts"
_COMPLETE_PRIOR_COLUMNS = _COLUMN_NAMES[3:]
_COMPLETE_PRIOR = "\n".join(
    f"  AND {column} IS NOT DISTINCT FROM %s"
    for column in _COMPLETE_PRIOR_COLUMNS
)


class EffectAttemptStore:
    """Caller-transactional representation of one effect-attempt state."""

    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def get(self, identity: EffectAttemptIdentity) -> EffectAttemptRecord:
        return self._get(identity, lock=False)

    def get_for_update(
        self,
        identity: EffectAttemptIdentity,
    ) -> EffectAttemptRecord:
        return self._get(identity, lock=True)

    def insert_absent(
        self,
        record: EffectAttemptRecord,
    ) -> EffectAttemptRecord | None:
        _require_record(record)
        row = self._connection.execute(
            f"""
            INSERT INTO cpk_effect_attempts ({_COLUMNS})
            VALUES ({_VALUES})
            ON CONFLICT (run_id, activity_id, attempt) DO NOTHING
            RETURNING run_id
            """,
            _record_values(record),
        ).fetchone()
        return None if row is None else record

    def compare_and_set(
        self,
        current: EffectAttemptRecord,
        replacement: EffectAttemptRecord,
    ) -> EffectAttemptRecord | None:
        _require_replacement(current, replacement)
        replacement_values = _record_values(replacement)[3:]
        identity = current.state.identity
        row = self._connection.execute(
            f"""
            UPDATE cpk_effect_attempts
            SET {', '.join(f'{name} = %s' for name in _COMPLETE_PRIOR_COLUMNS)}
            WHERE run_id = %s
              AND activity_id = %s
              AND attempt = %s
            {_COMPLETE_PRIOR}
            RETURNING run_id
            """,
            (
                *replacement_values,
                identity.run_id.value,
                identity.activity_id,
                identity.attempt,
                *_record_values(current)[3:],
            ),
        ).fetchone()
        return None if row is None else replacement

    def _get(
        self,
        identity: EffectAttemptIdentity,
        *,
        lock: bool,
    ) -> EffectAttemptRecord:
        _require_identity(identity)
        suffix = " FOR UPDATE" if lock else ""
        row = self._connection.execute(
            f"""
            {_SELECT}
            WHERE run_id = %s AND activity_id = %s AND attempt = %s{suffix}
            """,
            (identity.run_id.value, identity.activity_id, identity.attempt),
        ).fetchone()
        if row is None:
            raise KeyError("effect attempt was not found")
        return _decode_row(self._connection, row)


def _require_identity(identity: object) -> None:
    if type(identity) is not EffectAttemptIdentity:
        raise OperationsRecordError("effect attempt store input is invalid")


def _require_record(record: object) -> None:
    if type(record) is not EffectAttemptRecord:
        raise OperationsRecordError("effect attempt store input is invalid")


def _require_replacement(current: object, replacement: object) -> None:
    _require_record(current)
    _require_record(replacement)
    if (
        current.state.identity != replacement.state.identity
        or current.state.request_fingerprint != replacement.state.request_fingerprint
        or current.state.fence != replacement.state.fence
        or current.state.prior_attempt != replacement.state.prior_attempt
        or current.original_start_event != replacement.original_start_event
        or replacement.latest_transition_event.ordinal
        < current.latest_transition_event.ordinal
    ):
        raise OperationsRecordError("effect attempt store input is invalid")


def _record_values(record: EffectAttemptRecord) -> tuple[object, ...]:
    state = record.state
    identity = state.identity
    prior = state.prior_attempt
    recovery = state.recovery_decision
    original = record.original_start_event
    latest = record.latest_transition_event
    return (
        identity.run_id.value,
        identity.activity_id,
        identity.attempt,
        state.request_fingerprint,
        state.fence.worker_id,
        state.fence.generation,
        state.status.value,
        state.outcome_fingerprint,
        None if prior is None else prior.run_id.value,
        None if prior is None else prior.activity_id,
        None if prior is None else prior.attempt,
        None if recovery is None else recovery.decision_id,
        None if recovery is None else recovery.resolution.value,
        None if recovery is None else recovery.uncertain_fingerprint,
        None if recovery is None else recovery.evidence_fingerprint,
        original.event_id,
        original.run_id,
        original.ordinal,
        latest.event_id,
        latest.run_id,
        latest.ordinal,
    )


def _decode_row(connection: _Connection, row: object) -> EffectAttemptRecord:
    failed = False
    try:
        return _reconstruct_row(connection, row)
    except (ValueError, OperationsRecordError):
        failed = True
    if failed:
        raise OperationsRecordError("effect attempt row is invalid") from None
    raise RuntimeError("effect attempt row decoder did not return")


def _reconstruct_row(connection: _Connection, row: object) -> EffectAttemptRecord:
    if type(row) not in (tuple, list) or len(row) != len(_COLUMN_NAMES):
        raise ValueError("effect attempt row shape is invalid")
    identity = EffectAttemptIdentity(RunId(row[0]), row[1], row[2])
    prior = (
        None
        if row[8] is None and row[9] is None and row[10] is None
        else EffectAttemptIdentity(RunId(row[8]), row[9], row[10])
    )
    recovery = (
        None
        if all(row[index] is None for index in range(11, 15))
        else EffectRecoveryDecision(
            row[11],
            identity,
            EffectRecoveryResolution(row[12]),
            row[13],
            row[14],
        )
    )
    state = EffectAttemptState(
        identity=identity,
        request_fingerprint=row[3],
        fence=EffectAttemptFence(row[4], row[5]),
        status=EffectAttemptStatus(row[6]),
        outcome_fingerprint=row[7],
        prior_attempt=prior,
        recovery_decision=recovery,
    )
    event_store = PostgresExecutionStore(connection)
    original = event_store.get_event(row[15])
    latest = event_store.get_event(row[18])
    if (
        (original.event_id, original.run_id, original.ordinal)
        != (row[15], row[16], row[17])
        or (latest.event_id, latest.run_id, latest.ordinal)
        != (row[18], row[19], row[20])
    ):
        raise ValueError("effect attempt event coordinate is invalid")
    return EffectAttemptRecord(state, original, latest)


_FIRST_PAGE = f"""
SELECT {_COLUMNS}
FROM cpk_effect_attempts AS attempt
ORDER BY attempt.run_id, attempt.activity_id, attempt.attempt
LIMIT %s
"""
_NEXT_PAGE = f"""
SELECT {_COLUMNS}
FROM cpk_effect_attempts AS attempt
WHERE (attempt.run_id, attempt.activity_id, attempt.attempt) > (%s, %s, %s)
ORDER BY attempt.run_id, attempt.activity_id, attempt.attempt
LIMIT %s
"""


def _validate_current_rows(connection: _Connection, *, limit: int = 64) -> None:
    after: tuple[object, object, object] | None = None
    while True:
        query = _FIRST_PAGE if after is None else _NEXT_PAGE
        parameters = (limit,) if after is None else (*after, limit)
        rows = connection.execute(query, parameters).fetchall()
        if type(rows) not in (tuple, list) or len(rows) > limit:
            raise OperationsRecordError("effect attempt row is invalid")
        if not rows:
            return
        for row in rows:
            _decode_row(connection, row)
        last = rows[-1]
        after = (last[0], last[1], last[2])
        if len(rows) < limit:
            return


__all__ = ["EffectAttemptStore"]
