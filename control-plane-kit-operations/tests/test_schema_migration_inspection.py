from __future__ import annotations

import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres


class PostgresSchemaMigrationInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run the "
                "Docker-first operations test harness."
            )
        self.database_url = database_url
        self.schema = f"migration_inspection_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self._reset_schema()

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(
            f'DROP SCHEMA IF EXISTS "{self.schema}_backing" CASCADE'
        )
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_v1_manifest_is_immutable_ordered_and_matches_fresh_schema(self) -> None:
        manifest = self._required("POSTGRES_SCHEMA_V1_TABLE_COLUMNS")

        self.assertIsInstance(manifest, tuple)
        self.assertEqual(
            tuple(table for table, _columns in manifest),
            tuple(sorted(table for table, _columns in manifest)),
        )
        self.assertTrue(manifest)
        for table, columns in manifest:
            with self.subTest(table=table):
                self.assertIsInstance(table, str)
                self.assertIsInstance(columns, tuple)
                self.assertTrue(columns)

        self.connection.execute(postgres.POSTGRES_SCHEMA)

        self.assertEqual(manifest, self._table_column_manifest())
        with self.assertRaises(TypeError):
            manifest[0] = manifest[0]

    def test_empty_schema_observes_empty_without_mutation(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        observed_kind = self._required("ObservedSchemaKind")

        observed = inspect(self.connection)

        self.assertIs(observed.kind, observed_kind.EMPTY)
        self.assertEqual(observed.applied_migrations, ())
        self.assertEqual(self._table_names(), set())

    def test_exact_no_ledger_v1_observes_current_baseline(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        observed_kind = self._required("ObservedSchemaKind")
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        before = self._table_column_manifest()

        observed = inspect(self.connection)

        self.assertIs(observed.kind, observed_kind.CURRENT_BASELINE)
        self.assertEqual(observed.applied_migrations, ())
        self.assertEqual(self._table_column_manifest(), before)
        self.assertNotIn("cpk_schema_migrations", self._table_names())

    def test_exact_canonical_ledger_observes_versioned_public_history(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        observed_kind = self._required("ObservedSchemaKind")
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[0]
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self._create_ledger()
        self.connection.execute(
            """
            INSERT INTO cpk_schema_migrations
              (version, name, checksum_sha256)
            VALUES (%s, %s, %s)
            """,
            (migration.version, migration.name, migration.checksum_sha256),
        )

        observed = inspect(self.connection)

        self.assertIs(observed.kind, observed_kind.VERSIONED)
        self.assertEqual(len(observed.applied_migrations), 1)
        self.assertEqual(
            (
                observed.applied_migrations[0].version,
                observed.applied_migrations[0].name,
                observed.applied_migrations[0].checksum_sha256,
            ),
            (migration.version, migration.name, migration.checksum_sha256),
        )

    def test_unknown_no_ledger_manifests_fail_without_mutation(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        arrangements = (
            ("partial", "CREATE TABLE cpk_workspaces (workspace_id text)"),
            ("similarly-prefixed", "CREATE TABLE cpk_workspace_shadow (id text)"),
            ("unrelated", "CREATE TABLE client_application_data (id text)"),
            ("zero-column", "CREATE TABLE zero_column_table ()"),
        )
        for label, sql in arrangements:
            with self.subTest(label=label):
                self._reset_schema()
                self.connection.execute(sql)
                before = self._table_column_manifest()

                with self.assertRaises(error_type):
                    inspect(self.connection)

                self.assertEqual(self._table_column_manifest(), before)
                self.assertNotIn("cpk_schema_migrations", self._table_names())

        self._reset_schema()
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute("CREATE TABLE extra_table (id text)")
        before = self._table_column_manifest()
        with self.assertRaises(error_type):
            inspect(self.connection)
        self.assertEqual(self._table_column_manifest(), before)

    def test_lookalike_view_cannot_satisfy_the_v1_table_manifest(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        backing_schema = f"{self.schema}_backing"
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute(f'CREATE SCHEMA "{backing_schema}"')
        self.connection.execute(
            f'ALTER TABLE cpk_workspaces SET SCHEMA "{backing_schema}"'
        )
        self.connection.execute(
            f"""
            CREATE VIEW cpk_workspaces AS
            SELECT * FROM "{backing_schema}".cpk_workspaces
            """
        )

        with self.assertRaises(error_type):
            inspect(self.connection)

        self.assertNotIn("cpk_schema_migrations", self._table_names())
        self.connection.execute("DROP VIEW cpk_workspaces")
        self.connection.execute(f'DROP SCHEMA "{backing_schema}" CASCADE')

    def test_malformed_or_empty_ledger_contract_fails_closed(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        ledger_definitions = (
            """
            CREATE TABLE cpk_schema_migrations (
              version integer, name text, checksum_sha256 text
            )
            """,
            """
            CREATE TABLE cpk_schema_migrations (
              version integer, name text, checksum_sha256 text,
              applied_at timestamptz, unexpected text
            )
            """,
            """
            CREATE TABLE cpk_schema_migrations (
              version text, name text, checksum_sha256 text,
              applied_at timestamptz
            )
            """,
        )
        for definition in ledger_definitions:
            with self.subTest(definition=definition):
                self._reset_schema()
                self.connection.execute(postgres.POSTGRES_SCHEMA)
                self.connection.execute(definition)
                with self.assertRaises(error_type):
                    inspect(self.connection)

        self._reset_schema()
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self._create_ledger()
        with self.assertRaises(error_type):
            inspect(self.connection)

    def test_ledger_requires_exact_primary_key_and_applied_at_default(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[0]
        ledger_definitions = (
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
            """,
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL PRIMARY KEY,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL
            )
            """,
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL PRIMARY KEY,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT now()
            )
            """,
        )
        for definition in ledger_definitions:
            with self.subTest(definition=definition):
                self._reset_schema()
                self.connection.execute(postgres.POSTGRES_SCHEMA)
                self.connection.execute(definition)
                self.connection.execute(
                    """
                    INSERT INTO cpk_schema_migrations
                      (version, name, checksum_sha256, applied_at)
                    VALUES (%s, %s, %s, clock_timestamp())
                    """,
                    (migration.version, migration.name, migration.checksum_sha256),
                )

                with self.assertRaises(error_type):
                    inspect(self.connection)

    def test_drifted_gapped_and_newer_ledger_rows_fail_closed(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[0]
        rows = (
            (1, "renamed-baseline", migration.checksum_sha256),
            (1, migration.name, "f" * 64),
            (2, "newer-than-package", "e" * 64),
            (1, migration.name, "not-a-checksum"),
        )
        for row in rows:
            with self.subTest(row=row):
                self._reset_schema()
                self.connection.execute(postgres.POSTGRES_SCHEMA)
                self._create_ledger()
                self.connection.execute(
                    """
                    INSERT INTO cpk_schema_migrations
                      (version, name, checksum_sha256)
                    VALUES (%s, %s, %s)
                    """,
                    row,
                )
                with self.assertRaises(error_type):
                    inspect(self.connection)

        self._reset_schema()
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute(
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_schema_migrations
              (version, name, checksum_sha256)
            VALUES (1, %s, %s), (1, %s, %s)
            """,
            (
                migration.name,
                migration.checksum_sha256,
                migration.name,
                migration.checksum_sha256,
            ),
        )
        with self.assertRaises(error_type):
            inspect(self.connection)

    def test_final_verification_requires_current_history_and_exact_manifest(self) -> None:
        verify = self._required("verify_postgres_schema")
        inspect = self._required("inspect_postgres_schema")
        observed_kind = self._required("ObservedSchemaKind")
        error_type = self._required("SchemaMigrationError")
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[0]
        self.connection.execute(postgres.POSTGRES_SCHEMA)

        with self.assertRaises(error_type):
            verify(self.connection)

        self._create_ledger()
        self.connection.execute(
            """
            INSERT INTO cpk_schema_migrations
              (version, name, checksum_sha256)
            VALUES (%s, %s, %s)
            """,
            (migration.version, migration.name, migration.checksum_sha256),
        )
        verified = verify(self.connection)
        self.assertIs(verified.kind, observed_kind.VERSIONED)

        self.connection.execute(
            "ALTER TABLE cpk_workspaces DROP COLUMN metadata CASCADE"
        )
        observed = inspect(self.connection)
        self.assertIs(observed.kind, observed_kind.VERSIONED)
        with self.assertRaises(error_type):
            verify(self.connection)

    def test_errors_are_bounded_and_exclude_catalog_and_address_material(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        marker = "private_schema_material_that_must_not_be_echoed"
        self.connection.execute(f'CREATE TABLE "{marker}" (secret_value text)')

        with self.assertRaises(error_type) as raised:
            inspect(self.connection)

        message = str(raised.exception)
        self.assertLessEqual(len(message), 256)
        self.assertNotIn(marker, message)
        self.assertNotIn("secret_value", message)
        self.assertNotIn(self.database_url, message)

    def test_ledger_identity_projection_is_bounded_before_driver_fetch(self) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        marker = "private-ledger-material-" + ("x" * 4096)
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[0]
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self._create_ledger()
        self.connection.execute(
            """
            INSERT INTO cpk_schema_migrations
              (version, name, checksum_sha256)
            VALUES (%s, %s, %s)
            """,
            (migration.version, marker, marker),
        )

        class RecordingConnection:
            def __init__(self, connection):
                self.connection = connection
                self.ledger_query = ""

            def execute(self, query, params=None):
                if "FROM cpk_schema_migrations" in query:
                    self.ledger_query = query
                return self.connection.execute(query, params)

        recording = RecordingConnection(self.connection)
        with self.assertRaises(error_type) as raised:
            inspect(recording)

        normalized_query = " ".join(recording.ledger_query.lower().split())
        self.assertIn("octet_length(name)", normalized_query)
        self.assertIn("octet_length(checksum_sha256)", normalized_query)
        self.assertNotIn(marker, str(raised.exception))

    def test_driver_failures_are_categorical_bounded_and_context_suppressed(
        self,
    ) -> None:
        inspect = self._required("inspect_postgres_schema")
        error_type = self._required("SchemaMigrationError")
        marker = "private-driver-address-and-credential-material"

        class FailingConnection:
            def execute(self, _query, _params=None):
                raise RuntimeError(marker)

        with self.assertRaises(error_type) as raised:
            inspect(FailingConnection())

        message = str(raised.exception)
        self.assertLessEqual(len(message), 256)
        self.assertNotIn(marker, message)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_inspection_contract_is_exported_only_from_postgres_package(self) -> None:
        for name in (
            "POSTGRES_SCHEMA_V1_TABLE_COLUMNS",
            "POSTGRES_SCHEMA_MIGRATION_LEDGER_TABLE",
            "POSTGRES_SCHEMA_MIGRATION_LEDGER_COLUMNS",
            "inspect_postgres_schema",
            "verify_postgres_schema",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(self._required(name))

    def _create_ledger(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL PRIMARY KEY,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
            """
        )

    def _reset_schema(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def _table_names(self) -> set[str]:
        return {
            row[0]
            for row in self.connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_type = 'BASE TABLE'
                """
            ).fetchall()
        }

    def _table_column_manifest(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        rows = self.connection.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
            ORDER BY table_name, ordinal_position
            """
        ).fetchall()
        columns: dict[str, list[str]] = {}
        for table, column in rows:
            columns.setdefault(table, []).append(column)
        return tuple(
            (table, tuple(table_columns))
            for table, table_columns in columns.items()
        )

    def _required(self, name: str):
        value = getattr(postgres, name, None)
        if value is None:
            self.fail(f"{name} is not implemented")
        return value


if __name__ == "__main__":
    unittest.main()
