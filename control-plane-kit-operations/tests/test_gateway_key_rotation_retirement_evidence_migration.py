from __future__ import annotations

import os
import re
import unittest
import uuid

import psycopg

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module


_V13_HISTORY = (
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
)
_V14_IDENTITY = (14, "gateway-key-rotation-retirement-evidence")
_V15_IDENTITY = (15, "approval-subject-evidence")
_V16_IDENTITY = (16, "approval-scope-contracts")
_CURRENT_IDENTITY = (17, "graph-lineage-compatibility")
_TABLE = "cpk_gateway_key_rotations"
_CONSTRAINT = "cpk_gateway_key_rotations_retirement_check"
_CATEGORICAL_ERROR = "gateway key rotation retirement evidence is not accepted"
_LEGACY_DEFINITION = (
    "CHECK (((old_key_retired_at IS NULL) = "
    "(old_secret_revoked_at IS NULL)))"
)
_CURRENT_DEFINITION = (
    "CHECK (((old_secret_revoked_at IS NULL) OR "
    "(old_key_retired_at IS NOT NULL)))"
)
_COLUMN_CONTRACT = (
    ("old_key_retired_at", "timestamp with time zone", 6, "YES", True),
    ("old_secret_revoked_at", "timestamp with time zone", 6, "YES", True),
)
_RETIRED = "2026-08-09T12:02:00.000001Z"
_REVOKED = "2026-08-09T12:01:00Z"
_INITIAL_STATUS_DEFINITION = (
    "CHECK ((status = ANY (ARRAY['requested'::text, "
    "'awaiting-approval'::text, 'approved'::text, 'key-generated'::text, "
    "'overlap-deploying'::text, 'overlap-ready'::text, "
    "'new-key-active'::text, 'draining-old-grants'::text, "
    "'retirement-deploying'::text, 'completed'::text, 'blocked'::text, "
    "'rejected'::text])))"
)


class GatewayKeyRotationRetirementEvidenceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"gkret_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_appends_exact_two_sql_step_v14_program(self) -> None:
        self._v14()
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(registry.target_version, 17)
        self.assertEqual(
            tuple((migration.version, migration.name) for migration in registry.migrations),
            (
                *_V13_HISTORY,
                _V14_IDENTITY,
                _V15_IDENTITY,
                _V16_IDENTITY,
                _CURRENT_IDENTITY,
            ),
        )
        migration = self._v14()
        self.assertIsNone(migration.sql)
        self.assertEqual(len(migration.steps), 2)
        self.assertTrue(
            all(type(step) is postgres.SqlMigrationStep for step in migration.steps)
        )
        preflight = migration.steps[0].sql
        self.assertEqual(
            preflight.count(
                "LOCK TABLE cpk_gateway_key_rotations IN ACCESS EXCLUSIVE MODE;"
            ),
            1,
        )
        self.assertNotIn("cpk_gateway_key_rotation_transitions", preflight)
        self.assertNotIn("ALTER TABLE", preflight)
        self.assertIn("information_schema.columns", preflight)
        self.assertIn("datetime_precision", preflight)
        self.assertIn("column_default IS NULL", preflight)
        self.assertIn("count(DISTINCT constraints.conname)", preflight)
        self.assertIn(_CATEGORICAL_ERROR, preflight)
        self.assertRegex(
            preflight,
            r"IF constraint_count > 1[\s\S]*?THEN\s+"
            r"RAISE EXCEPTION USING\s+ERRCODE = 'P1110',\s+"
            r"MESSAGE = 'gateway key rotation retirement evidence is not accepted';",
        )
        self.assertNotRegex(
            preflight,
            r"SELECT\s+(?:[^;]*\.)?(?:old_key_retired_at|old_secret_revoked_at)\s+FROM",
        )
        self.assertFalse(
            hasattr(schema_module, "_upgrade_gateway_key_rotation_retirement_constraint")
        )
        self.assertEqual(
            tuple(step.checksum_sha256 for step in migration.steps),
            (
                "41490a76f354e4e24f10705a84cbbb6822852c105d04ad7b060195c8f9e29d96",
                "3c56c82ee3b752ad19117b0d8f56425dd3922f325bc4fb1e224ba2923b16bd81",
            ),
        )
        pinned = getattr(schema_module, "_POSTGRES_SCHEMA_V14_SHA256", None)
        self.assertEqual(
            pinned,
            "3cb2bade92c299c0d397f9d3462c526d768233fc064df51d6db9b43c3089ea90",
        )
        self.assertEqual(pinned, migration.checksum_sha256)

    def test_all_four_row_shapes_are_admitted_or_rejected_without_rewrite(self) -> None:
        self._v14()
        cases = (
            (None, None, True),
            (_RETIRED, None, True),
            (_RETIRED, _REVOKED, True),
            (None, _REVOKED, False),
        )
        for retired, revoked, accepted in cases:
            with self.subTest(retired=retired, revoked=revoked):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(13, connection)
                    self._seed_row(connection)
                    connection.execute(
                        f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_CONSTRAINT}"
                    )
                    connection.execute(
                        f"UPDATE {_TABLE} SET old_key_retired_at = %s, "
                        "old_secret_revoked_at = %s",
                        (retired, revoked),
                    )
                    before = self._snapshot(connection)

                    if accepted:
                        postgres.install_postgres_schema(connection)
                        self.assertEqual(self._row_evidence(connection), before[1])
                        self.assertEqual(self._definition(connection), _CURRENT_DEFINITION)
                        self.assertEqual(
                            self._history(connection)[-1][:2],
                            _CURRENT_IDENTITY,
                        )
                    else:
                        self._assert_preflight_rejection(connection)
                        self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_absent_legacy_and_current_form_closed_constraint_algebra(self) -> None:
        self._v14()
        for state in ("absent", "legacy", "current"):
            with self.subTest(state=state):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(13, connection)
                    self._seed_row(connection)
                    if state == "absent":
                        connection.execute(
                            f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_CONSTRAINT}"
                        )
                        before_oid = None
                    elif state == "legacy":
                        self._replace_constraint(connection, _LEGACY_DEFINITION)
                        before_oid = self._constraint_identity(connection)[0]
                    else:
                        before_oid = self._constraint_identity(connection)[0]
                    before_rows = self._row_evidence(connection)

                    postgres.install_postgres_schema(connection)

                    after = self._constraint_identity(connection)
                    self.assertEqual(after[1], _CURRENT_DEFINITION)
                    self.assertEqual(self._row_evidence(connection), before_rows)
                    if state == "current":
                        self.assertEqual(after[0], before_oid)
                    elif state == "legacy":
                        self.assertNotEqual(after[0], before_oid)
                    snapshot = self._snapshot(connection)
                    postgres.install_postgres_schema(connection)
                    self.assertEqual(self._snapshot(connection), snapshot)
                finally:
                    connection.close()

    def test_owned_invalid_catalog_truth_fails_before_convergence(self) -> None:
        self._v14()
        cases = (
            ("wrong-definition", "CHECK (old_secret_revoked_at IS NULL)"),
            ("unvalidated", "CHECK (old_secret_revoked_at IS NULL) NOT VALID"),
            ("wrong-type", "UNIQUE (old_key_retired_at, old_secret_revoked_at)"),
        )
        for label, definition in cases:
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(13, connection)
                    self._seed_row(connection)
                    self._replace_constraint(connection, definition)
                    self._assert_preflight_rejection(connection)
                finally:
                    connection.close()

    def test_other_relation_and_schema_lookalikes_are_ignored_and_preserved(self) -> None:
        self._v14()
        connection = self._connection()
        try:
            self._prepare(13, connection)
            self._seed_row(connection)
            connection.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_CONSTRAINT}")
            connection.execute(
                f"ALTER TABLE cpk_workspaces ADD CONSTRAINT {_CONSTRAINT} "
                "CHECK (name IS NOT NULL)"
            )
            connection.execute(f'CREATE SCHEMA "{self.schema}_other"')
            connection.execute(
                f'CREATE TABLE "{self.schema}_other".shadow (value text)'
            )
            connection.execute(
                f'ALTER TABLE "{self.schema}_other".shadow '
                f"ADD CONSTRAINT {_CONSTRAINT} CHECK (value IS NOT NULL)"
            )
            before = self._lookalikes(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._definition(connection), _CURRENT_DEFINITION)
            self.assertEqual(self._lookalikes(connection), before)
        finally:
            connection.close()

    def test_each_two_column_fact_drift_fails_in_preflight_without_mutation(self) -> None:
        self._v14()
        for column in ("old_key_retired_at", "old_secret_revoked_at"):
            mutations = (
                ("type", f"TYPE text USING {column}::text"),
                ("precision", f"TYPE timestamptz(5) USING {column}::timestamptz(5)"),
                ("nullability", "SET NOT NULL"),
                ("default", "SET DEFAULT clock_timestamp()"),
            )
            for fact, mutation in mutations:
                with self.subTest(column=column, fact=fact):
                    self._reset_schema()
                    connection = self._connection()
                    try:
                        self._prepare(13, connection)
                        self._seed_row(connection, retired=_RETIRED, revoked=_REVOKED)
                        connection.execute(
                            f"ALTER TABLE {_TABLE} ALTER COLUMN {column} {mutation}"
                        )
                        self._assert_preflight_rejection(connection)
                    finally:
                        connection.close()

    def test_each_v14_phase_failure_and_composed_suffix_roll_back_exactly(self) -> None:
        self._v14()
        for phase in (0, 1):
            with self.subTest(predecessor=13, phase=phase):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(13, connection)
                    self._seed_row(connection)
                    self._replace_constraint(connection, _LEGACY_DEFINITION)
                    before = self._snapshot(connection)
                    step = self._v14().steps[phase].sql

                    class FailingConnection:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=None):
                            if query == step:
                                raise RuntimeError("private driver material")
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

        for predecessor in (12, 13):
            with self.subTest(post_convergence_predecessor=predecessor):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(predecessor, connection)
                    self._seed_row(connection)
                    if predecessor == 12:
                        self._replace_status_constraint(
                            connection, _INITIAL_STATUS_DEFINITION
                        )
                    self._replace_constraint(connection, _LEGACY_DEFINITION)
                    before = self._snapshot(connection)
                    v13_steps = tuple(
                        step.sql
                        for step in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[12].steps
                    )
                    v14_steps = tuple(step.sql for step in self._v14().steps)
                    submitted_v13 = []
                    submitted_v14 = []

                    class PostConvergenceFailure:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=None):
                            if query in v13_steps:
                                submitted_v13.append(v13_steps.index(query))
                            if query in v14_steps:
                                submitted_v14.append(v14_steps.index(query))
                            if (
                                "INSERT INTO cpk_schema_migrations" in query
                                and params is not None
                                and params[0] == 14
                            ):
                                raise RuntimeError("private post-convergence material")
                            return connection.execute(query, params)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(PostConvergenceFailure())
                    self.assertEqual(
                        str(raised.exception), "schema migration application failed"
                    )
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(
                        submitted_v13, [0, 1, 2] if predecessor == 12 else []
                    )
                    self.assertEqual(submitted_v14, [0, 1])
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_rotation_lock_and_caller_rollback_cover_v14(self) -> None:
        self._v14()
        setup = self._connection()
        try:
            self._prepare(13, setup)
            self._seed_row(setup)
            self._replace_constraint(setup, _LEGACY_DEFINITION)
            before = self._snapshot(setup)
        finally:
            setup.close()

        caller = self._connection(autocommit=False)
        observer = self._connection()
        try:
            postgres.install_postgres_schema(caller)
            observer.execute("SET lock_timeout TO '250ms'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                observer.execute(f"SELECT count(*) FROM {_TABLE}")
            observer.execute("SELECT count(*) FROM cpk_gateway_key_rotation_transitions")
            caller.rollback()
            self.assertEqual(self._snapshot(observer), before)
        finally:
            caller.rollback()
            caller.close()
            observer.close()

    def test_final_verifier_is_exact_bounded_and_rejects_drift(self) -> None:
        self._v14()
        verifier = getattr(
            migration_inspection,
            "_verify_gateway_key_rotation_retirement_evidence_contract",
            None,
        )
        self.assertTrue(callable(verifier))

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class ScriptedConnection:
            def __init__(self, scripts):
                self.scripts = iter(scripts)
                self.queries = []

            def execute(self, query, params=None):
                self.queries.append((re.sub(r"\s+", " ", query).strip(), params))
                return Cursor(next(self.scripts))

        exact = ScriptedConnection(
            [list(_COLUMN_CONTRACT), [(_CONSTRAINT, "c", True, True)]]
        )
        verifier(exact)
        self.assertEqual(len(exact.queries), 2)
        self.assertIn("LIMIT 3", exact.queries[0][0])
        self.assertIn("LIMIT 2", exact.queries[1][0])
        self.assertNotIn("SELECT pg_get_constraintdef", exact.queries[1][0])
        self.assertEqual(exact.queries[1][1], (_CURRENT_DEFINITION,))

        valid_constraint = (_CONSTRAINT, "c", True, True)
        constraint_cases = (
            ("missing", []),
            ("duplicate", [valid_constraint, valid_constraint]),
            ("wrong-type", [(_CONSTRAINT, "u", True, True)]),
            ("unvalidated", [(_CONSTRAINT, "c", False, True)]),
            ("wrong-definition", [(_CONSTRAINT, "c", True, False)]),
        )
        for label, constraints in constraint_cases:
            with self.subTest(constraint=label):
                with self.assertRaises(postgres.SchemaMigrationError):
                    verifier(ScriptedConnection([list(_COLUMN_CONTRACT), constraints]))

        for missing_index in range(2):
            with self.subTest(missing_column=missing_index):
                columns = list(_COLUMN_CONTRACT)
                columns.pop(missing_index)
                with self.assertRaises(postgres.SchemaMigrationError):
                    verifier(ScriptedConnection([columns, [valid_constraint]]))

        for row_index in range(2):
            for fact_index, bad in (
                (1, "text"),
                (2, 5),
                (3, "NO"),
                (4, False),
            ):
                with self.subTest(column=row_index, fact=fact_index):
                    columns = [list(row) for row in _COLUMN_CONTRACT]
                    columns[row_index][fact_index] = bad
                    with self.assertRaises(postgres.SchemaMigrationError):
                        verifier(
                            ScriptedConnection(
                                [[tuple(row) for row in columns], [valid_constraint]]
                            )
                        )

        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            self._replace_constraint(connection, _LEGACY_DEFINITION)
            drifted = self._snapshot(connection)
            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.install_postgres_schema(connection)
            self.assertEqual(self._snapshot(connection), drifted)
        finally:
            connection.close()

    def _assert_preflight_rejection(self, connection) -> None:
        before = self._snapshot(connection)
        steps = tuple(step.sql for step in self._v14().steps)
        submitted = []

        class RecordingConnection:
            @property
            def autocommit(self):
                return connection.autocommit

            def transaction(self):
                return connection.transaction()

            def execute(self, query, params=None):
                if query in steps:
                    submitted.append(steps.index(query))
                return connection.execute(query, params)

        with self.assertRaises(postgres.SchemaMigrationError) as raised:
            postgres.install_postgres_schema(RecordingConnection())
        self._assert_bounded_error(raised.exception)
        self.assertEqual(submitted, [0])
        self.assertEqual(self._snapshot(connection), before)

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

    def _seed_row(self, connection, *, retired=None, revoked=None) -> None:
        connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        connection.execute(
            f"""
            INSERT INTO {_TABLE} (
              rotation_id, workspace_id, gateway_node_id, purpose, issuer,
              old_key_id, new_secret_reference, key_generation_correlation,
              maximum_grant_lifetime_seconds, clock_skew_seconds,
              correlation_id, requested_by, requested_at, intent_fingerprint,
              status, version, generation_provider_registration_id,
              generation_action_digest, old_key_retired_at,
              old_secret_revoked_at
            ) VALUES (
              'rotation-a', 'workspace-a', 'gateway-a', %s, 'cpk-server',
              'gateway-key-a', 'secret://workspace-secrets/keys/gateway-key-b',
              'generate-gateway-key-b', 120, 10, 'rotation-a', 'operator-a',
              '2026-08-09T12:00:00Z', %s, 'requested', 1, %s, %s, %s, %s
            )
            """,
            (
                DelegationKeyPurpose.GATEWAY_PROBE.value,
                "a" * 64,
                "provider.registration:a-1",
                "c" * 64,
                retired,
                revoked,
            ),
        )

    @staticmethod
    def _replace_constraint(connection, definition: str) -> None:
        connection.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_CONSTRAINT}")
        connection.execute(
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} {definition}"
        )

    @staticmethod
    def _replace_status_constraint(connection, definition: str) -> None:
        constraint = "cpk_gateway_key_rotations_status_check"
        connection.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {constraint}")
        connection.execute(
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT {constraint} {definition}"
        )

    def _connection(self, *, autocommit: bool = True):
        connection = psycopg.connect(self.database_url, autocommit=autocommit)
        connection.execute(f'SET search_path TO "{self.schema}"')
        return connection

    def _reset_schema(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    @staticmethod
    def _history(connection):
        return tuple(
            connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    @staticmethod
    def _row_evidence(connection):
        return tuple(
            connection.execute(
                f"SELECT ctid::text, rotation_id, status, version, "
                "generation_provider_registration_id, generation_action_digest, "
                "old_key_retired_at, old_secret_revoked_at "
                f"FROM {_TABLE} ORDER BY rotation_id"
            ).fetchall()
        )

    @staticmethod
    def _constraint_identity(connection):
        rows = connection.execute(
            """
            SELECT constraints.oid,
                   pg_get_constraintdef(constraints.oid, false),
                   constraints.contype::text, constraints.convalidated
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = %s AND constraints.conname = %s
            ORDER BY constraints.oid
            """,
            (_TABLE, _CONSTRAINT),
        ).fetchall()
        if len(rows) != 1:
            raise AssertionError("expected one owned retirement constraint")
        return rows[0]

    def _definition(self, connection):
        return self._constraint_identity(connection)[1]

    def _lookalikes(self, connection):
        return tuple(
            connection.execute(
                """
                SELECT namespace.nspname, relation.relname, constraints.oid,
                       pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE constraints.conname = %s
                  AND NOT (namespace.nspname = current_schema()
                           AND relation.relname = %s)
                ORDER BY namespace.nspname, relation.relname, constraints.oid
                """,
                (_CONSTRAINT, _TABLE),
            ).fetchall()
        )

    def _snapshot(self, connection):
        objects = tuple(
            connection.execute(
                """
                SELECT relation.relname, 'constraint', constraints.conname,
                       constraints.oid,
                       pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname IN (
                    'cpk_gateway_key_rotations',
                    'cpk_gateway_key_rotation_transitions'
                  )
                UNION ALL
                SELECT relation.relname, 'index', indexes.relname, indexes.oid,
                       pg_get_indexdef(indexes.oid)
                FROM pg_class AS indexes
                JOIN pg_index AS index_contract
                  ON index_contract.indexrelid = indexes.oid
                JOIN pg_class AS relation
                  ON relation.oid = index_contract.indrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = indexes.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname IN (
                    'cpk_gateway_key_rotations',
                    'cpk_gateway_key_rotation_transitions'
                  )
                  AND indexes.relkind = 'i'
                ORDER BY 1, 2, 3, 4
                """
            ).fetchall()
        )
        return self._history(connection), self._row_evidence(connection), objects

    @staticmethod
    def _assert_bounded_error(error: Exception) -> None:
        message = str(error)
        if message != _CATEGORICAL_ERROR:
            raise AssertionError(f"unexpected bounded category: {message!r}")
        if len(message) > 256:
            raise AssertionError("migration error is not bounded")
        for excluded in (
            "rotation-a",
            "workspace-a",
            "gateway-a",
            "secret://",
            "provider.registration",
            _CONSTRAINT,
            "SELECT",
            "ALTER TABLE",
            "postgresql://",
        ):
            if excluded in message:
                raise AssertionError("migration error contains forbidden material")
        if error.__context__ is not None or error.__cause__ is not None:
            raise AssertionError("migration error retains provider context")

    @staticmethod
    def _v14():
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[13]
        if (migration.version, migration.name) != _V14_IDENTITY:
            raise AssertionError("V14 retirement-evidence migration is missing")
        return migration


if __name__ == "__main__":
    unittest.main()
