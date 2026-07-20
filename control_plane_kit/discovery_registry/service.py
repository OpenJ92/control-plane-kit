"""Transactional application service for one discovery registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from typing import TypeAlias

from control_plane_kit.domains.discovery import (
    DeregisterDiscoveryInstance,
    DiscoveryAuthority,
    DiscoveryCommand,
    DiscoveryIdentity,
    DiscoveryOutcome,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    DiscoveryRegistrationRecord,
    DiscoveryRegistrationStatus,
    DiscoveryResult,
    DiscoveryScope,
    ExpireDiscoveryLeases,
    HeartbeatDiscoveryInstance,
    RegisterDiscoveryInstance,
    ResolveDiscoveryService,
    discovery_command_descriptor,
    discovery_result_from_descriptor,
)
from control_plane_kit.discovery_registry.protocols import DiscoveryUnitOfWork


class DiscoveryRegistryError(RuntimeError):
    pass


class DiscoveryDenied(DiscoveryRegistryError):
    pass


class DiscoveryConflict(DiscoveryRegistryError):
    pass


class DiscoveryMissing(DiscoveryRegistryError):
    pass


UnitOfWorkFactory: TypeAlias = Callable[[], DiscoveryUnitOfWork]


class DiscoveryRegistryService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def execute(
        self,
        command: DiscoveryCommand,
        authority: DiscoveryAuthority,
    ) -> DiscoveryResult:
        workspace_id = _workspace_id(command)
        _require_workspace(authority, workspace_id)
        intent = _intent_fingerprint(command, authority)
        recorded_at = _aware(self._clock())
        with self._unit_of_work_factory() as work:
            store = work.store
            store.lock_command(command.command_id)
            prior = store.get_command(command.command_id)
            if prior is not None:
                if prior.intent_fingerprint != intent:
                    raise DiscoveryConflict(
                        "discovery command id is bound to different intent"
                    )
                work.commit()
                return replace(
                    discovery_result_from_descriptor(prior.result_descriptor),
                    replayed=True,
                )

            result = self._apply(store, command, authority, recorded_at)
            store.add_command(
                command.command_id,
                workspace_id,
                _variant(command),
                intent,
                authority.actor_id,
                result.descriptor(),
                recorded_at,
            )
            work.commit()
            return result

    def _apply(
        self,
        store,
        command: DiscoveryCommand,
        authority: DiscoveryAuthority,
        recorded_at: datetime,
    ) -> DiscoveryResult:
        match command:
            case RegisterDiscoveryInstance(registration=registration):
                _authorize_registration(authority, registration)
                store.lock_identity(registration.identity)
                existing = store.get(registration.identity)
                if existing is not None and (
                    existing.status is DiscoveryRegistrationStatus.ACTIVE
                    and existing.registration.lease.expires_at > recorded_at
                ):
                    raise DiscoveryConflict("discovery instance is already active")
                saved = store.register(registration, recorded_at)
                if saved is None:
                    raise DiscoveryConflict("discovery registration changed concurrently")
                return DiscoveryResult(DiscoveryOutcome.REGISTERED, (saved,), 1)

            case HeartbeatDiscoveryInstance(
                identity=identity,
                expected_expires_at=expected,
                replacement_lease=lease,
            ):
                store.lock_identity(identity)
                existing = _existing(store, identity)
                _authorize_existing(authority, existing)
                if recorded_at >= expected:
                    raise DiscoveryConflict("expired discovery lease cannot be renewed")
                if lease.issued_at > expected or lease.expires_at <= expected:
                    raise DiscoveryConflict(
                        "heartbeat lease must be issued before and extend expected expiry"
                    )
                replacement = replace(existing.registration, lease=lease)
                saved = store.heartbeat(
                    identity,
                    expected,
                    replacement,
                    recorded_at,
                )
                if saved is None:
                    raise DiscoveryConflict("discovery heartbeat precondition is stale")
                return DiscoveryResult(DiscoveryOutcome.HEARTBEAT, (saved,), 1)

            case DeregisterDiscoveryInstance(
                identity=identity,
                expected_expires_at=expected,
            ):
                store.lock_identity(identity)
                existing = _existing(store, identity)
                _authorize_existing(authority, existing)
                if recorded_at >= expected:
                    raise DiscoveryConflict("expired discovery lease cannot be deregistered")
                saved = store.set_status(
                    identity,
                    expected,
                    DiscoveryRegistrationStatus.DEREGISTERED,
                    recorded_at,
                )
                if saved is None:
                    raise DiscoveryConflict("discovery deregistration precondition is stale")
                return DiscoveryResult(DiscoveryOutcome.DEREGISTERED, (saved,), 1)

            case ResolveDiscoveryService(
                workspace_id=workspace_id,
                service_id=service_id,
                observed_at=observed_at,
                limit=limit,
            ):
                _require_scope(authority, DiscoveryScope.RESOLVE, DiscoveryScope.MANAGE)
                registrations = store.resolve(
                    workspace_id,
                    service_id,
                    observed_at,
                    limit,
                )
                return DiscoveryResult(
                    DiscoveryOutcome.RESOLVED,
                    registrations,
                    len(registrations),
                )

            case ExpireDiscoveryLeases(
                workspace_id=workspace_id,
                observed_at=observed_at,
                limit=limit,
            ):
                _require_scope(authority, DiscoveryScope.MANAGE)
                expired = store.expire(workspace_id, observed_at, limit)
                return DiscoveryResult(
                    DiscoveryOutcome.EXPIRED,
                    expired,
                    len(expired),
                )


def _existing(store, identity: DiscoveryIdentity) -> DiscoveryRegistrationRecord:
    existing = store.get(identity)
    if existing is None or existing.status is not DiscoveryRegistrationStatus.ACTIVE:
        raise DiscoveryMissing("active discovery registration does not exist")
    return existing


def _authorize_registration(
    authority: DiscoveryAuthority,
    registration: DiscoveryRegistration,
) -> None:
    if registration.mode is DiscoveryRegistrationMode.CONTROL_PLANE:
        _require_scope(authority, DiscoveryScope.MANAGE)
        return
    _require_scope(authority, DiscoveryScope.REGISTER_SELF)
    _require_self_identity(authority, registration.identity)


def _authorize_existing(
    authority: DiscoveryAuthority,
    existing: DiscoveryRegistrationRecord,
) -> None:
    if existing.registration.mode is DiscoveryRegistrationMode.CONTROL_PLANE:
        _require_scope(authority, DiscoveryScope.MANAGE)
        return
    _require_scope(authority, DiscoveryScope.REGISTER_SELF)
    _require_self_identity(authority, existing.registration.identity)


def _require_self_identity(
    authority: DiscoveryAuthority,
    identity: DiscoveryIdentity,
) -> None:
    if (
        authority.subject_service_id != identity.service_id
        or authority.subject_instance_id != identity.instance_id
    ):
        raise DiscoveryDenied("self registration identity does not match authority")


def _require_workspace(authority: DiscoveryAuthority, workspace_id: str) -> None:
    if authority.workspace_id != workspace_id:
        raise DiscoveryDenied("discovery authority cannot cross workspace boundary")


def _require_scope(authority: DiscoveryAuthority, *allowed: DiscoveryScope) -> None:
    if not any(scope in authority.scopes for scope in allowed):
        raise DiscoveryDenied("discovery authority lacks required scope")


def _workspace_id(command: DiscoveryCommand) -> str:
    match command:
        case RegisterDiscoveryInstance(registration=registration):
            return registration.identity.workspace_id
        case HeartbeatDiscoveryInstance(identity=identity):
            return identity.workspace_id
        case DeregisterDiscoveryInstance(identity=identity):
            return identity.workspace_id
        case ResolveDiscoveryService(workspace_id=workspace_id):
            return workspace_id
        case ExpireDiscoveryLeases(workspace_id=workspace_id):
            return workspace_id


def _variant(command: DiscoveryCommand) -> str:
    return str(discovery_command_descriptor(command)["variant"])


def _intent_fingerprint(
    command: DiscoveryCommand,
    authority: DiscoveryAuthority,
) -> str:
    value = {
        "command": discovery_command_descriptor(command),
        "authority": authority.descriptor(),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DiscoveryRegistryError("discovery clock must be timezone-aware")
    return value.astimezone(timezone.utc)
