"""Persistence capabilities owned by one webhook-delivery application."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Self

from control_plane_kit.webhook.language import (
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookDeliveryState,
    WebhookEvent,
)


class WebhookCommandRecord(Protocol):
    command_id: str
    intent_fingerprint: str
    result_descriptor: dict[str, object]


class WebhookProjectionRecord(Protocol):
    state: WebhookDeliveryState
    journal_version: int


class WebhookIntentStore(Protocol):
    def add(self, intent: WebhookDeliveryIntent) -> None: ...
    def get(self, identity: WebhookDeliveryIdentity) -> WebhookDeliveryIntent | None: ...


class WebhookJournalStore(Protocol):
    def lock_delivery(self, identity: WebhookDeliveryIdentity) -> None: ...
    def append(
        self,
        identity: WebhookDeliveryIdentity,
        expected_ordinal: int,
        event: WebhookEvent,
    ) -> bool: ...
    def events_for(self, identity: WebhookDeliveryIdentity) -> tuple[WebhookEvent, ...]: ...


class WebhookProjectionStore(Protocol):
    def add(self, state: WebhookDeliveryState, journal_version: int) -> None: ...
    def get(self, identity: WebhookDeliveryIdentity) -> WebhookProjectionRecord | None: ...
    def replace(
        self,
        state: WebhookDeliveryState,
        expected_journal_version: int,
        replacement_journal_version: int,
    ) -> bool: ...


class WebhookCommandStore(Protocol):
    def lock_command(self, command_id: str) -> None: ...
    def get(self, command_id: str) -> WebhookCommandRecord | None: ...
    def add(
        self,
        command_id: str,
        workspace_id: str,
        variant: str,
        intent_fingerprint: str,
        actor_id: str,
        result_descriptor: dict[str, object],
        recorded_at: datetime,
    ) -> None: ...


class WebhookUnitOfWork(Protocol):
    @property
    def intents(self) -> WebhookIntentStore: ...
    @property
    def journal(self) -> WebhookJournalStore: ...
    @property
    def projections(self) -> WebhookProjectionStore: ...
    @property
    def commands(self) -> WebhookCommandStore: ...
    def __enter__(self) -> Self: ...
    def commit(self) -> None: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...
