"""Transactional interpretation of canonical CPK Postgres migrations."""

from __future__ import annotations

from control_plane_kit_operations.postgres.migration_inspection import (
    _acquire_schema_lock_plan,
    _verify_postgres_schema_under_transaction,
    inspect_postgres_schema,
)
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_SCHEMA_LOCK_PLAN,
    PENDING_SCHEMA_LOCK_PLAN,
)
from control_plane_kit_operations.postgres.migrations import (
    DeterministicBackfillStep,
    ObservedSchemaKind,
    SchemaBackfillKind,
    SchemaMigration,
    SchemaMigrationActionKind,
    SchemaMigrationError,
    SchemaMigrationPlan,
    SqlMigrationStep,
)
from control_plane_kit_operations.postgres.product_descriptor_backfill import (
    backfill_product_descriptor_content_v1,
)
from control_plane_kit_operations.postgres.graph_lineage_backfill import (
    backfill_graph_lineage_v1,
)
from control_plane_kit_operations.postgres.schema import (
    POSTGRES_SCHEMA_MIGRATIONS,
    MigrationPostgresConnection,
    PostgresConnection,
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
_TEMPORAL_MIGRATION_SQLSTATES = frozenset({"P1110", "22007", "22008"})
_CATEGORICAL_MIGRATION_FAILURES = {
    2: (
        "coordination timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    3: (
        "graph, product, and authority timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    4: (
        "secret registration timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    5: (
        "delegation signing-key timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    6: (
        "gateway probe timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    7: (
        "gateway key rotation timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    8: (
        "ingress evidence timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    9: (
        "secret-use authorization timestamps are not canonical UTC",
        _TEMPORAL_MIGRATION_SQLSTATES,
    ),
    11: ("gateway probe access path is not accepted", frozenset({"P1110"})),
    12: (
        "gateway key rotation generation evidence is not accepted",
        frozenset({"P1110"}),
    ),
    13: (
        "gateway key rotation status contract is not accepted",
        frozenset({"P1110"}),
    ),
    14: (
        "gateway key rotation retirement evidence is not accepted",
        frozenset({"P1110"}),
    ),
    15: ("approval subject evidence is not accepted", frozenset({"P1110"})),
    16: ("approval scope contract is not accepted", frozenset({"P1110"})),
    17: ("graph lineage compatibility is not accepted", frozenset({"P1110"})),
}


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


def _install_under_transaction(connection: PostgresConnection) -> None:
    failure = None
    active_migration_version = None
    try:
        connection.execute(_LOCK)
        observed = inspect_postgres_schema(connection)
        plan = POSTGRES_SCHEMA_MIGRATIONS.plan(observed)
        if observed.kind is not ObservedSchemaKind.VERSIONED:
            connection.execute(_CREATE_LEDGER)
        actions = plan.actions
        if observed.kind is ObservedSchemaKind.EMPTY:
            if (
                not actions
                or actions[0].kind is not SchemaMigrationActionKind.APPLY
                or actions[0].migration.version != 1
            ):
                raise SchemaMigrationError("empty schema migration plan is not accepted")
            first_action = actions[0]
            active_migration_version = first_action.migration.version
            _apply_schema_migration(connection, first_action.migration)
            active_migration_version = None
            _acquire_schema_lock_plan(connection, PENDING_SCHEMA_LOCK_PLAN)
            _record_schema_migration(connection, first_action.migration)
            actions = actions[1:]
        else:
            _acquire_schema_lock_plan(
                connection,
                PENDING_SCHEMA_LOCK_PLAN if actions else CURRENT_SCHEMA_LOCK_PLAN,
            )
        for action in actions:
            if action.kind is SchemaMigrationActionKind.APPLY:
                active_migration_version = action.migration.version
                _apply_schema_migration(connection, action.migration)
                active_migration_version = None
            _record_schema_migration(connection, action.migration)
        _verify_postgres_schema_under_transaction(connection)
    except SchemaMigrationError:
        raise
    except Exception as error:
        categorical_failure = _CATEGORICAL_MIGRATION_FAILURES.get(
            active_migration_version
        )
        failure = (
            categorical_failure[0]
            if categorical_failure is not None
            and getattr(error, "sqlstate", None) in categorical_failure[1]
            else "schema migration application failed"
        )
    if failure is not None:
        if failure in tuple(
            message for message, _sqlstates in _CATEGORICAL_MIGRATION_FAILURES.values()
        ):
            raise SchemaMigrationError(failure)
        raise SchemaMigrationError("schema migration application failed")


def _record_schema_migration(
    connection: PostgresConnection,
    migration: SchemaMigration,
) -> None:
    connection.execute(
        """
        INSERT INTO cpk_schema_migrations
          (version, name, checksum_sha256)
        VALUES (%s, %s, %s)
        """,
        (
            migration.version,
            migration.name,
            migration.checksum_sha256,
        ),
    )


def _apply_schema_migration(
    connection: PostgresConnection,
    migration: SchemaMigration,
) -> None:
    if migration.steps is None:
        connection.execute(migration.sql)
        return
    for step in migration.steps:
        if type(step) is SqlMigrationStep:
            connection.execute(step.sql)
        elif type(step) is DeterministicBackfillStep:
            if (
                step.kind is SchemaBackfillKind.PRODUCT_DESCRIPTOR_CONTENT
                and step.algorithm_version == 1
            ):
                backfill_product_descriptor_content_v1(connection)
            elif (
                step.kind is SchemaBackfillKind.GRAPH_LINEAGE
                and step.algorithm_version == 1
            ):
                backfill_graph_lineage_v1(connection)
            else:
                raise SchemaMigrationError("schema migration backfill is not supported")
        else:
            raise SchemaMigrationError("schema migration step is not supported")


__all__ = [
    "MigrationPostgresConnection",
    "install_postgres_schema",
    "plan_postgres_schema_install",
]
