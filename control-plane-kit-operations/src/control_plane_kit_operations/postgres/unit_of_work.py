"""Caller-owned Postgres transaction boundary for operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, Self

from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle


class TransactionalPostgresConnection(PostgresConnection, Protocol):
    """Postgres connection operations owned by a unit of work."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


PostgresConnectionFactory = Callable[[], TransactionalPostgresConnection]


class UnitOfWorkStateError(RuntimeError):
    """Raised when a unit of work is used outside its active transaction."""


class PostgresUnitOfWork:
    """Vend stores sharing one explicit caller-owned Postgres transaction."""

    def __init__(self, connection_factory: PostgresConnectionFactory) -> None:
        self._connection_factory = connection_factory
        self._connection: TransactionalPostgresConnection | None = None
        self._stores: PostgresStoreBundle | None = None
        self._commit_requested = False
        self._finished = False

    @property
    def stores(self) -> PostgresStoreBundle:
        """Return adapters bound to the active transaction connection."""

        if self._stores is None or self._finished:
            raise UnitOfWorkStateError("unit of work is not active")
        return self._stores

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise UnitOfWorkStateError("unit of work cannot be re-entered")
        self._connection = self._connection_factory()
        self._stores = PostgresStoreBundle(self._connection)
        self._commit_requested = False
        self._finished = False
        return self

    def commit(self) -> None:
        """Request commit when the complete command exits successfully."""

        self._active_connection()
        if self._commit_requested:
            raise UnitOfWorkStateError("unit of work commit was already requested")
        self._commit_requested = True

    def rollback(self) -> None:
        """Roll back the complete operator command exactly once."""

        connection = self._active_connection()
        connection.rollback()
        self._finished = True

    def close(self) -> None:
        """Close the transaction connection and invalidate bound stores."""

        if self._connection is None:
            return
        connection = self._connection
        self._connection = None
        self._stores = None
        connection.close()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if self._finished:
                return
            connection = self._active_connection()
            if exc_type is None and self._commit_requested:
                try:
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    self._finished = True
            else:
                self.rollback()
        finally:
            self.close()

    def _active_connection(self) -> TransactionalPostgresConnection:
        if self._connection is None or self._finished:
            raise UnitOfWorkStateError("unit of work is not active")
        return self._connection
