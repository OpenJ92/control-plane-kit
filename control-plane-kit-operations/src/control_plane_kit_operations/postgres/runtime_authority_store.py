"""Postgres store for workspace-scoped runtime authorities."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryCodec,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.runtime_authorities import (
    DockerRuntimeAuthority,
    DockerRuntimeAuthorityCodec,
    RegisteredRuntimeAuthorityDelivery,
    RegisteredRuntimeAuthorityDeliveryStatus,
    RegisteredRuntimeAuthority,
    RegisteredRuntimeAuthorityStatus,
    RuntimeAuthorityKind,
    RuntimeAuthorityNotFound,
    RuntimeAuthorityRegistrationConflict,
)


class RuntimeAuthorityStore:
    """Persist runtime authorities admitted into one workspace."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        *,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
        runtime_kind: RuntimeKind,
        authority: DockerRuntimeAuthority,
        admitted_by: str,
        admitted_at: str,
    ) -> RegisteredRuntimeAuthority:
        candidate = RegisteredRuntimeAuthority.from_authority(
            workspace_id=workspace_id,
            authority_ref=authority_ref,
            runtime_kind=runtime_kind,
            authority=authority,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )
        existing = self._get_active_by_ref(workspace_id, authority_ref)
        if existing is not None:
            if (
                existing.runtime_kind == candidate.runtime_kind
                and existing.authority == candidate.authority
            ):
                return existing
            raise RuntimeAuthorityRegistrationConflict(
                "registered runtime authority replacement requires explicit replacement policy"
            )

        self._connection.execute(
            """
            INSERT INTO cpk_runtime_authorities (
              registration_id,
              workspace_id,
              authority_ref,
              runtime_kind,
              authority_kind,
              authority,
              credential_references,
              admitted_by,
              admitted_at,
              status,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (
                candidate.registration_id,
                candidate.workspace_id,
                candidate.authority_ref.reference_id,
                candidate.runtime_kind.value,
                candidate.authority_kind.value,
                Jsonb(DockerRuntimeAuthorityCodec().encode(candidate.authority)),
                Jsonb(_credential_references(candidate)),
                candidate.admitted_by,
                candidate.admitted_at,
                candidate.status.value,
            ),
        )
        return candidate

    def get(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> RegisteredRuntimeAuthority:
        row = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              authority_ref,
              runtime_kind,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_runtime_authorities
            WHERE workspace_id = %s
              AND authority_ref = %s
            ORDER BY
              CASE status WHEN 'active' THEN 0 ELSE 1 END,
              admitted_at DESC,
              registration_id DESC
            LIMIT 1
            """,
            (workspace_id, authority_ref.reference_id),
        ).fetchone()
        if row is None:
            raise RuntimeAuthorityNotFound("registered runtime authority was not found")
        return _row_to_authority(row)

    def list_active(self, workspace_id: str) -> tuple[RegisteredRuntimeAuthority, ...]:
        rows = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              authority_ref,
              runtime_kind,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_runtime_authorities
            WHERE workspace_id = %s
              AND status = 'active'
            ORDER BY authority_ref
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_authority(row) for row in rows)

    def revoke(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> RegisteredRuntimeAuthority:
        current = self.get(workspace_id, authority_ref)
        if current.status is RegisteredRuntimeAuthorityStatus.REVOKED:
            return current
        self._connection.execute(
            """
            UPDATE cpk_runtime_authorities
            SET status = 'revoked'
            WHERE workspace_id = %s
              AND authority_ref = %s
              AND status = 'active'
            """,
            (workspace_id, authority_ref.reference_id),
        )
        return self.get(workspace_id, authority_ref)

    def _get_active_by_ref(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> RegisteredRuntimeAuthority | None:
        row = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              authority_ref,
              runtime_kind,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_runtime_authorities
            WHERE workspace_id = %s
              AND authority_ref = %s
              AND status = 'active'
            """,
            (workspace_id, authority_ref.reference_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_authority(row)


def _row_to_authority(row: tuple[Any, ...]) -> RegisteredRuntimeAuthority:
    return RegisteredRuntimeAuthority(
        registration_id=row[0],
        workspace_id=row[1],
        authority_ref=RuntimeAuthorityReference(row[2]),
        runtime_kind=RuntimeKind(row[3]),
        authority=DockerRuntimeAuthorityCodec().decode(row[4]),
        admitted_by=row[5],
        admitted_at=row[6],
        status=RegisteredRuntimeAuthorityStatus(row[7]),
        metadata=row[8],
    )


def _credential_references(authority: RegisteredRuntimeAuthority) -> dict[str, object]:
    if authority.authority_kind is RuntimeAuthorityKind.LOCAL_DOCKER_SOCKET:
        return {}
    descriptor = authority.authority.descriptor()
    references = descriptor.get("credential_references")
    if not isinstance(references, dict):
        return {}
    return dict(references)


class RuntimeAuthorityDeliveryStore:
    """Persist admitted process access delivery for runtime authorities."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        *,
        workspace_id: str,
        delivery: RuntimeAuthorityAccessDelivery,
        admitted_by: str,
        admitted_at: str,
    ) -> RegisteredRuntimeAuthorityDelivery:
        candidate = RegisteredRuntimeAuthorityDelivery.from_delivery(
            workspace_id=workspace_id,
            delivery=delivery,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )
        self._require_active_authority(workspace_id, delivery.authority_ref)
        existing = self._get_active_by_ref(workspace_id, delivery.authority_ref)
        if existing is not None:
            if existing.delivery == candidate.delivery:
                return existing
            raise RuntimeAuthorityRegistrationConflict(
                "registered runtime authority delivery replacement requires explicit replacement policy"
            )

        self._connection.execute(
            """
            INSERT INTO cpk_runtime_authority_deliveries (
              delivery_id,
              workspace_id,
              authority_ref,
              delivery_kind,
              delivery,
              secret_references,
              admitted_by,
              admitted_at,
              status,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (
                candidate.delivery_id,
                candidate.workspace_id,
                candidate.authority_ref.reference_id,
                candidate.delivery.delivery_kind.value,
                Jsonb(RuntimeAuthorityAccessDeliveryCodec().encode(delivery)),
                Jsonb(_delivery_secret_references(candidate)),
                candidate.admitted_by,
                candidate.admitted_at,
                candidate.status.value,
            ),
        )
        return candidate

    def get(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> RegisteredRuntimeAuthorityDelivery:
        row = self._connection.execute(
            """
            SELECT
              delivery_id,
              workspace_id,
              delivery,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_runtime_authority_deliveries
            WHERE workspace_id = %s
              AND authority_ref = %s
            ORDER BY
              CASE status WHEN 'active' THEN 0 ELSE 1 END,
              admitted_at DESC,
              delivery_id DESC
            LIMIT 1
            """,
            (workspace_id, authority_ref.reference_id),
        ).fetchone()
        if row is None:
            raise RuntimeAuthorityNotFound(
                "registered runtime authority delivery was not found"
            )
        return _row_to_delivery(row)

    def list_active(
        self,
        workspace_id: str,
    ) -> tuple[RegisteredRuntimeAuthorityDelivery, ...]:
        rows = self._connection.execute(
            """
            SELECT
              delivery_id,
              workspace_id,
              delivery,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_runtime_authority_deliveries
            WHERE workspace_id = %s
              AND status = 'active'
            ORDER BY authority_ref
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_delivery(row) for row in rows)

    def revoke(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> RegisteredRuntimeAuthorityDelivery:
        current = self.get(workspace_id, authority_ref)
        if current.status is RegisteredRuntimeAuthorityDeliveryStatus.REVOKED:
            return current
        self._connection.execute(
            """
            UPDATE cpk_runtime_authority_deliveries
            SET status = 'revoked'
            WHERE workspace_id = %s
              AND authority_ref = %s
              AND status = 'active'
            """,
            (workspace_id, authority_ref.reference_id),
        )
        return self.get(workspace_id, authority_ref)

    def _get_active_by_ref(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> RegisteredRuntimeAuthorityDelivery | None:
        row = self._connection.execute(
            """
            SELECT
              delivery_id,
              workspace_id,
              delivery,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_runtime_authority_deliveries
            WHERE workspace_id = %s
              AND authority_ref = %s
              AND status = 'active'
            """,
            (workspace_id, authority_ref.reference_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_delivery(row)

    def _require_active_authority(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT 1
            FROM cpk_runtime_authorities
            WHERE workspace_id = %s
              AND authority_ref = %s
              AND status = 'active'
            """,
            (workspace_id, authority_ref.reference_id),
        ).fetchone()
        if row is None:
            raise RuntimeAuthorityNotFound(
                "runtime authority delivery requires an active registered authority"
            )


def _row_to_delivery(row: tuple[Any, ...]) -> RegisteredRuntimeAuthorityDelivery:
    return RegisteredRuntimeAuthorityDelivery(
        delivery_id=row[0],
        workspace_id=row[1],
        delivery=RuntimeAuthorityAccessDeliveryCodec().decode(row[2]),
        admitted_by=row[3],
        admitted_at=row[4],
        status=RegisteredRuntimeAuthorityDeliveryStatus(row[5]),
        metadata=row[6],
    )


def _delivery_secret_references(
    delivery: RegisteredRuntimeAuthorityDelivery,
) -> list[dict[str, object]]:
    return [value.descriptor() for value in delivery.delivery.secret_references]
