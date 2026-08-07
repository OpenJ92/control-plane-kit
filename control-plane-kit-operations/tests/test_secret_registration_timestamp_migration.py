from __future__ import annotations

from datetime import datetime, timezone
import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
from control_plane_kit_core.secrets import SecretProviderId, SecretReference


_TEMPORAL_COLUMNS = (
    ("cpk_secret_providers", "admitted_at", "NO", 6),
    ("cpk_secret_providers", "revoked_at", "YES", 6),
    ("cpk_secret_references", "admitted_at", "NO", 6),
    ("cpk_secret_references", "revoked_at", "YES", 6),
)
_TEMPORAL_IDENTITIES = tuple(
    (table, column) for table, column, _, _ in _TEMPORAL_COLUMNS
)
_V3_HISTORY = [
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
]
_V4_HISTORY = [*_V3_HISTORY, (4, "secret-registration-timestamps")]
_CANONICAL = "2026-08-07T06:00:00.000001Z"
_NONCANONICAL_OFFSET = "2026-08-07T02:00:00-04:00"
_EXPECTED_REBUILT_OBJECTS = {
    ("constraint", "cpk_secret_providers_revocation_evidence_check"),
    ("constraint", "cpk_secret_references_revocation_evidence_check"),
    ("index", "cpk_secret_providers_history"),
    ("index", "cpk_secret_references_history"),
}


class SecretRegistrationTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"secret_registration_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_appends_exact_secret_registration_v4(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 4)
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations],
            _V4_HISTORY,
        )
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations[:3]],
            _V3_HISTORY,
        )

    def test_fresh_install_has_exact_v4_temporal_contract(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._ledger(), _V4_HISTORY)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_retained_values_and_null_revocations_migrate_without_loss(self) -> None:
        self._install_v3_baseline()
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        self._seed_retained_rows((_CANONICAL,) * 4, revoked=True)

        postgres.install_postgres_schema(self.connection)

        expected = datetime(2026, 8, 7, 6, 0, 0, 1, tzinfo=timezone.utc)
        self.assertEqual(self._retained_values(), (expected,) * 4)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)

        null_schema = f"{self.schema}_null"
        self.connection.execute(f'CREATE SCHEMA "{null_schema}"')
        self.connection.execute(f'SET search_path TO "{null_schema}"')
        try:
            self._install_v3_baseline()
            self._seed_retained_rows((_CANONICAL, None, _CANONICAL, None))

            postgres.install_postgres_schema(self.connection)

            self.assertEqual(
                self._retained_values(),
                (expected, None, expected, None),
            )
            self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)
        finally:
            self.connection.execute(f'SET search_path TO "{self.schema}"')
            self.connection.execute(f'DROP SCHEMA "{null_schema}" CASCADE')

    def test_each_retained_column_has_independent_atomic_preflight(self) -> None:
        for index, identity in enumerate(_TEMPORAL_IDENTITIES):
            with self.subTest(identity=identity):
                case_schema = f"{self.schema}_{index}"
                self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                self.connection.execute(f'SET search_path TO "{case_schema}"')
                try:
                    self._install_v3_baseline()
                    timestamps = [_CANONICAL] * 4
                    timestamps[index] = _NONCANONICAL_OFFSET
                    self._seed_retained_rows(tuple(timestamps), revoked=True)
                    before_objects = self._application_objects()

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(self.connection)

                    self.assertEqual(
                        str(raised.exception),
                        "secret registration timestamps are not canonical UTC",
                    )
                    self.assertLessEqual(len(str(raised.exception)), 256)
                    self.assertNotIn(_NONCANONICAL_OFFSET, str(raised.exception))
                    self.assertIsNone(raised.exception.__context__)
                    self.assertEqual(self._ledger(), _V3_HISTORY)
                    self.assertEqual(self._retained_values(), tuple(timestamps))
                    self.assertEqual(self._application_objects(), before_objects)
                    for table, column in _TEMPORAL_IDENTITIES:
                        self.assertEqual(
                            self._column_contract(table, column),
                            ("text", None, "NO" if column == "admitted_at" else "YES", True),
                        )
                finally:
                    self.connection.execute(f'SET search_path TO "{self.schema}"')
                    self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_calendar_invalid_value_has_bounded_v4_category(self) -> None:
        self._install_v3_baseline()
        invalid = "2026-02-30T06:00:00Z"
        retained = (invalid, None, _CANONICAL, None)
        self._seed_retained_rows(retained)
        before_objects = self._application_objects()

        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            str(raised.exception),
            "secret registration timestamps are not canonical UTC",
        )
        self.assertLessEqual(len(str(raised.exception)), 256)
        self.assertNotIn(invalid, str(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(self._ledger(), _V3_HISTORY)
        self.assertEqual(self._retained_values(), retained)
        self.assertEqual(self._application_objects(), before_objects)
        for table, column in _TEMPORAL_IDENTITIES:
            self.assertEqual(
                self._column_contract(table, column),
                ("text", None, "NO" if column == "admitted_at" else "YES", True),
            )

    def test_success_rebuilds_only_exact_dependent_objects(self) -> None:
        self._install_v3_baseline()
        self._seed_retained_rows((_CANONICAL,) * 4, revoked=True)
        before = self._application_objects()

        postgres.install_postgres_schema(self.connection)

        after = self._application_objects()
        self.assertEqual(set(after), set(before))
        changed = set()
        for identity, (before_oid, before_definition) in before.items():
            after_oid, after_definition = after[identity]
            with self.subTest(identity=identity):
                self.assertEqual(after_definition, before_definition)
                if after_oid != before_oid:
                    changed.add(identity)
        self.assertEqual(changed, _EXPECTED_REBUILT_OBJECTS)

    def test_current_verifier_rejects_every_owned_temporal_fact_drift(self) -> None:
        for identity_index, (table, column) in enumerate(_TEMPORAL_IDENTITIES):
            required = column == "admitted_at"
            mutations = (
                ("type", f"TYPE text USING {column}::text"),
                (
                    "precision",
                    f"TYPE timestamptz(5) USING {column}::timestamptz(5)",
                ),
                ("nullability", "DROP NOT NULL" if required else "SET NOT NULL"),
                ("default", "SET DEFAULT clock_timestamp()"),
            )
            for fact_index, (fact, mutation) in enumerate(mutations):
                with self.subTest(identity=(table, column), fact=fact):
                    case_schema = f"{self.schema}_{identity_index}_{fact_index}"
                    self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                    self.connection.execute(f'SET search_path TO "{case_schema}"')
                    try:
                        postgres.install_postgres_schema(self.connection)
                        self.connection.execute(
                            f"ALTER TABLE {table} ALTER COLUMN {column} {mutation}"
                        )

                        with self.assertRaises(
                            postgres.SchemaMigrationError
                        ) as raised:
                            postgres.verify_postgres_schema(self.connection)

                        self.assertEqual(
                            str(raised.exception),
                            "secret registration temporal schema is not current",
                        )
                        self.assertLessEqual(len(str(raised.exception)), 256)
                        self.assertIsNone(raised.exception.__context__)
                    finally:
                        self.connection.execute(
                            f'SET search_path TO "{self.schema}"'
                        )
                        self.connection.execute(
                            f'DROP SCHEMA "{case_schema}" CASCADE'
                        )

    def test_native_history_order_and_provider_revocation_replay(self) -> None:
        self._install_v3_baseline()
        self._seed_history_rows()

        postgres.install_postgres_schema(self.connection)
        self.connection.execute("SET TIME ZONE 'Asia/Tokyo'")
        providers = postgres.SecretProviderStore(self.connection)
        references = postgres.SecretReferenceStore(self.connection)

        provider_history = providers.list_history(
            "workspace-a",
            SecretProviderId("workspace-secrets"),
        )
        reference_history = references.list_history(
            "workspace-a",
            SecretReference("secret://workspace-secrets/workspace-a/database"),
        )
        self.assertEqual(
            [(item.registration_id, item.admitted_at) for item in provider_history],
            [
                ("provider-second", "2026-08-07T06:00:00Z"),
                ("provider-micro-a", "2026-08-07T06:00:00.000001Z"),
                ("provider-micro-z", "2026-08-07T06:00:00.000001Z"),
            ],
        )
        self.assertEqual(
            [(item.registration_id, item.admitted_at) for item in reference_history],
            [
                ("reference-second", "2026-08-07T06:00:00Z"),
                ("reference-micro-a", "2026-08-07T06:00:00.000001Z"),
                ("reference-micro-z", "2026-08-07T06:00:00.000001Z"),
            ],
        )
        replay = providers.revoke_active(
            "workspace-a",
            SecretProviderId("workspace-secrets"),
            revoked_by="operator-b",
            revoked_at="2026-08-07T06:02:00Z",
        )
        self.assertEqual(replay.registration_id, "provider-micro-z")
        self.assertEqual(replay.revoked_at, "2026-08-07T06:01:00.000001Z")

    def test_reinstall_preserves_v4_ledger_and_objects(self) -> None:
        postgres.install_postgres_schema(self.connection)
        before_ledger = self.connection.execute(
            "SELECT version, name, checksum_sha256, applied_at "
            "FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()
        before_objects = self._application_objects()

        postgres.install_postgres_schema(self.connection)

        self.assertEqual(
            self.connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall(),
            before_ledger,
        )
        self.assertEqual(self._application_objects(), before_objects)

    def _install_v3_baseline(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        self.connection.execute(registry.migrations[1].sql)
        self.connection.execute(registry.migrations[2].sql)
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
        for migration in registry.migrations[:3]:
            self.connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_retained_rows(
        self,
        timestamps: tuple[str | None, ...],
        *,
        revoked: bool = False,
    ) -> None:
        provider_admitted, provider_revoked, reference_admitted, reference_revoked = (
            timestamps
        )
        status = "revoked" if revoked else "active"
        revoked_by = "operator-a" if revoked else None
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_secret_providers (
              registration_id, workspace_id, provider_id, provider_kind,
              display_name, endpoint_reference, credential_reference,
              allowed_reference_prefixes, allowed_intents, admitted_by,
              admitted_at, status, revoked_by, revoked_at
            )
            VALUES (
              'provider-a', 'workspace-a', 'workspace-secrets',
              'control-plane-kit-secrets', 'Workspace secrets',
              'workspace-secrets', 'secret://bootstrap/provider-token',
              '["secret://workspace-secrets/workspace-a"]'::jsonb,
              '["postgres.password"]'::jsonb, 'operator-a', %s, %s, %s, %s
            )
            """,
            (provider_admitted, status, revoked_by, provider_revoked),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_secret_references (
              registration_id, workspace_id, secret_reference,
              provider_registration_id, allowed_intents, admitted_by,
              admitted_at, status, revoked_by, revoked_at
            )
            VALUES (
              'reference-a', 'workspace-a',
              'secret://workspace-secrets/workspace-a/database', 'provider-a',
              '["postgres.password"]'::jsonb, 'operator-a', %s, %s, %s, %s
            )
            """,
            (reference_admitted, status, revoked_by, reference_revoked),
        )

    def _seed_history_rows(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_secret_providers (
              registration_id, workspace_id, provider_id, provider_kind,
              display_name, endpoint_reference, credential_reference,
              allowed_reference_prefixes, allowed_intents, admitted_by,
              admitted_at, status, supersedes_registration_id, revoked_by,
              revoked_at
            ) VALUES
              ('provider-second', 'workspace-a', 'workspace-secrets',
               'control-plane-kit-secrets', 'First', 'workspace-secrets',
               'secret://bootstrap/provider-token',
               '["secret://workspace-secrets/workspace-a"]'::jsonb,
               '["postgres.password"]'::jsonb, 'operator-a',
               '2026-08-07T06:00:00Z', 'superseded', NULL, NULL, NULL),
              ('provider-micro-a', 'workspace-a', 'workspace-secrets',
               'control-plane-kit-secrets', 'Second A', 'workspace-secrets',
               'secret://bootstrap/provider-token',
               '["secret://workspace-secrets/workspace-a"]'::jsonb,
               '["postgres.password"]'::jsonb, 'operator-a',
               '2026-08-07T06:00:00.000001Z', 'superseded', 'provider-second',
               NULL, NULL),
              ('provider-micro-z', 'workspace-a', 'workspace-secrets',
               'control-plane-kit-secrets', 'Second Z', 'workspace-secrets',
               'secret://bootstrap/provider-token',
               '["secret://workspace-secrets/workspace-a"]'::jsonb,
               '["postgres.password"]'::jsonb, 'operator-a',
               '2026-08-07T06:00:00.000001Z', 'revoked', 'provider-micro-a',
               'operator-a', '2026-08-07T06:01:00.000001Z')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_secret_references (
              registration_id, workspace_id, secret_reference,
              provider_registration_id, allowed_intents, admitted_by,
              admitted_at, status, supersedes_registration_id, revoked_by,
              revoked_at
            ) VALUES
              ('reference-second', 'workspace-a',
               'secret://workspace-secrets/workspace-a/database',
               'provider-second', '["postgres.password"]'::jsonb, 'operator-a',
               '2026-08-07T06:00:00Z', 'superseded', NULL, NULL, NULL),
              ('reference-micro-a', 'workspace-a',
               'secret://workspace-secrets/workspace-a/database',
               'provider-micro-a', '["postgres.password"]'::jsonb, 'operator-a',
               '2026-08-07T06:00:00.000001Z', 'superseded', 'reference-second',
               NULL, NULL),
              ('reference-micro-z', 'workspace-a',
               'secret://workspace-secrets/workspace-a/database',
               'provider-micro-z', '["postgres.password"]'::jsonb, 'operator-a',
               '2026-08-07T06:00:00.000001Z', 'revoked', 'reference-micro-a',
               'operator-a', '2026-08-07T06:01:00.000001Z')
            """
        )

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _retained_values(self) -> tuple[object, ...]:
        provider = self.connection.execute(
            "SELECT admitted_at, revoked_at FROM cpk_secret_providers"
        ).fetchone()
        reference = self.connection.execute(
            "SELECT admitted_at, revoked_at FROM cpk_secret_references"
        ).fetchone()
        return (*provider, *reference)

    def _temporal_contract(self) -> tuple[tuple[str, str, str, int], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT table_name, column_name, is_nullable, datetime_precision
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND (table_name, column_name) IN (
                    ('cpk_secret_providers', 'admitted_at'),
                    ('cpk_secret_providers', 'revoked_at'),
                    ('cpk_secret_references', 'admitted_at'),
                    ('cpk_secret_references', 'revoked_at')
                  )
                ORDER BY table_name, column_name
                """
            ).fetchall()
        )

    def _column_contract(
        self,
        table: str,
        column: str,
    ) -> tuple[str, int | None, str, bool]:
        return self.connection.execute(
            """
            SELECT data_type, datetime_precision, is_nullable,
                   column_default IS NULL
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
            """,
            (table, column),
        ).fetchone()

    def _application_objects(
        self,
    ) -> dict[tuple[str, str], tuple[int, str]]:
        constraints = self.connection.execute(
            """
            SELECT conname, oid, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
              AND conrelid IN (
                'cpk_secret_providers'::regclass,
                'cpk_secret_references'::regclass
              )
            ORDER BY conname
            """
        ).fetchall()
        indexes = self.connection.execute(
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
              AND table_relation.relname IN (
                'cpk_secret_providers',
                'cpk_secret_references'
              )
            ORDER BY index_relation.relname
            """
        ).fetchall()
        return {
            **{
                ("constraint", name): (oid, definition)
                for name, oid, definition in constraints
            },
            **{
                ("index", name): (oid, definition)
                for name, oid, definition in indexes
            },
        }


if __name__ == "__main__":
    unittest.main()
