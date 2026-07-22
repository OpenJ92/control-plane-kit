"""Postgres store for operation sessions and command history."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.records import (
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
)


class PostgresActivityHistoryStore:
    """Postgres-backed operation session and action history store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def add_session(self, record: OperationSessionRecord) -> OperationSessionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at, closed_at,
               metadata, idempotency_key, intent_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.session_id,
                record.workspace_id,
                record.actor_id,
                record.title,
                record.status.value,
                record.created_at,
                record.closed_at,
                Jsonb(record.metadata),
                record.idempotency_key,
                record.intent_fingerprint,
            ),
        )
        return record

    def lock_session_idempotency(self, workspace_id: str, idempotency_key: str) -> None:
        """Serialize session starts before the session row exists."""

        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"operation-session:{workspace_id}:{idempotency_key}",),
        )

    def get_session(self, session_id: str) -> OperationSessionRecord:
        row = self._connection.execute(
            """
            SELECT session_id, workspace_id, actor_id, title, status, created_at,
                   closed_at, metadata, idempotency_key, intent_fingerprint
            FROM cpk_operation_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing session {session_id!r}")
        return _session_record(row)

    def session_for_idempotency(
        self,
        workspace_id: str,
        idempotency_key: str,
    ) -> OperationSessionRecord | None:
        row = self._connection.execute(
            """
            SELECT session_id, workspace_id, actor_id, title, status, created_at,
                   closed_at, metadata, idempotency_key, intent_fingerprint
            FROM cpk_operation_sessions
            WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (workspace_id, idempotency_key),
        ).fetchone()
        return None if row is None else _session_record(row)

    def sessions_for_workspace(
        self,
        workspace_id: str,
    ) -> tuple[OperationSessionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT session_id, workspace_id, actor_id, title, status, created_at,
                   closed_at, metadata, idempotency_key, intent_fingerprint
            FROM cpk_operation_sessions
            WHERE workspace_id = %s
            ORDER BY created_at ASC, session_id ASC
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_session_record(row) for row in rows)

    def transition_open_session(
        self,
        session_id: str,
        *,
        replacement: OperationSessionStatus,
        closed_at: str,
    ) -> OperationSessionRecord | None:
        if replacement not in {
            OperationSessionStatus.CLOSED,
            OperationSessionStatus.CANCELLED,
        }:
            raise ValueError("operation sessions may transition only to terminal")
        row = self._connection.execute(
            """
            UPDATE cpk_operation_sessions
            SET status = %s, closed_at = %s
            WHERE session_id = %s AND status = 'open'
            RETURNING session_id, workspace_id, actor_id, title, status, created_at,
                      closed_at, metadata, idempotency_key, intent_fingerprint
            """,
            (replacement.value, closed_at, session_id),
        ).fetchone()
        return None if row is None else _session_record(row)

    def add_action(self, record: OperationActionRecord) -> OperationActionRecord:
        self._connection.execute(
            """
            INSERT INTO cpk_operation_actions
              (action_id, session_id, ordinal, action_type, actor_id, payload,
               created_at, idempotency_key, intent_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.action_id,
                record.session_id,
                record.ordinal,
                record.action_type.value,
                record.actor_id,
                Jsonb(record.payload),
                record.created_at,
                record.idempotency_key,
                record.intent_fingerprint,
            ),
        )
        return record

    def action_for_idempotency(
        self,
        session_id: str,
        idempotency_key: str,
    ) -> OperationActionRecord | None:
        row = self._connection.execute(
            """
            SELECT action_id, session_id, ordinal, action_type, actor_id, payload,
                   created_at, idempotency_key, intent_fingerprint
            FROM cpk_operation_actions
            WHERE session_id = %s AND idempotency_key = %s
            """,
            (session_id, idempotency_key),
        ).fetchone()
        return None if row is None else _action_record(row)

    def next_action_ordinal(self, session_id: str) -> int:
        session = self._connection.execute(
            "SELECT session_id FROM cpk_operation_sessions WHERE session_id = %s FOR UPDATE",
            (session_id,),
        ).fetchone()
        if session is None:
            raise KeyError(f"missing session {session_id!r}")
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0) + 1
            FROM cpk_operation_actions
            WHERE session_id = %s
            """,
            (session_id,),
        ).fetchone()
        return int(row[0])

    def actions_for_session(self, session_id: str) -> tuple[OperationActionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT action_id, session_id, ordinal, action_type, actor_id, payload,
                   created_at, idempotency_key, intent_fingerprint
            FROM cpk_operation_actions
            WHERE session_id = %s
            ORDER BY ordinal ASC
            """,
            (session_id,),
        ).fetchall()
        return tuple(_action_record(row) for row in rows)


def _session_record(row: tuple[Any, ...]) -> OperationSessionRecord:
    return OperationSessionRecord(
        session_id=row[0],
        workspace_id=row[1],
        actor_id=row[2],
        title=row[3],
        status=OperationSessionStatus(row[4]),
        created_at=row[5],
        closed_at=row[6],
        metadata=row[7],
        idempotency_key=row[8],
        intent_fingerprint=row[9],
    )


def _action_record(row: tuple[Any, ...]) -> OperationActionRecord:
    return OperationActionRecord(
        action_id=row[0],
        session_id=row[1],
        ordinal=row[2],
        action_type=OperatorCommandKind(row[3]),
        actor_id=row[4],
        payload=row[5],
        created_at=row[6],
        idempotency_key=row[7],
        intent_fingerprint=row[8],
    )
