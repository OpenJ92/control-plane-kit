from __future__ import annotations

import ast
from datetime import datetime, timezone
import inspect
import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.postgres as postgres
import control_plane_kit_operations.postgres.schema as schema_module


_V5_HISTORY = [
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
    (5, "delegation-signing-key-timestamps"),
]
_V6_HISTORY = [*_V5_HISTORY, (6, "gateway-probe-timestamps")]
_CURRENT_HISTORY = [
    *_V6_HISTORY,
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
_TEMPORAL_COLUMNS = (
    ("requested_at", "timestamp with time zone", 6, "NO", True),
    ("completed_at", "timestamp with time zone", 6, "YES", True),
)
_TEMPORAL_IDENTITIES = tuple(value[0] for value in _TEMPORAL_COLUMNS)
_CANONICAL_SECONDS = "2026-08-08T12:00:00Z"
_CANONICAL_MICROS = "2026-08-08T12:00:00.000001Z"
_NONCANONICAL_OFFSET = "2026-08-08T08:00:00-04:00"
_V6_SCHEMA_SHA256 = "ae60d9014fdc65167daa7750417fb9f3b59ebc6a2a98903d74cde21e09d473cb"
_EXPECTED_CURRENT_REBUILT_OBJECTS = {
    ("constraint", "cpk_approval_requests_review_digest_check"),
    ("constraint", "cpk_cloudflare_ingress_resources_removed_evidence_check"),
    ("constraint", "cpk_gateway_probe_completion_check"),
    ("constraint", "cpk_gateway_key_rotations_activation_check"),
    ("constraint", "cpk_gateway_key_rotations_retirement_check"),
    ("constraint", "cpk_gateway_key_rotation_deployments_acceptance_check"),
    ("constraint", "cpk_gateway_key_rotations_generation_digest_check"),
    ("index", "cpk_cloudflare_ingress_resources_workspace"),
    ("index", "cpk_secret_use_authorizations_reference_history"),
}
_CANONICAL_DIGEST_CONSTRAINT = (
    "constraint",
    "cpk_gateway_key_rotations_generation_digest_check",
)
_APPROVAL_DIGEST_CONSTRAINT = (
    "constraint",
    "cpk_approval_requests_review_digest_check",
)
_APPROVAL_DIGEST_DEFINITION = (
    'CHECK (((review_digest COLLATE "C") ~ '
    "'^[0-9a-f]{64}$'::text))"
)
_CANONICAL_DIGEST_DEFINITION = (
    "CHECK (((generation_action_digest IS NULL) OR "
    '((generation_action_digest COLLATE "C") ~ '
    "'^[0-9a-f]{64}$'::text)))"
)
_CURRENT_ADDED_OBJECTS = {
    ("constraint", "cpk_activity_plans_desired_graph_revision_check"),
    ("constraint", "cpk_workspaces_desired_graph_revision_check"),
    ("constraint", "cpk_registered_products_content_digest_check"),
    ("constraint", "cpk_gateway_key_rotations_generation_provider_check"),
}


class GatewayProbeTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"gateway_probe_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_appends_exact_gateway_probe_v6(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 17)
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations],
            _CURRENT_HISTORY,
        )
        self.assertEqual(
            [(migration.version, migration.name) for migration in registry.migrations[:5]],
            _V5_HISTORY,
        )
        self.assertEqual(registry.migrations[5].checksum_sha256, _V6_SCHEMA_SHA256)
        self.assertEqual(
            getattr(schema_module, "_POSTGRES_SCHEMA_V6_SHA256", None),
            _V6_SCHEMA_SHA256,
        )
        tree = ast.parse(inspect.getsource(schema_module))
        guards = (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
        )
        self.assertTrue(
            any(
                isinstance(node.test.left, ast.Attribute)
                and isinstance(node.test.left.value, ast.Name)
                and node.test.left.value.id == "_POSTGRES_SCHEMA_V6"
                and node.test.left.attr == "checksum_sha256"
                and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], ast.NotEq)
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Name)
                and node.test.comparators[0].id == "_POSTGRES_SCHEMA_V6_SHA256"
                and any(
                    isinstance(statement, ast.Raise)
                    and isinstance(statement.exc, ast.Call)
                    and isinstance(statement.exc.func, ast.Name)
                    and statement.exc.func.id == "SchemaMigrationError"
                    for statement in node.body
                )
                for node in guards
            ),
            "V6 must fail import when its SQL differs from the pinned checksum",
        )

    def test_fresh_install_has_exact_v6_temporal_contract(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._ledger(), _CURRENT_HISTORY)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_all_four_status_shapes_migrate_without_loss(self) -> None:
        self._install_v5_baseline()
        self.connection.execute("SET TIME ZONE 'America/New_York'")
        shapes = (
            ("intended", _CANONICAL_SECONDS, None, None),
            ("succeeded", _CANONICAL_MICROS, _CANONICAL_SECONDS, "probe-succeeded"),
            ("rejected", _CANONICAL_SECONDS, _CANONICAL_MICROS, "probe-rejected"),
            ("failed", _CANONICAL_MICROS, _CANONICAL_MICROS, "probe-failed"),
        )
        self._seed_foundation()
        for index, (status, requested_at, completed_at, result_code) in enumerate(shapes):
            self._insert_attempt(
                index=index,
                status=status,
                requested_at=requested_at,
                completed_at=completed_at,
                result_code=result_code,
            )
        before = self._retained_rows()

        postgres.install_postgres_schema(self.connection)

        seconds = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        micros = datetime(2026, 8, 8, 12, 0, 0, 1, tzinfo=timezone.utc)
        expected_times = (
            (seconds, None),
            (micros, seconds),
            (seconds, micros),
            (micros, micros),
        )
        after = self._retained_rows()
        self.assertEqual(len(after), len(before))
        for before_row, after_row, (requested_at, completed_at) in zip(
            before,
            after,
            expected_times,
            strict=True,
        ):
            with self.subTest(probe_id=before_row[0]):
                self.assertEqual(
                    self._without_temporal_facts(after_row),
                    self._without_temporal_facts(before_row),
                )
                self.assertEqual(after_row[18], requested_at)
                self.assertEqual(after_row[20], completed_at)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_COLUMNS)

    def test_each_retained_column_has_independent_atomic_lexical_preflight(self) -> None:
        self._assert_each_invalid_retained_value(_NONCANONICAL_OFFSET, "lexical")

    def test_each_alter_position_has_independent_atomic_calendar_preflight(self) -> None:
        self._assert_each_invalid_retained_value(
            "2026-02-30T12:00:00Z",
            "calendar",
        )

    def test_success_rebuilds_only_current_temporal_constraints(self) -> None:
        self._install_v5_baseline()
        self._seed_foundation()
        self._insert_attempt(
            index=1,
            status="succeeded",
            requested_at=_CANONICAL_SECONDS,
            completed_at=_CANONICAL_MICROS,
            result_code="probe-succeeded",
        )
        before = self._application_objects()

        postgres.install_postgres_schema(self.connection)

        after = self._application_objects()
        self.assertEqual(set(after), set(before) | _CURRENT_ADDED_OBJECTS)
        changed = set()
        for identity, (before_oid, before_definition) in before.items():
            after_oid, after_definition = after[identity]
            with self.subTest(identity=identity):
                if identity == _CANONICAL_DIGEST_CONSTRAINT:
                    self.assertEqual(after_definition, _CANONICAL_DIGEST_DEFINITION)
                elif identity == _APPROVAL_DIGEST_CONSTRAINT:
                    self.assertEqual(after_definition, _APPROVAL_DIGEST_DEFINITION)
                else:
                    self.assertEqual(after_definition, before_definition)
                if after_oid != before_oid:
                    changed.add(identity)
        self.assertEqual(changed, _EXPECTED_CURRENT_REBUILT_OBJECTS)
        self.assertEqual(
            after[("index", "cpk_gateway_probe_workspace_timeline")],
            before[("index", "cpk_gateway_probe_workspace_timeline")],
        )

    def test_current_verifier_rejects_both_columns_by_all_four_facts(self) -> None:
        for column_index, (_column, _type, _precision, nullable, _no_default) in enumerate(
            _TEMPORAL_COLUMNS
        ):
            column = _TEMPORAL_COLUMNS[column_index][0]
            required = nullable == "NO"
            mutations = (
                ("type", f"TYPE text USING {column}::text"),
                ("precision", f"TYPE timestamptz(5) USING {column}::timestamptz(5)"),
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
                            "ALTER TABLE cpk_gateway_probe_attempts "
                            f"ALTER COLUMN {column} {mutation}"
                        )

                        with self.assertRaises(postgres.SchemaMigrationError) as raised:
                            postgres.verify_postgres_schema(self.connection)

                        self.assertEqual(
                            str(raised.exception),
                            "gateway probe temporal schema is not current",
                        )
                        self.assertLessEqual(len(str(raised.exception)), 256)
                        self.assertIsNone(raised.exception.__context__)
                        self.assertIsNone(raised.exception.__cause__)
                    finally:
                        self.connection.execute(f'SET search_path TO "{self.schema}"')
                        self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_reinstall_preserves_v6_ledger_and_objects(self) -> None:
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

    def _assert_each_invalid_retained_value(self, invalid: str, label: str) -> None:
        for index, column in enumerate(_TEMPORAL_IDENTITIES):
            with self.subTest(column=column):
                case_schema = f"{self.schema}_{label}_{index}"
                self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                self.connection.execute(f'SET search_path TO "{case_schema}"')
                try:
                    self._install_v5_baseline()
                    self._seed_foundation()
                    requested_at = invalid if column == "requested_at" else _CANONICAL_SECONDS
                    completed_at = invalid if column == "completed_at" else _CANONICAL_MICROS
                    self._insert_attempt(
                        index=index,
                        status="failed",
                        requested_at=requested_at,
                        completed_at=completed_at,
                        result_code="probe-failed",
                    )
                    retained = self._retained_rows()
                    before_objects = self._application_objects()

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(self.connection)

                    self.assertEqual(
                        str(raised.exception),
                        "gateway probe timestamps are not canonical UTC",
                    )
                    self.assertLessEqual(len(str(raised.exception)), 256)
                    for excluded in (
                        invalid,
                        self.schema,
                        "probe-",
                        "gateway-",
                        "grant-",
                        "SELECT",
                        "ALTER TABLE",
                    ):
                        self.assertNotIn(excluded, str(raised.exception))
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._ledger(), _V5_HISTORY)
                    self.assertEqual(self._retained_rows(), retained)
                    self.assertEqual(self._application_objects(), before_objects)
                    self._assert_v5_text_contract()
                finally:
                    self.connection.execute(f'SET search_path TO "{self.schema}"')
                    self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def _install_v5_baseline(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in registry.migrations[1:5]:
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
        for migration in registry.migrations[:5]:
            self.connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )
        self.connection.execute(schema_module._GRAPH_LINEAGE_CONSTRAINTS)

    def _seed_foundation(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_graph_versions (
              graph_id, workspace_id, version, graph_descriptor, created_by, created_at
            ) VALUES (
              'graph-current', 'workspace-a', 1, '{}'::jsonb,
              'operator-a', %s
            )
            """,
            (_CANONICAL_SECONDS,),
        )

    def _insert_attempt(
        self,
        *,
        index: int,
        status: str,
        requested_at: str,
        completed_at: str | None,
        result_code: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_gateway_probe_attempts (
              probe_id, workspace_id, request_id, actor_id, current_graph_id,
              gateway_node_id, gateway_runtime_id, access_path, probe_kind,
              target_id, request_digest, issuer, key_id, audience, grant_jti,
              issued_at, expires_at, status, requested_at, intent_fingerprint,
              completed_at, result_code, evidence
            ) VALUES (
              %s, 'workspace-a', %s, 'operator-a', 'graph-current',
              'gateway-a', 'runtime-a', 'runtime-private', 'http-status',
              'hello.http', %s, 'cpk-test', 'key-a', 'gateway:workspace-a:gateway-a',
              %s, 1800000000, 1800000060, %s, %s, %s, %s, %s,
              jsonb_build_object('probe_evidence', %s::text)
            )
            """,
            (
                f"probe-{index}",
                f"request-{index}",
                f"{index + 1:064x}",
                f"grant-{index}",
                status,
                requested_at,
                f"{index + 11:064x}",
                completed_at,
                result_code,
                f"retained-{index}",
            ),
        )

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version, name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _retained_rows(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT probe_id, workspace_id, request_id, actor_id, current_graph_id,
                       gateway_node_id, gateway_runtime_id, access_path, probe_kind,
                       target_id, request_digest, issuer, key_id, audience, grant_jti,
                       issued_at, expires_at, status, requested_at,
                       intent_fingerprint, completed_at, result_code, evidence
                FROM cpk_gateway_probe_attempts
                ORDER BY probe_id
                """
            ).fetchall()
        )

    @staticmethod
    def _without_temporal_facts(row: tuple[object, ...]) -> tuple[object, ...]:
        return row[:18] + row[19:20] + row[21:]

    def _temporal_contract(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT column_name, data_type, datetime_precision, is_nullable,
                       column_default IS NULL
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'cpk_gateway_probe_attempts'
                  AND column_name IN ('requested_at', 'completed_at')
                ORDER BY ordinal_position
                """
            ).fetchall()
        )

    def _column_contract(self, column: str) -> tuple[object, ...]:
        return self.connection.execute(
            """
            SELECT data_type, datetime_precision, is_nullable,
                   column_default IS NULL
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'cpk_gateway_probe_attempts'
              AND column_name = %s
            """,
            (column,),
        ).fetchone()

    def _assert_v5_text_contract(self) -> None:
        self.assertEqual(
            self._column_contract("requested_at"),
            ("text", None, "NO", True),
        )
        self.assertEqual(
            self._column_contract("completed_at"),
            ("text", None, "YES", True),
        )

    def _application_objects(self) -> dict[tuple[str, str], tuple[int, str]]:
        constraints = self.connection.execute(
            """
            SELECT conname, oid, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace
              AND conname <> 'cpk_schema_migrations_pkey'
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
              AND index_relation.relname <> 'cpk_schema_migrations_pkey'
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
