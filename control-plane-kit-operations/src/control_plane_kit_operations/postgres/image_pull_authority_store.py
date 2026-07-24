"""Postgres store for workspace-scoped OCI pull authority references."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.runtime_effects import (
    ImagePullAuthority,
    ImagePullAuthorityCodec,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.products import (
    ProductRegistrationConflict,
    ProductRegistrationNotFound,
    RegisteredImagePullAuthority,
    RegisteredImagePullAuthorityStatus,
)


class ImagePullAuthorityStore:
    """Persist pull authority admitted into one workspace's operational truth."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        *,
        workspace_id: str,
        authority: ImagePullAuthority,
        admitted_by: str,
        admitted_at: str,
    ) -> RegisteredImagePullAuthority:
        candidate = RegisteredImagePullAuthority.from_authority(
            workspace_id=workspace_id,
            authority=authority,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )
        existing = self._get_by_id(workspace_id, candidate.authority_id)
        if existing is not None:
            return existing

        active_conflict = self._active_with_scope(workspace_id, authority)
        if active_conflict is not None:
            raise ProductRegistrationConflict(
                "registered image pull authority replacement requires explicit replacement policy"
            )

        self._connection.execute(
            """
            INSERT INTO cpk_image_pull_authorities (
              authority_id,
              workspace_id,
              authority,
              registry,
              repository,
              credential_reference,
              admitted_by,
              admitted_at,
              status,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (
                candidate.authority_id,
                candidate.workspace_id,
                Jsonb(ImagePullAuthorityCodec().encode(candidate.authority)),
                candidate.authority.registry,
                candidate.authority.repository,
                candidate.authority.credential_reference.reference_id,
                candidate.admitted_by,
                candidate.admitted_at,
                candidate.status.value,
            ),
        )
        return candidate

    def get(
        self,
        workspace_id: str,
        authority_id: str,
    ) -> RegisteredImagePullAuthority:
        authority = self._get_by_id(workspace_id, authority_id)
        if authority is None:
            raise ProductRegistrationNotFound(
                "registered image pull authority was not found"
            )
        return authority

    def list_active(self, workspace_id: str) -> tuple[RegisteredImagePullAuthority, ...]:
        rows = self._connection.execute(
            """
            SELECT
              authority_id,
              workspace_id,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_image_pull_authorities
            WHERE workspace_id = %s
              AND status = 'active'
            ORDER BY registry, repository NULLS FIRST, authority_id
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_authority(row) for row in rows)

    def revoke(
        self,
        workspace_id: str,
        authority_id: str,
    ) -> RegisteredImagePullAuthority:
        current = self.get(workspace_id, authority_id)
        if current.status is RegisteredImagePullAuthorityStatus.REVOKED:
            return current
        self._connection.execute(
            """
            UPDATE cpk_image_pull_authorities
            SET status = 'revoked'
            WHERE workspace_id = %s
              AND authority_id = %s
            """,
            (workspace_id, authority_id),
        )
        return self.get(workspace_id, authority_id)

    def _get_by_id(
        self,
        workspace_id: str,
        authority_id: str,
    ) -> RegisteredImagePullAuthority | None:
        row = self._connection.execute(
            """
            SELECT
              authority_id,
              workspace_id,
              authority,
              admitted_by,
              admitted_at,
              status,
              metadata
            FROM cpk_image_pull_authorities
            WHERE workspace_id = %s
              AND authority_id = %s
            """,
            (workspace_id, authority_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_authority(row)

    def _active_with_scope(
        self,
        workspace_id: str,
        authority: ImagePullAuthority,
    ) -> RegisteredImagePullAuthority | None:
        for registered in self.list_active(workspace_id):
            if (
                registered.authority.registry == authority.registry
                and registered.authority.repository == authority.repository
            ):
                return registered
        return None


def _row_to_authority(row: tuple[Any, ...]) -> RegisteredImagePullAuthority:
    return RegisteredImagePullAuthority(
        authority_id=row[0],
        workspace_id=row[1],
        authority=ImagePullAuthorityCodec().decode(row[2]),
        admitted_by=row[3],
        admitted_at=row[4],
        status=RegisteredImagePullAuthorityStatus(row[5]),
        metadata=row[6],
    )
