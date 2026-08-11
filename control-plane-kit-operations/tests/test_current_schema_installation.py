from __future__ import annotations

import ast
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres


_EXPECTED_RELATIONS = (
    "cpk_activity_events",
    "cpk_activity_plans",
    "cpk_activity_runs",
    "cpk_approval_decisions",
    "cpk_approval_requests",
    "cpk_cloudflare_ingress_resources",
    "cpk_delegation_signing_keys",
    "cpk_execution_requests",
    "cpk_gateway_key_rotation_deployments",
    "cpk_gateway_key_rotation_revocations",
    "cpk_gateway_key_rotation_transitions",
    "cpk_gateway_key_rotations",
    "cpk_gateway_probe_attempts",
    "cpk_generated_ingress_secret_references",
    "cpk_graph_versions",
    "cpk_image_pull_authorities",
    "cpk_ingress_authorities",
    "cpk_observations",
    "cpk_operation_actions",
    "cpk_operation_sessions",
    "cpk_realized_graph_projections",
    "cpk_registered_products",
    "cpk_runtime_authorities",
    "cpk_runtime_authority_deliveries",
    "cpk_secret_providers",
    "cpk_secret_references",
    "cpk_secret_use_authorizations",
    "cpk_workspaces",
)

_FORBIDDEN_MODULES = (
    "graph_lineage_backfill.py",
    "migration_inspection.py",
    "migration_runner.py",
    "migrations.py",
    "product_descriptor_backfill.py",
)

_FORBIDDEN_EXPORTS = (
    "POSTGRES_SCHEMA",
    "POSTGRES_SCHEMA_MIGRATIONS",
    "POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS",
    "POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE",
    "POSTGRES_SCHEMA_V1_SHA256",
    "POSTGRES_SCHEMA_V1_TABLE_COLUMNS",
    "AppliedSchemaMigration",
    "DeterministicBackfillStep",
    "MigrationPostgresConnection",
    "ObservedSchemaKind",
    "ObservedSchemaState",
    "SchemaBackfillKind",
    "SchemaMigration",
    "SchemaMigrationAction",
    "SchemaMigrationActionKind",
    "SchemaMigrationError",
    "SchemaMigrationPlan",
    "SchemaMigrationRegistry",
    "SqlMigrationStep",
    "inspect_postgres_schema",
    "install_postgres_schema",
    "plan_postgres_schema_install",
    "verify_postgres_schema",
)

_FORBIDDEN_SCHEMA_NAMES = frozenset(
    {
        "AppliedSchemaMigration",
        "DeterministicBackfillStep",
        "MigrationIdentity",
        "ObservedSchemaKind",
        "ObservedSchemaState",
        "SchemaBackfillKind",
        "SchemaLockPlan",
        "SchemaMigration",
        "SchemaMigrationAction",
        "SchemaMigrationActionKind",
        "SchemaMigrationPlan",
        "SchemaMigrationRegistry",
        "SqlMigrationStep",
        "backfill_graph_lineage_v1",
        "backfill_product_descriptor_content_v1",
        "inspect_postgres_schema",
        "install_postgres_schema",
        "plan_postgres_schema_install",
        "verify_graph_lineage_v1",
        "verify_postgres_schema",
    }
)


class _RecordingConnection:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls: list[str] = []

    @property
    def autocommit(self):
        return self.delegate.autocommit

    def transaction(self):
        return self.delegate.transaction()

    def execute(self, query, params=()):
        text = query if isinstance(query, str) else str(query)
        self.calls.append(text)
        if params == ():
            return self.delegate.execute(query)
        return self.delegate.execute(query, params)


class _FailingConnection(_RecordingConnection):
    def __init__(self, delegate, *, fail_on: int) -> None:
        super().__init__(delegate)
        self.fail_on = fail_on

    def execute(self, query, params=()):
        if len(self.calls) + 1 == self.fail_on:
            self.calls.append(str(query))
            raise RuntimeError(
                "postgresql://operator:secret@internal.example:5432/private"
            )
        return super().execute(query, params)


class _AlwaysFailingConnection:
    autocommit = True

    @contextmanager
    def transaction(self):
        yield

    def execute(self, query, params=()):
        raise RuntimeError(
            "postgresql://operator:secret@internal.example:5432/private"
        )


def _captured_install_error(connection) -> BaseException:
    try:
        postgres.install_schema(connection)
    except BaseException as error:
        return error
    raise AssertionError("schema installation unexpectedly succeeded")


class CurrentSchemaStaticLawTests(unittest.TestCase):
    def test_public_and_source_residue_is_exactly_removed(self) -> None:
        self.assertTrue(hasattr(postgres, "SchemaInstallationError"))
        for name in _FORBIDDEN_EXPORTS:
            with self.subTest(export=name):
                self.assertFalse(hasattr(postgres, name))

        package = Path(postgres.__file__).resolve().parent
        for name in _FORBIDDEN_MODULES:
            with self.subTest(module=name):
                self.assertFalse((package / name).exists())

        observed_names: set[str] = set()
        for source in package.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    observed_names.add(node.name)
        self.assertEqual(observed_names & _FORBIDDEN_SCHEMA_NAMES, set())

        scoped_text = "\n".join(
            source.read_text(encoding="utf-8") for source in package.glob("*.py")
        )
        for value in (
            "cpk_schema_migrations",
            "operations-baseline",
            "schema-migration-program",
        ):
            with self.subTest(value=value):
                self.assertNotIn(value, scoped_text)

    def test_current_contract_has_only_exact_functional_truth(self) -> None:
        from control_plane_kit_operations.postgres import current_schema_contract

        contract = current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT
        self.assertEqual(len(contract.relations), 28)
        self.assertEqual(len(contract.columns), 353)
        self.assertEqual(len(contract.constraints), 231)
        self.assertEqual(len(contract.indexes), 77)
        self.assertFalse(hasattr(contract, "history"))
        self.assertEqual(
            tuple(relation.name for relation in contract.relations),
            _EXPECTED_RELATIONS,
        )
        self.assertNotIn("cpk_schema_migrations", repr(contract))
        self.assertFalse(hasattr(current_schema_contract, "SchemaLockPlan"))
        self.assertFalse(hasattr(current_schema_contract, "PENDING_SCHEMA_LOCK_PLAN"))

        source = Path(current_schema_contract.__file__).read_text(encoding="utf-8")
        imports = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        }
        self.assertLessEqual(imports, {"__future__", "dataclasses"})

    def test_direct_schema_program_is_unconditional_and_nonhistorical(self) -> None:
        from control_plane_kit_operations.postgres import schema

        self.assertTrue(hasattr(schema, "_CURRENT_SCHEMA_SQL"))
        sql = getattr(schema, "_CURRENT_SCHEMA_SQL", "")
        self.assertIsInstance(sql, str)
        normalized = " ".join(sql.lower().split())
        self.assertNotIn("if exists", normalized)
        self.assertNotIn("if not exists", normalized)
        self.assertNotIn("cpk_schema_migrations", normalized)
        for forbidden in (" drop ", " truncate ", " insert ", " update ", " delete "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, f" {normalized} ")
        statements = tuple(
            statement.strip()
            for statement in sql.split(";")
            if statement.strip()
        )
        self.assertTrue(statements)
        for statement in statements:
            with self.subTest(statement=statement[:80]):
                self.assertRegex(
                    " ".join(statement.lower().split()),
                    r"^(create table|create index|create unique index|alter table) ",
                )
        self.assertEqual(
            sum(statement.lower().startswith("create table ") for statement in statements),
            28,
        )


class CurrentSchemaInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required through the "
                "disposable operations Docker test environment"
            )
        self.database_url = database_url
        self.schema = f"schema_cutover_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.close()

    def test_object_free_install_creates_only_exact_current_truth(self) -> None:
        postgres.install_schema(self.connection)

        self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
        self.assertEqual(self._catalog_counts(), (28, 353, 231, 77))
        self.assertEqual(
            self.connection.execute(
                "SELECT to_regclass('cpk_schema_migrations') IS NULL"
            ).fetchone(),
            (True,),
        )

    def test_current_reinstall_is_query_only_and_identity_stable(self) -> None:
        postgres.install_schema(self.connection)
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        before = self._object_identities()
        recorder = _RecordingConnection(self.connection)

        postgres.install_schema(recorder)

        self.assertEqual(self._object_identities(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT workspace_id, name, lifecycle FROM cpk_workspaces"
            ).fetchall(),
            [("workspace-a", "Workspace A", "created")],
        )
        self.assertNotIn("cpk_schema_migrations", "\n".join(recorder.calls))
        for call in recorder.calls:
            normalized = " ".join(call.lower().split())
            self.assertNotRegex(
                normalized,
                r"\b(create|alter|drop|truncate|insert|update|delete)\b",
            )

    def test_nonempty_owned_object_families_require_reset_before_effect(self) -> None:
        fixtures = (
            "CREATE TABLE stray_table (value integer)",
            "CREATE VIEW stray_view AS SELECT 1 AS value",
            "CREATE MATERIALIZED VIEW stray_materialized AS SELECT 1 AS value",
            "CREATE SEQUENCE stray_sequence",
            "CREATE TYPE stray_enum AS ENUM ('value')",
            "CREATE DOMAIN stray_domain AS text",
            "CREATE FUNCTION stray_function() RETURNS integer LANGUAGE SQL "
            "IMMUTABLE AS 'SELECT 1'",
        )
        for ddl in fixtures:
            with self.subTest(ddl=ddl):
                self._reset_owned_schema()
                self.connection.execute(ddl)
                before = self._namespace_objects()

                error = _captured_install_error(self.connection)

                self._assert_install_error(error, "operations schema reset is required")
                self.assertEqual(self._namespace_objects(), before)
                self.assertEqual(set(self._relations()) & set(_EXPECTED_RELATIONS), set())

    def test_cross_schema_objects_are_preserved_and_ignored(self) -> None:
        other = f"other_{uuid.uuid4().hex}"
        self.connection.execute(f'CREATE SCHEMA "{other}"')
        try:
            self.connection.execute(f'CREATE TABLE "{other}".cpk_workspaces (x int)')
            self.connection.execute(f'CREATE TYPE "{other}".stray_enum AS ENUM (\'x\')')

            postgres.install_schema(self.connection)

            self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
            self.assertEqual(
                self.connection.execute(
                    "SELECT to_regclass(%s) IS NOT NULL",
                    (f'"{other}".cpk_workspaces',),
                ).fetchone(),
                (True,),
            )
        finally:
            self.connection.execute(f'DROP SCHEMA "{other}" CASCADE')

    def test_current_drift_rejects_without_repair_or_row_loss(self) -> None:
        postgres.install_schema(self.connection)
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        self.connection.execute("ALTER TABLE cpk_workspaces ADD COLUMN stray text")
        before = self._object_identities()

        error = _captured_install_error(self.connection)

        self._assert_install_error(error, "operations schema reset is required")
        self.assertEqual(self._object_identities(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT workspace_id, name, lifecycle, stray FROM cpk_workspaces"
            ).fetchall(),
            [("workspace-a", "Workspace A", "created", None)],
        )

    def test_empty_install_failure_rolls_back_every_schema_effect(self) -> None:
        failing = _FailingConnection(self.connection, fail_on=5)

        error = _captured_install_error(failing)

        self._assert_install_error(error, "operations schema installation failed")
        self.assertEqual(self._namespace_objects(), ())

    def test_caller_owned_outer_transaction_retains_authority(self) -> None:
        marker = f"cpk_cutover_marker_{uuid.uuid4().hex}"
        self.connection.execute(
            f'CREATE TABLE public."{marker}" (value text)'
        )
        self.connection.autocommit = False
        try:
            self.connection.execute(
                f'INSERT INTO public."{marker}" VALUES (\'outer\')'
            )
            postgres.install_schema(self.connection)
            self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
            self.connection.rollback()
        finally:
            if self.connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                self.connection.rollback()
            self.connection.autocommit = True
            self.connection.execute(f'DROP TABLE public."{marker}"')

        self.assertEqual(self._namespace_objects(), ())

    def test_concurrent_empty_installers_serialize_to_one_current_schema(self) -> None:
        barrier = threading.Barrier(2)
        failures: list[BaseException] = []

        def install() -> None:
            connection = psycopg.connect(self.database_url, autocommit=True)
            try:
                connection.execute(f'SET search_path TO "{self.schema}"')
                barrier.wait(timeout=5)
                postgres.install_schema(connection)
            except BaseException as error:
                failures.append(error)
            finally:
                connection.close()

        threads = (threading.Thread(target=install), threading.Thread(target=install))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(self._relations(), _EXPECTED_RELATIONS)
        self.assertEqual(self._catalog_counts(), (28, 353, 231, 77))

    def test_driver_failure_is_not_reset_advice_and_is_redacted(self) -> None:
        error = _captured_install_error(_AlwaysFailingConnection())

        self._assert_install_error(error, "operations schema installation failed")
        rendered = repr(error)
        for forbidden in ("operator", "secret", "internal.example", "5432", "private"):
            self.assertNotIn(forbidden, rendered)

    def _assert_install_error(self, error: BaseException, message: str) -> None:
        error_type = getattr(postgres, "SchemaInstallationError", None)
        self.assertIsNotNone(error_type)
        self.assertIs(type(error), error_type)
        self.assertEqual(str(error), message)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def _reset_owned_schema(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def _relations(self) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self.connection.execute(
                "SELECT relation.relname FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() AND relation.relkind='r' "
                "ORDER BY relation.relname"
            ).fetchall()
        )

    def _catalog_counts(self) -> tuple[int, int, int, int]:
        relation_count = self.connection.execute(
            "SELECT count(*) FROM pg_class AS relation "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema() AND relation.relkind='r'"
        ).fetchone()[0]
        column_count = self.connection.execute(
            "SELECT count(*) FROM pg_attribute AS attribute "
            "JOIN pg_class AS relation ON relation.oid=attribute.attrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema() AND relation.relkind='r' "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped"
        ).fetchone()[0]
        constraint_count = self.connection.execute(
            "SELECT count(*) FROM pg_constraint AS owned "
            "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=current_schema()"
        ).fetchone()[0]
        index_count = self.connection.execute(
            "SELECT count(*) FROM pg_class AS indexed "
            "JOIN pg_namespace AS namespace ON namespace.oid=indexed.relnamespace "
            "WHERE namespace.nspname=current_schema() AND indexed.relkind='i'"
        ).fetchone()[0]
        return relation_count, column_count, constraint_count, index_count

    def _namespace_objects(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            self.connection.execute(
                "SELECT 'relation', relation.relname FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "UNION ALL "
                "SELECT 'routine', routine.proname FROM pg_proc AS routine "
                "JOIN pg_namespace AS namespace ON namespace.oid=routine.pronamespace "
                "WHERE namespace.nspname=current_schema() "
                "UNION ALL "
                "SELECT 'type', owned_type.typname FROM pg_type AS owned_type "
                "JOIN pg_namespace AS namespace ON namespace.oid=owned_type.typnamespace "
                "WHERE namespace.nspname=current_schema() "
                "ORDER BY 1, 2"
            ).fetchall()
        )

    def _object_identities(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                "SELECT 'relation', relation.relname, relation.oid::bigint "
                "FROM pg_class AS relation "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "UNION ALL "
                "SELECT 'constraint', owned.conname, owned.oid::bigint "
                "FROM pg_constraint AS owned "
                "JOIN pg_class AS relation ON relation.oid=owned.conrelid "
                "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
                "WHERE namespace.nspname=current_schema() "
                "ORDER BY 1, 2, 3"
            ).fetchall()
        )


if __name__ == "__main__":
    unittest.main()
