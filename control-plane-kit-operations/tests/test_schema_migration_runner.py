from __future__ import annotations

import inspect
import os
import threading
import time
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres


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
                (postgres.SchemaMigrationActionKind.APPLY,),
            )
            self.assertEqual(tuple(inspect.signature(preview).parameters), ("connection",))
            self.assertEqual(tuple(inspect.signature(install).parameters), ("connection",))
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
            self.assertEqual(self._ledger_rows(connection), [(1, "operations-baseline")])
        finally:
            connection.close()

    def test_exact_legacy_baseline_records_without_rewriting_constraints(self) -> None:
        install = self._required("install_postgres_schema")
        connection = self._connection()
        try:
            connection.execute(postgres.POSTGRES_SCHEMA)
            before = self._application_constraint_identities(connection)

            install(connection)

            self.assertEqual(self._application_constraint_identities(connection), before)
            self.assertEqual(self._ledger_rows(connection), [(1, "operations-baseline")])
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
            self.assertEqual(self._ledger_rows(second), [(1, "operations-baseline")])
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
            SELECT conname, oid
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
              AND conrelid <> 'cpk_schema_migrations'::regclass
            ORDER BY conname
            """
        ).fetchall()

    def _required(self, name: str):
        value = getattr(postgres, name, None)
        if value is None:
            self.fail(f"{name} is not implemented")
        return value


if __name__ == "__main__":
    unittest.main()
