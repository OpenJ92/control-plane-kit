"""Narrow persistence capabilities owned by one discovery server."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Self

from control_plane_kit.discovery import (
    DiscoveryIdentity,
    DiscoveryRegistration,
    DiscoveryRegistrationRecord,
    DiscoveryRegistrationStatus,
)


class DiscoveryCommandLedgerRecord(Protocol):
    command_id: str
    intent_fingerprint: str
    result_descriptor: dict[str, object]


class DiscoveryStore(Protocol):
    def lock_command(self, command_id: str) -> None: ...
    def get_command(self, command_id: str) -> DiscoveryCommandLedgerRecord | None: ...
    def add_command(
        self,
        command_id: str,
        workspace_id: str,
        variant: str,
        intent_fingerprint: str,
        actor_id: str,
        result_descriptor: dict[str, object],
        recorded_at: datetime,
    ) -> None: ...
    def lock_identity(self, identity: DiscoveryIdentity) -> None: ...
    def get(self, identity: DiscoveryIdentity) -> DiscoveryRegistrationRecord | None: ...
    def register(
        self,
        registration: DiscoveryRegistration,
        updated_at: datetime,
    ) -> DiscoveryRegistrationRecord | None: ...
    def heartbeat(
        self,
        identity: DiscoveryIdentity,
        expected_expires_at: datetime,
        replacement: DiscoveryRegistration,
        updated_at: datetime,
    ) -> DiscoveryRegistrationRecord | None: ...
    def set_status(
        self,
        identity: DiscoveryIdentity,
        expected_expires_at: datetime,
        replacement: DiscoveryRegistrationStatus,
        updated_at: datetime,
    ) -> DiscoveryRegistrationRecord | None: ...
    def resolve(
        self,
        workspace_id: str,
        service_id: str,
        observed_at: datetime,
        limit: int,
    ) -> tuple[DiscoveryRegistrationRecord, ...]: ...
    def expire(
        self,
        workspace_id: str,
        observed_at: datetime,
        limit: int,
    ) -> tuple[DiscoveryRegistrationRecord, ...]: ...


class DiscoveryUnitOfWork(Protocol):
    @property
    def store(self) -> DiscoveryStore: ...
    def __enter__(self) -> Self: ...
    def commit(self) -> None: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...
