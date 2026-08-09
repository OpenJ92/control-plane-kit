from __future__ import annotations

import inspect
import os
import threading
import time
import typing
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_runner as runner_module
from control_plane_kit_operations.postgres import schema as schema_module


_CURRENT_HISTORY = [
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
    (5, "delegation-signing-key-timestamps"),
    (6, "gateway-probe-timestamps"),
    (7, "gateway-key-rotation-timestamps"),
    (8, "ingress-evidence-timestamps"),
    (9, "secret-use-authorization-timestamps"),
    (10, "product-descriptor-content"),
    (11, "gateway-probe-access-path"),
    (12, "gateway-key-rotation-generation-evidence"),
    (13, "gateway-key-rotation-status-contracts"),
]


class PostgresSchemaMigrationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run the "
                "Docker-first operations test harness."
            )
        self.database_url = database_url
        self.schema = f"migration_runner_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_preview_is_canonical_read_only_and_exported(self) -> None:
        preview = self._required("plan_postgres_schema_install")
        install = self._required("install_postgres_schema")
        connection = self._connection()
        try:
            before = self._table_names(connection)
            plan = preview(connection)

            self.assertEqual(before, set())
            self.assertEqual(self._table_names(connection), set())
            self.assertIs(plan.observed.kind, postgres.ObservedSchemaKind.EMPTY)
            self.assertEqual(
                tuple(action.kind for action in plan.actions),
                (
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                    postgres.SchemaMigrationActionKind.APPLY,
                ),
            )
            self.assertEqual(tuple(inspect.signature(preview).parameters), ("connection",))
            self.assertEqual(tuple(inspect.signature(install).parameters), ("connection",))
            self.assertIs(
                typing.get_type_hints(postgres.install_schema)["connection"],
                postgres.MigrationPostgresConnection,
            )
            with self.assertRaises(TypeError):
                install(connection, plan)
        finally:
            connection.close()

    def test_fresh_autocommit_install_is_atomic_and_verified(self) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection()
        try:
            install(connection)

            observed = postgres.verify_postgres_schema(connection)
            self.assertIs(observed.kind, postgres.ObservedSchemaKind.VERSIONED)
            self.assertEqual(
                self._ledger_rows(connection),
                _CURRENT_HISTORY,
            )
        finally:
            connection.close()

    def test_exact_legacy_baseline_rewrites_only_existing_temporal_constraints(
        self,
    ) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection()
        try:
            connection.execute(postgres.POSTGRES_SCHEMA)
            before = {
                name: (identity, definition)
                for name, identity, definition in self._application_constraint_identities(
                    connection
                )
            }
            before_indexes = {
                name: (identity, definition)
                for name, identity, definition in self._application_index_identities(
                    connection
                )
            }

            install(connection)

            after = {
                name: (identity, definition)
                for name, identity, definition in self._application_constraint_identities(
                    connection
                )
            }
            after_indexes = {
                name: (identity, definition)
                for name, identity, definition in self._application_index_identities(
                    connection
                )
            }
            rebuilt = {
                "cpk_activity_runs_settlement_check",
                "cpk_activity_runs_started_check",
                "cpk_execution_requests_claim_check",
                "cpk_gateway_key_rotation_deployments_acceptance_check",
                "cpk_gateway_key_rotations_activation_check",
                "cpk_gateway_key_rotations_generation_digest_check",
                "cpk_gateway_key_rotations_retirement_check",
                "cpk_gateway_probe_completion_check",
                "cpk_cloudflare_ingress_resources_removed_evidence_check",
                "cpk_operation_sessions_closed_check",
                "cpk_delegation_signing_keys_activation_evidence_check",
                "cpk_delegation_signing_keys_retirement_evidence_check",
                "cpk_delegation_signing_keys_revocation_evidence_check",
                "cpk_secret_providers_revocation_evidence_check",
                "cpk_secret_references_revocation_evidence_check",
            }
            self.assertLessEqual(rebuilt, set(before))
            for constraint, (identity, definition) in before.items():
                with self.subTest(constraint=constraint):
                    after_identity, after_definition = after[constraint]
                    if constraint == (
                        "cpk_gateway_key_rotations_generation_digest_check"
                    ):
                        self.assertEqual(
                            after_definition,
                            "CHECK (((generation_action_digest IS NULL) OR "
                            '((generation_action_digest COLLATE "C") ~ '
                            "'^[0-9a-f]{64}$'::text)))",
                        )
                    else:
                        self.assertEqual(after_definition, definition)
                    if constraint in rebuilt:
                        self.assertNotEqual(after_identity, identity)
                    else:
                        self.assertEqual(after_identity, identity)
            rebuilt_indexes = {
                "cpk_cloudflare_ingress_resources_workspace",
                "cpk_observations_latest_subject",
                "cpk_secret_providers_history",
                "cpk_secret_references_history",
                "cpk_secret_use_authorizations_reference_history",
            }
            self.assertLessEqual(rebuilt_indexes, set(before_indexes))
            for index, (identity, definition) in before_indexes.items():
                with self.subTest(index=index):
                    after_identity, after_definition = after_indexes[index]
                    self.assertEqual(after_definition, definition)
                    if index in rebuilt_indexes:
                        self.assertNotEqual(after_identity, identity)
                    else:
                        self.assertEqual(after_identity, identity)
            self.assertEqual(
                self._ledger_rows(connection),
                _CURRENT_HISTORY,
            )
        finally:
            connection.close()

    def test_repeated_install_preserves_database_application_time(self) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection()
        try:
            install(connection)
            before = connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations"
            ).fetchall()

            install(connection)

            self.assertEqual(
                connection.execute(
                    "SELECT version, name, checksum_sha256, applied_at "
                    "FROM cpk_schema_migrations"
                ).fetchall(),
                before,
            )
        finally:
            connection.close()

    def test_unknown_schema_fails_before_any_mutation(self) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection()
        try:
            connection.execute("CREATE TABLE client_application_data (id text)")

            with self.assertRaises(postgres.SchemaMigrationError):
                install(connection)

            self.assertEqual(self._table_names(connection), {"client_application_data"})
        finally:
            connection.close()

    def test_unlisted_column_order_is_not_accepted_as_compatibility(self) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection()
        try:
            install(connection)
            connection.execute("ALTER TABLE cpk_workspaces DROP COLUMN name")
            connection.execute(
                "ALTER TABLE cpk_workspaces ADD COLUMN name text NOT NULL DEFAULT ''"
            )
            connection.execute(
                "ALTER TABLE cpk_workspaces ALTER COLUMN name DROP DEFAULT"
            )

            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.verify_postgres_schema(connection)
        finally:
            connection.close()

    def test_caller_transaction_retains_commit_and_rollback_authority(self) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection(autocommit=False)
        try:
            install(connection)
            observer = self._connection()
            try:
                self.assertEqual(self._table_names(observer), set())
            finally:
                observer.close()

            connection.rollback()
        finally:
            connection.close()

        observer = self._connection()
        try:
            self.assertEqual(self._table_names(observer), set())
        finally:
            observer.close()

    def test_unsupported_program_rolls_back_ordered_sql_before_ledger(self) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection()
        production_registry = schema_module.POSTGRES_SCHEMA_MIGRATIONS
        original_runner_registry = runner_module.POSTGRES_SCHEMA_MIGRATIONS
        first_sql = "CREATE TABLE cpk_test_program_order (position integer)"
        second_sql = "INSERT INTO cpk_test_program_order (position) VALUES (1)"
        executed: list[str] = []

        class RecordingConnection:
            @property
            def autocommit(self):
                return connection.autocommit

            def transaction(self):
                return connection.transaction()

            def execute(self, query, params=()):
                if query in {first_sql, second_sql}:
                    executed.append(query)
                return connection.execute(query, params)

        try:
            install(connection)
            for algorithm_version in (2,):
                with self.subTest(algorithm_version=algorithm_version):
                    executed.clear()
                    program_registry = self._program_registry(
                        first_sql,
                        second_sql,
                        algorithm_version=algorithm_version,
                    )
                    runner_module.POSTGRES_SCHEMA_MIGRATIONS = program_registry
                    try:
                        with self.assertRaises(
                            postgres.SchemaMigrationError
                        ) as raised:
                            install(RecordingConnection())
                    finally:
                        runner_module.POSTGRES_SCHEMA_MIGRATIONS = (
                            original_runner_registry
                        )

                    self.assertEqual(executed, [first_sql, second_sql])
                    self.assertEqual(
                        str(raised.exception),
                        "schema migration backfill is not supported",
                    )
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(
                        connection.execute(
                            "SELECT to_regclass('cpk_test_program_order')"
                        ).fetchone()[0]
                    )
                    self.assertEqual(
                        self._ledger_rows(connection),
                        _CURRENT_HISTORY,
                    )
            self.assertIs(
                schema_module.POSTGRES_SCHEMA_MIGRATIONS,
                production_registry,
            )
            self.assertEqual(production_registry.target_version, 13)
            self.assertIs(
                runner_module.POSTGRES_SCHEMA_MIGRATIONS,
                production_registry,
            )
        finally:
            runner_module.POSTGRES_SCHEMA_MIGRATIONS = original_runner_registry
            connection.close()

    def test_failed_program_savepoint_preserves_outer_work_and_releases_lock(self) -> None:
        install = self._required("install_postgres_schema")
        setup = self._connection()
        try:
            install(setup)
        finally:
            setup.close()

        caller = self._connection(autocommit=False)
        second = self._connection(application_name="cpk-program-lock-observer")
        production_registry = schema_module.POSTGRES_SCHEMA_MIGRATIONS
        original_runner_registry = runner_module.POSTGRES_SCHEMA_MIGRATIONS
        second_finished = threading.Event()
        second_failures: list[BaseException] = []
        thread = None
        try:
            second.execute("SET lock_timeout TO '5s'")
            second.execute("SET statement_timeout TO '10s'")
            caller.execute("CREATE TABLE cpk_caller_owned_after_failure (id integer)")
            runner_module.POSTGRES_SCHEMA_MIGRATIONS = self._program_registry(
                "CREATE TABLE cpk_test_program_savepoint (position integer)",
                "INSERT INTO cpk_test_program_savepoint (position) VALUES (1)",
                algorithm_version=2,
            )
            try:
                with self.assertRaises(postgres.SchemaMigrationError):
                    install(caller)
            finally:
                runner_module.POSTGRES_SCHEMA_MIGRATIONS = original_runner_registry

            self.assertIsNone(
                caller.execute(
                    "SELECT to_regclass('cpk_test_program_savepoint')"
                ).fetchone()[0]
            )
            self.assertEqual(self._ledger_rows(caller), _CURRENT_HISTORY)

            def run_second() -> None:
                try:
                    install(second)
                except BaseException as error:
                    second_failures.append(error)
                finally:
                    second_finished.set()

            thread = threading.Thread(target=run_second)
            thread.start()
            self.assertTrue(
                second_finished.wait(timeout=10),
                "failed migration savepoint retained the advisory lock",
            )
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(second_failures, [])

            caller.commit()
        finally:
            runner_module.POSTGRES_SCHEMA_MIGRATIONS = original_runner_registry
            if thread is not None and thread.is_alive():
                caller.rollback()
                second.cancel()
                thread.join(timeout=15)
            if not caller.closed:
                caller.rollback()
                caller.close()
            thread_is_alive = thread is not None and thread.is_alive()
            if not thread_is_alive:
                second.close()
            self.assertFalse(
                thread_is_alive,
                "lock observer remained alive after database timeout and cancellation",
            )

        observer = self._connection()
        try:
            self.assertIsNotNone(
                observer.execute(
                    "SELECT to_regclass('cpk_caller_owned_after_failure')"
                ).fetchone()[0]
            )
            self.assertIsNone(
                observer.execute(
                    "SELECT to_regclass('cpk_test_program_savepoint')"
                ).fetchone()[0]
            )
            self.assertEqual(self._ledger_rows(observer), _CURRENT_HISTORY)
            self.assertIs(
                schema_module.POSTGRES_SCHEMA_MIGRATIONS,
                production_registry,
            )
            self.assertIs(
                runner_module.POSTGRES_SCHEMA_MIGRATIONS,
                production_registry,
            )
        finally:
            observer.close()

    def test_injected_ledger_failure_rolls_back_and_discards_provider_error(self) -> None:
        install = self._required("install_postgres_schema")
        marker = "private-database-address-and-credential-material"
        delegate = self._connection()

        class FailingConnection:
            @property
            def autocommit(self):
                return delegate.autocommit

            def transaction(self):
                return delegate.transaction()

            def execute(self, query, params=()):
                if "INSERT INTO cpk_schema_migrations" in query:
                    raise RuntimeError(marker)
                return delegate.execute(query, params)

        try:
            with self.assertRaises(postgres.SchemaMigrationError) as raised:
                install(FailingConnection())
        finally:
            delegate.close()

        self.assertNotIn(marker, str(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        observer = self._connection()
        try:
            self.assertEqual(self._table_names(observer), set())
        finally:
            observer.close()

    def test_final_verification_failure_rolls_back_every_effect(self) -> None:
        install = self._required("install_postgres_schema")
        delegate = self._connection()

        class DriftBeforeVerificationConnection:
            ledger_recorded = False
            drifted = False

            @property
            def autocommit(self):
                return delegate.autocommit

            def transaction(self):
                return delegate.transaction()

            def execute(self, query, params=()):
                if "INSERT INTO cpk_schema_migrations" in query:
                    self.ledger_recorded = True
                elif (
                    self.ledger_recorded
                    and not self.drifted
                    and "FROM information_schema.tables" in query
                ):
                    delegate.execute(
                        "ALTER TABLE cpk_workspaces DROP COLUMN metadata CASCADE"
                    )
                    self.drifted = True
                return delegate.execute(query, params)

        try:
            with self.assertRaises(postgres.SchemaMigrationError):
                install(DriftBeforeVerificationConnection())
        finally:
            delegate.close()

        observer = self._connection()
        try:
            self.assertEqual(self._table_names(observer), set())
        finally:
            observer.close()

    def test_two_installers_serialize_and_converge_on_one_identity(self) -> None:
        install = self._required("install_postgres_schema")
        first = self._connection(autocommit=False, application_name="cpk-runner-first")
        second = self._connection(application_name="cpk-runner-second")
        finished = threading.Event()
        failures: list[BaseException] = []

        try:
            install(first)

            def run_second() -> None:
                try:
                    install(second)
                except BaseException as error:
                    failures.append(error)
                finally:
                    finished.set()

            thread = threading.Thread(target=run_second)
            thread.start()
            self._wait_for_advisory_lock("cpk-runner-second")
            self.assertFalse(finished.is_set())

            first.commit()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(
                self._ledger_rows(second),
                _CURRENT_HISTORY,
            )
        finally:
            if not first.closed:
                first.rollback()
                first.close()
            second.close()

    def _connection(
        self,
        *,
        autocommit: bool = True,
        application_name: str | None = None,
    ):
        kwargs = {"autocommit": autocommit}
        if application_name is not None:
            kwargs["application_name"] = application_name
        connection = psycopg.connect(self.database_url, **kwargs)
        connection.execute(f'SET search_path TO "{self.schema}"')
        if not autocommit:
            connection.commit()
        return connection

    def _wait_for_advisory_lock(self, application_name: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = self.admin.execute(
                """
                SELECT wait_event
                FROM pg_stat_activity
                WHERE application_name = %s
                  AND wait_event_type = 'Lock'
                """,
                (application_name,),
            ).fetchone()
            if row is not None and row[0] == "advisory":
                return
            time.sleep(0.01)
        self.fail("second installer did not wait on the advisory lock")

    def _program_registry(
        self,
        first_sql: str,
        second_sql: str,
        *,
        algorithm_version: int = 1,
    ):
        production = schema_module.POSTGRES_SCHEMA_MIGRATIONS
        program = postgres.SchemaMigration(
            version=production.target_version + 1,
            name="test-program",
            steps=(
                postgres.SqlMigrationStep(first_sql),
                postgres.SqlMigrationStep(second_sql),
                postgres.DeterministicBackfillStep(
                    postgres.SchemaBackfillKind.PRODUCT_DESCRIPTOR_CONTENT,
                    algorithm_version,
                ),
            ),
        )
        return postgres.SchemaMigrationRegistry((*production.migrations, program))

    def _table_names(self, connection) -> set[str]:
        return {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_type = 'BASE TABLE'
                """
            ).fetchall()
        }

    def _ledger_rows(self, connection) -> list[tuple[int, str]]:
        return connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _application_constraint_identities(self, connection):
        return connection.execute(
            """
            SELECT conname, oid, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
              AND conname <> 'cpk_schema_migrations_pkey'
            ORDER BY conname
            """
        ).fetchall()

    def _application_index_identities(self, connection):
        return connection.execute(
            """
            SELECT index_relation.relname, index_relation.oid,
                   pg_get_indexdef(index_relation.oid)
            FROM pg_index
            JOIN pg_class AS table_relation
              ON table_relation.oid = pg_index.indrelid
            JOIN pg_namespace
              ON pg_namespace.oid = table_relation.relnamespace
            JOIN pg_class AS index_relation
              ON index_relation.oid = pg_index.indexrelid
            WHERE pg_namespace.nspname = current_schema()
              AND index_relation.relname <> 'cpk_schema_migrations_pkey'
            ORDER BY index_relation.relname
            """
        ).fetchall()

    def _required(self, name: str):
        value = getattr(postgres, name, None)
        if value is None:
            self.fail(f"{name} is not implemented")
        return value


if __name__ == "__main__":
    unittest.main()
