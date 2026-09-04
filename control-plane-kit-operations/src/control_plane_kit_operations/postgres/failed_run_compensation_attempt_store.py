"""Postgres representation of immutable compensation-attempt bindings."""

from __future__ import annotations

from typing import Any, Protocol

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations.records import (
    FailedRunCompensationAttemptBinding,
    OperationsRecordError,
)


class _Connection(Protocol):
    def execute(self, query: str, params: object = ...) -> Any: ...


_COLUMNS = (
    "program_id, position, source_run_id, source_activity_id, source_attempt, "
    "inverse_run_id, inverse_activity_id, inverse_attempt"
)


class FailedRunCompensationAttemptStore:
    """Caller-transactional immutable binding store."""

    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def insert(
        self,
        binding: FailedRunCompensationAttemptBinding,
    ) -> FailedRunCompensationAttemptBinding:
        admitted = _require_binding(binding)
        source = admitted.source_attempt
        inverse = admitted.inverse_attempt
        self._connection.execute(
            f"INSERT INTO cpk_failed_run_compensation_attempt_bindings "
            f"({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                admitted.program_id,
                admitted.position,
                source.run_id.value,
                source.activity_id,
                source.attempt,
                inverse.run_id.value,
                inverse.activity_id,
                inverse.attempt,
            ),
        )
        return admitted

    def get(
        self,
        program_id: str,
        position: int,
    ) -> FailedRunCompensationAttemptBinding:
        row = self._connection.execute(
            f"SELECT {_COLUMNS} "
            "FROM cpk_failed_run_compensation_attempt_bindings "
            "WHERE program_id = %s AND position = %s",
            (program_id, position),
        ).fetchone()
        if row is None:
            raise KeyError("compensation attempt binding was not found")
        return _decode(row)

    def get_for_attempt(
        self,
        identity: EffectAttemptIdentity,
    ) -> FailedRunCompensationAttemptBinding:
        if type(identity) is not EffectAttemptIdentity:
            raise OperationsRecordError(
                "compensation attempt binding store input is invalid"
            )
        row = self._connection.execute(
            f"SELECT {_COLUMNS} "
            "FROM cpk_failed_run_compensation_attempt_bindings "
            "WHERE inverse_run_id = %s AND inverse_activity_id = %s "
            "AND inverse_attempt = %s",
            (identity.run_id.value, identity.activity_id, identity.attempt),
        ).fetchone()
        if row is None:
            raise KeyError("compensation attempt binding was not found")
        return _decode(row)

    def for_program(
        self,
        program_id: str,
    ) -> tuple[FailedRunCompensationAttemptBinding, ...]:
        rows = self._connection.execute(
            f"SELECT {_COLUMNS} "
            "FROM cpk_failed_run_compensation_attempt_bindings "
            "WHERE program_id = %s ORDER BY position",
            (program_id,),
        ).fetchall()
        return tuple(_decode(row) for row in rows)


def _require_binding(
    value: object,
) -> FailedRunCompensationAttemptBinding:
    if type(value) is not FailedRunCompensationAttemptBinding:
        raise OperationsRecordError(
            "compensation attempt binding store input is invalid"
        )
    return value


def _decode(row: object) -> FailedRunCompensationAttemptBinding:
    try:
        if type(row) not in (tuple, list) or len(row) != 8:
            raise ValueError
        return FailedRunCompensationAttemptBinding(
            row[0],
            row[1],
            EffectAttemptIdentity(RunId(row[2]), row[3], row[4]),
            EffectAttemptIdentity(RunId(row[5]), row[6], row[7]),
        )
    except (TypeError, ValueError, OperationsRecordError) as error:
        raise OperationsRecordError(
            "compensation attempt binding row is invalid"
        ) from error


__all__ = ["FailedRunCompensationAttemptStore"]
