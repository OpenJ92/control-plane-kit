"""Postgres persistence for one admitted failed-run compensation program."""

from __future__ import annotations

import json
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import (
    FailedRunCompensationProgram,
    SuccessfulEffectEvidence,
)
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.records import (
    BoundedEvidence,
    FailedRunCompensationRecord,
    FailureEvidence,
    FailureCategory,
    OperationsRecordError,
)


class _Connection(Protocol):
    def execute(self, query: str, params: object = ...) -> Any: ...


class FailedRunCompensationStore:
    """Caller-transactional program store and succeeded-effect projection."""

    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def successful_effects_for_run(
        self,
        run_id: str,
    ) -> tuple[SuccessfulEffectEvidence, ...]:
        rows = self._connection.execute(
            """
            SELECT outcome.run_id, outcome.activity_id, outcome.attempt,
                   outcome.request_fingerprint, outcome.outcome_fingerprint,
                   outcome.direct_event_id, outcome.direct_event_ordinal
            FROM cpk_effect_attempt_outcomes AS outcome
            JOIN cpk_effect_attempts AS attempt
              ON attempt.run_id = outcome.run_id
             AND attempt.activity_id = outcome.activity_id
             AND attempt.attempt = outcome.attempt
            JOIN cpk_activity_events AS event
              ON event.event_id = outcome.direct_event_id
             AND event.run_id = outcome.direct_event_run_id
             AND event.ordinal = outcome.direct_event_ordinal
            WHERE outcome.run_id = %s
              AND outcome.status = 'succeeded'
              AND attempt.status = 'succeeded'
              AND attempt.request_fingerprint = outcome.request_fingerprint
              AND attempt.outcome_fingerprint = outcome.outcome_fingerprint
              AND event.event_type = 'step_succeeded'
            ORDER BY outcome.direct_event_ordinal DESC, outcome.activity_id ASC
            """,
            (run_id,),
        ).fetchall()
        return tuple(_successful_effect(row) for row in rows)

    def unresolved_attempt_count(self, run_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT count(*)
            FROM cpk_effect_attempts
            WHERE run_id = %s
              AND status IN ('started', 'uncertain')
            """,
            (run_id,),
        ).fetchone()
        return int(row[0])

    def succeeded_attempt_count(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT count(*) FROM cpk_effect_attempts "
            "WHERE run_id = %s AND status = 'succeeded'",
            (run_id,),
        ).fetchone()
        return int(row[0])

    def insert(
        self,
        record: FailedRunCompensationRecord,
        program: FailedRunCompensationProgram,
    ) -> None:
        if type(record) is not FailedRunCompensationRecord:
            raise OperationsRecordError("compensation record is invalid")
        if type(program) is not FailedRunCompensationProgram:
            raise OperationsRecordError("compensation program is invalid")
        descriptor = program.descriptor()
        preimage = json.dumps(
            descriptor,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        self._connection.execute(
            """
            INSERT INTO cpk_failed_run_compensations
              (program_id, workspace_id, request_id, run_id, plan_id, session_id,
               action_id, event_id, actor_id, reason, source_failure,
               authority_reference_fingerprint, command_fingerprint,
               evidence_fingerprint, program_fingerprint, program_preimage,
               created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s)
            """,
            (
                record.program_id,
                record.workspace_id,
                record.request_id,
                record.run_id,
                record.plan_id,
                record.session_id,
                record.action_id,
                record.event_id,
                record.actor_id,
                record.reason,
                Jsonb(_failure_descriptor(record.source_failure)),
                record.authority_reference_fingerprint,
                record.command_fingerprint,
                record.evidence_fingerprint,
                record.program_fingerprint,
                preimage,
                encode_postgres_timestamp(record.created_at),
            ),
        )
        for step in program.steps:
            source = step.source_effect
            identity = source.attempt_identity
            self._connection.execute(
                """
                INSERT INTO cpk_failed_run_compensation_steps
                  (program_id, position, source_run_id, source_activity_id,
                   source_attempt, source_request_fingerprint,
                   source_outcome_fingerprint, source_completion_event_id,
                   source_completion_ordinal, operation, material_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    program.program_id,
                    step.position,
                    identity.run_id.value,
                    identity.activity_id,
                    identity.attempt,
                    source.request_fingerprint,
                    source.outcome_fingerprint,
                    source.completion_event_id,
                    source.completion_ordinal,
                    Jsonb(step.descriptor()["operation"]),
                    step.material_source.value,
                ),
            )

    def get(
        self,
        program_id: str,
    ) -> tuple[FailedRunCompensationRecord, FailedRunCompensationProgram]:
        row = self._connection.execute(
            """
            SELECT program_id, workspace_id, request_id, run_id, plan_id,
                   session_id, action_id, event_id, actor_id, reason,
                   source_failure, authority_reference_fingerprint,
                   command_fingerprint, evidence_fingerprint,
                   program_fingerprint, program_preimage, created_at
            FROM cpk_failed_run_compensations
            WHERE program_id = %s
            """,
            (program_id,),
        ).fetchone()
        if row is None:
            raise KeyError("failed-run compensation program was not found")
        step_rows = self._connection.execute(
            """
            SELECT position, source_run_id, source_activity_id, source_attempt,
                   source_request_fingerprint, source_outcome_fingerprint,
                   source_completion_event_id, source_completion_ordinal,
                   operation, material_source
            FROM cpk_failed_run_compensation_steps
            WHERE program_id = %s
            ORDER BY position ASC
            """,
            (program_id,),
        ).fetchall()
        try:
            program = FailedRunCompensationProgram.from_descriptor(
                json.loads(bytes(row[15]).decode("ascii"))
            )
            record = FailedRunCompensationRecord(
                program_id=row[0],
                workspace_id=row[1],
                request_id=row[2],
                run_id=row[3],
                plan_id=row[4],
                session_id=row[5],
                action_id=row[6],
                event_id=row[7],
                actor_id=row[8],
                reason=row[9],
                source_failure=_failure(row[10]),
                authority_reference_fingerprint=row[11],
                command_fingerprint=row[12],
                evidence_fingerprint=row[13],
                program_fingerprint=row[14],
                created_at=decode_postgres_timestamp(row[16]),
            )
        except (TypeError, ValueError) as error:
            raise OperationsRecordError(
                "failed-run compensation row is invalid"
            ) from error
        if (
            record.program_id != program.program_id
            or record.workspace_id != program.evidence.lineage.workspace_id
            or record.request_id != program.evidence.lineage.request_id
            or record.run_id != program.evidence.lineage.run_id.value
            or record.plan_id != program.evidence.lineage.plan_id
            or record.reason != program.evidence.reason.value
            or _fingerprint(_failure_descriptor(record.source_failure))
            != program.evidence.source_failure_fingerprint
            or record.evidence_fingerprint != _fingerprint(program.evidence.descriptor())
            or record.program_fingerprint != program.fingerprint()
            or tuple(_step_descriptor(step) for step in step_rows)
            != tuple(step.descriptor() for step in program.steps)
        ):
            raise OperationsRecordError(
                "failed-run compensation row is incongruent"
            )
        return record, program


def _successful_effect(row: object) -> SuccessfulEffectEvidence:
    from control_plane_kit_core.operations import EffectAttemptIdentity, RunId

    try:
        return SuccessfulEffectEvidence(
            EffectAttemptIdentity(RunId(row[0]), row[1], row[2]),
            row[3],
            row[4],
            row[5],
            row[6],
        )
    except (TypeError, ValueError) as error:
        raise OperationsRecordError("succeeded effect evidence is invalid") from error


def _failure_descriptor(value: FailureEvidence) -> dict[str, object]:
    return {
        "category": value.category.value,
        "code": value.code,
        "message": value.message,
        "details": value.details.descriptor(),
    }


def _failure(value: object) -> FailureEvidence:
    if type(value) is not dict or set(value) != {
        "category",
        "code",
        "message",
        "details",
    }:
        raise OperationsRecordError("source failure row is invalid")
    return FailureEvidence(
        FailureCategory(value["category"]),
        value["code"],
        value["message"],
        BoundedEvidence.from_mapping(value["details"]),
    )


def _step_descriptor(row: object) -> dict[str, object]:
    return {
        "position": row[0],
        "source_effect": {
            "attempt_identity": {
                "run_id": row[1],
                "activity_id": row[2],
                "attempt": row[3],
            },
            "request_fingerprint": row[4],
            "outcome_fingerprint": row[5],
            "completion_event_id": row[6],
            "completion_ordinal": row[7],
        },
        "operation": row[8],
        "material_source": row[9],
    }


def _fingerprint(value: object) -> str:
    import hashlib

    preimage = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(preimage).hexdigest()


__all__ = ["FailedRunCompensationStore"]
