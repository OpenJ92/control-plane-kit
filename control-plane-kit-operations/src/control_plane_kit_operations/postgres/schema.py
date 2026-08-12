"""Direct installation and verification of the current operations schema."""

from __future__ import annotations

from importlib.resources import files
from typing import Any, ContextManager, Protocol

from .current_data_validation import CurrentRowDrift, validate_current_rows
from .current_schema_contract import CURRENT_POSTGRES_SCHEMA_CONTRACT
from .current_schema_verification import (
    current_namespace_adjuncts_are_exact,
    current_schema_contract_is_exact,
    expected_relations_are_present,
    namespace_is_object_free,
)


class PostgresConnection(Protocol):
    def execute(self, query: str, params: object = ...) -> Any: ...


class _SchemaConnection(PostgresConnection, Protocol):
    @property
    def autocommit(self) -> bool: ...

    def transaction(self) -> ContextManager[Any]: ...


class SchemaInstallationError(RuntimeError):
    """A bounded failure to install or verify the operations schema."""


class _ResetRequired(Exception):
    pass


_CURRENT_SCHEMA_SQL = (
    files(__package__).joinpath("current_schema.sql").read_text(encoding="utf-8")
)
_ADVISORY_LOCK_SQL = """
SELECT pg_advisory_xact_lock(
  hashtextextended(current_database() || chr(31) || current_schema(), 0)
)
"""
_CURRENT_RELATIONS = tuple(
    relation.name for relation in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations
)
_CURRENT_RELATION_LOCK_SQL = "\n".join(
    f'LOCK TABLE ONLY "{relation}" IN SHARE MODE;' for relation in _CURRENT_RELATIONS
)


def install_schema(connection: _SchemaConnection) -> None:
    """Create an empty owned namespace or verify exact current truth."""

    try:
        autocommit = connection.autocommit
    except Exception:
        autocommit = None
    if type(autocommit) is not bool:
        raise SchemaInstallationError(
            "operations schema installation failed"
        ) from None

    failure: str | None = None
    try:
        with connection.transaction():
            _install_under_transaction(connection)
    except _ResetRequired:
        failure = "operations schema reset is required"
    except Exception:
        failure = "operations schema installation failed"
    if failure is not None:
        raise SchemaInstallationError(failure) from None


def _install_under_transaction(connection: _SchemaConnection) -> None:
    connection.execute(_ADVISORY_LOCK_SQL)
    if namespace_is_object_free(connection):
        connection.execute(_CURRENT_SCHEMA_SQL)
        connection.execute(_CURRENT_RELATION_LOCK_SQL)
        if not _current_schema_is_exact(connection):
            raise RuntimeError("fresh schema verification failed")
        return

    if not expected_relations_are_present(connection):
        raise _ResetRequired
    connection.execute(_CURRENT_RELATION_LOCK_SQL)
    if not _current_schema_is_exact(connection):
        raise _ResetRequired


def _current_schema_is_exact(connection: PostgresConnection) -> bool:
    if not current_schema_contract_is_exact(connection):
        return False
    if not current_namespace_adjuncts_are_exact(connection):
        return False
    try:
        validate_current_rows(connection)
    except CurrentRowDrift:
        return False
    return True


__all__ = ["PostgresConnection", "SchemaInstallationError", "install_schema"]
