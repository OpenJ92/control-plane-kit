"""Postgres stores for admitted secret providers and provider handles."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProvider,
    RegisteredSecretProviderStatus,
    RegisteredSecretReference,
    RegisteredSecretReferenceStatus,
    SecretProviderKind,
    SecretProviderNotFound,
    SecretProviderRegistrationConflict,
)


class SecretProviderStore:
    """Persist workspace provider admission without provider material."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        candidate: RegisteredSecretProvider,
    ) -> RegisteredSecretProvider:
        if not isinstance(candidate, RegisteredSecretProvider):
            raise TypeError("provider store requires RegisteredSecretProvider")
        if candidate.status is not RegisteredSecretProviderStatus.ACTIVE:
            raise SecretProviderRegistrationConflict(
                "new provider registration must be active"
            )
        existing_candidate = self._get_by_registration_or_none(
            candidate.workspace_id,
            candidate.registration_id,
            for_update=True,
        )
        if existing_candidate is not None:
            return existing_candidate

        active = self._get_active_or_none(
            candidate.workspace_id,
            candidate.provider_id,
            for_update=True,
        )
        if active is not None and active.same_admission_as(candidate):
            return active

        self._validate_supersession(candidate, active)
        if active is not None:
            self._connection.execute(
                """
                UPDATE cpk_secret_providers
                SET status = 'superseded'
                WHERE workspace_id = %s
                  AND registration_id = %s
                  AND status = 'active'
                """,
                (candidate.workspace_id, active.registration_id),
            )

        self._connection.execute(
            """
            INSERT INTO cpk_secret_providers (
              registration_id,
              workspace_id,
              provider_id,
              provider_kind,
              display_name,
              endpoint_reference,
              credential_reference,
              allowed_reference_prefixes,
              allowed_intents,
              admitted_by,
              admitted_at,
              status,
              supersedes_registration_id,
              revoked_by,
              revoked_at,
              metadata
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              NULL, NULL, %s
            )
            """,
            (
                candidate.registration_id,
                candidate.workspace_id,
                candidate.provider_id.value,
                candidate.provider_kind.value,
                candidate.display_name,
                candidate.endpoint_reference.reference_id,
                candidate.credential_reference.reference_id,
                Jsonb(
                    [
                        reference.reference_id
                        for reference in candidate.allowed_reference_prefixes
                    ]
                ),
                Jsonb([intent.value for intent in candidate.allowed_intents]),
                candidate.admitted_by,
                candidate.admitted_at,
                candidate.status.value,
                candidate.supersedes_registration_id,
                Jsonb(dict(candidate.metadata)),
            ),
        )
        return candidate

    def get_active(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
    ) -> RegisteredSecretProvider:
        provider = self._get_active_or_none(workspace_id, provider_id)
        if provider is None:
            raise SecretProviderNotFound(
                "active registered secret provider was not found"
            )
        return provider

    def require_active_registration(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> RegisteredSecretProvider:
        provider = self._get_by_registration_or_none(
            workspace_id,
            registration_id,
        )
        if (
            provider is None
            or provider.status is not RegisteredSecretProviderStatus.ACTIVE
        ):
            raise SecretProviderNotFound(
                "active registered secret provider was not found"
            )
        return provider

    def get_by_registration(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> RegisteredSecretProvider:
        provider = self._get_by_registration_or_none(
            workspace_id,
            registration_id,
        )
        if provider is None:
            raise SecretProviderNotFound(
                "registered secret provider was not found"
            )
        return provider

    def list_active(
        self,
        workspace_id: str,
    ) -> tuple[RegisteredSecretProvider, ...]:
        rows = self._connection.execute(
            f"""
            {_PROVIDER_SELECT}
            WHERE workspace_id = %s
              AND status = 'active'
            ORDER BY provider_id
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_provider(row) for row in rows)

    def list_history(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
    ) -> tuple[RegisteredSecretProvider, ...]:
        rows = self._connection.execute(
            f"""
            {_PROVIDER_SELECT}
            WHERE workspace_id = %s
              AND provider_id = %s
            ORDER BY admitted_at, registration_id
            """,
            (workspace_id, provider_id.value),
        ).fetchall()
        return tuple(_row_to_provider(row) for row in rows)

    def revoke_active(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
        *,
        revoked_by: str,
        revoked_at: str,
    ) -> RegisteredSecretProvider:
        active = self._get_active_or_none(
            workspace_id,
            provider_id,
            for_update=True,
        )
        if active is None:
            history = self.list_history(workspace_id, provider_id)
            if history and history[-1].status is RegisteredSecretProviderStatus.REVOKED:
                return history[-1]
            raise SecretProviderNotFound(
                "active registered secret provider was not found"
            )
        self._connection.execute(
            """
            UPDATE cpk_secret_providers
            SET status = 'revoked',
                revoked_by = %s,
                revoked_at = %s
            WHERE workspace_id = %s
              AND registration_id = %s
              AND status = 'active'
            """,
            (revoked_by, revoked_at, workspace_id, active.registration_id),
        )
        return self.get_by_registration(workspace_id, active.registration_id)

    def _validate_supersession(
        self,
        candidate: RegisteredSecretProvider,
        active: RegisteredSecretProvider | None,
    ) -> None:
        supersedes = candidate.supersedes_registration_id
        if supersedes is None:
            if active is not None or self.list_history(
                candidate.workspace_id,
                candidate.provider_id,
            ):
                raise SecretProviderRegistrationConflict(
                    "provider replacement requires explicit supersession"
                )
            return

        target = self._get_by_registration_or_none(
            candidate.workspace_id,
            supersedes,
            for_update=True,
        )
        if target is None or target.provider_id != candidate.provider_id:
            raise SecretProviderRegistrationConflict(
                "provider supersession target is invalid"
            )
        if active is not None and active.registration_id != supersedes:
            raise SecretProviderRegistrationConflict(
                "provider supersession evidence is stale"
            )

    def _get_active_or_none(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
        *,
        for_update: bool = False,
    ) -> RegisteredSecretProvider | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            {_PROVIDER_SELECT}
            WHERE workspace_id = %s
              AND provider_id = %s
              AND status = 'active'
            {suffix}
            """,
            (workspace_id, provider_id.value),
        ).fetchone()
        return None if row is None else _row_to_provider(row)

    def _get_by_registration_or_none(
        self,
        workspace_id: str,
        registration_id: str,
        *,
        for_update: bool = False,
    ) -> RegisteredSecretProvider | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            {_PROVIDER_SELECT}
            WHERE workspace_id = %s
              AND registration_id = %s
            {suffix}
            """,
            (workspace_id, registration_id),
        ).fetchone()
        return None if row is None else _row_to_provider(row)


class SecretReferenceStore:
    """Persist admitted handles while retaining every lifecycle row."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        candidate: RegisteredSecretReference,
    ) -> RegisteredSecretReference:
        if not isinstance(candidate, RegisteredSecretReference):
            raise TypeError("reference store requires RegisteredSecretReference")
        if candidate.status is not RegisteredSecretReferenceStatus.ACTIVE:
            raise SecretProviderRegistrationConflict(
                "new secret reference registration must be active"
            )
        existing_candidate = self._get_by_registration_or_none(
            candidate.workspace_id,
            candidate.registration_id,
            for_update=True,
        )
        if existing_candidate is not None:
            return existing_candidate

        active = self._get_active_or_none(
            candidate.workspace_id,
            candidate.reference,
            for_update=True,
        )
        if active is not None and active.same_admission_as(candidate):
            return active

        self._validate_supersession(candidate, active)
        if active is not None:
            self._connection.execute(
                """
                UPDATE cpk_secret_references
                SET status = 'superseded'
                WHERE workspace_id = %s
                  AND registration_id = %s
                  AND status = 'active'
                """,
                (candidate.workspace_id, active.registration_id),
            )

        self._connection.execute(
            """
            INSERT INTO cpk_secret_references (
              registration_id,
              workspace_id,
              secret_reference,
              provider_registration_id,
              allowed_intents,
              admitted_by,
              admitted_at,
              status,
              supersedes_registration_id,
              revoked_by,
              revoked_at,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s)
            """,
            (
                candidate.registration_id,
                candidate.workspace_id,
                candidate.reference.reference_id,
                candidate.provider_registration_id,
                Jsonb([intent.value for intent in candidate.allowed_intents]),
                candidate.admitted_by,
                candidate.admitted_at,
                candidate.status.value,
                candidate.supersedes_registration_id,
                Jsonb(dict(candidate.metadata)),
            ),
        )
        return candidate

    def get_by_registration(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> RegisteredSecretReference:
        reference = self._get_by_registration_or_none(
            workspace_id,
            registration_id,
        )
        if reference is None:
            raise SecretProviderNotFound(
                "registered secret reference was not found"
            )
        return reference

    def get_active(
        self,
        workspace_id: str,
        reference: SecretReference,
    ) -> RegisteredSecretReference:
        registered = self._get_active_or_none(workspace_id, reference)
        if registered is None:
            raise SecretProviderNotFound(
                "active registered secret reference was not found"
            )
        return registered

    def list_active(
        self,
        workspace_id: str,
    ) -> tuple[RegisteredSecretReference, ...]:
        rows = self._connection.execute(
            f"""
            {_REFERENCE_SELECT}
            WHERE workspace_id = %s
              AND status = 'active'
            ORDER BY secret_reference
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_reference(row) for row in rows)

    def list_history(
        self,
        workspace_id: str,
        reference: SecretReference,
    ) -> tuple[RegisteredSecretReference, ...]:
        rows = self._connection.execute(
            f"""
            {_REFERENCE_SELECT}
            WHERE workspace_id = %s
              AND secret_reference = %s
            ORDER BY admitted_at, registration_id
            """,
            (workspace_id, reference.reference_id),
        ).fetchall()
        return tuple(_row_to_reference(row) for row in rows)

    def revoke(
        self,
        workspace_id: str,
        registration_id: str,
        *,
        revoked_by: str,
        revoked_at: str,
    ) -> RegisteredSecretReference:
        current = self._get_by_registration_or_none(
            workspace_id,
            registration_id,
            for_update=True,
        )
        if current is None:
            raise SecretProviderNotFound(
                "registered secret reference was not found"
            )
        if current.status is RegisteredSecretReferenceStatus.REVOKED:
            return current
        if current.status is not RegisteredSecretReferenceStatus.ACTIVE:
            raise SecretProviderRegistrationConflict(
                "only an active secret reference may be revoked"
            )
        self._connection.execute(
            """
            UPDATE cpk_secret_references
            SET status = 'revoked',
                revoked_by = %s,
                revoked_at = %s
            WHERE workspace_id = %s
              AND registration_id = %s
              AND status = 'active'
            """,
            (revoked_by, revoked_at, workspace_id, registration_id),
        )
        return self.get_by_registration(workspace_id, registration_id)

    def _validate_supersession(
        self,
        candidate: RegisteredSecretReference,
        active: RegisteredSecretReference | None,
    ) -> None:
        supersedes = candidate.supersedes_registration_id
        if supersedes is None:
            if active is not None or self.list_history(
                candidate.workspace_id,
                candidate.reference,
            ):
                raise SecretProviderRegistrationConflict(
                    "secret reference replacement requires explicit supersession"
                )
            return

        target = self._get_by_registration_or_none(
            candidate.workspace_id,
            supersedes,
            for_update=True,
        )
        if target is None or target.reference != candidate.reference:
            raise SecretProviderRegistrationConflict(
                "secret reference supersession target is invalid"
            )
        if active is not None and active.registration_id != supersedes:
            raise SecretProviderRegistrationConflict(
                "secret reference supersession evidence is stale"
            )

    def _get_active_or_none(
        self,
        workspace_id: str,
        reference: SecretReference,
        *,
        for_update: bool = False,
    ) -> RegisteredSecretReference | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            {_REFERENCE_SELECT}
            WHERE workspace_id = %s
              AND secret_reference = %s
              AND status = 'active'
            {suffix}
            """,
            (workspace_id, reference.reference_id),
        ).fetchone()
        return None if row is None else _row_to_reference(row)

    def _get_by_registration_or_none(
        self,
        workspace_id: str,
        registration_id: str,
        *,
        for_update: bool = False,
    ) -> RegisteredSecretReference | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            {_REFERENCE_SELECT}
            WHERE workspace_id = %s
              AND registration_id = %s
            {suffix}
            """,
            (workspace_id, registration_id),
        ).fetchone()
        return None if row is None else _row_to_reference(row)


_PROVIDER_SELECT = """
SELECT
  registration_id,
  workspace_id,
  provider_id,
  provider_kind,
  display_name,
  endpoint_reference,
  credential_reference,
  allowed_reference_prefixes,
  allowed_intents,
  admitted_by,
  admitted_at,
  status,
  supersedes_registration_id,
  revoked_by,
  revoked_at,
  metadata
FROM cpk_secret_providers
"""

_REFERENCE_SELECT = """
SELECT
  registration_id,
  workspace_id,
  secret_reference,
  provider_registration_id,
  allowed_intents,
  admitted_by,
  admitted_at,
  status,
  supersedes_registration_id,
  revoked_by,
  revoked_at,
  metadata
FROM cpk_secret_references
"""


def _row_to_provider(row: tuple[Any, ...]) -> RegisteredSecretProvider:
    return RegisteredSecretProvider(
        registration_id=row[0],
        workspace_id=row[1],
        provider_id=SecretProviderId(row[2]),
        provider_kind=SecretProviderKind(row[3]),
        display_name=row[4],
        endpoint_reference=SecretProviderEndpointReference(row[5]),
        credential_reference=SecretReference(row[6]),
        allowed_reference_prefixes=tuple(
            SecretReference(value) for value in row[7]
        ),
        allowed_intents=tuple(SecretUseIntent(value) for value in row[8]),
        admitted_by=row[9],
        admitted_at=row[10],
        status=RegisteredSecretProviderStatus(row[11]),
        supersedes_registration_id=row[12],
        revoked_by=row[13],
        revoked_at=row[14],
        metadata=row[15],
    )


def _row_to_reference(row: tuple[Any, ...]) -> RegisteredSecretReference:
    return RegisteredSecretReference(
        registration_id=row[0],
        workspace_id=row[1],
        reference=SecretReference(row[2]),
        provider_registration_id=row[3],
        allowed_intents=tuple(SecretUseIntent(value) for value in row[4]),
        admitted_by=row[5],
        admitted_at=row[6],
        status=RegisteredSecretReferenceStatus(row[7]),
        supersedes_registration_id=row[8],
        revoked_by=row[9],
        revoked_at=row[10],
        metadata=row[11],
    )
