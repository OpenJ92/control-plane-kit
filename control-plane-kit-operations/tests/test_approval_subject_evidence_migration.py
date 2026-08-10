from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import re
import threading
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
from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import schema as schema_module
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.postgres.gateway_key_rotation_store import (
    GatewayKeyRotationStore,
)


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
_V15_STEP_SHA256 = (
    "5214d4f631b13f4265b963862b7e80f27571e6f16d4a8bd9bb2ed77389c1b552",
    "1e9a3e00f7bff06c530f4301ebf696852cf7b57e2fac79c4dd11301723b715ac",
    "50dd1ca16fe7a3eb83bce6d25f0bcbad0bb82301d60d169f07a643aae749dd99",
)
_V15_SHA256 = "215c6a71efd06f699c1d988a7e55435920075726009f030eecbd4a8c0fd91a0b"
_CONSTRAINT_DEFINITIONS = {
    "cpk_approval_requests_rotation_fk": (
        "FOREIGN KEY (rotation_id) REFERENCES "
        "cpk_gateway_key_rotations(rotation_id)"
    ),
    "cpk_approval_requests_subject_kind_check": (
        "CHECK (subject_kind IN ('activity-plan', 'gateway-key-rotation'))"
    ),
    "cpk_approval_requests_review_digest_check": (
        'CHECK ((review_digest COLLATE "C") ~ \'^[0-9a-f]{64}$\')'
    ),
    "cpk_approval_requests_subject_identity_check": (
        "CHECK ((subject_kind = 'activity-plan' AND plan_id IS NOT NULL "
        "AND rotation_id IS NULL) OR (subject_kind = 'gateway-key-rotation' "
        "AND plan_id IS NULL AND rotation_id IS NOT NULL))"
    ),
}


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
        self.assertEqual(
            tuple(step.checksum_sha256 for step in migration.steps),
            _V15_STEP_SHA256,
        )
        self.assertEqual(migration.checksum_sha256, _V15_SHA256)
        self.assertEqual(
            getattr(schema_module, "_POSTGRES_SCHEMA_V15_SHA256"),
            _V15_SHA256,
        )

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
        for plan_id in ("", "-invalid", "plan-invalid-\u00e9", "p" * 201):
            with self.subTest(plan_id=plan_id[:16]):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    self._downgrade_subject_contract(connection)
                    self._seed_legacy_plan_approval(connection, plan_id=plan_id)
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
            (
                "minimal", "a", "b", "c", DelegationKeyPurpose.GATEWAY_PROBE,
                "d", "e", 1, 0, "1" * 64,
            ),
            (
                "punctuation",
                "rotation.a-1:edge",
                "workspace.a-1:edge",
                "gateway.a-1:edge",
                DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
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
                DelegationKeyPurpose.GATEWAY_PROBE,
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
                        purpose=vector[4],
                        issuer=vector[5],
                        old_key_id=vector[6],
                        maximum_grant_lifetime_seconds=vector[7],
                        clock_skew_seconds=vector[8],
                        rotation_intent_digest=vector[9],
                    )
                    self._seed_current_rotation_approval(connection, subject)
                    before = self._approval_rows(connection)

                    postgres.install_postgres_schema(connection)

                    self.assertEqual(self._approval_rows(connection), before)
                    self.assertEqual(self._history(connection)[-1][:2], _V15_IDENTITY)
                finally:
                    connection.close()

    def test_current_activity_plan_subject_and_owned_objects_are_identity(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
            self._seed_current_plan_approval(connection, "plan-a")
            before_rows = self._approval_rows(connection)
            before_objects = self._owned_object_identities(
                connection,
                excluded=("cpk_approval_requests_review_digest_check",),
            )

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._approval_rows(connection), before_rows)
            self.assertEqual(
                self._owned_object_identities(
                    connection,
                    excluded=("cpk_approval_requests_review_digest_check",),
                ),
                before_objects,
            )
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

    def test_duplicate_rotation_identity_rejects_before_v15_mutation(self) -> None:
        connection = self._connection()
        try:
            self._prepare(14, connection)
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
            self._seed_current_rotation_approval(connection, subject)
            connection.execute(
                "DROP INDEX cpk_approval_requests_rotation_identity"
            )
            connection.execute(
                "INSERT INTO cpk_approval_requests ("
                "request_id, session_id, rotation_id, subject_kind, "
                "subject_payload, review_digest, requested_by, requested_at, "
                "required_scope, max_risk, destructive"
                ") SELECT "
                "'request-b', session_id, rotation_id, subject_kind, "
                "subject_payload, review_digest, requested_by, requested_at, "
                "required_scope, max_risk, destructive "
                "FROM cpk_approval_requests WHERE request_id = 'request-a'"
            )
            before = self._snapshot(connection)

            with self.assertRaises(postgres.SchemaMigrationError) as raised:
                postgres.install_postgres_schema(connection)

            self.assertEqual(str(raised.exception), _CATEGORICAL_ERROR)
            self.assertIsNone(raised.exception.__context__)
            self.assertIsNone(raised.exception.__cause__)
            self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def test_final_verifier_rejects_extra_index_key_or_include(self) -> None:
        definitions = (
            "ON cpk_approval_requests (rotation_id, request_id) "
            "WHERE rotation_id IS NOT NULL",
            "ON cpk_approval_requests (rotation_id) INCLUDE (request_id) "
            "WHERE rotation_id IS NOT NULL",
        )
        for definition in definitions:
            with self.subTest(definition=definition):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(15, connection)
                    connection.execute(
                        "DROP INDEX cpk_approval_requests_rotation_identity"
                    )
                    connection.execute(
                        "CREATE UNIQUE INDEX "
                        "cpk_approval_requests_rotation_identity " + definition
                    )

                    with self.assertRaises(postgres.SchemaMigrationError):
                        postgres.verify_postgres_schema(connection)
                finally:
                    connection.close()

    def test_legacy_zero_and_multiple_plan_subjects_converge_exactly(self) -> None:
        for plan_ids in ((), ("plan-a", "plan.b-2:edge")):
            with self.subTest(plan_ids=plan_ids):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    self._downgrade_subject_contract(connection)
                    self._seed_legacy_plan_approvals(connection, plan_ids)

                    postgres.install_postgres_schema(connection)

                    rows = self._approval_rows(connection)
                    self.assertEqual(len(rows), len(plan_ids))
                    for row, plan_id in zip(rows, plan_ids, strict=True):
                        subject = ActivityPlanApprovalSubject(plan_id)
                        self.assertEqual(
                            row[1:],
                            (
                                plan_id,
                                None,
                                subject.kind.value,
                                subject.descriptor(),
                                subject.review_digest,
                            ),
                        )
                finally:
                    connection.close()

    def test_current_subject_drift_matrix_rejects_before_mutation(self) -> None:
        mutations = (
            (
                "unknown-kind",
                "ALTER TABLE cpk_approval_requests DROP CONSTRAINT "
                "cpk_approval_requests_subject_kind_check; "
                "ALTER TABLE cpk_approval_requests DROP CONSTRAINT "
                "cpk_approval_requests_subject_identity_check; "
                "UPDATE cpk_approval_requests SET subject_kind = 'unknown'",
            ),
            (
                "identity-cross-product",
                "ALTER TABLE cpk_approval_requests DROP CONSTRAINT "
                "cpk_approval_requests_subject_identity_check; "
                "UPDATE cpk_approval_requests SET plan_id = 'plan-a'",
            ),
            (
                "payload-mismatch",
                "UPDATE cpk_approval_requests SET subject_payload = '{}'::jsonb",
            ),
            (
                "digest-mismatch",
                "UPDATE cpk_approval_requests SET review_digest = repeat('b', 64)",
            ),
            (
                "malformed-source",
                "UPDATE cpk_gateway_key_rotations SET issuer = 'invalid-é'",
            ),
            (
                "missing-source",
                "ALTER TABLE cpk_approval_requests DROP CONSTRAINT "
                "cpk_approval_requests_rotation_fk; "
                "DELETE FROM cpk_gateway_key_rotations",
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    subject = self._rotation_subject()
                    self._seed_current_rotation_approval(connection, subject)
                    if label == "identity-cross-product":
                        connection.execute(
                            "INSERT INTO cpk_activity_plans "
                            "(plan_id, session_id, base_graph_id, desired_graph_id, "
                            "status, created_at, payload) VALUES "
                            "('plan-a', 'session-a', 'graph-a', 'graph-b', "
                            "'planned', '2026-08-09T12:00:01Z', '{}'::jsonb)"
                        )
                    connection.execute(mutation)
                    self._assert_preflight_rejection(connection)
                finally:
                    connection.close()

    def test_every_rotation_source_scalar_rejects_invalid_boundary(self) -> None:
        mutations = (
            (
                "rotation_id",
                "ALTER TABLE cpk_approval_requests DROP CONSTRAINT "
                "cpk_approval_requests_rotation_fk; "
                "UPDATE cpk_gateway_key_rotations SET rotation_id = '-invalid'; "
                "UPDATE cpk_approval_requests SET rotation_id = '-invalid'",
            ),
            (
                "workspace_id",
                "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                "VALUES ('-invalid', 'Invalid', 'created'); "
                "UPDATE cpk_gateway_key_rotations SET workspace_id = '-invalid'",
            ),
            (
                "gateway_node_id",
                "UPDATE cpk_gateway_key_rotations "
                "SET gateway_node_id = '-invalid'",
            ),
            (
                "purpose",
                "ALTER TABLE cpk_gateway_key_rotations DROP CONSTRAINT "
                "cpk_gateway_key_rotations_purpose_check; "
                "UPDATE cpk_gateway_key_rotations SET purpose = 'unsupported'",
            ),
            (
                "issuer",
                "UPDATE cpk_gateway_key_rotations SET issuer = '-invalid'",
            ),
            (
                "old_key_id",
                "UPDATE cpk_gateway_key_rotations SET old_key_id = '-invalid'",
            ),
            (
                "lifetime-low",
                "ALTER TABLE cpk_gateway_key_rotations DROP CONSTRAINT "
                "cpk_gateway_key_rotations_lifetime_check; "
                "UPDATE cpk_gateway_key_rotations "
                "SET maximum_grant_lifetime_seconds = 0",
            ),
            (
                "lifetime-high",
                "ALTER TABLE cpk_gateway_key_rotations DROP CONSTRAINT "
                "cpk_gateway_key_rotations_lifetime_check; "
                "UPDATE cpk_gateway_key_rotations "
                "SET maximum_grant_lifetime_seconds = 301",
            ),
            (
                "skew-low",
                "ALTER TABLE cpk_gateway_key_rotations DROP CONSTRAINT "
                "cpk_gateway_key_rotations_skew_check; "
                "UPDATE cpk_gateway_key_rotations SET clock_skew_seconds = -1",
            ),
            (
                "skew-high",
                "ALTER TABLE cpk_gateway_key_rotations DROP CONSTRAINT "
                "cpk_gateway_key_rotations_skew_check; "
                "UPDATE cpk_gateway_key_rotations SET clock_skew_seconds = 61",
            ),
            (
                "intent-digest",
                "ALTER TABLE cpk_gateway_key_rotations DROP CONSTRAINT "
                "cpk_gateway_key_rotations_fingerprint_check; "
                "UPDATE cpk_gateway_key_rotations "
                "SET intent_fingerprint = 'invalid'",
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    self._seed_current_rotation_approval(
                        connection, self._rotation_subject()
                    )
                    connection.execute(mutation)
                    self._assert_preflight_rejection(connection)
                finally:
                    connection.close()

    def test_each_subject_column_drift_fails_final_verification(self) -> None:
        mutations = (
            ("plan_id", "SET NOT NULL"),
            ("rotation_id", "SET NOT NULL"),
            ("subject_kind", "DROP NOT NULL"),
            ("subject_payload", "DROP NOT NULL"),
            ("review_digest", "DROP NOT NULL"),
        )
        for column, mutation in mutations:
            with self.subTest(column=column):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(15, connection)
                    connection.execute(
                        f"ALTER TABLE cpk_approval_requests "
                        f"ALTER COLUMN {column} {mutation}"
                    )
                    with self.assertRaises(postgres.SchemaMigrationError):
                        postgres.verify_postgres_schema(connection)
                finally:
                    connection.close()

    def test_each_owned_constraint_absence_installs_exact_contract(self) -> None:
        for constraint, definition in _CONSTRAINT_DEFINITIONS.items():
            with self.subTest(constraint=constraint):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    connection.execute(
                        f"ALTER TABLE cpk_approval_requests "
                        f"DROP CONSTRAINT {constraint}"
                    )

                    for step in self._v15().steps:
                        connection.execute(step.sql)

                    observed = self._constraint_identity(connection, constraint)
                    self.assertTrue(observed[2])
                    self.assertIn(
                        re.sub(r"\s+", " ", definition).strip().split(" ")[0],
                        observed[1],
                    )
                    connection.execute(self._v15().steps[0].sql)
                finally:
                    connection.close()

    def test_each_owned_constraint_wrong_or_unvalidated_rejects(self) -> None:
        for constraint, definition in _CONSTRAINT_DEFINITIONS.items():
            wrong = (
                "FOREIGN KEY (rotation_id) REFERENCES "
                "cpk_operation_sessions(session_id)"
                if constraint == "cpk_approval_requests_rotation_fk"
                else "CHECK (true)"
            )
            for state, replacement in (
                ("wrong", wrong),
                ("unvalidated", definition + " NOT VALID"),
                ("wrong-type", "UNIQUE (rotation_id)"),
            ):
                with self.subTest(constraint=constraint, state=state):
                    self._reset_schema()
                    connection = self._connection()
                    try:
                        self._prepare(14, connection)
                        connection.execute(
                            f"ALTER TABLE cpk_approval_requests "
                            f"DROP CONSTRAINT {constraint}"
                        )
                        connection.execute(
                            f"ALTER TABLE cpk_approval_requests "
                            f"ADD CONSTRAINT {constraint} {replacement}"
                        )
                        self._assert_preflight_rejection(connection)
                    finally:
                        connection.close()

    def test_target_index_absent_exact_and_invalid_preflight_states(self) -> None:
        definitions = (
            "ON cpk_approval_requests (rotation_id)",
            "ON cpk_approval_requests (request_id) WHERE rotation_id IS NOT NULL",
            "ON cpk_approval_requests (rotation_id) WHERE rotation_id IS NULL",
        )
        for definition in definitions:
            with self.subTest(definition=definition):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    connection.execute(
                        "DROP INDEX cpk_approval_requests_rotation_identity"
                    )
                    connection.execute(
                        "CREATE INDEX cpk_approval_requests_rotation_identity "
                        + definition
                    )
                    self._assert_preflight_rejection(connection)
                finally:
                    connection.close()

        self._reset_schema()
        connection = self._connection()
        try:
            self._prepare(14, connection)
            exact_oid = self._index_oid(
                connection, "cpk_approval_requests_rotation_identity"
            )
            postgres.install_postgres_schema(connection)
            self.assertEqual(
                self._index_oid(
                    connection, "cpk_approval_requests_rotation_identity"
                ),
                exact_oid,
            )
        finally:
            connection.close()

    def test_final_verifier_rejects_each_owned_constraint_drift(self) -> None:
        for constraint, definition in _CONSTRAINT_DEFINITIONS.items():
            for state in ("missing", "unvalidated"):
                with self.subTest(constraint=constraint, state=state):
                    self._reset_schema()
                    connection = self._connection()
                    try:
                        self._prepare(15, connection)
                        connection.execute(
                            f"ALTER TABLE cpk_approval_requests "
                            f"DROP CONSTRAINT {constraint}"
                        )
                        if state == "unvalidated":
                            connection.execute(
                                f"ALTER TABLE cpk_approval_requests "
                                f"ADD CONSTRAINT {constraint} "
                                f"{definition} NOT VALID"
                            )
                        with self.assertRaises(postgres.SchemaMigrationError):
                            postgres.verify_postgres_schema(connection)
                    finally:
                        connection.close()

    def test_cross_schema_lookalikes_and_equivalent_index_are_preserved(self) -> None:
        connection = self._connection()
        other = f"{self.schema}_other"
        try:
            self._prepare(14, connection)
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(other)))
            connection.execute(
                sql.SQL(
                    "CREATE TABLE {}.lookalike_subjects "
                    "(review_digest text, rotation_id text)"
                ).format(sql.Identifier(other))
            )
            connection.execute(
                sql.SQL(
                    "ALTER TABLE {}.lookalike_subjects ADD CONSTRAINT "
                    "cpk_approval_requests_review_digest_check "
                    "CHECK (review_digest <> '')"
                ).format(sql.Identifier(other))
            )
            connection.execute(
                sql.SQL(
                    "CREATE INDEX cpk_approval_requests_rotation_identity "
                    "ON {}.lookalike_subjects (rotation_id)"
                ).format(sql.Identifier(other))
            )
            lookalikes = self._other_schema_object_identities(connection, other)
            connection.execute(
                "ALTER TABLE cpk_approval_requests DROP CONSTRAINT "
                "cpk_approval_requests_review_digest_check"
            )
            connection.execute("DROP INDEX cpk_approval_requests_rotation_identity")
            connection.execute(
                "CREATE UNIQUE INDEX equivalent_rotation_identity "
                "ON cpk_approval_requests (rotation_id) "
                "WHERE rotation_id IS NOT NULL"
            )
            equivalent_oid = self._index_oid(connection, "equivalent_rotation_identity")

            for step in self._v15().steps:
                connection.execute(step.sql)

            self.assertEqual(
                self._other_schema_object_identities(connection, other), lookalikes
            )
            self.assertEqual(
                self._index_oid(connection, "equivalent_rotation_identity"),
                equivalent_oid,
            )
            self.assertIsNotNone(
                self._index_oid(connection, "cpk_approval_requests_rotation_identity")
            )
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

    def test_each_v15_phase_and_ledger_failure_roll_back_v14_truth(self) -> None:
        targets = tuple(step.sql for step in self._v15().steps) + ("ledger",)
        for target in targets:
            with self.subTest(target=target if target == "ledger" else targets.index(target)):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(14, connection)
                    self._downgrade_subject_contract(connection)
                    self._seed_legacy_plan_approval(connection, plan_id="plan-a")
                    before = self._snapshot(connection)

                    class FailingConnection:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=None):
                            if query == target:
                                raise RuntimeError("private driver material")
                            if (
                                target == "ledger"
                                and "INSERT INTO cpk_schema_migrations" in query
                                and params is not None
                                and params[0] == 15
                            ):
                                raise RuntimeError("private ledger material")
                            return connection.execute(query, params)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(FailingConnection())
                    self.assertEqual(
                        str(raised.exception), "schema migration application failed"
                    )
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_caller_savepoint_lock_and_rollback_cover_v15(self) -> None:
        setup = self._connection()
        try:
            self._prepare(14, setup)
            self._downgrade_subject_contract(setup)
            self._seed_legacy_plan_approval(setup, plan_id="plan-a")
            before = self._snapshot(setup)
        finally:
            setup.close()

        caller = self._connection(autocommit=False)
        observer = self._connection()
        try:
            postgres.install_postgres_schema(caller)
            observer.execute("SET lock_timeout TO '250ms'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                observer.execute("SELECT count(*) FROM cpk_approval_requests")
            caller.rollback()
            self.assertEqual(self._snapshot(observer), before)
        finally:
            caller.rollback()
            caller.close()
            observer.close()

    def test_final_verifier_reads_bounded_structural_and_scalar_results(self) -> None:
        verifier = getattr(
            migration_inspection,
            "_verify_approval_subject_evidence_contract",
        )
        columns = list(
            getattr(migration_inspection, "_APPROVAL_SUBJECT_EVIDENCE_COLUMNS")
        )
        constraints = list(
            getattr(migration_inspection, "_APPROVAL_SUBJECT_EVIDENCE_CONSTRAINTS")
        )
        index = [
            (
                "cpk_approval_requests_rotation_identity",
                "i",
                "btree",
                True,
                True,
                True,
                True,
                1,
                1,
                True,
                True,
            )
        ]

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class ScriptedConnection:
            def __init__(self):
                self.rows = iter((columns, constraints, index, [(True,)]))
                self.queries = []

            def execute(self, query, params=()):
                self.queries.append((re.sub(r"\s+", " ", query).strip(), params))
                return Cursor(next(self.rows))

        connection = ScriptedConnection()
        verifier(connection)

        self.assertEqual(len(connection.queries), 4)
        self.assertIn("LIMIT 6", connection.queries[0][0])
        self.assertIn("LIMIT 5", connection.queries[1][0])
        self.assertIn("LIMIT 2", connection.queries[2][0])
        self.assertIn("SELECT NOT EXISTS", connection.queries[3][0])
        self.assertNotIn("SELECT subject_payload", connection.queries[3][0])

    def test_migration_first_blocks_package_rotation_read_before_approval(self) -> None:
        setup = self._connection()
        try:
            self._prepare(14, setup)
            self._seed_current_rotation_approval(setup, self._rotation_subject())
        finally:
            setup.close()

        migration = self._connection(autocommit=False)
        service = self._connection(autocommit=False)
        rotation_acquired = threading.Event()
        approval_read = threading.Event()
        try:
            migration.execute(self._v15().steps[0].sql)

            def package_sequence() -> None:
                GatewayKeyRotationStore(service).get_for_update("rotation-a")
                rotation_acquired.set()
                PostgresActivityHistoryStore(service).approval_request_for_rotation(
                    "rotation-a"
                )
                approval_read.set()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(package_sequence)
                time.sleep(0.25)
                self.assertFalse(rotation_acquired.is_set())
                self.assertFalse(approval_read.is_set())
                migration.rollback()
                future.result(timeout=10)
            self.assertTrue(rotation_acquired.is_set())
            self.assertTrue(approval_read.is_set())
        finally:
            migration.rollback()
            service.rollback()
            migration.close()
            service.close()

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
            GatewayKeyRotationStore(service).get_for_update("rotation-a")
            PostgresActivityHistoryStore(service).approval_request_for_rotation(
                "rotation-a"
            )

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
        ApprovalSubjectEvidenceMigrationTests._seed_legacy_plan_approvals(
            connection, (plan_id,)
        )

    @staticmethod
    def _seed_legacy_plan_approvals(connection, plan_ids: tuple[str, ...]) -> None:
        if not plan_ids:
            return
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
        for index, plan_id in enumerate(plan_ids):
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
                VALUES (%s, 'session-a', %s, 'operator-a',
                        '2026-08-09T12:00:02Z', 'plan:approve', 'low', false)
                """,
                (
                    "request-a"
                    if len(plan_ids) == 1
                    else f"request-{index:03d}",
                    plan_id,
                ),
            )

    @staticmethod
    def _seed_current_plan_approval(connection, plan_id: str) -> None:
        subject = ActivityPlanApprovalSubject(plan_id)
        connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        connection.execute(
            "INSERT INTO cpk_operation_sessions "
            "(session_id, workspace_id, actor_id, title, status, created_at) "
            "VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', "
            "'open', '2026-08-09T12:00:00Z')"
        )
        connection.execute(
            "INSERT INTO cpk_activity_plans "
            "(plan_id, session_id, base_graph_id, desired_graph_id, status, "
            "created_at, payload) VALUES (%s, 'session-a', 'graph-a', "
            "'graph-b', 'planned', '2026-08-09T12:00:01Z', '{}'::jsonb)",
            (plan_id,),
        )
        connection.execute(
            "INSERT INTO cpk_approval_requests "
            "(request_id, session_id, plan_id, subject_kind, subject_payload, "
            "review_digest, requested_by, requested_at, required_scope, "
            "max_risk, destructive) VALUES ('request-a', 'session-a', %s, "
            "'activity-plan', %s, %s, 'operator-a', "
            "'2026-08-09T12:00:02Z', 'plan:approve', 'low', false)",
            (plan_id, Jsonb(subject.descriptor()), subject.review_digest),
        )

    def _connection(self, *, autocommit: bool = True):
        connection = psycopg.connect(self.database_url, autocommit=autocommit)
        connection.execute(f'SET search_path TO "{self.schema}"')
        if not autocommit:
            connection.commit()
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
    def _rotation_subject() -> GatewayKeyRotationApprovalSubject:
        return GatewayKeyRotationApprovalSubject(
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

    @staticmethod
    def _constraint_identity(connection, constraint):
        rows = connection.execute(
            """
            SELECT constraints.oid,
                   pg_get_constraintdef(constraints.oid, false),
                   constraints.convalidated
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'cpk_approval_requests'
              AND constraints.conname = %s
            ORDER BY constraints.oid
            """,
            (constraint,),
        ).fetchall()
        if len(rows) != 1:
            raise AssertionError("expected one owned approval constraint")
        return rows[0]

    @staticmethod
    def _index_oid(connection, name):
        row = connection.execute(
            "SELECT indexes.oid FROM pg_class AS indexes "
            "JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace "
            "WHERE namespace.nspname = current_schema() AND indexes.relname = %s",
            (name,),
        ).fetchone()
        return None if row is None else row[0]

    @staticmethod
    def _other_schema_object_identities(connection, schema):
        return tuple(
            connection.execute(
                """
                SELECT 'constraint', constraints.oid
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = %s
                  AND constraints.conname =
                    'cpk_approval_requests_review_digest_check'
                UNION ALL
                SELECT 'index', indexes.oid
                FROM pg_class AS indexes
                JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace
                WHERE namespace.nspname = %s
                  AND indexes.relname =
                    'cpk_approval_requests_rotation_identity'
                ORDER BY 1, 2
                """,
                (schema, schema),
            ).fetchall()
        )

    @staticmethod
    def _owned_object_identities(connection, *, excluded=()):
        return tuple(
            row
            for row in connection.execute(
                """
                SELECT 'constraint', constraints.conname, constraints.oid
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_approval_requests'
                UNION ALL
                SELECT 'index', indexes.relname, indexes.oid
                FROM pg_class AS indexes
                JOIN pg_index AS index_contract
                  ON index_contract.indexrelid = indexes.oid
                JOIN pg_class AS relation ON relation.oid = index_contract.indrelid
                JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_approval_requests'
                ORDER BY 1, 2, 3
                """
            ).fetchall()
            if row[1] not in excluded
        )

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
        objects = tuple(
            connection.execute(
                """
                SELECT 'constraint', constraints.conname, constraints.oid,
                       pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_approval_requests'
                UNION ALL
                SELECT 'index', indexes.relname, indexes.oid,
                       pg_get_indexdef(indexes.oid)
                FROM pg_class AS indexes
                JOIN pg_index AS index_contract
                  ON index_contract.indexrelid = indexes.oid
                JOIN pg_class AS relation ON relation.oid = index_contract.indrelid
                JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'cpk_approval_requests'
                ORDER BY 1, 2, 3
                """
            ).fetchall()
        )
        return self._history(connection), columns, rows, objects

    def _assert_preflight_rejection(self, connection) -> None:
        before = self._snapshot(connection)
        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.install_postgres_schema(connection)
        self.assertEqual(str(raised.exception), _CATEGORICAL_ERROR)
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(self._snapshot(connection), before)

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
