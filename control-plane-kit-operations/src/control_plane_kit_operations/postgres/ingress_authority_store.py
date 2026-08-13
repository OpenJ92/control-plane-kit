"""Postgres store for workspace-scoped named ingress authorities."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

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
    IngressAuthorityRegistrationError,
    OwnedIngressResourceStatus,
    OwnedIngressResourceConflict,
    RegisteredIngressAuthority,
    RegisteredIngressAuthorityStatus,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.read_pages import (
    IdentityReadCursor,
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageError,
    ReadPageRequest,
)


_BLOCKING_INGRESS_RESOURCE_STATUSES = (
    OwnedIngressResourceStatus.ALLOCATING,
    OwnedIngressResourceStatus.ACTIVE,
    OwnedIngressResourceStatus.REMOVING,
    OwnedIngressResourceStatus.UNCERTAIN,
    OwnedIngressResourceStatus.ORPHANED,
)


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
        encoded_admitted_at = encode_postgres_timestamp(candidate.admitted_at)
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
                encoded_admitted_at,
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

    def active_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[RegisteredIngressAuthority]:
        if request.collection is not ReadCollection.INGRESS_AUTHORITIES:
            raise ReadPageError("ingress authority page request is incongruent")
        cursor = request.cursor
        seek = ""
        if cursor is None:
            parameters: tuple[object, ...] = (
                request.scope.workspace_id,
                request.limit + 1,
            )
        else:
            seek = "AND authority_ref > %s"
            parameters = (
                request.scope.workspace_id,
                cursor.item_id,
                request.limit + 1,
            )
        rows = self._connection.execute(
            f"""
            SELECT registration_id, workspace_id, authority_ref, authority,
                   admitted_by, admitted_at, status, metadata
            FROM cpk_ingress_authorities
            WHERE workspace_id = %s
              AND status = 'active'
              {seek}
            ORDER BY authority_ref ASC
            LIMIT %s
            """,
            parameters,
        ).fetchall()
        return ReadPage.from_candidates(
            request,
            tuple(
                ReadPageCandidate(
                    _row_to_authority(row),
                    IdentityReadCursor(
                        ReadCollection.INGRESS_AUTHORITIES,
                        request.scope,
                        row[2],
                    ),
                )
                for row in rows
            ),
        )

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
        encoded_created_at = encode_postgres_timestamp(resource.created_at)
        encoded_observed_at = encode_postgres_timestamp(resource.observed_at)
        encoded_removed_at = (
            None
            if resource.removed_at is None
            else encode_postgres_timestamp(resource.removed_at)
        )
        existing = self._get_blocking_cloudflare(
            resource.workspace_id,
            resource.ingress_id,
        )
        if existing is not None:
            if existing == resource:
                return existing
            raise OwnedIngressResourceConflict(
                "owned ingress resource replacement requires explicit policy"
            )
        resource = replace(
            resource,
            epoch=self._next_cloudflare_epoch(
                resource.workspace_id,
                resource.ingress_id,
            ),
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
                encoded_created_at,
                encoded_observed_at,
                resource.source_run_id,
                resource.source_activity_id,
                resource.source_event_id,
                encoded_removed_at,
                resource.removed_by_run_id,
            ),
        )
        return resource

    def require_active_cloudflare(
        self,
        workspace_id: str,
        ingress_id: str,
    ) -> CloudflareOwnedIngressResource:
        resource = self._get_cloudflare_by_status(
            workspace_id,
            ingress_id,
            (OwnedIngressResourceStatus.ACTIVE,),
        )
        if resource is None:
            raise IngressAuthorityNotFound("active owned ingress resource was not found")
        return resource

    def mark_removing(
        self,
        workspace_id: str,
        ingress_id: str,
        *,
        source_run_id: str,
    ) -> CloudflareOwnedIngressResource:
        resource = self.require_active_cloudflare(workspace_id, ingress_id)
        updated = replace(
            resource,
            status=OwnedIngressResourceStatus.REMOVING,
            source_run_id=source_run_id,
        )
        self._update_cloudflare_status(updated)
        return updated

    def mark_removed(
        self,
        workspace_id: str,
        ingress_id: str,
        *,
        removed_at: str,
        removed_by_run_id: str,
    ) -> CloudflareOwnedIngressResource:
        encode_postgres_timestamp(removed_at)
        resource = self._get_cloudflare_by_status(
            workspace_id,
            ingress_id,
            (
                OwnedIngressResourceStatus.ACTIVE,
                OwnedIngressResourceStatus.REMOVING,
            ),
        )
        if resource is None:
            raise IngressAuthorityNotFound("removable owned ingress resource was not found")
        updated = replace(
            resource,
            status=OwnedIngressResourceStatus.REMOVED,
            removed_at=removed_at,
            removed_by_run_id=removed_by_run_id,
        )
        self._update_cloudflare_status(updated)
        return updated

    def mark_uncertain(
        self,
        workspace_id: str,
        ingress_id: str,
        *,
        source_run_id: str,
    ) -> CloudflareOwnedIngressResource:
        resource = self._get_blocking_cloudflare(workspace_id, ingress_id)
        if resource is None:
            raise IngressAuthorityNotFound("owned ingress resource was not found")
        updated = replace(
            resource,
            status=OwnedIngressResourceStatus.UNCERTAIN,
            source_run_id=source_run_id,
        )
        self._update_cloudflare_status(updated)
        return updated

    def get_cloudflare(
        self,
        workspace_id: str,
        ingress_id: str,
    ) -> CloudflareOwnedIngressResource:
        resource = self._get_blocking_cloudflare(workspace_id, ingress_id)
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

    def _get_blocking_cloudflare(
        self,
        workspace_id: str,
        ingress_id: str,
    ) -> CloudflareOwnedIngressResource | None:
        return self._get_cloudflare_by_status(
            workspace_id,
            ingress_id,
            _BLOCKING_INGRESS_RESOURCE_STATUSES,
        )

    def _get_cloudflare_by_status(
        self,
        workspace_id: str,
        ingress_id: str,
        statuses: tuple[OwnedIngressResourceStatus, ...],
    ) -> CloudflareOwnedIngressResource | None:
        if not statuses:
            raise IngressAuthorityRegistrationError(
                "owned ingress status filter must not be empty"
            )
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
              AND status = ANY(%s)
            ORDER BY epoch DESC
            LIMIT 1
            """,
            (workspace_id, ingress_id, [status.value for status in statuses]),
        ).fetchone()
        if row is None:
            return None
        return _row_to_cloudflare_resource(row)

    def _next_cloudflare_epoch(self, workspace_id: str, ingress_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(epoch), 0) + 1
            FROM cpk_cloudflare_ingress_resources
            WHERE workspace_id = %s
              AND ingress_id = %s
            """,
            (workspace_id, ingress_id),
        ).fetchone()
        return row[0]

    def _update_cloudflare_status(
        self,
        resource: CloudflareOwnedIngressResource,
    ) -> None:
        encoded_removed_at = (
            None
            if resource.removed_at is None
            else encode_postgres_timestamp(resource.removed_at)
        )
        self._connection.execute(
            """
            UPDATE cpk_cloudflare_ingress_resources
            SET
              status = %s,
              source_run_id = %s,
              removed_at = %s,
              removed_by_run_id = %s
            WHERE workspace_id = %s
              AND ingress_id = %s
              AND epoch = %s
            """,
            (
                resource.status.value,
                resource.source_run_id,
                encoded_removed_at,
                resource.removed_by_run_id,
                resource.workspace_id,
                resource.ingress_id,
                resource.epoch,
            ),
        )


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
        encoded_recorded_at = encode_postgres_timestamp(evidence.recorded_at)
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
              source_event_id,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                evidence.workspace_id,
                evidence.purpose.value,
                evidence.secret_ref.reference_id,
                encoded_recorded_at,
                evidence.source_run_id,
                evidence.source_activity_id,
                evidence.source_event_id,
                Jsonb(
                    {
                        "provider_registration_id": (
                            evidence.provider_registration_id
                        ),
                        "reference_registration_id": (
                            evidence.reference_registration_id
                        ),
                        "custody_id": evidence.custody_id,
                        "provider_version_id": evidence.provider_version_id,
                        "provider_version_number": (
                            evidence.provider_version_number
                        ),
                    }
                ),
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
              source_event_id,
              metadata
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
              source_event_id,
              metadata
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
        admitted_at=decode_postgres_timestamp(row[5]),
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
        created_at=decode_postgres_timestamp(row[13]),
        observed_at=decode_postgres_timestamp(row[14]),
        source_run_id=row[15],
        source_activity_id=row[16],
        source_event_id=row[17],
        removed_at=(
            None if row[18] is None else decode_postgres_timestamp(row[18])
        ),
        removed_by_run_id=row[19],
    )


def _row_to_generated_ingress_secret_reference(
    row: tuple[Any, ...],
) -> GeneratedIngressSecretReference:
    metadata = row[7]
    if not isinstance(metadata, Mapping):
        raise IngressAuthorityRegistrationError(
            "generated ingress secret custody metadata is malformed"
        )
    return GeneratedIngressSecretReference(
        workspace_id=row[0],
        purpose=GeneratedSecretPurpose(row[1]),
        secret_ref=SecretReference(row[2]),
        provider_registration_id=_metadata_text(
            metadata,
            "provider_registration_id",
        ),
        reference_registration_id=_metadata_text(
            metadata,
            "reference_registration_id",
        ),
        custody_id=_metadata_text(metadata, "custody_id"),
        provider_version_id=_metadata_text(metadata, "provider_version_id"),
        provider_version_number=_metadata_positive_int(
            metadata,
            "provider_version_number",
        ),
        recorded_at=decode_postgres_timestamp(row[3]),
        source_run_id=row[4],
        source_activity_id=row[5],
        source_event_id=row[6],
    )


def _metadata_text(metadata: Mapping[str, object], name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str):
        raise IngressAuthorityRegistrationError(
            "generated ingress secret custody metadata is incomplete"
        )
    return value


def _metadata_positive_int(metadata: Mapping[str, object], name: str) -> int:
    value = metadata.get(name)
    if type(value) is not int or value < 1:
        raise IngressAuthorityRegistrationError(
            "generated ingress secret custody metadata is incomplete"
        )
    return value


def _credential_references(authority: RegisteredIngressAuthority) -> dict[str, object]:
    if authority.provider_kind is IngressAuthorityProviderKind.CLOUDFLARE:
        return {"api_token_ref": authority.authority.api_token_ref.reference_id}
    return {}
