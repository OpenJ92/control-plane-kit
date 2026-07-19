"""Webhook-application-owned Postgres transaction boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, Self, TypeVar

from control_plane_kit.webhook.postgres import (
    PostgresWebhookCommandStore,
    PostgresWebhookIntentStore,
    PostgresWebhookJournalStore,
    PostgresWebhookProjectionStore,
)


class TransactionalConnection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> object: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[], TransactionalConnection]
StoreT = TypeVar("StoreT")


class PostgresWebhookUnitOfWork:
    """Vend all webhook stores over one explicit application transaction."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory
        self._connection: TransactionalConnection | None = None
        self._intents: PostgresWebhookIntentStore | None = None
        self._journal: PostgresWebhookJournalStore | None = None
        self._projections: PostgresWebhookProjectionStore | None = None
        self._commands: PostgresWebhookCommandStore | None = None
        self._commit_requested = False

    @property
    def intents(self) -> PostgresWebhookIntentStore:
        return self._active("intents", self._intents)

    @property
    def journal(self) -> PostgresWebhookJournalStore:
        return self._active("journal", self._journal)

    @property
    def projections(self) -> PostgresWebhookProjectionStore:
        return self._active("projections", self._projections)

    @property
    def commands(self) -> PostgresWebhookCommandStore:
        return self._active("commands", self._commands)

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("webhook unit of work cannot be re-entered")
        connection = self._connection_factory()
        self._connection = connection
        self._intents = PostgresWebhookIntentStore(connection)
        self._journal = PostgresWebhookJournalStore(connection)
        self._projections = PostgresWebhookProjectionStore(connection)
        self._commands = PostgresWebhookCommandStore(connection)
        self._commit_requested = False
        return self

    def commit(self) -> None:
        if self._connection is None:
            raise RuntimeError("webhook unit of work is not active")
        if self._commit_requested:
            raise RuntimeError("webhook unit of work commit was already requested")
        self._commit_requested = True

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._connection is None:
            return
        connection = self._connection
        try:
            if exc_type is None and self._commit_requested:
                try:
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            else:
                connection.rollback()
        finally:
            self._connection = None
            self._intents = None
            self._journal = None
            self._projections = None
            self._commands = None
            connection.close()

    @staticmethod
    def _active(label: str, value: StoreT | None) -> StoreT:
        if value is None:
            raise RuntimeError(f"webhook {label} store is not active")
        return value
