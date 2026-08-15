"""Postgres store for execution admission and run ownership."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    RecoveryDecisionKind,
)
from control_plane_kit_core.operations import RunId
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_cursor_timestamp,
    decode_postgres_timestamp,
    encode_postgres_cursor_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.read_pages import (
    OrdinalReadCursor,
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageError,
    ReadPageRequest,
    TemporalReadCursor,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    FailureEvidence,
    OperationsRecordError,
    RetryIdentity,
)


@dataclass(frozen=True)
class _ExecutionLeaseObservation:
    request: ExecutionRequestRecord
    observed_at: str
    expired: bool


class PostgresExecutionStore:
    """Postgres-backed execution request store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def add_request(self, record: ExecutionRequestRecord) -> ExecutionRequestRecord:
        claim = record.claim
        self._connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claim_generation, claimed_at, lease_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.identity.request_id,
                record.identity.workspace_id,
                record.identity.session_id,
                record.identity.plan_id,
                record.status.value,
                record.requested_by,
                encode_postgres_timestamp(record.requested_at),
                record.approval_request_id,
                record.approval_decision_id,
                record.idempotency.key,
                record.idempotency.intent_fingerprint,
                None if claim is None else claim.worker_id,
                None if claim is None else claim.generation,
                None
                if claim is None
                else encode_postgres_timestamp(claim.claimed_at),
                None
                if claim is None
                else encode_postgres_timestamp(claim.lease_expires_at),
            ),
        )
        return record

    def lock_admission_idempotency(
        self,
        workspace_id: str,
        idempotency_key: str,
    ) -> None:
        """Serialize execution admission before the request row exists."""

        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"execution-admission:{workspace_id}:{idempotency_key}",),
        )

    def get_request(self, request_id: str) -> ExecutionRequestRecord:
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = %s
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing execution request {request_id!r}")
        return _execution_request(row)

    def request_for_idempotency(
        self,
        workspace_id: str,
        idempotency_key: str,
    ) -> ExecutionRequestRecord | None:
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (workspace_id, idempotency_key),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def claim_request(
        self,
        request_id: str,
        worker_id: str,
        lease_duration_seconds: int,
    ) -> ExecutionRequestRecord | None:
        if (
            type(lease_duration_seconds) is not int
            or not 1 <= lease_duration_seconds <= 3600
        ):
            raise OperationsRecordError("lease duration is invalid")
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = %s
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing execution request {request_id!r}")
        current = _execution_request(row)
        if current.status is not ExecutionRequestStatus.QUEUED:
            return None
        updated = self._connection.execute(
            """
            WITH observed AS (
              SELECT clock_timestamp() AS observed_at
            )
            UPDATE cpk_execution_requests
            SET status = 'claimed', claim_worker_id = %s,
                claim_generation = 1,
                claimed_at = observed.observed_at,
                lease_expires_at = observed.observed_at
                  + (%s * interval '1 second')
            FROM observed
            WHERE request_id = %s AND status = 'queued'
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claim_generation, claimed_at,
                      lease_expires_at
            """,
            (
                worker_id,
                lease_duration_seconds,
                request_id,
            ),
        ).fetchone()
        return None if updated is None else _execution_request(updated)

    def get_request_for_update(self, request_id: str) -> ExecutionRequestRecord:
        row = self._connection.execute(
            """
            SELECT request_id, workspace_id, session_id, plan_id, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, idempotency_key, intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at,
                   lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = %s
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError("missing execution request")
        return _execution_request(row)

    def observe_request_lease_for_update(
        self,
        request_id: str,
    ) -> _ExecutionLeaseObservation:
        request = self.get_request_for_update(request_id)
        if request.claim is None:
            raise OperationsRecordError(
                "execution request does not have an active lease"
            )
        observed = self._connection.execute(
            """
            WITH observed AS (
              SELECT clock_timestamp() AS observed_at
            )
            SELECT observed.observed_at,
                   request.lease_expires_at <= observed.observed_at AS expired
            FROM cpk_execution_requests AS request
            CROSS JOIN observed
            WHERE request.request_id = %s
            """,
            (request_id,),
        ).fetchone()
        if observed is None:
            raise KeyError("missing execution request")
        return _ExecutionLeaseObservation(
            request=request,
            observed_at=decode_postgres_timestamp(observed[0]),
            expired=observed[1],
        )

    def add_run(self, record: ActivityRunRecord) -> ActivityRunRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, prior_run_id, status,
               created_at, started_at, settled_at, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.run_id,
                record.plan_id,
                record.admission.request_id,
                record.retry.attempt,
                record.retry.prior_run_id,
                record.status.value,
                encode_postgres_timestamp(record.created_at),
                _encode_optional_timestamp(record.started_at),
                _encode_optional_timestamp(record.settled_at),
                _json(record.metadata.descriptor()),
            ),
        )
        return record

    def get_run(self, run_id: str) -> ActivityRunRecord:
        _require_run_id(run_id)
        row = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing activity run {run_id!r}")
        return _activity_run(row)

    def get_run_for_update(self, run_id: str) -> ActivityRunRecord:
        _require_run_id(run_id)
        row = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE run_id = %s
            FOR UPDATE
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing activity run {run_id!r}")
        return _activity_run(row)

    def compare_and_set_run_status(
        self,
        run_id: str,
        *,
        expected: ActivityRunStatus,
        replacement: ActivityRunStatus,
        started_at: str | None = None,
        settled_at: str | None = None,
    ) -> ActivityRunRecord | None:
        _require_run_id(run_id)
        encoded_started_at = _encode_optional_timestamp(started_at)
        encoded_settled_at = _encode_optional_timestamp(settled_at)
        row = self._connection.execute(
            """
            UPDATE cpk_activity_runs
            SET status = %s,
                started_at = COALESCE(%s, started_at),
                settled_at = COALESCE(settled_at, %s)
            WHERE run_id = %s
              AND status = %s
              AND settled_at IS NULL
            RETURNING run_id, plan_id, request_id, attempt, prior_run_id, status,
                      created_at, started_at, settled_at, metadata
            """,
            (
                replacement.value,
                encoded_started_at,
                encoded_settled_at,
                run_id,
                expected.value,
            ),
        ).fetchone()
        return None if row is None else _activity_run(row)

    def runs_for_request(self, request_id: str) -> tuple[ActivityRunRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE request_id = %s
            ORDER BY attempt ASC, run_id ASC
            """,
            (request_id,),
        ).fetchall()
        return tuple(_activity_run(row) for row in rows)

    def runs_for_plan(self, plan_id: str) -> tuple[ActivityRunRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE plan_id = %s
            ORDER BY created_at ASC, run_id ASC
            """,
            (plan_id,),
        ).fetchall()
        return tuple(_activity_run(row) for row in rows)

    def run_page(self, request: ReadPageRequest) -> ReadPage[ActivityRunRecord]:
        if request.collection is not ReadCollection.PLAN_RUNS:
            raise ReadPageError("run page request is incongruent")
        cursor = request.cursor
        if cursor is not None:
            _require_page_run_id(cursor.item_id)
        seek = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (
                request.scope.workspace_id,
                request.scope.plan_id,
                request.limit + 1,
            )
        else:
            seek = "AND (run.created_at, run.run_id) > (%s, %s)"
            parameters = (
                request.scope.workspace_id,
                request.scope.plan_id,
                encode_postgres_cursor_timestamp(cursor.instant),
                cursor.item_id,
                request.limit + 1,
            )
        rows = self._connection.execute(
            f"""
            SELECT run.run_id, run.plan_id, run.request_id, run.attempt,
                   run.prior_run_id, run.status, run.created_at, run.started_at,
                   run.settled_at, run.metadata
            FROM cpk_activity_runs AS run
            JOIN cpk_execution_requests AS request
              ON request.request_id = run.request_id
             AND request.plan_id = run.plan_id
            JOIN cpk_activity_plans AS plan
              ON plan.plan_id = run.plan_id
             AND plan.session_id = request.session_id
            JOIN cpk_operation_sessions AS session
              ON session.session_id = plan.session_id
             AND session.workspace_id = request.workspace_id
            WHERE request.workspace_id = %s
              AND run.plan_id = %s
              {seek}
            ORDER BY run.created_at ASC, run.run_id ASC
            LIMIT %s
            """,
            parameters,
        ).fetchall()
        candidates = tuple(
            ReadPageCandidate(
                item=_activity_run(row),
                cursor_after_item=TemporalReadCursor(
                    ReadCollection.PLAN_RUNS,
                    request.scope,
                    decode_postgres_cursor_timestamp(row[6]),
                    row[0],
                ),
            )
            for row in rows
        )
        return ReadPage.from_candidates(request, candidates)

    def add_event(self, record: ActivityEventRecord) -> ActivityEventRecord:
        payload = {
            "activity_id": record.activity_id,
            "evidence": record.evidence.descriptor(),
            "failure": None
            if record.failure is None
            else {
                "category": record.failure.category.value,
                "code": record.failure.code,
                "message": record.failure.message,
                "details": record.failure.details.descriptor(),
            },
            "recovery": (
                None
                if record.recovery is None
                else record.recovery.descriptor()
            ),
        }
        self._connection.execute(
            """
            INSERT INTO cpk_activity_events
              (event_id, run_id, ordinal, event_type, occurred_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                record.event_id,
                record.run_id,
                record.ordinal,
                record.kind.value,
                encode_postgres_timestamp(record.occurred_at),
                _json(payload),
            ),
        )
        return record

    def get_event(self, event_id: str) -> ActivityEventRecord:
        row = self._connection.execute(
            """
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events
            WHERE event_id = %s
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing activity event {event_id!r}")
        return _activity_event(row)

    def next_event_ordinal(self, run_id: str) -> int:
        _require_run_id(run_id)
        locked = self._connection.execute(
            "SELECT run_id FROM cpk_activity_runs WHERE run_id = %s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if locked is None:
            raise KeyError(f"missing activity run {run_id!r}")
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0) + 1
            FROM cpk_activity_events
            WHERE run_id = %s
            """,
            (run_id,),
        ).fetchone()
        return int(row[0])

    def events_for_run(self, run_id: str) -> tuple[ActivityEventRecord, ...]:
        _require_run_id(run_id)
        rows = self._connection.execute(
            """
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events
            WHERE run_id = %s
            ORDER BY ordinal ASC
            """,
            (run_id,),
        ).fetchall()
        return tuple(_activity_event(row) for row in rows)

    def event_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[ActivityEventRecord]:
        if request.collection is not ReadCollection.RUN_EVENTS:
            raise ReadPageError("event page request is incongruent")
        _require_page_run_id(request.scope.run_id)
        cursor = request.cursor
        parameters: tuple[object, ...]
        seek = ""
        if cursor is None:
            parameters = (request.scope.run_id, request.limit + 1)
        else:
            seek = "AND (ordinal, event_id) > (%s, %s)"
            parameters = (
                request.scope.run_id,
                cursor.ordinal,
                cursor.item_id,
                request.limit + 1,
            )
        rows = self._connection.execute(
            f"""
            SELECT event_id, run_id, ordinal, event_type, occurred_at, payload
            FROM cpk_activity_events
            WHERE run_id = %s
              {seek}
            ORDER BY ordinal ASC, event_id ASC
            LIMIT %s
            """,
            parameters,
        ).fetchall()
        candidates = tuple(
            ReadPageCandidate(
                item=_activity_event(row),
                cursor_after_item=OrdinalReadCursor(
                    ReadCollection.RUN_EVENTS,
                    request.scope,
                    row[2],
                    row[0],
                ),
            )
            for row in rows
        )
        return ReadPage.from_candidates(request, candidates)


def _execution_request(row: tuple[Any, ...]) -> ExecutionRequestRecord:
    claim = (
        None
        if row[11] is None
        else ClaimIdentity(
            worker_id=row[11],
            generation=row[12],
            claimed_at=decode_postgres_timestamp(row[13]),
            lease_expires_at=decode_postgres_timestamp(row[14]),
        )
    )
    return ExecutionRequestRecord(
        identity=ExecutionRequestIdentity(
            request_id=row[0],
            workspace_id=row[1],
            session_id=row[2],
            plan_id=row[3],
        ),
        status=ExecutionRequestStatus(row[4]),
        requested_by=row[5],
        requested_at=decode_postgres_timestamp(row[6]),
        approval_request_id=row[7],
        approval_decision_id=row[8],
        idempotency=ExecutionIdempotency(
            key=row[9],
            intent_fingerprint=row[10],
        ),
        claim=claim,
    )


def _activity_run(row: tuple[Any, ...]) -> ActivityRunRecord:
    return ActivityRunRecord(
        run_id=row[0],
        plan_id=row[1],
        admission=AdmittedRun(row[2]),
        retry=RetryIdentity(row[3], row[4]),
        status=ActivityRunStatus(row[5]),
        created_at=decode_postgres_timestamp(row[6]),
        started_at=_decode_optional_timestamp(row[7]),
        settled_at=_decode_optional_timestamp(row[8]),
        metadata=BoundedEvidence.from_mapping(row[9]),
    )


def _activity_event(row: tuple[Any, ...]) -> ActivityEventRecord:
    payload = row[5]
    if not isinstance(payload, dict):
        raise ValueError("persisted activity event payload must be an object")
    evidence = payload.get("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("persisted activity event evidence must be an object")
    return ActivityEventRecord(
        event_id=row[0],
        run_id=row[1],
        ordinal=row[2],
        kind=ActivityEventKind(row[3]),
        occurred_at=decode_postgres_timestamp(row[4]),
        activity_id=payload.get("activity_id"),
        evidence=BoundedEvidence.from_mapping(evidence),
        failure=_failure_evidence(payload.get("failure")),
        recovery=_recovery_evidence(payload.get("recovery")),
    )


_RECOVERY_EVIDENCE_KEYS = frozenset(
    {
        "decision",
        "retained_run_id",
        "prior_fence",
        "replacement_fence",
    }
)
_RECOVERY_FENCE_KEYS = frozenset({"worker_id", "generation"})


def _recovery_evidence(
    value: object,
) -> ExecutionLeaseRecoveryEvidence | None:
    if value is None:
        return None
    malformed = False
    decoded = None
    try:
        if type(value) is not dict or frozenset(value) != _RECOVERY_EVIDENCE_KEYS:
            raise ValueError("recovery evidence shape is invalid")
        replacement_value = value["replacement_fence"]
        decoded = ExecutionLeaseRecoveryEvidence(
            decision_kind=RecoveryDecisionKind(value["decision"]),
            retained_run_id=RunId(value["retained_run_id"]),
            prior_fence=_recovery_fence(value["prior_fence"]),
            replacement_fence=(
                None
                if replacement_value is None
                else _recovery_fence(replacement_value)
            ),
        )
    except ValueError:
        malformed = True
    if malformed or decoded is None:
        raise OperationsRecordError(
            "persisted recovery evidence is malformed"
        ) from None
    return decoded


def _recovery_fence(value: object) -> ExecutionLeaseFence:
    if type(value) is not dict or frozenset(value) != _RECOVERY_FENCE_KEYS:
        raise ValueError("recovery fence shape is invalid")
    return ExecutionLeaseFence(
        worker_id=value["worker_id"],
        generation=value["generation"],
    )


def _failure_evidence(value: object) -> FailureEvidence | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("persisted activity failure must be an object")
    details = value.get("details", {})
    if not isinstance(details, dict):
        raise ValueError("persisted activity failure details must be an object")
    return FailureEvidence(
        category=FailureCategory(value["category"]),
        code=value["code"],
        message=value["message"],
        details=BoundedEvidence.from_mapping(details),
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _encode_optional_timestamp(value: str | None) -> object:
    return None if value is None else encode_postgres_timestamp(value)


def _decode_optional_timestamp(value: object) -> str | None:
    return None if value is None else decode_postgres_timestamp(value)


def _require_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise OperationsRecordError("run_id is malformed")


def _require_page_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise ReadPageError("run page identity is malformed")
