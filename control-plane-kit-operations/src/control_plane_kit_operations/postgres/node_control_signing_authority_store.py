"""Locked Postgres truth for reloading node-control signing authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.node_control_attempts import (
    NodeControlIntendedAttempt,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizedSecretUse,
    RegisteredSecretProvider,
    RegisteredSecretProviderStatus,
    RegisteredSecretReference,
    RegisteredSecretReferenceStatus,
    SecretProviderKind,
)


class _NodeControlSigningAuthorityStoreError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _LockedSigningFamily:
    authorization: AuthorizedSecretUse
    reference: RegisteredSecretReference
    provider: RegisteredSecretProvider


@dataclass(frozen=True, slots=True)
class _LockedSigningTruth:
    transit: _LockedSigningFamily
    workload: _LockedSigningFamily


class _NodeControlSigningAuthorityStore:
    """Read and lock both retained authorization chains in one bounded query."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def get_for_share(
        self,
        attempt: NodeControlIntendedAttempt,
    ) -> _LockedSigningTruth:
        if type(attempt) is not NodeControlIntendedAttempt:
            raise _NodeControlSigningAuthorityStoreError(
                "node-control signing authority attempt is malformed"
            )
        row = self._connection.execute(
            """
            SELECT
              transit_auth.authorization_id,
              transit_auth.workspace_id,
              transit_auth.reference_registration_id,
              transit_auth.provider_registration_id,
              transit_auth.secret_reference,
              transit_auth.use_intent,
              transit_auth.actor_subject,
              transit_auth.correlation_id,
              transit_auth.requested_at,
              transit_auth.intent_fingerprint,
              transit_auth.operation_id,
              transit_auth.session_id,
              transit_auth.run_id,
              transit_auth.activity_id,
              transit_auth.effect_id,
              transit_auth.probe_id,
              transit_ref.registration_id,
              transit_ref.workspace_id,
              transit_ref.secret_reference,
              transit_ref.provider_registration_id,
              transit_ref.allowed_intents,
              transit_ref.admitted_by,
              transit_ref.admitted_at,
              transit_ref.status,
              transit_ref.supersedes_registration_id,
              transit_ref.revoked_by,
              transit_ref.revoked_at,
              transit_provider.registration_id,
              transit_provider.workspace_id,
              transit_provider.provider_id,
              transit_provider.provider_kind,
              transit_provider.display_name,
              transit_provider.endpoint_reference,
              transit_provider.credential_reference,
              transit_provider.allowed_reference_prefixes,
              transit_provider.allowed_intents,
              transit_provider.admitted_by,
              transit_provider.admitted_at,
              transit_provider.status,
              transit_provider.supersedes_registration_id,
              transit_provider.revoked_by,
              transit_provider.revoked_at,
              workload_auth.authorization_id,
              workload_auth.workspace_id,
              workload_auth.reference_registration_id,
              workload_auth.provider_registration_id,
              workload_auth.secret_reference,
              workload_auth.use_intent,
              workload_auth.actor_subject,
              workload_auth.correlation_id,
              workload_auth.requested_at,
              workload_auth.intent_fingerprint,
              workload_auth.operation_id,
              workload_auth.session_id,
              workload_auth.run_id,
              workload_auth.activity_id,
              workload_auth.effect_id,
              workload_auth.probe_id,
              workload_ref.registration_id,
              workload_ref.workspace_id,
              workload_ref.secret_reference,
              workload_ref.provider_registration_id,
              workload_ref.allowed_intents,
              workload_ref.admitted_by,
              workload_ref.admitted_at,
              workload_ref.status,
              workload_ref.supersedes_registration_id,
              workload_ref.revoked_by,
              workload_ref.revoked_at,
              workload_provider.registration_id,
              workload_provider.workspace_id,
              workload_provider.provider_id,
              workload_provider.provider_kind,
              workload_provider.display_name,
              workload_provider.endpoint_reference,
              workload_provider.credential_reference,
              workload_provider.allowed_reference_prefixes,
              workload_provider.allowed_intents,
              workload_provider.admitted_by,
              workload_provider.admitted_at,
              workload_provider.status,
              workload_provider.supersedes_registration_id,
              workload_provider.revoked_by,
              workload_provider.revoked_at
            FROM cpk_secret_use_authorizations AS transit_auth
            JOIN cpk_secret_references AS transit_ref
              ON transit_ref.registration_id=transit_auth.reference_registration_id
             AND transit_ref.workspace_id=transit_auth.workspace_id
             AND transit_ref.secret_reference=transit_auth.secret_reference
             AND transit_ref.status='active'
            JOIN cpk_secret_providers AS transit_provider
              ON transit_provider.registration_id=transit_auth.provider_registration_id
             AND transit_provider.registration_id=transit_ref.provider_registration_id
             AND transit_provider.workspace_id=transit_auth.workspace_id
             AND transit_provider.status='active'
            JOIN cpk_secret_use_authorizations AS workload_auth
              ON workload_auth.authorization_id=%s
             AND workload_auth.workspace_id=transit_auth.workspace_id
            JOIN cpk_secret_references AS workload_ref
              ON workload_ref.registration_id=workload_auth.reference_registration_id
             AND workload_ref.workspace_id=workload_auth.workspace_id
             AND workload_ref.secret_reference=workload_auth.secret_reference
             AND workload_ref.status='active'
            JOIN cpk_secret_providers AS workload_provider
              ON workload_provider.registration_id=workload_auth.provider_registration_id
             AND workload_provider.registration_id=workload_ref.provider_registration_id
             AND workload_provider.workspace_id=workload_auth.workspace_id
             AND workload_provider.status='active'
            WHERE transit_auth.authorization_id=%s
              AND transit_auth.workspace_id=%s
            FOR SHARE OF transit_auth, transit_ref, transit_provider,
                         workload_auth, workload_ref, workload_provider
            """,
            (
                attempt.workload_authorization_id,
                attempt.transit_authorization_id,
                attempt.workspace_id,
            ),
        ).fetchone()
        if row is None:
            raise _NodeControlSigningAuthorityStoreError(
                "node-control signing authority truth is unavailable"
            )
        failed = False
        try:
            truth = _LockedSigningTruth(
                transit=_family(row, 0),
                workload=_family(row, 42),
            )
        except (TypeError, ValueError):
            failed = True
            truth = None
        if failed or truth is None:
            raise _NodeControlSigningAuthorityStoreError(
                "node-control signing authority truth is malformed"
            ) from None
        return truth


def _family(row: tuple[Any, ...], offset: int) -> _LockedSigningFamily:
    authorization = AuthorizedSecretUse(
        authorization_id=row[offset],
        workspace_id=row[offset + 1],
        reference_registration_id=row[offset + 2],
        provider_registration_id=row[offset + 3],
        reference=SecretReference(row[offset + 4]),
        intent=SecretUseIntent(row[offset + 5]),
        actor_subject=row[offset + 6],
        correlation_id=row[offset + 7],
        requested_at=decode_postgres_timestamp(row[offset + 8]),
        intent_fingerprint=row[offset + 9],
        operation_id=row[offset + 10],
        session_id=row[offset + 11],
        run_id=row[offset + 12],
        activity_id=row[offset + 13],
        effect_id=row[offset + 14],
        probe_id=row[offset + 15],
    )
    reference = RegisteredSecretReference(
        registration_id=row[offset + 16],
        workspace_id=row[offset + 17],
        reference=SecretReference(row[offset + 18]),
        provider_registration_id=row[offset + 19],
        allowed_intents=tuple(SecretUseIntent(value) for value in row[offset + 20]),
        admitted_by=row[offset + 21],
        admitted_at=decode_postgres_timestamp(row[offset + 22]),
        status=RegisteredSecretReferenceStatus(row[offset + 23]),
        supersedes_registration_id=row[offset + 24],
        revoked_by=row[offset + 25],
        revoked_at=_optional_timestamp(row[offset + 26]),
    )
    provider = RegisteredSecretProvider(
        registration_id=row[offset + 27],
        workspace_id=row[offset + 28],
        provider_id=SecretProviderId(row[offset + 29]),
        provider_kind=SecretProviderKind(row[offset + 30]),
        display_name=row[offset + 31],
        endpoint_reference=SecretProviderEndpointReference(row[offset + 32]),
        credential_reference=SecretReference(row[offset + 33]),
        allowed_reference_prefixes=tuple(
            SecretReference(value) for value in row[offset + 34]
        ),
        allowed_intents=tuple(SecretUseIntent(value) for value in row[offset + 35]),
        admitted_by=row[offset + 36],
        admitted_at=decode_postgres_timestamp(row[offset + 37]),
        status=RegisteredSecretProviderStatus(row[offset + 38]),
        supersedes_registration_id=row[offset + 39],
        revoked_by=row[offset + 40],
        revoked_at=_optional_timestamp(row[offset + 41]),
    )
    return _LockedSigningFamily(authorization, reference, provider)


def _optional_timestamp(value: object) -> str | None:
    return None if value is None else decode_postgres_timestamp(value)
