"""Transactional interpretation of canonical CPK Postgres migrations."""

from __future__ import annotations

from control_plane_kit_operations.postgres.migration_inspection import (
    inspect_postgres_schema,
    verify_postgres_schema,
)
from control_plane_kit_operations.postgres.migrations import (
    ObservedSchemaKind,
    SchemaMigrationActionKind,
    SchemaMigrationError,
    SchemaMigrationPlan,
)
from control_plane_kit_operations.postgres.schema import (
    POSTGRES_SCHEMA,
    POSTGRES_SCHEMA_MIGRATIONS,
    MigrationPostgresConnection,
    PostgresConnection,
    _GRAPH_LINEAGE_CONSTRAINTS,
    _backfill_graph_lineage,
    _upgrade_approval_scope_constraints,
    _upgrade_gateway_key_rotation_retirement_constraint,
    _upgrade_gateway_key_rotation_status_constraints,
)


_CREATE_LEDGER = """
CREATE TABLE cpk_schema_migrations (
  version integer NOT NULL PRIMARY KEY,
  name text NOT NULL,
  checksum_sha256 text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
)
"""
_LOCK = """
SELECT pg_advisory_xact_lock(
  hashtextextended(current_database() || chr(31) || current_schema(), 0)
)
"""


def plan_postgres_schema_install(
    connection: PostgresConnection,
) -> SchemaMigrationPlan:
    """Inspect current truth and produce the canonical mutation plan."""

    return POSTGRES_SCHEMA_MIGRATIONS.plan(inspect_postgres_schema(connection))


def install_postgres_schema(connection: MigrationPostgresConnection) -> None:
    """Apply canonical CPK migrations inside one caller-aware transaction."""

    plan_postgres_schema_install(connection)
    try:
        autocommit = connection.autocommit
    except Exception:
        autocommit = None
    if type(autocommit) is not bool:
        raise SchemaMigrationError("migration connection contract is not accepted")

    if autocommit:
        failed = False
        try:
            with connection.transaction():
                _install_under_transaction(connection)
        except SchemaMigrationError:
            raise
        except Exception:
            failed = True
        if failed:
            raise SchemaMigrationError("schema migration transaction failed")
    else:
        _install_under_transaction(connection)


def _install_under_transaction(connection: PostgresConnection) -> None:
    failure = None
    active_migration_version = None
    try:
        connection.execute(_LOCK)
        observed = inspect_postgres_schema(connection)
        plan = POSTGRES_SCHEMA_MIGRATIONS.plan(observed)
        if observed.kind is not ObservedSchemaKind.VERSIONED:
            connection.execute(_CREATE_LEDGER)
        for action in plan.actions:
            if action.kind is SchemaMigrationActionKind.APPLY:
                active_migration_version = action.migration.version
                connection.execute(action.migration.sql)
                active_migration_version = None
            connection.execute(
                """
                INSERT INTO cpk_schema_migrations
                  (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (
                    action.migration.version,
                    action.migration.name,
                    action.migration.checksum_sha256,
                ),
            )
        if observed.kind is not ObservedSchemaKind.EMPTY:
            connection.execute(POSTGRES_SCHEMA)
        _upgrade_approval_scope_constraints(connection)
        _upgrade_gateway_key_rotation_status_constraints(connection)
        _upgrade_gateway_key_rotation_retirement_constraint(connection)
        _backfill_graph_lineage(connection)
        connection.execute(_GRAPH_LINEAGE_CONSTRAINTS)
        verify_postgres_schema(connection)
    except SchemaMigrationError:
        raise
    except Exception as error:
        failure = (
            "coordination timestamps are not canonical UTC"
            if active_migration_version == 2
            and getattr(error, "sqlstate", None) in {"P1110", "22007", "22008"}
            else "schema migration application failed"
        )
    if failure is not None:
        if failure == "coordination timestamps are not canonical UTC":
            raise SchemaMigrationError(failure)
        raise SchemaMigrationError("schema migration application failed")


__all__ = [
    "MigrationPostgresConnection",
    "install_postgres_schema",
    "plan_postgres_schema_install",
]
