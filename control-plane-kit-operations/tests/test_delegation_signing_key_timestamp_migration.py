from __future__ import annotations

from datetime import datetime, timezone
import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres


_TEMPORAL_COLUMNS = (
    ("admitted_at", "NO", 6, True),
    ("activated_at", "YES", 6, True),
    ("retired_at", "YES", 6, True),
    ("revoked_at", "YES", 6, True),
)
_TEMPORAL_IDENTITIES = tuple(column for column, _, _, _ in _TEMPORAL_COLUMNS)
_V4_HISTORY = [
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
]
_V5_HISTORY = [*_V4_HISTORY, (5, "delegation-signing-key-timestamps")]
_CURRENT_HISTORY = [
    *_V5_HISTORY,
    (6, "gateway-probe-timestamps"),
    (7, "gateway-key-rotation-timestamps"),
    (8, "ingress-evidence-timestamps"),
    (9, "secret-use-authorization-timestamps"),
    (10, "product-descriptor-content"),
    (11, "gateway-probe-access-path"),
    (12, "gateway-key-rotation-generation-evidence"),
    (13, "gateway-key-rotation-status-contracts"),
    (14, "gateway-key-rotation-retirement-evidence"),
    (15, "approval-subject-evidence"),
    (16, "approval-scope-contracts"),
    (17, "graph-lineage-compatibility"),
]
_CANONICAL = "2026-08-07T06:00:00.000001Z"
_NONCANONICAL_OFFSET = "2026-08-07T02:00:00-04:00"
_EXPECTED_REBUILT_OBJECTS = {
    ("constraint", "cpk_delegation_signing_keys_activation_evidence_check"),
    ("constraint", "cpk_delegation_signing_keys_retirement_evidence_check"),
    ("constraint", "cpk_delegation_signing_keys_revocation_evidence_check"),
}


class DelegationSigningKeyTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"delegation_key_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_appends_exact_delegation_signing_key_v5(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 17)
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations[:5]],
            _V5_HISTORY,
        )
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations[:4]],
            _V4_HISTORY,
        )

    def test_fresh_install_has_exact_v5_temporal_contract(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._ledger(), _CURRENT_HISTORY)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_all_nine_reachable_lifecycle_shapes_migrate_without_loss(self) -> None:
        self._install_v4_baseline()
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        expected_text = self._seed_lifecycle_matrix()

        postgres.install_postgres_schema(self.connection)

        expected_time = datetime(2026, 8, 7, 6, 0, 0, 1, tzinfo=timezone.utc)
        observed = self._retained_matrix()
        self.assertEqual(
            observed,
            tuple(
                (
                    key_id,
                    status,
                    expected_time,
                    activated_by,
                    None if activated_at is None else expected_time,
                    retired_by,
                    None if retired_at is None else expected_time,
                    revoked_by,
                    None if revoked_at is None else expected_time,
                )
                for (
                    key_id,
                    status,
                    _admitted_at,
                    activated_by,
                    activated_at,
                    retired_by,
                    retired_at,
                    revoked_by,
                    revoked_at,
                ) in expected_text
            ),
        )
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)

    def test_each_retained_column_has_independent_atomic_preflight(self) -> None:
        for index, column in enumerate(_TEMPORAL_IDENTITIES):
            with self.subTest(column=column):
                case_schema = f"{self.schema}_{index}"
                self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                self.connection.execute(f'SET search_path TO "{case_schema}"')
                try:
                    self._install_v4_baseline()
                    retained = self._seed_one_for_column(column, _NONCANONICAL_OFFSET)
                    before_objects = self._application_objects()

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(self.connection)

                    self._assert_bounded_failure(raised.exception)
                    self.assertEqual(self._ledger(), _V4_HISTORY)
                    self.assertEqual(self._retained_matrix(), retained)
                    self.assertEqual(self._application_objects(), before_objects)
                    self._assert_v4_text_contract()
                finally:
                    self.connection.execute(f'SET search_path TO "{self.schema}"')
                    self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_calendar_invalid_value_rolls_back_rows_schema_objects_and_ledger(self) -> None:
        invalid = "2026-02-30T06:00:00Z"
        for index, column in enumerate(_TEMPORAL_IDENTITIES):
            with self.subTest(column=column):
                case_schema = f"{self.schema}_calendar_{index}"
                self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                self.connection.execute(f'SET search_path TO "{case_schema}"')
                try:
                    self._install_v4_baseline()
                    retained = self._seed_one_for_column(column, invalid)
                    before_objects = self._application_objects()

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(self.connection)

                    self._assert_bounded_failure(raised.exception)
                    self.assertNotIn(invalid, str(raised.exception))
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertEqual(self._ledger(), _V4_HISTORY)
                    self.assertEqual(self._retained_matrix(), retained)
                    self.assertEqual(self._application_objects(), before_objects)
                    self._assert_v4_text_contract()
                finally:
                    self.connection.execute(f'SET search_path TO "{self.schema}"')
                    self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_success_rebuilds_only_three_evidence_constraints(self) -> None:
        self._install_v4_baseline()
        self._seed_lifecycle_matrix()
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

    def test_current_verifier_rejects_all_four_by_four_temporal_fact_drift(self) -> None:
        for column_index, (column, nullable, _precision, _default_absent) in enumerate(
            _TEMPORAL_COLUMNS
        ):
            required = nullable == "NO"
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
                with self.subTest(column=column, fact=fact):
                    case_schema = f"{self.schema}_{column_index}_{fact_index}"
                    self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                    self.connection.execute(f'SET search_path TO "{case_schema}"')
                    try:
                        postgres.install_postgres_schema(self.connection)
                        self.connection.execute(
                            "ALTER TABLE cpk_delegation_signing_keys "
                            f"ALTER COLUMN {column} {mutation}"
                        )

                        with self.assertRaises(
                            postgres.SchemaMigrationError
                        ) as raised:
                            postgres.verify_postgres_schema(self.connection)

                        self.assertEqual(
                            str(raised.exception),
                            "database schema contract is not current",
                        )
                        self.assertLessEqual(len(str(raised.exception)), 256)
                        self.assertIsNone(raised.exception.__context__)
                    finally:
                        self.connection.execute(f'SET search_path TO "{self.schema}"')
                        self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_reinstall_preserves_v5_ledger_and_objects(self) -> None:
        postgres.install_postgres_schema(self.connection)
        before_ledger = self.connection.execute(
            "SELECT version, name, checksum_sha256, applied_at "
            "FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(
            [(version, name) for version, name, _checksum, _applied_at in before_ledger],
            _CURRENT_HISTORY,
        )
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

    def _install_v4_baseline(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in registry.migrations[1:4]:
            self.connection.execute(migration.sql)
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
        for migration in registry.migrations[:4]:
            self.connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_lifecycle_matrix(self) -> tuple[tuple[object, ...], ...]:
        shapes = (
            ("verify-new", "verify-only", None, None, None),
            ("verify-demoted", "verify-only", "activated", None, None),
            ("active", "active", "activated", None, None),
            ("retired-new", "retired", None, "retired", None),
            ("retired-active", "retired", "activated", "retired", None),
            ("revoked-new", "revoked", None, None, "revoked"),
            ("revoked-active", "revoked", "activated", None, "revoked"),
            ("revoked-retired", "revoked", None, "retired", "revoked"),
            (
                "revoked-active-retired",
                "revoked",
                "activated",
                "retired",
                "revoked",
            ),
        )
        self._seed_workspace()
        for index, (key_id, status, activated, retired, revoked) in enumerate(shapes):
            self._insert_key(
                index=index,
                key_id=key_id,
                status=status,
                admitted_at=_CANONICAL,
                activated_at=_CANONICAL if activated else None,
                retired_at=_CANONICAL if retired else None,
                revoked_at=_CANONICAL if revoked else None,
            )
        return self._retained_matrix()

    def _seed_one_for_column(
        self,
        column: str,
        value: str,
    ) -> tuple[tuple[object, ...], ...]:
        self._seed_workspace()
        activated_at = value if column == "activated_at" else None
        retired_at = value if column == "retired_at" else None
        revoked_at = value if column == "revoked_at" else None
        status = {
            "activated_at": "active",
            "retired_at": "retired",
            "revoked_at": "revoked",
        }.get(column, "verify-only")
        self._insert_key(
            index=1,
            key_id="retained-key",
            status=status,
            admitted_at=value if column == "admitted_at" else _CANONICAL,
            activated_at=activated_at,
            retired_at=retired_at,
            revoked_at=revoked_at,
        )
        return self._retained_matrix()

    def _seed_workspace(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )

    def _insert_key(
        self,
        *,
        index: int,
        key_id: str,
        status: str,
        admitted_at: str,
        activated_at: str | None,
        retired_at: str | None,
        revoked_at: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys (
              registration_id, workspace_id, purpose, issuer, key_id, algorithm,
              public_key_pem, public_fingerprint_sha256, private_key_reference,
              admitted_by, admitted_at, status, activated_by, activated_at,
              retired_by, retired_at, revoked_by, revoked_at
            ) VALUES (
              %s, 'workspace-a', 'gateway-probe', 'cpk-server', %s, 'ed25519',
              'public material', %s, %s, 'operator-a', %s, %s, %s, %s,
              %s, %s, %s, %s
            )
            """,
            (
                f"dkey_{index + 1:064x}",
                key_id,
                f"{index + 1:064x}",
                f"secret://workspace-secrets/keys/{key_id}",
                admitted_at,
                status,
                "operator-a" if activated_at is not None else None,
                activated_at,
                "operator-a" if retired_at is not None else None,
                retired_at,
                "operator-a" if revoked_at is not None else None,
                revoked_at,
            ),
        )

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _retained_matrix(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT key_id, status, admitted_at, activated_by, activated_at,
                       retired_by, retired_at, revoked_by, revoked_at
                FROM cpk_delegation_signing_keys
                ORDER BY registration_id
                """
            ).fetchall()
        )

    def _temporal_contract(self) -> tuple[tuple[str, str, int, bool], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT column_name, is_nullable, datetime_precision,
                       column_default IS NULL
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_delegation_signing_keys'
                  AND column_name IN (
                    'admitted_at', 'activated_at', 'retired_at', 'revoked_at'
                  )
                ORDER BY ordinal_position
                """
            ).fetchall()
        )

    def _column_contract(self, column: str) -> tuple[str, int | None, str, bool]:
        return self.connection.execute(
            """
            SELECT data_type, datetime_precision, is_nullable,
                   column_default IS NULL
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'cpk_delegation_signing_keys'
              AND column_name = %s
            """,
            (column,),
        ).fetchone()

    def _assert_v4_text_contract(self) -> None:
        for column, nullable, _precision, _default_absent in _TEMPORAL_COLUMNS:
            self.assertEqual(
                self._column_contract(column),
                ("text", None, nullable, True),
            )

    def _assert_bounded_failure(self, error: Exception) -> None:
        self.assertEqual(
            str(error),
            "delegation signing-key timestamps are not canonical UTC",
        )
        self.assertLessEqual(len(str(error)), 256)
        self.assertNotIn(_NONCANONICAL_OFFSET, str(error))
        self.assertNotIn("secret://", str(error))
        self.assertNotIn("public material", str(error))
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)

    def _application_objects(self) -> dict[tuple[str, str], tuple[int, str]]:
        constraints = self.connection.execute(
            """
            SELECT conname, oid, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
              AND conrelid = 'cpk_delegation_signing_keys'::regclass
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
              AND table_relation.relname = 'cpk_delegation_signing_keys'
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
