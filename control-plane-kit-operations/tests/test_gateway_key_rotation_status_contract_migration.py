from __future__ import annotations

import os
import re
import unittest
import uuid

import psycopg

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationStatus,
)
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module


_V12_HISTORY = (
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
)
_V13_IDENTITY = (13, "gateway-key-rotation-status-contracts")
_CATEGORICAL_ERROR = "gateway key rotation status contract is not accepted"
_ROTATIONS = "cpk_gateway_key_rotations"
_TRANSITIONS = "cpk_gateway_key_rotation_transitions"
_ROTATION_CONSTRAINT = "cpk_gateway_key_rotations_status_check"
_FROM_CONSTRAINT = "cpk_gateway_key_rotation_transitions_from_status_check"
_TO_CONSTRAINT = "cpk_gateway_key_rotation_transitions_to_status_check"
_TARGETS = (
    (_ROTATIONS, "status", _ROTATION_CONSTRAINT),
    (_TRANSITIONS, "from_status", _FROM_CONSTRAINT),
    (_TRANSITIONS, "to_status", _TO_CONSTRAINT),
)

_INITIAL_STATUSES = (
    "requested",
    "awaiting-approval",
    "approved",
    "key-generated",
    "overlap-deploying",
    "overlap-ready",
    "new-key-active",
    "draining-old-grants",
    "retirement-deploying",
    "completed",
    "blocked",
    "rejected",
)
_GENERATION_STATUSES = (
    "requested",
    "awaiting-approval",
    "approved",
    "generation-prepared",
    "key-generated",
    "overlap-deploying",
    "overlap-ready",
    "new-key-active",
    "draining-old-grants",
    "retirement-deploying",
    "completed",
    "blocked",
    "rejected",
)
_RETIREMENT_READY_STATUSES = (
    "requested",
    "awaiting-approval",
    "approved",
    "generation-prepared",
    "key-generated",
    "overlap-deploying",
    "overlap-ready",
    "new-key-active",
    "draining-old-grants",
    "retirement-deploying",
    "retirement-ready",
    "completed",
    "blocked",
    "rejected",
)
_CURRENT_STATUSES = (
    "requested",
    "awaiting-approval",
    "approved",
    "generation-prepared",
    "key-generated",
    "overlap-deploying",
    "overlap-ready",
    "new-key-active",
    "draining-old-grants",
    "retirement-deploying",
    "retirement-ready",
    "old-key-retired",
    "revocation-prepared",
    "completed",
    "blocked",
    "rejected",
)
_SHIPPED_VOCABULARIES = (
    ("initial", _INITIAL_STATUSES),
    ("generation", _GENERATION_STATUSES),
    ("retirement-ready", _RETIREMENT_READY_STATUSES),
    ("current", _CURRENT_STATUSES),
)


class GatewayKeyRotationStatusContractMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"gateway_rotation_status_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_appends_exact_three_sql_step_v13_program(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(
            tuple(status.value for status in GatewayKeyRotationStatus),
            _CURRENT_STATUSES,
        )
        self.assertEqual(registry.target_version, 13)
        self.assertEqual(
            tuple((migration.version, migration.name) for migration in registry.migrations),
            (*_V12_HISTORY, _V13_IDENTITY),
        )
        migration = self._v13()
        self.assertIsNone(migration.sql)
        self.assertEqual(len(migration.steps), 3)
        self.assertTrue(
            all(type(step) is postgres.SqlMigrationStep for step in migration.steps)
        )
        preflight = migration.steps[0].sql
        rotation_lock = (
            "LOCK TABLE cpk_gateway_key_rotations IN ACCESS EXCLUSIVE MODE;"
        )
        transition_lock = (
            "LOCK TABLE cpk_gateway_key_rotation_transitions "
            "IN ACCESS EXCLUSIVE MODE;"
        )
        self.assertLess(preflight.index(rotation_lock), preflight.index(transition_lock))
        self.assertIn("count(DISTINCT constraints.conname)", preflight)
        self.assertIn("pg_get_constraintdef", preflight)
        self.assertNotRegex(
            preflight,
            r"SELECT\s+(?:[^;]*\.)?(?:status|from_status|to_status)\s+FROM",
        )
        for status in _CURRENT_STATUSES:
            self.assertIn(f"'{status}'", preflight)
        self.assertNotIn("private-status-material", repr(migration))
        self.assertEqual(
            tuple(step.checksum_sha256 for step in migration.steps),
            (
                "5b7d232b158667a02990a9f218f2e5bd09123909790390f7abfa31e648375cc7",
                "2504b7f90f9203a5960b39114e10915123fb2e184dec87007cbf60589ec453aa",
                "a1d6c1e856c8e1fb321dfae7fbfa5275dbc131605ea88ae02c8c8ede6b0c15db",
            ),
        )
        pinned = getattr(schema_module, "_POSTGRES_SCHEMA_V13_SHA256", None)
        self.assertEqual(
            pinned,
            "101b76750e72d449928d9d236e05ada77708be667b30a9f490092b124d82c319",
        )
        self.assertEqual(pinned, migration.checksum_sha256)
        self.assertFalse(
            hasattr(schema_module, "_upgrade_gateway_key_rotation_status_constraints")
        )

    def test_every_shipped_vocabulary_converges_for_each_owned_constraint(
        self,
    ) -> None:
        for vocabulary_name, statuses in _SHIPPED_VOCABULARIES:
            for table, column, constraint in _TARGETS:
                with self.subTest(vocabulary=vocabulary_name, constraint=constraint):
                    self._reset_schema()
                    connection = self._connection()
                    try:
                        self._prepare_v12(connection)
                        self._seed_rows(connection)
                        self._replace_status_constraint(
                            connection, table, column, constraint, statuses
                        )
                        before_rows = self._owned_rows(connection)
                        before_unrelated = self._unrelated_objects(connection)
                        before_target = self._target_identities(connection)[constraint]

                        postgres.install_postgres_schema(connection)

                        self.assertEqual(
                            tuple(row[:2] for row in self._history(connection)),
                            (*_V12_HISTORY, _V13_IDENTITY),
                        )
                        self.assertEqual(self._owned_rows(connection), before_rows)
                        self.assertEqual(
                            self._unrelated_objects(connection), before_unrelated
                        )
                        after_target = self._target_identities(connection)[constraint]
                        self.assertEqual(
                            after_target[1], self._definition(column, _CURRENT_STATUSES)
                        )
                        if vocabulary_name == "current":
                            self.assertEqual(after_target, before_target)
                        else:
                            self.assertNotEqual(after_target[0], before_target[0])
                        before_repeat = self._complete_snapshot(connection)

                        postgres.install_postgres_schema(connection)

                        self.assertEqual(
                            self._complete_snapshot(connection), before_repeat
                        )
                    finally:
                        connection.close()

    def test_absent_targets_install_without_touching_same_named_other_objects(
        self,
    ) -> None:
        for table, column, constraint in _TARGETS:
            with self.subTest(constraint=constraint):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v12(connection)
                    self._seed_rows(connection)
                    connection.execute(
                        f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"
                    )
                    other_schema = f"{self.schema}_other"
                    connection.execute(f'CREATE SCHEMA "{other_schema}"')
                    connection.execute(
                        f'CREATE TABLE "{other_schema}".shadow (value text)'
                    )
                    connection.execute(
                        f'ALTER TABLE "{other_schema}".shadow '
                        f"ADD CONSTRAINT {constraint} CHECK (value IS NOT NULL)"
                    )
                    connection.execute(
                        f"ALTER TABLE cpk_workspaces ADD CONSTRAINT {constraint} "
                        "CHECK (name IS NOT NULL)"
                    )
                    before_other = self._other_named_identities(connection)

                    postgres.install_postgres_schema(connection)

                    targets = self._target_identities(connection)
                    self.assertIn(constraint, targets)
                    self.assertEqual(
                        targets[constraint][1],
                        self._definition(column, _CURRENT_STATUSES),
                    )
                    self.assertEqual(
                        self._other_named_identities(connection), before_other
                    )
                finally:
                    connection.close()

    def test_invalid_or_mixed_catalog_truth_fails_before_any_status_ddl(self) -> None:
        cases = (
            ("arbitrary", "CHECK ({column} <> 'private-invalid-definition')"),
            (
                "subset",
                "CHECK ({column} IN ('requested', 'completed', 'blocked', 'rejected'))",
            ),
            ("unvalidated", "CHECK ({column} IS NOT NULL) NOT VALID"),
            ("wrong-type", "UNIQUE ({column})"),
            (
                "reordered",
                "CHECK ({column} IN ("
                + ", ".join(f"'{status}'" for status in reversed(_CURRENT_STATUSES))
                + "))",
            ),
            (
                "superset",
                "CHECK ({column} IN ("
                + ", ".join(f"'{status}'" for status in _CURRENT_STATUSES)
                + ", 'unlisted'))",
            ),
            (
                "logically-altered",
                "CHECK ({column} IS NOT NULL AND {column} IN ("
                + ", ".join(f"'{status}'" for status in _CURRENT_STATUSES)
                + "))",
            ),
        )
        for label, definition in cases:
            with self.subTest(label=label):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v12(connection)
                    self._seed_rows(connection)
                    self._replace_raw_constraint(
                        connection,
                        _TRANSITIONS,
                        _FROM_CONSTRAINT,
                        definition.format(column="from_status"),
                    )
                    before = self._complete_snapshot(connection)
                    executed: list[str] = []

                    class RecordingConnection:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=None):
                            normalized = re.sub(r"\s+", " ", query).strip()
                            if normalized.startswith("ALTER TABLE") and any(
                                name in normalized
                                for name in (
                                    _ROTATION_CONSTRAINT,
                                    _FROM_CONSTRAINT,
                                    _TO_CONSTRAINT,
                                )
                            ):
                                executed.append(normalized)
                            return connection.execute(query, params)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(RecordingConnection())

                    self._assert_bounded_status_error(raised.exception)
                    self.assertEqual(executed, [])
                    self.assertEqual(self._complete_snapshot(connection), before)
                    self.assertEqual(
                        tuple(row[:2] for row in self._history(connection)),
                        _V12_HISTORY,
                    )
                finally:
                    connection.close()

        self._reset_schema()
        connection = self._connection()
        try:
            self._prepare_v12(connection)
            self._seed_rows(connection)
            self._replace_status_constraint(
                connection,
                _ROTATIONS,
                "status",
                _ROTATION_CONSTRAINT,
                _INITIAL_STATUSES,
            )
            self._replace_raw_constraint(
                connection,
                _TRANSITIONS,
                _FROM_CONSTRAINT,
                "CHECK (from_status IS NOT NULL)",
            )
            before_rotation = self._target_identities(connection)[_ROTATION_CONSTRAINT]
            executed = []

            class MixedRecordingConnection:
                @property
                def autocommit(self):
                    return connection.autocommit

                def transaction(self):
                    return connection.transaction()

                def execute(self, query, params=None):
                    normalized = re.sub(r"\s+", " ", query).strip()
                    if normalized.startswith("ALTER TABLE") and any(
                        name in normalized
                        for name in (
                            _ROTATION_CONSTRAINT,
                            _FROM_CONSTRAINT,
                            _TO_CONSTRAINT,
                        )
                    ):
                        executed.append(normalized)
                    return connection.execute(query, params)

            with self.assertRaises(postgres.SchemaMigrationError):
                postgres.install_postgres_schema(MixedRecordingConnection())

            self.assertEqual(executed, [])
            self.assertEqual(
                self._target_identities(connection)[_ROTATION_CONSTRAINT],
                before_rotation,
            )
        finally:
            connection.close()

    def test_unlisted_retained_values_fail_without_disclosure_or_mutation(self) -> None:
        marker = "private-status-material-" + ("x" * 4096)
        mutations = (
            (
                _ROTATIONS,
                _ROTATION_CONSTRAINT,
                f"UPDATE {_ROTATIONS} SET status = %s",
            ),
            (
                _TRANSITIONS,
                _FROM_CONSTRAINT,
                f"UPDATE {_TRANSITIONS} SET from_status = %s",
            ),
            (
                _TRANSITIONS,
                _TO_CONSTRAINT,
                f"UPDATE {_TRANSITIONS} SET to_status = %s",
            ),
        )
        for table, constraint, mutation in mutations:
            with self.subTest(constraint=constraint):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v12(connection)
                    self._seed_rows(connection)
                    connection.execute(
                        f"ALTER TABLE {table} DROP CONSTRAINT {constraint}"
                    )
                    connection.execute(mutation, (marker,))
                    before = self._complete_snapshot(connection)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(connection)

                    self._assert_bounded_status_error(raised.exception, marker)
                    self.assertEqual(self._complete_snapshot(connection), before)
                finally:
                    connection.close()

    def test_current_literals_are_enforced_by_all_three_postgres_constraints(
        self,
    ) -> None:
        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            self.assertEqual(self._history(connection)[-1][:2], _V13_IDENTITY)
            connection.execute(
                """
                INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
                VALUES ('workspace-a', 'Workspace A', 'created')
                """
            )
            connection.execute(
                f"""
                INSERT INTO {_ROTATIONS} (
                  rotation_id, workspace_id, gateway_node_id, purpose, issuer,
                  old_key_id, new_secret_reference, key_generation_correlation,
                  maximum_grant_lifetime_seconds, clock_skew_seconds,
                  correlation_id, requested_by, requested_at, intent_fingerprint,
                  status, version
                ) VALUES (
                  'rotation-a', 'workspace-a', 'gateway-a', %s, 'cpk-server',
                  'gateway-key-a',
                  'secret://workspace-secrets/keys/gateway-key-b',
                  'generate-gateway-key-b', 120, 10, 'rotation-a', 'operator-a',
                  '2026-08-09T12:00:00Z', %s, 'requested', 1
                )
                """,
                (DelegationKeyPurpose.GATEWAY_PROBE.value, "a" * 64),
            )
            connection.execute(
                f"""
                INSERT INTO {_TRANSITIONS} (
                  rotation_id, transition_id, from_status, to_status,
                  from_version, to_version, transition_fingerprint,
                  advanced_by, advanced_at
                ) VALUES (
                  'rotation-a', 'transition-a', 'requested', 'awaiting-approval',
                  1, 2, %s, 'operator-a', '2026-08-09T12:01:00Z'
                )
                """,
                ("b" * 64,),
            )

            for status in _CURRENT_STATUSES:
                with self.subTest(table=_ROTATIONS, status=status):
                    failure_code = (
                        "bounded-failure" if status in {"blocked", "rejected"} else None
                    )
                    connection.execute(
                        f"UPDATE {_ROTATIONS} SET status = %s, failure_code = %s",
                        (status, failure_code),
                    )
                with self.subTest(table=_TRANSITIONS, status=status):
                    connection.execute(
                        f"UPDATE {_TRANSITIONS} "
                        "SET from_status = %s, to_status = %s",
                        (status, status),
                    )

            for table, column, mutation in (
                (
                    _ROTATIONS,
                    "status",
                    f"UPDATE {_ROTATIONS} SET status = 'unlisted', "
                    "failure_code = NULL",
                ),
                (
                    _TRANSITIONS,
                    "from_status",
                    f"UPDATE {_TRANSITIONS} SET from_status = 'unlisted'",
                ),
                (
                    _TRANSITIONS,
                    "to_status",
                    f"UPDATE {_TRANSITIONS} SET to_status = 'unlisted'",
                ),
            ):
                with self.subTest(table=table, column=column, status="unlisted"):
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        connection.execute(mutation)
        finally:
            connection.close()

    def test_each_sql_phase_failure_rolls_back_exact_v12_truth(self) -> None:
        migration = self._v13()
        for phase, step in enumerate(migration.steps):
            with self.subTest(phase=phase):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare_v12(connection)
                    self._seed_rows(connection)
                    self._replace_status_constraint(
                        connection,
                        _ROTATIONS,
                        "status",
                        _ROTATION_CONSTRAINT,
                        _INITIAL_STATUSES,
                    )
                    before = self._complete_snapshot(connection)

                    class FailingConnection:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=None):
                            if query == step.sql:
                                raise RuntimeError("private driver material")
                            return connection.execute(query, params)

                    with self.assertRaises(postgres.SchemaMigrationError) as raised:
                        postgres.install_postgres_schema(FailingConnection())

                    self.assertEqual(
                        str(raised.exception), "schema migration application failed"
                    )
                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertEqual(self._complete_snapshot(connection), before)
                    self.assertEqual(
                        tuple(row[:2] for row in self._history(connection)),
                        _V12_HISTORY,
                    )
                finally:
                    connection.close()

    def test_both_access_exclusive_locks_and_caller_rollback_cover_v13(self) -> None:
        setup = self._connection()
        try:
            self._prepare_v12(setup)
            self._seed_rows(setup)
            self._replace_status_constraint(
                setup,
                _ROTATIONS,
                "status",
                _ROTATION_CONSTRAINT,
                _INITIAL_STATUSES,
            )
            before = self._complete_snapshot(setup)
        finally:
            setup.close()

        caller = self._connection(autocommit=False)
        observer = self._connection()
        try:
            postgres.install_postgres_schema(caller)
            self.assertEqual(self._history(caller)[-1][:2], _V13_IDENTITY)
            observer.execute("SET lock_timeout TO '250ms'")
            for table in (_ROTATIONS, _TRANSITIONS):
                with self.subTest(table=table):
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        observer.execute(f"SELECT count(*) FROM {table}")

            caller.rollback()
            self.assertEqual(self._complete_snapshot(observer), before)
            self.assertEqual(
                tuple(row[:2] for row in self._history(observer)), _V12_HISTORY
            )
        finally:
            caller.rollback()
            caller.close()
            observer.close()

    def test_final_verifier_is_bounded_and_rejects_each_target_drift(self) -> None:
        verifier = getattr(
            migration_inspection,
            "_verify_gateway_key_rotation_status_contracts",
            None,
        )
        self.assertTrue(callable(verifier))

        valid = [
            (_ROTATION_CONSTRAINT, "c", True, True),
            (_FROM_CONSTRAINT, "c", True, True),
            (_TO_CONSTRAINT, "c", True, True),
        ]

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class ScriptedConnection:
            def __init__(self, rows):
                self.rows = rows
                self.queries = []

            def execute(self, query, params=None):
                self.queries.append((re.sub(r"\s+", " ", query).strip(), params))
                return Cursor(self.rows)

        exact = ScriptedConnection(valid)
        verifier(exact)
        self.assertEqual(len(exact.queries), 1)
        query, params = exact.queries[0]
        self.assertIn("LIMIT 4", query)
        self.assertIn("pg_get_constraintdef", query)
        self.assertNotIn("SELECT pg_get_constraintdef", query)
        self.assertEqual(len(params), 3)

        for label, rows in (
            ("missing", valid[:-1]),
            ("duplicate", [*valid, valid[0]]),
            ("wrong-type", [(valid[0][0], "u", True, True), *valid[1:]]),
            ("unvalidated", [(valid[0][0], "c", False, True), *valid[1:]]),
            ("wrong-definition", [(valid[0][0], "c", True, False), *valid[1:]]),
        ):
            with self.subTest(label=label):
                with self.assertRaises(postgres.SchemaMigrationError) as raised:
                    verifier(ScriptedConnection(rows))
                self.assertLessEqual(len(str(raised.exception)), 256)

        for table, column, constraint in _TARGETS:
            with self.subTest(drift=constraint):
                self._reset_schema()
                connection = self._connection()
                try:
                    postgres.install_postgres_schema(connection)
                    self._replace_status_constraint(
                        connection,
                        table,
                        column,
                        constraint,
                        _INITIAL_STATUSES,
                    )
                    with self.assertRaises(postgres.SchemaMigrationError):
                        postgres.verify_postgres_schema(connection)
                finally:
                    connection.close()

    def test_already_v13_drift_is_rejected_instead_of_perpetually_repaired(
        self,
    ) -> None:
        connection = self._connection()
        try:
            postgres.install_postgres_schema(connection)
            self.assertEqual(self._history(connection)[-1][:2], _V13_IDENTITY)
            self._replace_status_constraint(
                connection,
                _ROTATIONS,
                "status",
                _ROTATION_CONSTRAINT,
                _INITIAL_STATUSES,
            )
            drifted = self._target_identities(connection)[_ROTATION_CONSTRAINT]
            ledger = self._history(connection)

            with self.assertRaises(postgres.SchemaMigrationError) as raised:
                postgres.install_postgres_schema(connection)

            self.assertLessEqual(len(str(raised.exception)), 256)
            self.assertEqual(
                self._target_identities(connection)[_ROTATION_CONSTRAINT], drifted
            )
            self.assertEqual(self._history(connection), ledger)
        finally:
            connection.close()

    def _prepare_v12(self, connection) -> None:
        connection.execute(postgres.POSTGRES_SCHEMA)
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[1:12]:
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
        for migration in postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[:12]:
            connection.execute(
                """
                INSERT INTO cpk_schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum_sha256),
            )

    def _seed_rows(self, connection) -> None:
        connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        connection.execute(
            f"""
            INSERT INTO {_ROTATIONS} (
              rotation_id, workspace_id, gateway_node_id, purpose, issuer,
              old_key_id, new_secret_reference, key_generation_correlation,
              maximum_grant_lifetime_seconds, clock_skew_seconds,
              correlation_id, requested_by, requested_at, intent_fingerprint,
              status, version
            ) VALUES (
              'rotation-a', 'workspace-a', 'gateway-a', %s, 'cpk-server',
              'gateway-key-a',
              'secret://workspace-secrets/keys/gateway-key-b',
              'generate-gateway-key-b', 120, 10, 'rotation-a', 'operator-a',
              '2026-08-09T12:00:00Z', %s, 'requested', 1
            )
            """,
            (DelegationKeyPurpose.GATEWAY_PROBE.value, "a" * 64),
        )
        connection.execute(
            f"""
            INSERT INTO {_TRANSITIONS} (
              rotation_id, transition_id, from_status, to_status,
              from_version, to_version, transition_fingerprint,
              advanced_by, advanced_at
            ) VALUES (
              'rotation-a', 'transition-a', 'requested', 'awaiting-approval',
              1, 2, %s, 'operator-a', '2026-08-09T12:01:00Z'
            )
            """,
            ("b" * 64,),
        )

    @staticmethod
    def _replace_status_constraint(
        connection,
        table: str,
        column: str,
        constraint: str,
        statuses: tuple[str, ...],
    ) -> None:
        values = ", ".join(f"'{status}'" for status in statuses)
        connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        connection.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK ({column} IN ({values}))"
        )

    @staticmethod
    def _replace_raw_constraint(
        connection, table: str, constraint: str, definition: str
    ) -> None:
        connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        connection.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} {definition}"
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
    def _history(connection) -> tuple[tuple[object, ...], ...]:
        return tuple(
            connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    @staticmethod
    def _owned_rows(connection) -> tuple[tuple[object, ...], ...]:
        rotations = tuple(
            connection.execute(
                f"SELECT ctid::text, rotation_id, status, version FROM {_ROTATIONS} "
                "ORDER BY rotation_id"
            ).fetchall()
        )
        transitions = tuple(
            connection.execute(
                f"SELECT ctid::text, rotation_id, transition_id, from_status, "
                f"to_status, from_version, to_version FROM {_TRANSITIONS} "
                "ORDER BY rotation_id, transition_id"
            ).fetchall()
        )
        return rotations, transitions

    @staticmethod
    def _target_identities(connection) -> dict[str, tuple[int, str, bool, str]]:
        return {
            name: (oid, definition, validated, relation)
            for name, oid, definition, validated, relation in connection.execute(
                """
                SELECT constraints.conname, constraints.oid,
                       pg_get_constraintdef(constraints.oid, false),
                       constraints.convalidated, relation.relname
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND (relation.relname, constraints.conname) IN (
                    ('cpk_gateway_key_rotations',
                     'cpk_gateway_key_rotations_status_check'),
                    ('cpk_gateway_key_rotation_transitions',
                     'cpk_gateway_key_rotation_transitions_from_status_check'),
                    ('cpk_gateway_key_rotation_transitions',
                     'cpk_gateway_key_rotation_transitions_to_status_check')
                  )
                ORDER BY constraints.conname, constraints.oid
                """
            ).fetchall()
        }

    def _other_named_identities(
        self, connection
    ) -> dict[tuple[str, str, str], tuple[int, str]]:
        return {
            (schema, relation, name): (oid, definition)
            for schema, relation, name, oid, definition in connection.execute(
                """
                SELECT namespace.nspname, relation.relname, constraints.conname,
                       constraints.oid, pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE constraints.conname = ANY(%s)
                  AND namespace.nspname IN (%s, %s)
                  AND NOT (
                    namespace.nspname = %s
                    AND (relation.relname, constraints.conname) IN (
                      ('cpk_gateway_key_rotations',
                       'cpk_gateway_key_rotations_status_check'),
                      ('cpk_gateway_key_rotation_transitions',
                       'cpk_gateway_key_rotation_transitions_from_status_check'),
                      ('cpk_gateway_key_rotation_transitions',
                       'cpk_gateway_key_rotation_transitions_to_status_check')
                    )
                  )
                ORDER BY namespace.nspname, relation.relname, constraints.conname
                """,
                (
                    [_ROTATION_CONSTRAINT, _FROM_CONSTRAINT, _TO_CONSTRAINT],
                    self.schema,
                    f"{self.schema}_other",
                    self.schema,
                ),
            ).fetchall()
        }

    @staticmethod
    def _unrelated_objects(connection) -> dict[tuple[str, str, str], tuple[int, str]]:
        return {
            (relation, kind, name): (oid, definition)
            for relation, kind, name, oid, definition in connection.execute(
                """
                SELECT relation.relname, 'constraint', constraints.conname,
                       constraints.oid, pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname IN (
                    'cpk_gateway_key_rotations',
                    'cpk_gateway_key_rotation_transitions'
                  )
                  AND constraints.conname <> ALL(%s)
                UNION ALL
                SELECT relation.relname, 'index', indexes.relname,
                       indexes.oid, pg_get_indexdef(indexes.oid)
                FROM pg_class AS indexes
                JOIN pg_index AS index_contract ON index_contract.indexrelid = indexes.oid
                JOIN pg_class AS relation ON relation.oid = index_contract.indrelid
                JOIN pg_namespace AS namespace ON namespace.oid = indexes.relnamespace
                WHERE namespace.nspname = current_schema()
                  AND relation.relname IN (
                    'cpk_gateway_key_rotations',
                    'cpk_gateway_key_rotation_transitions'
                  )
                  AND indexes.relkind = 'i'
                ORDER BY 1, 2, 3
                """,
                ([_ROTATION_CONSTRAINT, _FROM_CONSTRAINT, _TO_CONSTRAINT],),
            ).fetchall()
        }

    def _complete_snapshot(self, connection) -> tuple[object, ...]:
        return (
            self._history(connection),
            self._owned_rows(connection),
            self._target_identities(connection),
            self._unrelated_objects(connection),
        )

    @staticmethod
    def _definition(column: str, statuses: tuple[str, ...]) -> str:
        values = ", ".join(f"'{status}'::text" for status in statuses)
        return f"CHECK (({column} = ANY (ARRAY[{values}])))"

    @staticmethod
    def _assert_bounded_status_error(error: Exception, marker: str = "") -> None:
        message = str(error)
        if message != _CATEGORICAL_ERROR:
            raise AssertionError(f"unexpected bounded category: {message!r}")
        if len(message) > 256:
            raise AssertionError("migration error is not bounded")
        for excluded in (
            marker,
            "rotation-a",
            "transition-a",
            "workspace-a",
            "gateway-a",
            "secret://",
            "provider.registration",
            "SELECT",
            "ALTER TABLE",
            "postgresql://",
        ):
            if excluded and excluded in message:
                raise AssertionError("migration error contains forbidden material")
        if error.__context__ is not None or error.__cause__ is not None:
            raise AssertionError("migration error retains provider context")

    @staticmethod
    def _v13():
        migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[-1]
        if (migration.version, migration.name) != _V13_IDENTITY:
            raise AssertionError("V13 status-contract migration is missing")
        return migration


if __name__ == "__main__":
    unittest.main()
