"""Postgres source-of-truth adapter for one discovery registry server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Protocol

from control_plane_kit.discovery import (
    DiscoveryIdentity,
    DiscoveryLease,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    DiscoveryRegistrationRecord,
    DiscoveryRegistrationStatus,
)
from control_plane_kit.topology.graph import Endpoint, LiteralAddress
from control_plane_kit.core.types import EndpointScope, Protocol as ConnectionProtocol


DISCOVERY_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cpk_discovery_registrations (
  workspace_id text NOT NULL,
  service_id text NOT NULL,
  instance_id text NOT NULL,
  address text NOT NULL,
  protocol_transport text NOT NULL,
  protocol_application text NOT NULL,
  endpoint_scope text NOT NULL,
  registration_mode text NOT NULL,
  issued_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  status text NOT NULL,
  revision bigint NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (workspace_id, service_id, instance_id),
  CONSTRAINT cpk_discovery_status_check CHECK (
    status IN ('active', 'deregistered', 'expired')
  ),
  CONSTRAINT cpk_discovery_mode_check CHECK (
    registration_mode IN ('control-plane', 'self')
  ),
  CONSTRAINT cpk_discovery_scope_check CHECK (
    endpoint_scope IN ('private', 'public')
  ),
  CONSTRAINT cpk_discovery_protocol_check CHECK (
    (protocol_application = 'raw' AND protocol_transport IN ('tcp', 'udp'))
    OR
    (protocol_application = 'dns' AND protocol_transport IN ('tcp', 'udp'))
    OR
    (
      protocol_transport = 'tcp'
      AND protocol_application IN (
        'http', 'postgres', 'redis', 'smtp', 'otlp-http', 'otlp-grpc',
        'nats', 'amqp', 'kafka', 's3'
      )
    )
  ),
  CONSTRAINT cpk_discovery_lease_check CHECK (expires_at > issued_at),
  CONSTRAINT cpk_discovery_revision_check CHECK (revision > 0)
);
CREATE INDEX IF NOT EXISTS cpk_discovery_resolution
  ON cpk_discovery_registrations
  (workspace_id, service_id, status, expires_at, instance_id);

CREATE TABLE IF NOT EXISTS cpk_discovery_commands (
  command_id text PRIMARY KEY,
  workspace_id text NOT NULL,
  variant text NOT NULL,
  intent_fingerprint text NOT NULL,
  actor_id text NOT NULL,
  result_descriptor jsonb NOT NULL,
  recorded_at timestamptz NOT NULL,
  CONSTRAINT cpk_discovery_command_variant_check CHECK (
    variant IN ('register', 'heartbeat', 'deregister', 'resolve', 'expire')
  )
);
CREATE INDEX IF NOT EXISTS cpk_discovery_commands_workspace
  ON cpk_discovery_commands (workspace_id, recorded_at, command_id);
"""


class Connection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> Any: ...


@dataclass(frozen=True)
class PostgresDiscoveryCommandRecord:
    command_id: str
    intent_fingerprint: str
    result_descriptor: dict[str, object]


class PostgresDiscoveryStore:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def lock_command(self, command_id: str) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"discovery-command:{command_id}",),
        )

    def get_command(self, command_id: str) -> PostgresDiscoveryCommandRecord | None:
        row = self._connection.execute(
            """
            SELECT command_id, intent_fingerprint, result_descriptor
            FROM cpk_discovery_commands WHERE command_id = %s
            """,
            (command_id,),
        ).fetchone()
        return None if row is None else PostgresDiscoveryCommandRecord(row[0], row[1], row[2])

    def add_command(
        self,
        command_id: str,
        workspace_id: str,
        variant: str,
        intent_fingerprint: str,
        actor_id: str,
        result_descriptor: dict[str, object],
        recorded_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO cpk_discovery_commands
              (command_id, workspace_id, variant, intent_fingerprint,
               actor_id, result_descriptor, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                command_id,
                workspace_id,
                variant,
                intent_fingerprint,
                actor_id,
                _json(result_descriptor),
                recorded_at,
            ),
        )

    def lock_identity(self, identity: DiscoveryIdentity) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"discovery:{identity.workspace_id}:{identity.service_id}:{identity.instance_id}",),
        )

    def get(self, identity: DiscoveryIdentity) -> DiscoveryRegistrationRecord | None:
        row = self._connection.execute(
            _SELECT + " WHERE workspace_id = %s AND service_id = %s AND instance_id = %s",
            (identity.workspace_id, identity.service_id, identity.instance_id),
        ).fetchone()
        return None if row is None else _record(row)

    def register(
        self,
        registration: DiscoveryRegistration,
        updated_at: datetime,
    ) -> DiscoveryRegistrationRecord | None:
        identity = registration.identity
        endpoint = registration.endpoint
        row = self._connection.execute(
            """
            INSERT INTO cpk_discovery_registrations
              (workspace_id, service_id, instance_id, address,
               protocol_transport, protocol_application, endpoint_scope,
               registration_mode, issued_at, expires_at, status, revision, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', 1, %s)
            ON CONFLICT (workspace_id, service_id, instance_id) DO UPDATE SET
              address = EXCLUDED.address,
              protocol_transport = EXCLUDED.protocol_transport,
              protocol_application = EXCLUDED.protocol_application,
              endpoint_scope = EXCLUDED.endpoint_scope,
              registration_mode = EXCLUDED.registration_mode,
              issued_at = EXCLUDED.issued_at,
              expires_at = EXCLUDED.expires_at,
              status = 'active',
              revision = cpk_discovery_registrations.revision + 1,
              updated_at = EXCLUDED.updated_at
            WHERE cpk_discovery_registrations.status <> 'active'
               OR cpk_discovery_registrations.expires_at <= EXCLUDED.updated_at
            RETURNING workspace_id, service_id, instance_id, address,
              protocol_transport, protocol_application, endpoint_scope,
              registration_mode, issued_at, expires_at, status, revision, updated_at
            """,
            (
                identity.workspace_id,
                identity.service_id,
                identity.instance_id,
                endpoint.url,
                endpoint.protocol.transport.value,
                endpoint.protocol.application.value,
                endpoint.scope.value,
                registration.mode.value,
                registration.lease.issued_at,
                registration.lease.expires_at,
                updated_at,
            ),
        ).fetchone()
        return None if row is None else _record(row)

    def heartbeat(
        self,
        identity: DiscoveryIdentity,
        expected_expires_at: datetime,
        replacement: DiscoveryRegistration,
        updated_at: datetime,
    ) -> DiscoveryRegistrationRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_discovery_registrations
            SET issued_at = %s, expires_at = %s, revision = revision + 1, updated_at = %s
            WHERE workspace_id = %s AND service_id = %s AND instance_id = %s
              AND status = 'active' AND expires_at = %s
            RETURNING workspace_id, service_id, instance_id, address,
              protocol_transport, protocol_application, endpoint_scope,
              registration_mode, issued_at, expires_at, status, revision, updated_at
            """,
            (
                replacement.lease.issued_at,
                replacement.lease.expires_at,
                updated_at,
                identity.workspace_id,
                identity.service_id,
                identity.instance_id,
                expected_expires_at,
            ),
        ).fetchone()
        return None if row is None else _record(row)

    def set_status(
        self,
        identity: DiscoveryIdentity,
        expected_expires_at: datetime,
        replacement: DiscoveryRegistrationStatus,
        updated_at: datetime,
    ) -> DiscoveryRegistrationRecord | None:
        row = self._connection.execute(
            """
            UPDATE cpk_discovery_registrations
            SET status = %s, revision = revision + 1, updated_at = %s
            WHERE workspace_id = %s AND service_id = %s AND instance_id = %s
              AND status = 'active' AND expires_at = %s
            RETURNING workspace_id, service_id, instance_id, address,
              protocol_transport, protocol_application, endpoint_scope,
              registration_mode, issued_at, expires_at, status, revision, updated_at
            """,
            (
                replacement.value,
                updated_at,
                identity.workspace_id,
                identity.service_id,
                identity.instance_id,
                expected_expires_at,
            ),
        ).fetchone()
        return None if row is None else _record(row)

    def resolve(
        self,
        workspace_id: str,
        service_id: str,
        observed_at: datetime,
        limit: int,
    ) -> tuple[DiscoveryRegistrationRecord, ...]:
        rows = self._connection.execute(
            _SELECT + """
            WHERE workspace_id = %s AND service_id = %s
              AND status = 'active' AND expires_at > %s
            ORDER BY instance_id LIMIT %s
            """,
            (workspace_id, service_id, observed_at, limit),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def expire(
        self,
        workspace_id: str,
        observed_at: datetime,
        limit: int,
    ) -> tuple[DiscoveryRegistrationRecord, ...]:
        rows = self._connection.execute(
            """
            WITH candidates AS (
              SELECT workspace_id, service_id, instance_id
              FROM cpk_discovery_registrations
              WHERE workspace_id = %s AND status = 'active' AND expires_at <= %s
              ORDER BY expires_at, service_id, instance_id
              LIMIT %s FOR UPDATE SKIP LOCKED
            )
            UPDATE cpk_discovery_registrations AS registration
            SET status = 'expired', revision = revision + 1, updated_at = %s
            FROM candidates
            WHERE registration.workspace_id = candidates.workspace_id
              AND registration.service_id = candidates.service_id
              AND registration.instance_id = candidates.instance_id
            RETURNING registration.workspace_id, registration.service_id,
              registration.instance_id, registration.address,
              registration.protocol_transport, registration.protocol_application,
              registration.endpoint_scope, registration.registration_mode,
              registration.issued_at, registration.expires_at, registration.status,
              registration.revision, registration.updated_at
            """,
            (workspace_id, observed_at, limit, observed_at),
        ).fetchall()
        return tuple(sorted((_record(row) for row in rows), key=_record_key))


_SELECT = """
SELECT workspace_id, service_id, instance_id, address,
  protocol_transport, protocol_application, endpoint_scope,
  registration_mode, issued_at, expires_at, status, revision, updated_at
FROM cpk_discovery_registrations
"""


def _record(row: tuple[Any, ...]) -> DiscoveryRegistrationRecord:
    identity = DiscoveryIdentity(row[0], row[1], row[2])
    protocol = ConnectionProtocol.from_descriptor(
        {"transport": row[4], "application": row[5]}
    )
    registration = DiscoveryRegistration(
        identity,
        Endpoint(LiteralAddress(row[3]), protocol, EndpointScope(row[6])),
        DiscoveryRegistrationMode(row[7]),
        DiscoveryLease(row[8], row[9]),
    )
    return DiscoveryRegistrationRecord(
        registration,
        DiscoveryRegistrationStatus(row[10]),
        row[11],
        row[12],
    )


def _record_key(value: DiscoveryRegistrationRecord) -> tuple[str, str]:
    identity = value.registration.identity
    return identity.service_id, identity.instance_id


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
