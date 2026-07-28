"""Postgres store for workspace-scoped named ingress authorities."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthorityCodec,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    GeneratedSecretRecordingConflict,
    IngressAuthority,
    IngressAuthorityNotFound,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationConflict,
    OwnedIngressResourceStatus,
    OwnedIngressResourceConflict,
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


class IngressResourceStore:
    """Persist owned public-ingress allocation evidence."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def record_cloudflare(
        self,
        resource: CloudflareOwnedIngressResource,
    ) -> CloudflareOwnedIngressResource:
        if not isinstance(resource, CloudflareOwnedIngressResource):
            raise TypeError("record_cloudflare requires CloudflareOwnedIngressResource")
        existing = self._get_cloudflare(resource.workspace_id, resource.ingress_id)
        if existing is not None:
            if existing == resource:
                return existing
            raise OwnedIngressResourceConflict(
                "owned ingress resource replacement requires explicit policy"
            )
        self._connection.execute(
            """
            INSERT INTO cpk_cloudflare_ingress_resources (
              workspace_id,
              runtime_id,
              ingress_id,
              epoch,
              status,
              authority_ref,
              provider_kind,
              tunnel_name,
              tunnel_id,
              dns_record_id,
              hostname,
              zone_id,
              lifecycle,
              created_at,
              observed_at,
              source_run_id,
              source_activity_id,
              source_event_id,
              removed_at,
              removed_by_run_id
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                resource.workspace_id,
                resource.runtime_id,
                resource.ingress_id,
                resource.epoch,
                resource.status.value,
                resource.authority_ref.reference_id,
                resource.provider_kind.value,
                resource.tunnel_name,
                resource.tunnel_id,
                resource.dns_record_id,
                resource.hostname,
                resource.zone_id,
                resource.lifecycle.value,
                resource.created_at,
                resource.observed_at,
                resource.source_run_id,
                resource.source_activity_id,
                resource.source_event_id,
                resource.removed_at,
                resource.removed_by_run_id,
            ),
        )
        return resource

    def get_cloudflare(
        self,
        workspace_id: str,
        ingress_id: str,
    ) -> CloudflareOwnedIngressResource:
        resource = self._get_cloudflare(workspace_id, ingress_id)
        if resource is None:
            raise IngressAuthorityNotFound("owned ingress resource was not found")
        return resource

    def list_cloudflare(
        self,
        workspace_id: str,
    ) -> tuple[CloudflareOwnedIngressResource, ...]:
        rows = self._connection.execute(
            """
            SELECT
              workspace_id,
              runtime_id,
              ingress_id,
              epoch,
              status,
              authority_ref,
              provider_kind,
              tunnel_name,
              tunnel_id,
              dns_record_id,
              hostname,
              zone_id,
              lifecycle,
              created_at,
              observed_at,
              source_run_id,
              source_activity_id,
              source_event_id,
              removed_at,
              removed_by_run_id
            FROM cpk_cloudflare_ingress_resources
            WHERE workspace_id = %s
            ORDER BY ingress_id, epoch
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_cloudflare_resource(row) for row in rows)

    def _get_cloudflare(
        self,
        workspace_id: str,
        ingress_id: str,
    ) -> CloudflareOwnedIngressResource | None:
        row = self._connection.execute(
            """
            SELECT
              workspace_id,
              runtime_id,
              ingress_id,
              epoch,
              status,
              authority_ref,
              provider_kind,
              tunnel_name,
              tunnel_id,
              dns_record_id,
              hostname,
              zone_id,
              lifecycle,
              created_at,
              observed_at,
              source_run_id,
              source_activity_id,
              source_event_id,
              removed_at,
              removed_by_run_id
            FROM cpk_cloudflare_ingress_resources
            WHERE workspace_id = %s
              AND ingress_id = %s
              AND status IN ('allocating', 'active', 'removing')
            ORDER BY epoch DESC
            LIMIT 1
            """,
            (workspace_id, ingress_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_cloudflare_resource(row)


class GeneratedIngressSecretReferenceStore:
    """Persist reference-only evidence for generated ingress secrets."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def record(
        self,
        evidence: GeneratedIngressSecretReference,
    ) -> GeneratedIngressSecretReference:
        if not isinstance(evidence, GeneratedIngressSecretReference):
            raise TypeError("record requires GeneratedIngressSecretReference")
        existing = self._get_by_source(
            workspace_id=evidence.workspace_id,
            purpose=evidence.purpose,
            source_run_id=evidence.source_run_id,
            source_activity_id=evidence.source_activity_id,
            source_event_id=evidence.source_event_id,
        )
        if existing is not None:
            if existing == evidence:
                return existing
            raise GeneratedSecretRecordingConflict(
                "generated secret reference replacement requires explicit policy"
            )
        self._connection.execute(
            """
            INSERT INTO cpk_generated_ingress_secret_references (
              workspace_id,
              purpose,
              secret_ref,
              recorded_at,
              source_run_id,
              source_activity_id,
              source_event_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                evidence.workspace_id,
                evidence.purpose.value,
                evidence.secret_ref.reference_id,
                evidence.recorded_at,
                evidence.source_run_id,
                evidence.source_activity_id,
                evidence.source_event_id,
            ),
        )
        return evidence

    def get_by_source(
        self,
        *,
        workspace_id: str,
        purpose: GeneratedSecretPurpose,
        source_run_id: str,
        source_activity_id: str,
        source_event_id: str,
    ) -> GeneratedIngressSecretReference:
        evidence = self._get_by_source(
            workspace_id=workspace_id,
            purpose=purpose,
            source_run_id=source_run_id,
            source_activity_id=source_activity_id,
            source_event_id=source_event_id,
        )
        if evidence is None:
            raise IngressAuthorityNotFound(
                "generated ingress secret reference was not found"
            )
        return evidence

    def list_for_workspace(
        self,
        workspace_id: str,
    ) -> tuple[GeneratedIngressSecretReference, ...]:
        rows = self._connection.execute(
            """
            SELECT
              workspace_id,
              purpose,
              secret_ref,
              recorded_at,
              source_run_id,
              source_activity_id,
              source_event_id
            FROM cpk_generated_ingress_secret_references
            WHERE workspace_id = %s
            ORDER BY recorded_at DESC, purpose, source_event_id
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_generated_ingress_secret_reference(row) for row in rows)

    def _get_by_source(
        self,
        *,
        workspace_id: str,
        purpose: GeneratedSecretPurpose,
        source_run_id: str,
        source_activity_id: str,
        source_event_id: str,
    ) -> GeneratedIngressSecretReference | None:
        row = self._connection.execute(
            """
            SELECT
              workspace_id,
              purpose,
              secret_ref,
              recorded_at,
              source_run_id,
              source_activity_id,
              source_event_id
            FROM cpk_generated_ingress_secret_references
            WHERE workspace_id = %s
              AND purpose = %s
              AND source_run_id = %s
              AND source_activity_id = %s
              AND source_event_id = %s
            """,
            (
                workspace_id,
                purpose.value,
                source_run_id,
                source_activity_id,
                source_event_id,
            ),
        ).fetchone()
        if row is None:
            return None
        return _row_to_generated_ingress_secret_reference(row)


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


def _row_to_cloudflare_resource(row: tuple[Any, ...]) -> CloudflareOwnedIngressResource:
    return CloudflareOwnedIngressResource(
        workspace_id=row[0],
        runtime_id=row[1],
        ingress_id=row[2],
        epoch=row[3],
        status=OwnedIngressResourceStatus(row[4]),
        authority_ref=IngressAuthorityReference(row[5]),
        provider_kind=IngressAuthorityProviderKind(row[6]),
        tunnel_name=row[7],
        tunnel_id=row[8],
        dns_record_id=row[9],
        hostname=row[10],
        zone_id=row[11],
        lifecycle=PublicIngressLifecycle(row[12]),
        created_at=row[13],
        observed_at=row[14],
        source_run_id=row[15],
        source_activity_id=row[16],
        source_event_id=row[17],
        removed_at=row[18],
        removed_by_run_id=row[19],
    )


def _row_to_generated_ingress_secret_reference(
    row: tuple[Any, ...],
) -> GeneratedIngressSecretReference:
    return GeneratedIngressSecretReference(
        workspace_id=row[0],
        purpose=GeneratedSecretPurpose(row[1]),
        secret_ref=SecretReference(row[2]),
        recorded_at=row[3],
        source_run_id=row[4],
        source_activity_id=row[5],
        source_event_id=row[6],
    )


def _credential_references(authority: RegisteredIngressAuthority) -> dict[str, object]:
    if authority.provider_kind is IngressAuthorityProviderKind.CLOUDFLARE:
        return {"api_token_ref": authority.authority.api_token_ref.reference_id}
    return {}
