"""Postgres store for workspace-scoped named ingress authorities."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_operations.ingress_authorities import (
    CloudflareZoneIngressAuthorityCodec,
    IngressAuthority,
    IngressAuthorityNotFound,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationConflict,
    RegisteredIngressAuthority,
    RegisteredIngressAuthorityStatus,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection


class IngressAuthorityStore:
    """Persist named public ingress authorities admitted into one workspace."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        *,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
        authority: IngressAuthority,
        admitted_by: str,
        admitted_at: str,
    ) -> RegisteredIngressAuthority:
        candidate = RegisteredIngressAuthority.from_authority(
            workspace_id=workspace_id,
            authority_ref=authority_ref,
            authority=authority,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )
        existing = self._get_active_by_ref(workspace_id, authority_ref)
        if existing is not None:
            if existing.authority == candidate.authority:
                return existing
            raise IngressAuthorityRegistrationConflict(
                "registered ingress authority replacement requires explicit replacement policy"
            )

        self._connection.execute(
            """
            INSERT INTO cpk_ingress_authorities (
              registration_id,
              workspace_id,
              authority_ref,
              provider_kind,
              authority,
              credential_references,
              allowed_hostname_pattern,
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
                candidate.provider_kind.value,
                Jsonb(CloudflareZoneIngressAuthorityCodec().encode(candidate.authority)),
                Jsonb(_credential_references(candidate)),
                candidate.authority.allowed_hostname_pattern,
                candidate.admitted_by,
                candidate.admitted_at,
                candidate.status.value,
            ),
        )
        return candidate

    def get(
        self,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
    ) -> RegisteredIngressAuthority:
        row = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              authority_ref,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_ingress_authorities
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
            raise IngressAuthorityNotFound(
                "registered ingress authority was not found"
            )
        return _row_to_authority(row)

    def list_active(self, workspace_id: str) -> tuple[RegisteredIngressAuthority, ...]:
        rows = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              authority_ref,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_ingress_authorities
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
        authority_ref: IngressAuthorityReference,
    ) -> RegisteredIngressAuthority:
        current = self.get(workspace_id, authority_ref)
        if current.status is RegisteredIngressAuthorityStatus.REVOKED:
            return current
        self._connection.execute(
            """
            UPDATE cpk_ingress_authorities
            SET status = 'revoked'
            WHERE workspace_id = %s
              AND authority_ref = %s
              AND status = 'active'
            """,
            (workspace_id, authority_ref.reference_id),
        )
        return self.get(workspace_id, authority_ref)

    def require_active_for_hostname(
        self,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
        hostname: str,
    ) -> RegisteredIngressAuthority:
        authority = self._get_active_by_ref(workspace_id, authority_ref)
        if authority is None or not authority.authority.allows_hostname(hostname):
            raise IngressAuthorityNotFound(
                "active ingress authority does not allow hostname"
            )
        return authority

    def _get_active_by_ref(
        self,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
    ) -> RegisteredIngressAuthority | None:
        row = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              authority_ref,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_ingress_authorities
            WHERE workspace_id = %s
              AND authority_ref = %s
              AND status = 'active'
            """,
            (workspace_id, authority_ref.reference_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_authority(row)


def _row_to_authority(row: tuple[Any, ...]) -> RegisteredIngressAuthority:
    return RegisteredIngressAuthority(
        registration_id=row[0],
        workspace_id=row[1],
        authority_ref=IngressAuthorityReference(row[2]),
        authority=CloudflareZoneIngressAuthorityCodec().decode(row[3]),
        admitted_by=row[4],
        admitted_at=row[5],
        status=RegisteredIngressAuthorityStatus(row[6]),
        metadata=row[7],
    )


def _credential_references(authority: RegisteredIngressAuthority) -> dict[str, object]:
    if authority.provider_kind is IngressAuthorityProviderKind.CLOUDFLARE:
        return {"api_token_ref": authority.authority.api_token_ref.reference_id}
    return {}
