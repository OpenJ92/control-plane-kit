"""Postgres store for execution admission and run ownership."""

from __future__ import annotations

from typing import Any

from control_plane_kit_core.operations.lifecycle import ExecutionRequestStatus
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.records import (
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
)


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
               claim_worker_id, claimed_at, lease_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.identity.request_id,
                record.identity.workspace_id,
                record.identity.session_id,
                record.identity.plan_id,
                record.status.value,
                record.requested_by,
                record.requested_at,
                record.approval_request_id,
                record.approval_decision_id,
                record.idempotency.key,
                record.idempotency.intent_fingerprint,
                None if claim is None else claim.worker_id,
                None if claim is None else claim.claimed_at,
                None if claim is None else claim.lease_expires_at,
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
                   claim_worker_id, claimed_at, lease_expires_at
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
                   claim_worker_id, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (workspace_id, idempotency_key),
        ).fetchone()
        return None if row is None else _execution_request(row)


def _execution_request(row: tuple[Any, ...]) -> ExecutionRequestRecord:
    claim = (
        None
        if row[11] is None
        else ClaimIdentity(
            worker_id=row[11],
            claimed_at=row[12],
            lease_expires_at=row[13],
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
        requested_at=row[6],
        approval_request_id=row[7],
        approval_decision_id=row[8],
        idempotency=ExecutionIdempotency(
            key=row[9],
            intent_fingerprint=row[10],
        ),
        claim=claim,
    )
