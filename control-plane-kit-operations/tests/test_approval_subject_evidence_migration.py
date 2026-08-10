from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import time
import unittest
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module


_V14_HISTORY = (
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
    (14, "gateway-key-rotation-retirement-evidence"),
)
_V15_IDENTITY = (15, "approval-subject-evidence")
_TABLE = "cpk_approval_requests"
_DIGEST_CONSTRAINT = "cpk_approval_requests_review_digest_check"
_CATEGORICAL_ERROR = "approval subject evidence is not accepted"
_V1_SHA256 = "fc9b5547fc51ec681130c41facea785dbd24649049417455b184ea05886beed8"


class ApprovalSubjectEvidenceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"apsub_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_appends_exact_three_sql_step_v15_program(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        self.assertEqual(registry.target_version, 15)
        self.assertEqual(
            tuple((migration.version, migration.name) for migration in registry.migrations),
            (*_V14_HISTORY, _V15_IDENTITY),
        )

        migration = self._v15()
        self.assertIsNone(migration.sql)
        self.assertEqual(len(migration.steps), 3)
        self.assertTrue(
            all(type(step) is postgres.SqlMigrationStep for step in migration.steps)
        )
        preflight = migration.steps[0].sql
        self.assertLess(
            preflight.index(
                "LOCK TABLE cpk_gateway_key_rotations IN EXCLUSIVE MODE;"
            ),
            preflight.index(
                "LOCK TABLE cpk_approval_requests IN ACCESS EXCLUSIVE MODE;"
            ),
        )
        self.assertGreaterEqual(preflight.count('COLLATE "C"'), 8)
        self.assertIn("octet_length", preflight)
        self.assertIn("information_schema.columns", preflight)
        self.assertIn("count(DISTINCT constraints.oid)", preflight)
        self.assertIn("pg_get_indexdef", preflight)
        self.assertNotIn("ALTER TABLE", preflight)
        self.assertIn(_CATEGORICAL_ERROR, preflight)
        self.assertIn('COLLATE "C"', migration.steps[2].sql)

    def test_frozen_v1_and_internal_current_replay_are_separate(self) -> None:
        current = getattr(schema_module, "_CURRENT_POSTGRES_SCHEMA")

        self.assertEqual(
            hashlib.sha256(postgres.POSTGRES_SCHEMA.encode("utf-8")).hexdigest(),
            _V1_SHA256,
        )
        self.assertEqual(postgres.POSTGRES_SCHEMA_V1_SHA256, _V1_SHA256)
        self.assertIn(
            "ALTER TABLE cpk_approval_requests\n  ADD COLUMN IF NOT EXISTS rotation_id",
            postgres.POSTGRES_SCHEMA,
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS cpk_approval_requests",
            current,
        )
        self.assertNotIn(
            "ALTER TABLE cpk_approval_requests\n  ADD COLUMN IF NOT EXISTS rotation_id",
            current,
        )
        self.assertNotIn("UPDATE cpk_approval_requests\nSET subject_kind", current)
        self.assertNotIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "cpk_approval_requests_rotation_identity",
            current,
        )
        self.assertFalse(
            hasattr(schema_module, "_upgrade_approval_subject_evidence")
        )

    def test_exact_legacy_plan_subject_is_reconstructed(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            self._downgrade_subject_contract(connection)
            self._seed_legacy_plan_approval(connection, plan_id="plan.a-1:edge")

            postgres.install_postgres_schema(connection)

            subject = ActivityPlanApprovalSubject("plan.a-1:edge")
            row = connection.execute(
                "SELECT plan_id, rotation_id, subject_kind, subject_payload, "
                f"review_digest FROM {_TABLE} WHERE request_id = 'request-a'"
            ).fetchone()
            self.assertEqual(
                row,
                (
                    "plan.a-1:edge",
                    None,
                    subject.kind.value,
                    subject.descriptor(),
                    subject.review_digest,
                ),
            )
            self.assertEqual(self._history(connection)[-1][:2], _V15_IDENTITY)
            self.assertIn('COLLATE "C"', self._digest_constraint(connection)[1])
        finally:
            connection.close()

    def test_inherited_digest_check_is_replaced_once_then_preserved(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            legacy = self._digest_constraint(connection)
            self.assertNotIn('COLLATE "C"', legacy[1])

            postgres.install_postgres_schema(connection)

            canonical = self._digest_constraint(connection)
            self.assertNotEqual(canonical[0], legacy[0])
            self.assertIn('COLLATE "C"', canonical[1])
            snapshot = self._snapshot(connection)
            postgres.install_postgres_schema(connection)
            self.assertEqual(self._snapshot(connection), snapshot)
        finally:
            connection.close()

    def test_malformed_legacy_identifier_rejects_without_mutation_or_context(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            self._downgrade_subject_contract(connection)
            self._seed_legacy_plan_approval(connection, plan_id="plan-invalid-\u00e9")
            before = self._snapshot(connection)

            with self.assertRaises(postgres.SchemaMigrationError) as raised:
                postgres.install_postgres_schema(connection)

            self.assertEqual(str(raised.exception), _CATEGORICAL_ERROR)
            self.assertIsNone(raised.exception.__context__)
            self.assertIsNone(raised.exception.__cause__)
            self.assertNotIn("plan-invalid", str(raised.exception))
            self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def test_current_rotation_subject_codec_vectors_are_identity(self) -> None:
        vectors = (
            ("minimal", "a", "b", "c", "d", "e", 1, 0, "1" * 64),
            (
                "punctuation",
                "rotation.a-1:edge",
                "workspace.a-1:edge",
                "gateway.a-1:edge",
                "issuer.a-1:edge",
                "key.a-1:edge",
                300,
                60,
                "a" * 64,
            ),
            (
                "maximum",
                "r" * 200,
                "w" * 200,
                "g" * 200,
                "i" * 200,
                "k" * 200,
                300,
                60,
                "f" * 64,
            ),
        )
        for vector in vectors:
            with self.subTest(vector=vector[0]):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    subject = GatewayKeyRotationApprovalSubject(
                        rotation_id=vector[1],
                        workspace_id=vector[2],
                        gateway_node_id=vector[3],
                        purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                        issuer=vector[4],
                        old_key_id=vector[5],
                        maximum_grant_lifetime_seconds=vector[6],
                        clock_skew_seconds=vector[7],
                        rotation_intent_digest=vector[8],
                    )
                    self._seed_current_rotation_approval(connection, subject)
                    before = self._approval_rows(connection)

                    postgres.install_postgres_schema(connection)

                    self.assertEqual(self._approval_rows(connection), before)
                    self.assertEqual(self._history(connection)[-1][:2], _V15_IDENTITY)
                finally:
                    connection.close()

    def test_already_v15_subject_drift_rejects_instead_of_repairing(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            self._downgrade_subject_contract(connection)
            self._seed_legacy_plan_approval(connection, plan_id="plan-a")
            for step in self._v15().steps:
                connection.execute(step.sql)
            connection.execute(
                "UPDATE cpk_approval_requests "
                "SET subject_payload = '{\"kind\":\"activity-plan\","
                "\"plan_id\":\"other\"}'::jsonb"
            )
            drifted = self._snapshot(connection)

            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.install_postgres_schema(connection)

            self.assertEqual(self._snapshot(connection), drifted)
        finally:
            connection.close()

    def test_constraint_lookalike_is_preserved_while_owned_absence_installs(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            connection.execute(
                f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_DIGEST_CONSTRAINT}"
            )
            connection.execute(
                "CREATE TABLE unrelated_subjects (review_digest text NOT NULL)"
            )
            connection.execute(
                "ALTER TABLE unrelated_subjects ADD CONSTRAINT "
                f"{_DIGEST_CONSTRAINT} CHECK (review_digest <> '')"
            )
            lookalike_oid = connection.execute(
                "SELECT oid FROM pg_constraint WHERE conrelid = "
                "'unrelated_subjects'::regclass AND conname = %s",
                (_DIGEST_CONSTRAINT,),
            ).fetchone()[0]

            for step in self._v15().steps:
                connection.execute(step.sql)

            self.assertIn('COLLATE "C"', self._digest_constraint(connection)[1])
            self.assertEqual(
                connection.execute(
                    "SELECT oid FROM pg_constraint WHERE conrelid = "
                    "'unrelated_subjects'::regclass AND conname = %s",
                    (_DIGEST_CONSTRAINT,),
                ).fetchone()[0],
                lookalike_oid,
            )
        finally:
            connection.close()

    def test_wrong_owned_digest_constraint_rejects_before_mutation(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            connection.execute(
                f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_DIGEST_CONSTRAINT}"
            )
            connection.execute(
                f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_DIGEST_CONSTRAINT} "
                "CHECK (review_digest <> '')"
            )
            before = self._snapshot(connection)

            with self.assertRaises(postgres.SchemaMigrationError) as raised:
                postgres.install_postgres_schema(connection)

            self.assertEqual(str(raised.exception), _CATEGORICAL_ERROR)
            self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def test_target_index_name_on_wrong_relation_rejects(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            connection.execute("DROP INDEX cpk_approval_requests_rotation_identity")
            connection.execute("CREATE TABLE unrelated_indexes (rotation_id text)")
            connection.execute(
                "CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity "
                "ON unrelated_indexes (rotation_id)"
            )
            before = self._snapshot(connection)

            with self.assertRaises(psycopg.Error) as raised:
                connection.execute(self._v15().steps[0].sql)

            self.assertEqual(raised.exception.sqlstate, "P1110")
            self.assertEqual(
                raised.exception.diag.message_primary,
                _CATEGORICAL_ERROR,
            )
            self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def test_explicit_c_admission_overrides_non_c_column_collation(self) -> None:
        connection = self._connection()
        try:
            collation = connection.execute(
                "SELECT collname FROM pg_collation "
                "WHERE collprovider = 'i' ORDER BY collname LIMIT 1"
            ).fetchone()
            if collation is None:
                self.skipTest("PostgreSQL image exposes no ICU collation")
            connection.execute(
                sql.SQL("CREATE TABLE collated_values (value text COLLATE {})").format(
                    sql.Identifier(collation[0])
                )
            )
            connection.execute(
                "INSERT INTO collated_values VALUES ('a'), ('a.b-1:c_d'), "
                "('invalid-\u00e9'), ('-invalid')"
            )

            admitted = connection.execute(
                "SELECT value FROM collated_values WHERE "
                "octet_length(value) BETWEEN 1 AND 200 "
                "AND (value COLLATE \"C\") ~ '^[A-Za-z0-9]' "
                "AND (value COLLATE \"C\") !~ '[^A-Za-z0-9._:-]' "
                "ORDER BY value COLLATE \"C\""
            ).fetchall()

            self.assertEqual(admitted, [("a",), ("a.b-1:c_d",)])
        finally:
            connection.close()

    def test_rotation_service_lock_sequence_completes_without_deadlock(self) -> None:
        setup = self._connection()
        try:
            self._prepare(14, setup)
            subject = GatewayKeyRotationApprovalSubject(
                rotation_id="rotation-a",
                workspace_id="workspace-a",
                gateway_node_id="gateway-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="issuer-a",
                old_key_id="key-a",
                maximum_grant_lifetime_seconds=300,
                clock_skew_seconds=60,
                rotation_intent_digest="a" * 64,
            )
            self._seed_current_rotation_approval(setup, subject)
        finally:
            setup.close()

        service = psycopg.connect(self.database_url, autocommit=False)
        migration = psycopg.connect(self.database_url, autocommit=False)
        try:
            service.execute(f'SET search_path TO "{self.schema}"')
            migration.execute(f'SET search_path TO "{self.schema}"')
            migration.execute("SET lock_timeout TO '5s'")
            service.execute(
                "SELECT rotation_id FROM cpk_gateway_key_rotations "
                "WHERE rotation_id = 'rotation-a' FOR UPDATE"
            ).fetchone()
            service.execute(
                "SELECT request_id FROM cpk_approval_requests "
                "WHERE rotation_id = 'rotation-a'"
            ).fetchone()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(postgres.install_postgres_schema, migration)
                time.sleep(0.25)
                self.assertFalse(future.done())
                service.execute(
                    "UPDATE cpk_gateway_key_rotations SET version = version "
                    "WHERE rotation_id = 'rotation-a'"
                )
                service.commit()
                future.result(timeout=10)

            self.assertEqual(self._history(migration)[-1][:2], _V15_IDENTITY)
        finally:
            service.rollback()
            migration.rollback()
            service.close()
            migration.close()

    def _prepare(self, version: int, connection) -> None:
        connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[1:version]:
            migration_runner._apply_schema_migration(connection, migration)
        connection.execute(
            """
            CREATE TABLE cpk_schema_migrations (
              version integer NOT NULL PRIMARY KEY,
              name text NOT NULL,
              checksum_sha256 text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )
            """
        )
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[:version]:
            connection.execute(
                "INSERT INTO cpk_schema_migrations "
                "(version, name, checksum_sha256) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum_sha256),
            )

    @staticmethod
    def _downgrade_subject_contract(connection) -> None:
        connection.execute(
            """
            ALTER TABLE cpk_approval_requests
              DROP COLUMN rotation_id CASCADE,
              DROP COLUMN subject_kind CASCADE,
              DROP COLUMN subject_payload CASCADE,
              DROP COLUMN review_digest CASCADE;
            ALTER TABLE cpk_approval_requests ALTER COLUMN plan_id SET NOT NULL;
            """
        )

    @staticmethod
    def _seed_legacy_plan_approval(connection, *, plan_id: str) -> None:
        connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open',
                    '2026-08-09T12:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id, status,
               created_at, payload)
            VALUES (%s, 'session-a', 'graph-a', 'graph-b', 'planned',
                    '2026-08-09T12:00:01Z', '{}'::jsonb)
            """,
            (plan_id,),
        )
        connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('request-a', 'session-a', %s, 'operator-a',
                    '2026-08-09T12:00:02Z', 'plan:approve', 'low', false)
            """,
            (plan_id,),
        )

    def _connection(self):
        connection = psycopg.connect(self.database_url, autocommit=True)
        connection.execute(f'SET search_path TO "{self.schema}"')
        return connection

    def _reset_schema(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    @staticmethod
    def _seed_current_rotation_approval(connection, subject) -> None:
        connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES (%s, 'Workspace', 'created')",
            (subject.workspace_id,),
        )
        connection.execute(
            "INSERT INTO cpk_operation_sessions "
            "(session_id, workspace_id, actor_id, title, status, created_at) "
            "VALUES ('session-a', %s, 'operator-a', 'Rotate', 'open', "
            "'2026-08-09T12:00:00Z')",
            (subject.workspace_id,),
        )
        connection.execute(
            """
            INSERT INTO cpk_gateway_key_rotations (
              rotation_id, workspace_id, gateway_node_id, purpose, issuer,
              old_key_id, new_secret_reference, key_generation_correlation,
              maximum_grant_lifetime_seconds, clock_skew_seconds,
              correlation_id, requested_by, requested_at, intent_fingerprint,
              status, version
            ) VALUES (
              %s, %s, %s, %s, %s, %s,
              'secret://workspace-secrets/keys/new', 'generation-a', %s, %s,
              'correlation-a', 'operator-a', '2026-08-09T12:00:01Z', %s,
              'requested', 1
            )
            """,
            (
                subject.rotation_id,
                subject.workspace_id,
                subject.gateway_node_id,
                subject.purpose.value,
                subject.issuer,
                subject.old_key_id,
                subject.maximum_grant_lifetime_seconds,
                subject.clock_skew_seconds,
                subject.rotation_intent_digest,
            ),
        )
        connection.execute(
            """
            INSERT INTO cpk_approval_requests (
              request_id, session_id, rotation_id, subject_kind,
              subject_payload, review_digest, requested_by, requested_at,
              required_scope, max_risk, destructive
            ) VALUES (
              'request-a', 'session-a', %s, 'gateway-key-rotation', %s, %s,
              'operator-a', '2026-08-09T12:00:02Z',
              'delegation-key:rotate-approve', 'high', true
            )
            """,
            (subject.rotation_id, Jsonb(subject.descriptor()), subject.review_digest),
        )

    @staticmethod
    def _history(connection):
        return tuple(
            connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    @staticmethod
    def _digest_constraint(connection):
        rows = connection.execute(
            """
            SELECT constraints.oid,
                   pg_get_constraintdef(constraints.oid, false),
                   constraints.contype::text,
                   constraints.convalidated
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = %s
              AND constraints.conname = %s
            ORDER BY constraints.oid
            """,
            (_TABLE, _DIGEST_CONSTRAINT),
        ).fetchall()
        if len(rows) != 1:
            raise AssertionError("expected one owned review-digest constraint")
        return rows[0]

    def _snapshot(self, connection):
        columns = tuple(
            connection.execute(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = %s
                ORDER BY ordinal_position
                """,
                (_TABLE,),
            ).fetchall()
        )
        rows = tuple(
            connection.execute(f"SELECT * FROM {_TABLE} ORDER BY request_id").fetchall()
        )
        return self._history(connection), columns, rows

    @staticmethod
    def _approval_rows(connection):
        return tuple(
            connection.execute(
                "SELECT request_id, plan_id, rotation_id, subject_kind, "
                "subject_payload, review_digest FROM cpk_approval_requests "
                "ORDER BY request_id"
            ).fetchall()
        )

    @staticmethod
    def _v15():
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[-1]
        if (migration.version, migration.name) != _V15_IDENTITY:
            raise AssertionError("V15 approval-subject-evidence migration is missing")
        return migration


if __name__ == "__main__":
    unittest.main()
