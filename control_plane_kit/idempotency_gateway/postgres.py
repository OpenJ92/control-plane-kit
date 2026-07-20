"""Postgres source-of-truth adapter for one idempotency gateway server."""

from __future__ import annotations

from typing import Any, Protocol

from control_plane_kit.domains.idempotency import (
    IdempotencyIdentity,
    IdempotencyMethod,
    IdempotencyRecord,
    IdempotencyRecordStatus,
)


IDEMPOTENCY_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cpk_idempotency_requests (
  request_id text PRIMARY KEY,
  gateway_id text NOT NULL,
  key_fingerprint text NOT NULL,
  tenant_fingerprint text NOT NULL,
  actor_fingerprint text NOT NULL,
  method text NOT NULL,
  route_fingerprint text NOT NULL,
  payload_fingerprint text NOT NULL,
  intent_fingerprint text NOT NULL,
  status text NOT NULL,
  created_at text NOT NULL,
  expires_at text NOT NULL,
  lease_expires_at text NOT NULL,
  result_status integer,
  result_reference text,
  completed_at text,
  CONSTRAINT cpk_idempotency_status_check CHECK (
    status IN ('in-flight', 'succeeded', 'failed', 'uncertain')
  ),
  CONSTRAINT cpk_idempotency_result_shape_check CHECK (
    (status IN ('succeeded', 'failed') AND result_status IS NOT NULL AND completed_at IS NOT NULL)
    OR
    (status IN ('in-flight', 'uncertain') AND result_status IS NULL AND completed_at IS NULL AND result_reference IS NULL)
  ),
  CONSTRAINT cpk_idempotency_method_check CHECK (
    method IN ('POST', 'PUT', 'PATCH', 'DELETE')
  ),
  UNIQUE (gateway_id, tenant_fingerprint, key_fingerprint)
);
CREATE INDEX IF NOT EXISTS cpk_idempotency_expiry
  ON cpk_idempotency_requests (gateway_id, expires_at);
"""


class Connection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


class PostgresIdempotencyStore:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def lock_key(self, gateway_id: str, tenant_fingerprint: str, key_fingerprint: str) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"idempotency:{gateway_id}:{tenant_fingerprint}:{key_fingerprint}",),
        )

    def add(self, record: IdempotencyRecord) -> IdempotencyRecord:
        identity = record.identity
        self._connection.execute(
            """
            INSERT INTO cpk_idempotency_requests
              (request_id, gateway_id, key_fingerprint, tenant_fingerprint,
               actor_fingerprint, method, route_fingerprint, payload_fingerprint,
               intent_fingerprint, status, created_at, expires_at, lease_expires_at,
               result_status, result_reference, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (record.request_id, identity.gateway_id, identity.key_fingerprint,
             identity.tenant_fingerprint, identity.actor_fingerprint,
             identity.method.value, identity.route_fingerprint,
             identity.payload_fingerprint, identity.intent_fingerprint,
             record.status.value, record.created_at, record.expires_at,
             record.lease_expires_at, record.result_status,
             record.result_reference, record.completed_at),
        )
        return record

    def get_for_key(self, gateway_id: str, tenant_fingerprint: str, key_fingerprint: str) -> IdempotencyRecord | None:
        row = self._connection.execute(
            """
            SELECT request_id, gateway_id, key_fingerprint, tenant_fingerprint,
                   actor_fingerprint, method, route_fingerprint, payload_fingerprint,
                   intent_fingerprint, status, created_at, expires_at,
                   lease_expires_at, result_status, result_reference, completed_at
            FROM cpk_idempotency_requests
            WHERE gateway_id = %s AND tenant_fingerprint = %s AND key_fingerprint = %s
            """,
            (gateway_id, tenant_fingerprint, key_fingerprint),
        ).fetchone()
        return None if row is None else _record(row)

    def complete(self, request_id: str, *, expected: IdempotencyRecordStatus, replacement: IdempotencyRecordStatus, result_status: int, result_reference: str | None, completed_at: str) -> IdempotencyRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_idempotency_requests
            SET status = %s, result_status = %s, result_reference = %s, completed_at = %s
            WHERE request_id = %s AND status = %s
            RETURNING request_id, gateway_id, key_fingerprint, tenant_fingerprint,
                      actor_fingerprint, method, route_fingerprint, payload_fingerprint,
                      intent_fingerprint, status, created_at, expires_at,
                      lease_expires_at, result_status, result_reference, completed_at
            """,
            (replacement.value, result_status, result_reference, completed_at, request_id, expected.value),
        ).fetchone()
        return None if row is None else _record(row)

    def mark_uncertain(self, request_id: str) -> IdempotencyRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_idempotency_requests SET status = 'uncertain'
            WHERE request_id = %s AND status = 'in-flight'
            RETURNING request_id, gateway_id, key_fingerprint, tenant_fingerprint,
                      actor_fingerprint, method, route_fingerprint, payload_fingerprint,
                      intent_fingerprint, status, created_at, expires_at,
                      lease_expires_at, result_status, result_reference, completed_at
            """,
            (request_id,),
        ).fetchone()
        return None if row is None else _record(row)

    def delete_expired_terminal(self, gateway_id: str, observed_at: str) -> int:
        result = self._connection.execute(
            """
            DELETE FROM cpk_idempotency_requests
            WHERE gateway_id = %s AND expires_at <= %s
              AND status IN ('succeeded', 'failed')
            """,
            (gateway_id, observed_at),
        )
        return result.rowcount

    def count_for_gateway(self, gateway_id: str) -> int:
        return self._connection.execute(
            "SELECT count(*) FROM cpk_idempotency_requests WHERE gateway_id = %s",
            (gateway_id,),
        ).fetchone()[0]


def _record(row: tuple[Any, ...]) -> IdempotencyRecord:
    identity = IdempotencyIdentity(
        row[1], row[2], row[3], row[4], IdempotencyMethod(row[5]), row[6], row[7], row[8]
    )
    return IdempotencyRecord(
        row[0], identity, IdempotencyRecordStatus(row[9]), row[10], row[11],
        row[12], row[13], row[14], row[15]
    )
