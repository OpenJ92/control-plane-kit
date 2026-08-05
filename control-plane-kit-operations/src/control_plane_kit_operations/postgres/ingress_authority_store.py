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
    CloudflareOwnedHostnameReservation,
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
    OwnedHostnameReservationConflict,
    OwnedHostnameReservationStatus,
    OwnedIngressResourceStatus,
    OwnedIngressResourceConflict,
    RegisteredIngressAuthority,
    RegisteredIngressAuthorityStatus,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection


_BLOCKING_INGRESS_RESOURCE_STATUSES = (
    OwnedIngressResourceStatus.ALLOCATING,
    OwnedIngressResourceStatus.ACTIVE,
    OwnedIngressResourceStatus.REMOVING,
    OwnedIngressResourceStatus.UNCERTAIN,
    OwnedIngressResourceStatus.ORPHANED,
)

_RESERVATION_COLUMNS = """
  reservation_id, workspace_id, ingress_id, authority_ref, provider_kind,
  dns_record_id, hostname, zone_id, lifecycle, status, version, created_at,
  observed_at, source_run_id, source_activity_id, source_event_id,
  transitioned_at, transition_run_id, transition_activity_id,
  transition_event_id, released_at, released_by_run_id
"""

_ALLOWED_RESERVATION_TRANSITIONS = {
    OwnedHostnameReservationStatus.RESERVING: frozenset(
        {
            OwnedHostnameReservationStatus.BOUND,
            OwnedHostnameReservationStatus.UNCERTAIN,
        }
    ),
    OwnedHostnameReservationStatus.BOUND: frozenset(
        {
            OwnedHostnameReservationStatus.RESERVED,
            OwnedHostnameReservationStatus.UNCERTAIN,
        }
    ),
    OwnedHostnameReservationStatus.RESERVED: frozenset(
        {
            OwnedHostnameReservationStatus.BOUND,
            OwnedHostnameReservationStatus.RELEASING,
            OwnedHostnameReservationStatus.UNCERTAIN,
        }
    ),
    OwnedHostnameReservationStatus.RELEASING: frozenset(
        {
            OwnedHostnameReservationStatus.RELEASED,
            OwnedHostnameReservationStatus.UNCERTAIN,
        }
    ),
    OwnedHostnameReservationStatus.RELEASED: frozenset(),
    OwnedHostnameReservationStatus.UNCERTAIN: frozenset(),
}


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


class IngressReservationStore:
    """Persist exact retained hostname identity independently from tunnel epochs."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def record_cloudflare(
        self,
        reservation: CloudflareOwnedHostnameReservation,
    ) -> CloudflareOwnedHostnameReservation:
        if not isinstance(reservation, CloudflareOwnedHostnameReservation):
            raise TypeError(
                "record_cloudflare requires CloudflareOwnedHostnameReservation"
            )
        self._lock_workspace(reservation.workspace_id)
        by_id = self._get_by_id(
            reservation.workspace_id,
            reservation.reservation_id,
        )
        if by_id is not None:
            if by_id == reservation:
                return by_id
            raise OwnedHostnameReservationConflict(
                "hostname reservation identity replacement requires explicit policy"
            )
        blocking = self._get_live_by_keys(
            reservation.workspace_id,
            reservation.ingress_id,
            reservation.authority_ref.reference_id,
            reservation.hostname,
        )
        if blocking is not None:
            if blocking == reservation:
                return blocking
            raise OwnedHostnameReservationConflict(
                "live hostname reservation ownership already exists"
            )
        self._connection.execute(
            """
            INSERT INTO cpk_cloudflare_hostname_reservations (
              reservation_id, workspace_id, ingress_id, authority_ref,
              provider_kind, dns_record_id, hostname, zone_id, lifecycle,
              status, version, created_at, observed_at, source_run_id,
              source_activity_id, source_event_id, transitioned_at,
              transition_run_id, transition_activity_id, transition_event_id,
              released_at, released_by_run_id
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            _reservation_parameters(reservation),
        )
        return reservation

    def require_cloudflare(
        self,
        workspace_id: str,
        reservation_id: str,
    ) -> CloudflareOwnedHostnameReservation:
        reservation = self._get_by_id(workspace_id, reservation_id)
        if reservation is None:
            raise IngressAuthorityNotFound("hostname reservation was not found")
        return reservation

    def require_cloudflare_for_update(
        self,
        workspace_id: str,
        reservation_id: str,
    ) -> CloudflareOwnedHostnameReservation:
        row = self._connection.execute(
            f"""
            SELECT {_RESERVATION_COLUMNS}
            FROM cpk_cloudflare_hostname_reservations
            WHERE workspace_id = %s AND reservation_id = %s
            FOR UPDATE
            """,
            (workspace_id, reservation_id),
        ).fetchone()
        if row is None:
            raise IngressAuthorityNotFound("hostname reservation was not found")
        return _row_to_cloudflare_reservation(row)

    def require_live_cloudflare_for_ingress(
        self,
        workspace_id: str,
        ingress_id: str,
    ) -> CloudflareOwnedHostnameReservation:
        row = self._connection.execute(
            f"""
            SELECT {_RESERVATION_COLUMNS}
            FROM cpk_cloudflare_hostname_reservations
            WHERE workspace_id = %s
              AND ingress_id = %s
              AND status <> 'released'
            ORDER BY created_at, reservation_id
            LIMIT 1
            """,
            (workspace_id, ingress_id),
        ).fetchone()
        if row is None:
            raise IngressAuthorityNotFound(
                "live hostname reservation was not found"
            )
        return _row_to_cloudflare_reservation(row)

    def list_cloudflare(
        self,
        workspace_id: str,
    ) -> tuple[CloudflareOwnedHostnameReservation, ...]:
        rows = self._connection.execute(
            f"""
            SELECT {_RESERVATION_COLUMNS}
            FROM cpk_cloudflare_hostname_reservations
            WHERE workspace_id = %s
            ORDER BY ingress_id, created_at, reservation_id
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_cloudflare_reservation(row) for row in rows)

    def mark_bound(
        self,
        workspace_id: str,
        reservation_id: str,
        **evidence: object,
    ) -> CloudflareOwnedHostnameReservation:
        return self._transition(
            workspace_id,
            reservation_id,
            OwnedHostnameReservationStatus.BOUND,
            **evidence,
        )

    def mark_reserved(
        self,
        workspace_id: str,
        reservation_id: str,
        **evidence: object,
    ) -> CloudflareOwnedHostnameReservation:
        return self._transition(
            workspace_id,
            reservation_id,
            OwnedHostnameReservationStatus.RESERVED,
            **evidence,
        )

    def mark_releasing(
        self,
        workspace_id: str,
        reservation_id: str,
        **evidence: object,
    ) -> CloudflareOwnedHostnameReservation:
        return self._transition(
            workspace_id,
            reservation_id,
            OwnedHostnameReservationStatus.RELEASING,
            **evidence,
        )

    def mark_released(
        self,
        workspace_id: str,
        reservation_id: str,
        *,
        released_by_run_id: str,
        **evidence: object,
    ) -> CloudflareOwnedHostnameReservation:
        return self._transition(
            workspace_id,
            reservation_id,
            OwnedHostnameReservationStatus.RELEASED,
            released_by_run_id=released_by_run_id,
            **evidence,
        )

    def mark_uncertain(
        self,
        workspace_id: str,
        reservation_id: str,
        **evidence: object,
    ) -> CloudflareOwnedHostnameReservation:
        return self._transition(
            workspace_id,
            reservation_id,
            OwnedHostnameReservationStatus.UNCERTAIN,
            **evidence,
        )

    def _transition(
        self,
        workspace_id: str,
        reservation_id: str,
        status: OwnedHostnameReservationStatus,
        *,
        expected_version: int,
        transitioned_at: str,
        source_run_id: str,
        source_activity_id: str,
        source_event_id: str,
        released_by_run_id: str | None = None,
    ) -> CloudflareOwnedHostnameReservation:
        current = self.require_cloudflare_for_update(workspace_id, reservation_id)
        if current.version != expected_version:
            raise OwnedHostnameReservationConflict(
                "hostname reservation version changed"
            )
        if status not in _ALLOWED_RESERVATION_TRANSITIONS[current.status]:
            raise OwnedHostnameReservationConflict(
                f"hostname reservation cannot transition from {current.status.value} "
                f"to {status.value}"
            )
        if (
            status is OwnedHostnameReservationStatus.RESERVED
            and self._has_blocking_realization(reservation_id)
        ):
            raise OwnedHostnameReservationConflict(
                "hostname reservation cannot become reserved while a realization remains"
            )
        updated = replace(
            current,
            status=status,
            version=current.version + 1,
            observed_at=transitioned_at,
            transitioned_at=transitioned_at,
            transition_run_id=source_run_id,
            transition_activity_id=source_activity_id,
            transition_event_id=source_event_id,
            released_at=(
                transitioned_at
                if status is OwnedHostnameReservationStatus.RELEASED
                else None
            ),
            released_by_run_id=released_by_run_id,
        )
        cursor = self._connection.execute(
            """
            UPDATE cpk_cloudflare_hostname_reservations
            SET status = %s,
                version = %s,
                observed_at = %s,
                transitioned_at = %s,
                transition_run_id = %s,
                transition_activity_id = %s,
                transition_event_id = %s,
                released_at = %s,
                released_by_run_id = %s
            WHERE workspace_id = %s
              AND reservation_id = %s
              AND version = %s
            """,
            (
                updated.status.value,
                updated.version,
                updated.observed_at,
                updated.transitioned_at,
                updated.transition_run_id,
                updated.transition_activity_id,
                updated.transition_event_id,
                updated.released_at,
                updated.released_by_run_id,
                workspace_id,
                reservation_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise OwnedHostnameReservationConflict(
                "hostname reservation transition lost optimistic concurrency"
            )
        return updated

    def _get_by_id(
        self,
        workspace_id: str,
        reservation_id: str,
    ) -> CloudflareOwnedHostnameReservation | None:
        row = self._connection.execute(
            f"""
            SELECT {_RESERVATION_COLUMNS}
            FROM cpk_cloudflare_hostname_reservations
            WHERE workspace_id = %s AND reservation_id = %s
            """,
            (workspace_id, reservation_id),
        ).fetchone()
        return None if row is None else _row_to_cloudflare_reservation(row)

    def _has_blocking_realization(self, reservation_id: str) -> bool:
        row = self._connection.execute(
            """
            SELECT 1
            FROM cpk_cloudflare_ingress_resources
            WHERE reservation_id = %s
              AND status <> 'removed'
            LIMIT 1
            """,
            (reservation_id,),
        ).fetchone()
        return row is not None

    def _get_live_by_keys(
        self,
        workspace_id: str,
        ingress_id: str,
        authority_ref: str,
        hostname: str,
    ) -> CloudflareOwnedHostnameReservation | None:
        row = self._connection.execute(
            f"""
            SELECT {_RESERVATION_COLUMNS}
            FROM cpk_cloudflare_hostname_reservations
            WHERE workspace_id = %s
              AND status <> 'released'
              AND (
                ingress_id = %s
                OR (authority_ref = %s AND hostname = %s)
              )
            ORDER BY created_at, reservation_id
            LIMIT 1
            """,
            (workspace_id, ingress_id, authority_ref, hostname),
        ).fetchone()
        return None if row is None else _row_to_cloudflare_reservation(row)

    def _lock_workspace(self, workspace_id: str) -> None:
        row = self._connection.execute(
            "SELECT workspace_id FROM cpk_workspaces WHERE workspace_id = %s FOR UPDATE",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise IngressAuthorityNotFound("reservation workspace was not found")


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
        if resource.reservation_id is not None:
            reservation = IngressReservationStore(
                self._connection
            ).require_cloudflare_for_update(
                resource.workspace_id,
                resource.reservation_id,
            )
            if (
                reservation.ingress_id != resource.ingress_id
                or reservation.authority_ref != resource.authority_ref
                or reservation.hostname != resource.hostname
                or reservation.zone_id != resource.zone_id
                or reservation.dns_record_id != resource.dns_record_id
            ):
                raise OwnedIngressResourceConflict(
                    "ingress realization does not match hostname reservation"
                )
            if reservation.status in {
                OwnedHostnameReservationStatus.RELEASING,
                OwnedHostnameReservationStatus.RELEASED,
                OwnedHostnameReservationStatus.UNCERTAIN,
            }:
                raise OwnedIngressResourceConflict(
                    "hostname reservation status blocks tunnel realization"
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
              reservation_id,
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
              %s, %s, %s, %s, %s, %s
            )
            """,
            (
                resource.workspace_id,
                resource.runtime_id,
                resource.ingress_id,
                resource.reservation_id,
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
        expected_epoch: int | None = None,
    ) -> CloudflareOwnedIngressResource:
        resource = self.require_active_cloudflare(workspace_id, ingress_id)
        _require_expected_epoch(resource, expected_epoch)
        updated = replace(
            resource,
            status=OwnedIngressResourceStatus.REMOVING,
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
        expected_epoch: int | None = None,
    ) -> CloudflareOwnedIngressResource:
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
        _require_expected_epoch(resource, expected_epoch)
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
        expected_epoch: int | None = None,
    ) -> CloudflareOwnedIngressResource:
        resource = self._get_blocking_cloudflare(workspace_id, ingress_id)
        if resource is None:
            raise IngressAuthorityNotFound("owned ingress resource was not found")
        _require_expected_epoch(resource, expected_epoch)
        updated = replace(
            resource,
            status=OwnedIngressResourceStatus.UNCERTAIN,
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
              reservation_id,
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

    def require_latest_removed_cloudflare(
        self,
        workspace_id: str,
        ingress_id: str,
        reservation_id: str,
    ) -> CloudflareOwnedIngressResource:
        resource = self._get_cloudflare_by_status(
            workspace_id,
            ingress_id,
            (OwnedIngressResourceStatus.REMOVED,),
        )
        if resource is None or resource.reservation_id != reservation_id:
            raise IngressAuthorityNotFound(
                "removed ingress realization for reservation was not found"
            )
        return resource

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
              reservation_id,
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
        self._connection.execute(
            """
            UPDATE cpk_cloudflare_ingress_resources
            SET
              status = %s,
              removed_at = %s,
              removed_by_run_id = %s
            WHERE workspace_id = %s
              AND ingress_id = %s
              AND epoch = %s
            """,
            (
                resource.status.value,
                resource.removed_at,
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
                evidence.recorded_at,
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
        admitted_at=row[5],
        status=RegisteredIngressAuthorityStatus(row[6]),
        metadata=row[7],
    )


def _row_to_cloudflare_resource(row: tuple[Any, ...]) -> CloudflareOwnedIngressResource:
    return CloudflareOwnedIngressResource(
        workspace_id=row[0],
        runtime_id=row[1],
        ingress_id=row[2],
        reservation_id=row[3],
        epoch=row[4],
        status=OwnedIngressResourceStatus(row[5]),
        authority_ref=IngressAuthorityReference(row[6]),
        provider_kind=IngressAuthorityProviderKind(row[7]),
        tunnel_name=row[8],
        tunnel_id=row[9],
        dns_record_id=row[10],
        hostname=row[11],
        zone_id=row[12],
        lifecycle=PublicIngressLifecycle(row[13]),
        created_at=row[14],
        observed_at=row[15],
        source_run_id=row[16],
        source_activity_id=row[17],
        source_event_id=row[18],
        removed_at=row[19],
        removed_by_run_id=row[20],
    )


def _row_to_cloudflare_reservation(
    row: tuple[Any, ...],
) -> CloudflareOwnedHostnameReservation:
    return CloudflareOwnedHostnameReservation(
        reservation_id=row[0],
        workspace_id=row[1],
        ingress_id=row[2],
        authority_ref=IngressAuthorityReference(row[3]),
        provider_kind=IngressAuthorityProviderKind(row[4]),
        dns_record_id=row[5],
        hostname=row[6],
        zone_id=row[7],
        lifecycle=PublicIngressLifecycle(row[8]),
        status=OwnedHostnameReservationStatus(row[9]),
        version=row[10],
        created_at=row[11],
        observed_at=row[12],
        source_run_id=row[13],
        source_activity_id=row[14],
        source_event_id=row[15],
        transitioned_at=row[16],
        transition_run_id=row[17],
        transition_activity_id=row[18],
        transition_event_id=row[19],
        released_at=row[20],
        released_by_run_id=row[21],
    )


def _reservation_parameters(
    reservation: CloudflareOwnedHostnameReservation,
) -> tuple[object, ...]:
    return (
        reservation.reservation_id,
        reservation.workspace_id,
        reservation.ingress_id,
        reservation.authority_ref.reference_id,
        reservation.provider_kind.value,
        reservation.dns_record_id,
        reservation.hostname,
        reservation.zone_id,
        reservation.lifecycle.value,
        reservation.status.value,
        reservation.version,
        reservation.created_at,
        reservation.observed_at,
        reservation.source_run_id,
        reservation.source_activity_id,
        reservation.source_event_id,
        reservation.transitioned_at,
        reservation.transition_run_id,
        reservation.transition_activity_id,
        reservation.transition_event_id,
        reservation.released_at,
        reservation.released_by_run_id,
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
        recorded_at=row[3],
        source_run_id=row[4],
        source_activity_id=row[5],
        source_event_id=row[6],
    )


def _require_expected_epoch(
    resource: CloudflareOwnedIngressResource,
    expected_epoch: int | None,
) -> None:
    if expected_epoch is None:
        return
    if type(expected_epoch) is not int or expected_epoch < 1:
        raise OwnedIngressResourceConflict(
            "expected ingress realization epoch must be positive"
        )
    if resource.epoch != expected_epoch:
        raise OwnedIngressResourceConflict(
            "ingress realization epoch changed"
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
