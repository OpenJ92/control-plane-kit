"""Discovery-server-owned Postgres transaction boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, Self

from control_plane_kit.discovery_registry.postgres import (
    DISCOVERY_POSTGRES_SCHEMA,
    PostgresDiscoveryStore,
)


class TransactionalConnection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ()) -> object: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[], TransactionalConnection]


class PostgresDiscoveryUnitOfWork:
    """Vend one registry store over one explicit command transaction."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory
        self._connection: TransactionalConnection | None = None
        self._store: PostgresDiscoveryStore | None = None
        self._commit_requested = False

    @property
    def store(self) -> PostgresDiscoveryStore:
        if self._store is None:
            raise RuntimeError("discovery unit of work is not active")
        return self._store

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("discovery unit of work cannot be re-entered")
        self._connection = self._connection_factory()
        self._store = PostgresDiscoveryStore(self._connection)
        self._commit_requested = False
        return self

    def commit(self) -> None:
        if self._connection is None:
            raise RuntimeError("discovery unit of work is not active")
        if self._commit_requested:
            raise RuntimeError("discovery unit of work commit was already requested")
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
            self._store = None
            connection.close()


def install_discovery_schema(connection_factory: ConnectionFactory) -> None:
    connection = connection_factory()
    try:
        connection.execute(DISCOVERY_POSTGRES_SCHEMA)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
