"""Postgres store for delegated gateway probe intent and result evidence."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.gateway_delegation import (
    GatewayProbeAccessPath,
    GatewayProbeCommandKind,
)
from control_plane_kit_operations.gateway_probes import (
    GatewayProbeAttempt,
    GatewayProbeAttemptStatus,
    GatewayProbeConflict,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.records import BoundedEvidence


class GatewayProbeStore:
    """Persist bounded probe evidence without compact grants or signatures."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def lock_request_id(self, workspace_id: str, request_id: str) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"gateway-probe:{workspace_id}:{request_id}",),
        )

    def add(self, record: GatewayProbeAttempt) -> GatewayProbeAttempt:
        self._connection.execute(
            """
            INSERT INTO cpk_gateway_probe_attempts (
              probe_id, workspace_id, request_id, actor_id, current_graph_id,
              gateway_node_id, gateway_runtime_id, probe_kind, target_id,
              access_path, request_digest, issuer, key_id, audience, grant_jti, issued_at,
              expires_at, status, requested_at, intent_fingerprint,
              completed_at, result_code, evidence
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            _record_values(record),
        )
        return record

    def get(self, probe_id: str) -> GatewayProbeAttempt:
        row = self._connection.execute(
            f"{_SELECT} WHERE probe_id = %s",
            (probe_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing gateway probe {probe_id!r}")
        return _row_to_attempt(row)

    def get_by_request_id(
        self,
        workspace_id: str,
        request_id: str,
    ) -> GatewayProbeAttempt | None:
        row = self._connection.execute(
            f"{_SELECT} WHERE workspace_id = %s AND request_id = %s",
            (workspace_id, request_id),
        ).fetchone()
        return None if row is None else _row_to_attempt(row)

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[GatewayProbeAttempt, ...]:
        rows = self._connection.execute(
            f"""
            {_SELECT}
            WHERE workspace_id = %s
            ORDER BY issued_at DESC, probe_id DESC
            LIMIT %s OFFSET %s
            """,
            (workspace_id, limit, offset),
        ).fetchall()
        return tuple(_row_to_attempt(row) for row in rows)

    def count_for_workspace(self, workspace_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT COUNT(*)
            FROM cpk_gateway_probe_attempts
            WHERE workspace_id = %s
            """,
            (workspace_id,),
        ).fetchone()
        return int(row[0])

    def complete(
        self,
        probe_id: str,
        *,
        status: GatewayProbeAttemptStatus,
        completed_at: str,
        result_code: str,
        evidence: BoundedEvidence,
    ) -> GatewayProbeAttempt:
        if status is GatewayProbeAttemptStatus.INTENDED:
            raise GatewayProbeConflict("gateway probe completion must be terminal")
        row = self._connection.execute(
            f"{_SELECT} WHERE probe_id = %s FOR UPDATE",
            (probe_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing gateway probe {probe_id!r}")
        current = _row_to_attempt(row)
        if current.status is not GatewayProbeAttemptStatus.INTENDED:
            return current
        updated = self._connection.execute(
            f"""
            UPDATE cpk_gateway_probe_attempts
            SET status = %s, completed_at = %s, result_code = %s, evidence = %s
            WHERE probe_id = %s AND status = 'intended'
            RETURNING {_COLUMNS}
            """,
            (
                status.value,
                completed_at,
                result_code,
                Jsonb(evidence.descriptor()),
                probe_id,
            ),
        ).fetchone()
        if updated is None:
            raise GatewayProbeConflict("gateway probe result was folded concurrently")
        return _row_to_attempt(updated)


_COLUMNS = """
probe_id, workspace_id, request_id, actor_id, current_graph_id,
gateway_node_id, gateway_runtime_id, probe_kind, target_id, request_digest,
access_path, issuer, key_id, audience, grant_jti, issued_at, expires_at, status,
requested_at, intent_fingerprint, completed_at, result_code, evidence
"""
_SELECT = f"SELECT {_COLUMNS} FROM cpk_gateway_probe_attempts"


def _record_values(record: GatewayProbeAttempt) -> tuple[object, ...]:
    return (
        record.probe_id,
        record.workspace_id,
        record.request_id,
        record.actor_id,
        record.current_graph_id,
        record.gateway_node_id,
        record.gateway_runtime_id,
        record.probe_kind.value,
        record.target_id,
        record.access_path.value,
        record.request_digest,
        record.issuer,
        record.key_id,
        record.audience,
        record.grant_jti,
        record.issued_at,
        record.expires_at,
        record.status.value,
        record.requested_at,
        record.intent_fingerprint,
        record.completed_at,
        record.result_code,
        Jsonb(record.evidence.descriptor()),
    )


def _row_to_attempt(row: tuple[Any, ...]) -> GatewayProbeAttempt:
    return GatewayProbeAttempt(
        probe_id=row[0],
        workspace_id=row[1],
        request_id=row[2],
        actor_id=row[3],
        current_graph_id=row[4],
        gateway_node_id=row[5],
        gateway_runtime_id=row[6],
        probe_kind=GatewayProbeCommandKind(row[7]),
        target_id=row[8],
        request_digest=row[9],
        access_path=GatewayProbeAccessPath(row[10]),
        issuer=row[11],
        key_id=row[12],
        audience=row[13],
        grant_jti=row[14],
        issued_at=row[15],
        expires_at=row[16],
        status=GatewayProbeAttemptStatus(row[17]),
        requested_at=row[18],
        intent_fingerprint=row[19],
        completed_at=row[20],
        result_code=row[21],
        evidence=BoundedEvidence.from_mapping(row[22]),
    )
