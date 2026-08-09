from __future__ import annotations

import ast
from datetime import datetime, timezone
import importlib.util
import inspect
import os
import unittest
import uuid

import psycopg

import control_plane_kit_operations.gateway_key_rotations as rotation_module
import control_plane_kit_operations.postgres as postgres
import control_plane_kit_operations.postgres.schema as schema_module
from control_plane_kit_operations.postgres.gateway_key_rotation_store import (
    GatewayKeyRotationStore,
)


_V6_HISTORY = [
    (1, "operations-baseline"),
    (2, "coordination-timestamps"),
    (3, "graph-product-authority-timestamps"),
    (4, "secret-registration-timestamps"),
    (5, "delegation-signing-key-timestamps"),
    (6, "gateway-probe-timestamps"),
]
_V7_HISTORY = [*_V6_HISTORY, (7, "gateway-key-rotation-timestamps")]
_CURRENT_HISTORY = [
    *_V7_HISTORY,
    (8, "ingress-evidence-timestamps"),
    (9, "secret-use-authorization-timestamps"),
    (10, "product-descriptor-content"),
    (11, "gateway-probe-access-path"),
    (12, "gateway-key-rotation-generation-evidence"),
]
_TEMPORAL_COLUMNS = (
    ("cpk_gateway_key_rotation_deployments", "accepted_at", "YES"),
    ("cpk_gateway_key_rotation_deployments", "prepared_at", "NO"),
    ("cpk_gateway_key_rotation_revocations", "prepared_at", "NO"),
    ("cpk_gateway_key_rotation_transitions", "advanced_at", "NO"),
    ("cpk_gateway_key_rotations", "new_key_activated_at", "YES"),
    ("cpk_gateway_key_rotations", "old_key_retired_at", "YES"),
    ("cpk_gateway_key_rotations", "old_secret_revoked_at", "YES"),
    ("cpk_gateway_key_rotations", "requested_at", "NO"),
    ("cpk_gateway_key_rotations", "updated_at", "YES"),
)
_TEMPORAL_CONTRACT = tuple(
    (table, column, "timestamp with time zone", 6, nullable, True)
    for table, column, nullable in _TEMPORAL_COLUMNS
)
_SECONDS = "2026-08-08T12:00:00Z"
_MICROS = "2026-08-08T12:00:00.000001Z"
_V7_SCHEMA_SHA256 = "65c0309b51e82e4ad313f113cd5df266f61e6c8b98aa5d5ff7194b53b6e5a775"
_EXPECTED_REBUILT_OBJECTS = {
    ("constraint", "cpk_cloudflare_ingress_resources_removed_evidence_check"),
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
_CANONICAL_DIGEST_DEFINITION = (
    "CHECK (((generation_action_digest IS NULL) OR "
    '((generation_action_digest COLLATE "C") ~ '
    "'^[0-9a-f]{64}$'::text)))"
)
_CURRENT_ADDED_OBJECTS = {
    ("constraint", "cpk_registered_products_content_digest_check"),
    ("constraint", "cpk_gateway_key_rotations_generation_provider_check"),
}


class GatewayKeyRotationTimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.schema = f"gateway_rotation_time_{uuid.uuid4().hex}"
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')

    def tearDown(self) -> None:
        self.connection.execute("SET search_path TO public")
        self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.connection.close()

    def test_registry_appends_exact_gateway_key_rotation_v7(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 12)
        self.assertEqual(
            [(value.version, value.name) for value in registry.migrations[:7]],
            _V7_HISTORY,
        )
        self.assertEqual(
            [(value.version, value.name) for value in registry.migrations[:6]],
            _V6_HISTORY,
        )
        migration = registry.migrations[6]
        expected_checksum = getattr(schema_module, "_POSTGRES_SCHEMA_V7_SHA256", None)
        self.assertEqual(expected_checksum, _V7_SCHEMA_SHA256)
        self.assertEqual(migration.checksum_sha256, _V7_SCHEMA_SHA256)

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
                and node.test.left.value.id == "_POSTGRES_SCHEMA_V7"
                and node.test.left.attr == "checksum_sha256"
                and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], ast.NotEq)
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Name)
                and node.test.comparators[0].id == "_POSTGRES_SCHEMA_V7_SHA256"
                and any(
                    isinstance(statement, ast.Raise)
                    and isinstance(statement.exc, ast.Call)
                    and isinstance(statement.exc.func, ast.Name)
                    and statement.exc.func.id == "SchemaMigrationError"
                    for statement in node.body
                )
                for node in guards
            ),
            "V7 must fail import when its SQL differs from its pinned checksum",
        )

    def test_fresh_install_has_exact_v7_temporal_contract(self) -> None:
        postgres.install_postgres_schema(self.connection)

        self.assertEqual(self._ledger(), _CURRENT_HISTORY)
        self.assertEqual(self._temporal_contract(), _TEMPORAL_CONTRACT)
        self.assertIs(
            postgres.verify_postgres_schema(self.connection).kind,
            postgres.ObservedSchemaKind.VERSIONED,
        )

    def test_rotation_language_has_no_postgres_import_edge(self) -> None:
        tree = ast.parse(inspect.getsource(rotation_module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = importlib.util.resolve_name(
                        "." * node.level + (node.module or ""),
                        rotation_module.__package__,
                    )
                    imported.add(base)
                    if node.module is None:
                        imported.update(f"{base}.{alias.name}" for alias in node.names)
                elif node.module is not None:
                    imported.add(node.module)
        self.assertFalse(
            any(name.startswith("control_plane_kit_operations.postgres") for name in imported)
        )

    def test_legal_lifecycle_shapes_migrate_without_inventing_evidence(self) -> None:
        self._install_v6_baseline()
        self._seed_rows()
        before = self._retained_rows()

        postgres.install_postgres_schema(self.connection)

        after = self._retained_rows()
        self.assertEqual(len(after), len(before))
        for table in before:
            self.assertEqual(len(after[table]), len(before[table]))
            for before_row, after_row in zip(before[table], after[table], strict=True):
                self.assertEqual(
                    self._without_temporal(table, after_row),
                    self._without_temporal(table, before_row),
                )
        rotations = {
            row[0]: row for row in after["cpk_gateway_key_rotations"]
        }
        requested = rotations["rotation-requested"]
        completed = rotations["rotation-completed"]
        seconds = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        micros = datetime(2026, 8, 8, 12, 0, 0, 1, tzinfo=timezone.utc)
        self.assertEqual(
            tuple(requested[index] for index in (12, 23, 25, 26, 29)),
            (seconds, None, None, None, None),
        )
        self.assertEqual(
            tuple(completed[index] for index in (12, 23, 25, 26, 29)),
            (micros, micros, seconds, micros, micros),
        )
        self.assertEqual(
            after["cpk_gateway_key_rotation_revocations"][0][8],
            seconds,
        )
        self.assertEqual(
            after["cpk_gateway_key_rotation_transitions"][0][8],
            micros,
        )
        overlap, retirement = after["cpk_gateway_key_rotation_deployments"]
        self.assertEqual((overlap[14], overlap[17]), (micros, None))
        self.assertEqual(
            (retirement[14], retirement[17]),
            (seconds, micros),
        )
        self.assertEqual(self._temporal_contract(), _TEMPORAL_CONTRACT)

    def test_each_retained_column_has_independent_atomic_lexical_preflight(self) -> None:
        self._assert_each_invalid("2026-08-08T08:00:00-04:00", "lexical")

    def test_each_alter_position_has_independent_atomic_calendar_failure(self) -> None:
        self._assert_each_invalid("2026-02-30T12:00:00Z", "calendar")

    def test_success_rebuilds_only_exact_timestamp_constraints(self) -> None:
        self._install_v6_baseline()
        self._seed_rows()
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
                else:
                    self.assertEqual(after_definition, before_definition)
                if after_oid != before_oid:
                    changed.add(identity)
        self.assertEqual(changed, _EXPECTED_REBUILT_OBJECTS)

    def test_current_verifier_rejects_all_nine_by_all_four_facts(self) -> None:
        for column_index, (table, column, nullable) in enumerate(_TEMPORAL_COLUMNS):
            mutations = (
                ("type", f"TYPE text USING {column}::text"),
                ("precision", f"TYPE timestamptz(5) USING {column}::timestamptz(5)"),
                ("nullability", "DROP NOT NULL" if nullable == "NO" else "SET NOT NULL"),
                ("default", "SET DEFAULT clock_timestamp()"),
            )
            for fact_index, (fact, mutation) in enumerate(mutations):
                with self.subTest(table=table, column=column, fact=fact):
                    case_schema = f"{self.schema}_verify_{column_index}_{fact_index}"
                    self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                    self.connection.execute(f'SET search_path TO "{case_schema}"')
                    try:
                        postgres.install_postgres_schema(self.connection)
                        self.connection.execute(
                            f"ALTER TABLE {table} ALTER COLUMN {column} {mutation}"
                        )
                        with self.assertRaises(postgres.SchemaMigrationError) as raised:
                            postgres.verify_postgres_schema(self.connection)
                        self.assertEqual(
                            str(raised.exception),
                            "gateway key rotation temporal schema is not current",
                        )
                        self.assertLessEqual(len(str(raised.exception)), 256)
                        self.assertIsNone(raised.exception.__context__)
                        self.assertIsNone(raised.exception.__cause__)
                    finally:
                        self.connection.execute(f'SET search_path TO "{self.schema}"')
                        self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def test_every_selector_decodes_seconds_microseconds_and_nulls_in_utc(self) -> None:
        postgres.install_postgres_schema(self.connection)
        self._seed_rows(native=True)
        self.connection.execute("SET TIME ZONE 'Asia/Tokyo'")
        store = GatewayKeyRotationStore(self.connection)

        requested = store.get("rotation-requested")
        completed = store.get_for_update("rotation-completed")
        correlated = store.for_correlation("workspace-a", "correlation-completed")
        nonterminal = store.nonterminal_for_binding(
            "workspace-a", requested.gateway_node_id, requested.purpose, requested.issuer
        )
        transition = store.transition_for_id("rotation-completed", "transition-completed")
        transitions = store.transitions("rotation-completed")

        self.assertEqual(requested.requested_at, _SECONDS)
        self.assertIsNone(requested.updated_at)
        self.assertEqual(completed.new_key_activated_at, _MICROS)
        self.assertEqual(completed.old_key_retired_at, _SECONDS)
        self.assertEqual(completed.old_secret_revoked_at, _MICROS)
        self.assertEqual(completed.updated_at, _MICROS)
        self.assertEqual(completed.revocation.prepared_at, _SECONDS)
        self.assertEqual(completed.overlap_deployment.prepared_at, _MICROS)
        self.assertIsNone(completed.overlap_deployment.accepted_at)
        self.assertEqual(completed.retirement_deployment.accepted_at, _MICROS)
        self.assertEqual(correlated, completed)
        self.assertEqual(nonterminal, requested)
        self.assertEqual(transition.advanced_at, _MICROS)
        self.assertEqual(transitions, (transition,))

    def _assert_each_invalid(self, invalid: str, label: str) -> None:
        for index, (table, column, _nullable) in enumerate(_TEMPORAL_COLUMNS):
            with self.subTest(table=table, column=column):
                case_schema = f"{self.schema}_{label}_{index}"
                self.connection.execute(f'CREATE SCHEMA "{case_schema}"')
                self.connection.execute(f'SET search_path TO "{case_schema}"')
                try:
                    self._install_v6_baseline()
                    self._seed_rows()
                    predicate = (
                        "rotation_id = 'rotation-completed' AND status = 'accepted'"
                        if table == "cpk_gateway_key_rotation_deployments"
                        and column == "accepted_at"
                        else "rotation_id = 'rotation-completed'"
                    )
                    self.connection.execute(
                        f"UPDATE {table} SET {column} = %s WHERE {predicate}",
                        (invalid,),
                    )
                    retained = self._retained_rows()
                    objects = self._application_objects()
                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(self.connection)
                    self.assertEqual(
                        str(raised.exception),
                        "gateway key rotation timestamps are not canonical UTC",
                    )
                    for excluded in (
                        invalid,
                        self.schema,
                        "rotation-",
                        "secret://",
                        "SELECT",
                        "ALTER TABLE",
                    ):
                        self.assertNotIn(excluded, str(raised.exception))
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._ledger(), _V6_HISTORY)
                    self.assertEqual(self._retained_rows(), retained)
                    self.assertEqual(self._application_objects(), objects)
                    self.assertEqual(self._column_contract(table, column)[0], "text")
                finally:
                    self.connection.execute(f'SET search_path TO "{self.schema}"')
                    self.connection.execute(f'DROP SCHEMA "{case_schema}" CASCADE')

    def _install_v6_baseline(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in registry.migrations[1:6]:
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
        for migration in registry.migrations[:6]:
            self.connection.execute(
                "INSERT INTO cpk_schema_migrations (version,name,checksum_sha256) "
                "VALUES (%s,%s,%s)",
                (migration.version, migration.name, migration.checksum_sha256),
            )
        self.connection.execute(schema_module._GRAPH_LINEAGE_CONSTRAINTS)

    def _seed_rows(self, *, native: bool = False) -> None:
        seconds = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc) if native else _SECONDS
        micros = (
            datetime(2026, 8, 8, 12, 0, 0, 1, tzinfo=timezone.utc)
            if native
            else _MICROS
        )
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id,name,lifecycle) "
            "VALUES ('workspace-a','Workspace A','created')"
        )
        base = (
            "rotation_id,workspace_id,gateway_node_id,purpose,issuer,old_key_id,"
            "new_secret_reference,key_generation_correlation,maximum_grant_lifetime_seconds,"
            "clock_skew_seconds,correlation_id,requested_by,requested_at,intent_fingerprint,"
            "status,version,approval_request_id,approval_decision_id,"
            "generation_provider_registration_id,generation_action_digest,new_key_id,"
            "new_secret_version_id,new_secret_version_number,new_key_activated_at,"
            "drain_deadline_epoch,old_key_retired_at,old_secret_revoked_at,failure_code,"
            "updated_by,updated_at"
        )
        self.connection.execute(
            f"INSERT INTO cpk_gateway_key_rotations ({base}) VALUES ("
            "%s,'workspace-a','gateway-requested','gateway-probe','cpk-server','key-a',"
            "'secret://workspace-secrets/keys/key-b','generate-requested',60,5,"
            "'correlation-requested','operator-a',%s,%s,'requested',1,"
            "NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            ("rotation-requested", seconds, "1" * 64),
        )
        self.connection.execute(
            f"INSERT INTO cpk_gateway_key_rotations ({base}) VALUES ("
            "%s,'workspace-a','gateway-completed','gateway-probe','cpk-server','key-a',"
            "'secret://workspace-secrets/keys/key-b','generate-completed',60,5,"
            "'correlation-completed','operator-a',%s,%s,'completed',13,"
            "'approval-request','approval-decision','provider-registration',%s,"
            "'key-b','version-b',1,%s,1800000065,%s,%s,NULL,'operator-a',%s)",
            (
                "rotation-completed",
                micros,
                "2" * 64,
                "3" * 64,
                micros,
                seconds,
                micros,
                micros,
            ),
        )
        self.connection.execute(
            "INSERT INTO cpk_gateway_key_rotation_revocations VALUES ("
            "'rotation-completed','provider-registration',"
            "'secret://workspace-secrets/keys/key-a','version-a',1,'revoke-a',"
            "'revoke-correlation',%s,%s)",
            ("4" * 64, seconds),
        )
        self.connection.execute(
            "INSERT INTO cpk_gateway_key_rotation_transitions VALUES ("
            "'rotation-completed','transition-completed','revocation-prepared','completed',"
            "12,13,%s,'operator-a',%s,NULL)",
            ("5" * 64, micros),
        )
        deployment = (
            "rotation_id,phase,status,session_id,plan_id,approval_request_id,"
            "approval_decision_id,execution_request_id,run_id,base_authored_graph_id,"
            "base_realized_projection_id,desired_authored_graph_id,"
            "desired_realized_projection_id,desired_revision,prepared_at,"
            "accepted_current_graph_id,accepted_current_projection_id,accepted_at"
        )
        self.connection.execute(
            f"INSERT INTO cpk_gateway_key_rotation_deployments ({deployment}) VALUES ("
            "'rotation-completed','overlap','prepared','session-overlap','plan-overlap',"
            "'approval-request-overlap','approval-decision-overlap','execution-overlap',"
            "'run-overlap','graph-a','projection-a','graph-a','projection-a-b',2,%s,"
            "NULL,NULL,NULL)",
            (micros,),
        )
        self.connection.execute(
            f"INSERT INTO cpk_gateway_key_rotation_deployments ({deployment}) VALUES ("
            "'rotation-completed','retirement','accepted','session-retirement',"
            "'plan-retirement','approval-request-retirement','approval-decision-retirement',"
            "'execution-retirement','run-retirement','graph-a','projection-a-b','graph-a',"
            "'projection-b',3,%s,'graph-a','projection-b',%s)",
            (seconds, micros),
        )

    def _ledger(self) -> list[tuple[int, str]]:
        return self.connection.execute(
            "SELECT version,name FROM cpk_schema_migrations ORDER BY version"
        ).fetchall()

    def _temporal_contract(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self.connection.execute(
                """
                SELECT table_name,column_name,data_type,datetime_precision,is_nullable,
                       column_default IS NULL
                FROM information_schema.columns
                WHERE table_schema=current_schema()
                  AND (table_name,column_name) IN (
                    ('cpk_gateway_key_rotations','requested_at'),
                    ('cpk_gateway_key_rotations','new_key_activated_at'),
                    ('cpk_gateway_key_rotations','old_key_retired_at'),
                    ('cpk_gateway_key_rotations','old_secret_revoked_at'),
                    ('cpk_gateway_key_rotations','updated_at'),
                    ('cpk_gateway_key_rotation_revocations','prepared_at'),
                    ('cpk_gateway_key_rotation_transitions','advanced_at'),
                    ('cpk_gateway_key_rotation_deployments','prepared_at'),
                    ('cpk_gateway_key_rotation_deployments','accepted_at')
                  )
                ORDER BY table_name,column_name
                """
            ).fetchall()
        )

    def _column_contract(self, table: str, column: str) -> tuple[object, ...]:
        return self.connection.execute(
            """
            SELECT data_type,datetime_precision,is_nullable,column_default IS NULL
            FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=%s AND column_name=%s
            """,
            (table, column),
        ).fetchone()

    def _retained_rows(self) -> dict[str, tuple[tuple[object, ...], ...]]:
        return {
            table: tuple(
                self.connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            )
            for table in (
                "cpk_gateway_key_rotations",
                "cpk_gateway_key_rotation_revocations",
                "cpk_gateway_key_rotation_transitions",
                "cpk_gateway_key_rotation_deployments",
            )
        }

    @staticmethod
    def _without_temporal(table: str, row: tuple[object, ...]) -> tuple[object, ...]:
        positions = {
            "cpk_gateway_key_rotations": {12, 23, 25, 26, 29},
            "cpk_gateway_key_rotation_revocations": {8},
            "cpk_gateway_key_rotation_transitions": {8},
            "cpk_gateway_key_rotation_deployments": {14, 17},
        }[table]
        return tuple(value for index, value in enumerate(row) if index not in positions)

    def _application_objects(self) -> dict[tuple[str, str], tuple[int, str]]:
        constraints = self.connection.execute(
            """
            SELECT conname,oid,pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace=current_schema()::regnamespace
              AND conname <> 'cpk_schema_migrations_pkey'
            ORDER BY conname
            """
        ).fetchall()
        indexes = self.connection.execute(
            """
            SELECT index_relation.relname,index_relation.oid,
                   pg_get_indexdef(index_relation.oid)
            FROM pg_index
            JOIN pg_class AS table_relation ON table_relation.oid=pg_index.indrelid
            JOIN pg_namespace ON pg_namespace.oid=table_relation.relnamespace
            JOIN pg_class AS index_relation ON index_relation.oid=pg_index.indexrelid
            WHERE pg_namespace.nspname=current_schema()
              AND index_relation.relname <> 'cpk_schema_migrations_pkey'
            ORDER BY index_relation.relname
            """
        ).fetchall()
        return {
            **{("constraint", name): (oid, definition) for name, oid, definition in constraints},
            **{("index", name): (oid, definition) for name, oid, definition in indexes},
        }


if __name__ == "__main__":
    unittest.main()
