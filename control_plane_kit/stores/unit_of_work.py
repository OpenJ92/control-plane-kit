"""Caller-owned Postgres transaction boundary for operator commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, Self

from control_plane_kit.stores.postgres import PostgresConnection, PostgresStoreBundle


class TransactionalPostgresConnection(PostgresConnection, Protocol):
    """Postgres connection operations owned by a unit of work."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


PostgresConnectionFactory = Callable[[], TransactionalPostgresConnection]


class UnitOfWorkStateError(RuntimeError):
    """Raised when a unit of work is used outside its active transaction."""


class PostgresUnitOfWork:
    """Vend stores sharing one explicit caller-owned Postgres transaction.

    Entering opens one connection and binds every store adapter to it. The
    application command must call :meth:`commit` explicitly. Exceptional and
    uncommitted exits roll back, and every exit closes the connection.
    """

    def __init__(self, connection_factory: PostgresConnectionFactory) -> None:
        self._connection_factory = connection_factory
        self._connection: TransactionalPostgresConnection | None = None
        self._stores: PostgresStoreBundle | None = None
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
        self._finished = False
        return self

    def commit(self) -> None:
        """Commit the complete operator command exactly once."""

        connection = self._active_connection()
        connection.commit()
        self._finished = True

    def rollback(self) -> None:
        """Roll back the complete operator command exactly once."""

        connection = self._active_connection()
        connection.rollback()
        self._finished = True

    def close(self) -> None:
        """Close the transaction connection and invalidate bound stores."""

        if self._connection is None:
            return
        self._connection.close()
        self._connection = None
        self._stores = None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if not self._finished:
                self.rollback()
        finally:
            self.close()

    def _active_connection(self) -> TransactionalPostgresConnection:
        if self._connection is None or self._finished:
            raise UnitOfWorkStateError("unit of work is not active")
        return self._connection
