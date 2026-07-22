"""Store bundle boundary for Postgres-backed operations."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit_operations.postgres.schema import PostgresConnection


@dataclass(frozen=True)
class PostgresStoreBundle:
    """Stores bound to one caller-owned Postgres connection.

    Domain stores are added in later issues. The bundle already preserves the
    important ownership law: every future store is constructed from this single
    connection and cannot commit independently through the bundle.
    """

    connection: PostgresConnection
