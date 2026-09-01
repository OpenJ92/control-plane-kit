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
from control_plane_kit_core.policies import PolicyScope
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
    CoordinatorStatus,
    ExecutionCommandReceiptRecord,
    ExecutionCommandReceiptStatus,
    ExecutionCommandResultRecord,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    FailureEvidence,
    OperationsRecordError,
    RetryIdentity,
    canonical_positive_decimal,
    positive_int_from_canonical_decimal,
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

    def lock_command_idempotency(
        self,
        run_id: str,
        idempotency_key: str,
    ) -> None:
        """Serialize one run-scoped coordinator command before receipt lookup."""

        _require_run_id(run_id)
        _require_command_key(idempotency_key)
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"execution-command:{run_id}:{idempotency_key}",),
        )

    def add_command_receipt(
        self,
        record: ExecutionCommandReceiptRecord,
    ) -> ExecutionCommandReceiptRecord:
        if type(record) is not ExecutionCommandReceiptRecord:
            raise OperationsRecordError("execution command receipt must be typed")
        self._connection.execute(
            """
            INSERT INTO cpk_execution_command_receipts
              (run_id, idempotency_key, intent_fingerprint, worker_id,
               authority_scopes, claim_generation, max_effects, admitted_at,
               initial_run, receipt_status, completed_at, result)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb,
                    %s, %s, %s::jsonb)
            """,
            (
                record.run_id,
                record.idempotency_key,
                record.intent_fingerprint,
                record.worker_id,
                _json([scope.value for scope in record.authority_scopes]),
                record.claim_generation,
                canonical_positive_decimal(record.max_effects),
                encode_postgres_timestamp(record.admitted_at),
                _json(_run_descriptor(record.initial_run)),
                record.status.value,
                _encode_optional_timestamp(record.completed_at),
                None if record.result is None else _json(_result_descriptor(record.result)),
            ),
        )
        return record

    def command_receipt_for_idempotency(
        self,
        run_id: str,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> ExecutionCommandReceiptRecord | None:
        _require_run_id(run_id)
        _require_command_key(idempotency_key)
        lock = "FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            SELECT run_id, idempotency_key, intent_fingerprint, worker_id,
                   authority_scopes, claim_generation, max_effects, admitted_at,
                   initial_run, receipt_status, completed_at, result
            FROM cpk_execution_command_receipts
            WHERE run_id = %s AND idempotency_key = %s
            {lock}
            """,
            (run_id, idempotency_key),
        ).fetchone()
        return None if row is None else _command_receipt(row)

    def complete_command_receipt(
        self,
        run_id: str,
        idempotency_key: str,
        *,
        intent_fingerprint: str,
        completed_at: str,
        result: ExecutionCommandResultRecord,
    ) -> ExecutionCommandReceiptRecord | None:
        _require_run_id(run_id)
        _require_command_key(idempotency_key)
        if type(result) is not ExecutionCommandResultRecord:
            raise OperationsRecordError("execution command result must be typed")
        row = self._connection.execute(
            """
            UPDATE cpk_execution_command_receipts
            SET receipt_status = 'completed', completed_at = %s,
                result = %s::jsonb
            WHERE run_id = %s
              AND idempotency_key = %s
              AND intent_fingerprint = %s
              AND receipt_status = 'incomplete'
              AND completed_at IS NULL
              AND result IS NULL
            RETURNING run_id, idempotency_key, intent_fingerprint, worker_id,
                      authority_scopes, claim_generation, max_effects, admitted_at,
                      initial_run, receipt_status, completed_at, result
            """,
            (
                encode_postgres_timestamp(completed_at),
                _json(_result_descriptor(result)),
                run_id,
                idempotency_key,
                intent_fingerprint,
            ),
        ).fetchone()
        return None if row is None else _command_receipt(row)

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

    def get_latest_run_for_request_for_update(
        self,
        request_id: str,
    ) -> ActivityRunRecord:
        _recovery_request_id(request_id)
        row = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE request_id = %s
            ORDER BY attempt DESC
            LIMIT 1
            FOR UPDATE
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError("missing activity run")
        return _activity_run(row)

    def get_run_for_request_for_update(
        self,
        request_id: str,
        run_id: str,
    ) -> ActivityRunRecord:
        _recovery_request_id(request_id)
        _require_run_id(run_id)
        row = self._connection.execute(
            """
            SELECT run_id, plan_id, request_id, attempt, prior_run_id, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs
            WHERE request_id = %s AND run_id = %s
            FOR UPDATE
            """,
            (request_id, run_id),
        ).fetchone()
        if row is None:
            raise KeyError("activity run was not found for request")
        return _activity_run(row)

    def rotate_request_claim(
        self,
        request_id: str,
        *,
        expected_fence: ExecutionLeaseFence,
        replacement_fence: ExecutionLeaseFence,
        observed_at: str,
        lease_duration_seconds: int,
    ) -> ExecutionRequestRecord | None:
        _recovery_request_id(request_id)
        _recovery_fence_pair(expected_fence, replacement_fence)
        encoded_observed_at = _recovery_observed_at(observed_at)
        if (
            type(lease_duration_seconds) is not int
            or not 1 <= lease_duration_seconds <= 3600
        ):
            raise OperationsRecordError("recovery lease duration is invalid")
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET claim_worker_id = %s,
                claim_generation = %s,
                claimed_at = %s,
                lease_expires_at = %s + (%s * interval '1 second')
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
              AND claim_generation = %s
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claim_generation, claimed_at,
                      lease_expires_at
            """,
            (
                replacement_fence.worker_id,
                replacement_fence.generation,
                encoded_observed_at,
                encoded_observed_at,
                lease_duration_seconds,
                request_id,
                expected_fence.worker_id,
                expected_fence.generation,
            ),
        ).fetchone()
        return None if row is None else _execution_request(row)

    def abandon_request_claim(
        self,
        request_id: str,
        *,
        expected_fence: ExecutionLeaseFence,
        observed_at: str,
    ) -> ExecutionRequestRecord | None:
        _recovery_request_id(request_id)
        if type(expected_fence) is not ExecutionLeaseFence:
            raise OperationsRecordError("recovery claim fence is invalid")
        encoded_observed_at = _recovery_observed_at(observed_at)
        row = self._connection.execute(
            """
            UPDATE cpk_execution_requests
            SET status = 'abandoned',
                claim_worker_id = NULL,
                claim_generation = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL
            WHERE request_id = %s
              AND status = 'claimed'
              AND claim_worker_id = %s
              AND claim_generation = %s
              AND lease_expires_at <= %s
            RETURNING request_id, workspace_id, session_id, plan_id, status,
                      requested_by, requested_at, approval_request_id,
                      approval_decision_id, idempotency_key, intent_fingerprint,
                      claim_worker_id, claim_generation, claimed_at,
                      lease_expires_at
            """,
            (
                request_id,
                expected_fence.worker_id,
                expected_fence.generation,
                encoded_observed_at,
            ),
        ).fetchone()
        return None if row is None else _execution_request(row)

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


_RUN_DESCRIPTOR_KEYS = frozenset(
    {
        "run_id",
        "plan_id",
        "request_id",
        "attempt",
        "prior_run_id",
        "status",
        "created_at",
        "started_at",
        "settled_at",
        "metadata",
    }
)
_RESULT_DESCRIPTOR_KEYS = frozenset(
    {"run", "status", "effects_attempted", "activity_id"}
)


def _run_descriptor(record: ActivityRunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "plan_id": record.plan_id,
        "request_id": record.admission.request_id,
        "attempt": record.retry.attempt,
        "prior_run_id": record.retry.prior_run_id,
        "status": record.status.value,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "settled_at": record.settled_at,
        "metadata": record.metadata.descriptor(),
    }


def _run_from_descriptor(value: object) -> ActivityRunRecord:
    if type(value) is not dict or frozenset(value) != _RUN_DESCRIPTOR_KEYS:
        raise OperationsRecordError("persisted execution command run is malformed")
    metadata = value["metadata"]
    if type(metadata) is not dict:
        raise OperationsRecordError("persisted execution command metadata is malformed")
    try:
        return ActivityRunRecord(
            run_id=value["run_id"],
            plan_id=value["plan_id"],
            admission=AdmittedRun(value["request_id"]),
            retry=RetryIdentity(value["attempt"], value["prior_run_id"]),
            status=ActivityRunStatus(value["status"]),
            created_at=value["created_at"],
            started_at=value["started_at"],
            settled_at=value["settled_at"],
            metadata=BoundedEvidence.from_mapping(metadata),
        )
    except (TypeError, ValueError):
        raise OperationsRecordError(
            "persisted execution command run is malformed"
        ) from None


def _result_descriptor(record: ExecutionCommandResultRecord) -> dict[str, object]:
    return {
        "run": _run_descriptor(record.run),
        "status": record.status.value,
        "effects_attempted": record.effects_attempted,
        "activity_id": record.activity_id,
    }


def _result_from_descriptor(value: object) -> ExecutionCommandResultRecord:
    if type(value) is not dict or frozenset(value) != _RESULT_DESCRIPTOR_KEYS:
        raise OperationsRecordError("persisted execution command result is malformed")
    try:
        return ExecutionCommandResultRecord(
            run=_run_from_descriptor(value["run"]),
            status=CoordinatorStatus(value["status"]),
            effects_attempted=value["effects_attempted"],
            activity_id=value["activity_id"],
        )
    except (TypeError, ValueError):
        raise OperationsRecordError(
            "persisted execution command result is malformed"
        ) from None


def _command_receipt(row: tuple[Any, ...]) -> ExecutionCommandReceiptRecord:
    scopes = row[4]
    if type(scopes) is not list:
        raise OperationsRecordError("persisted execution command scopes are malformed")
    try:
        return ExecutionCommandReceiptRecord(
            run_id=row[0],
            idempotency_key=row[1],
            intent_fingerprint=row[2],
            worker_id=row[3],
            authority_scopes=tuple(PolicyScope(value) for value in scopes),
            claim_generation=row[5],
            max_effects=positive_int_from_canonical_decimal(row[6]),
            admitted_at=decode_postgres_timestamp(row[7]),
            initial_run=_run_from_descriptor(row[8]),
            status=ExecutionCommandReceiptStatus(row[9]),
            completed_at=_decode_optional_timestamp(row[10]),
            result=None if row[11] is None else _result_from_descriptor(row[11]),
        )
    except (TypeError, ValueError):
        raise OperationsRecordError(
            "persisted execution command receipt is malformed"
        ) from None


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
    if not {"category", "code", "message"} <= value.keys():
        raise ValueError("persisted activity failure is malformed")
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


def _recovery_request_id(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise OperationsRecordError("recovery request identity is invalid")


def _recovery_observed_at(value: object) -> object:
    if type(value) is not str:
        raise OperationsRecordError("recovery observation time is invalid")
    try:
        return encode_postgres_timestamp(value)
    except ValueError:
        pass
    raise OperationsRecordError("recovery observation time is invalid")


def _recovery_fence_pair(
    expected: object,
    replacement: object,
) -> None:
    if (
        type(expected) is not ExecutionLeaseFence
        or type(replacement) is not ExecutionLeaseFence
        or expected.generation >= 2**63 - 1
        or replacement.generation != expected.generation + 1
    ):
        raise OperationsRecordError("recovery claim fence is invalid")


def _require_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise OperationsRecordError("run_id is malformed")


def _require_command_key(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 200
        or any(ord(character) < 32 for character in value)
    ):
        raise OperationsRecordError("execution command key is malformed")


def _require_page_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        return
    raise ReadPageError("run page identity is malformed")
