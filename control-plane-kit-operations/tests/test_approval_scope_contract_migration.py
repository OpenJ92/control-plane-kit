from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
import os
import threading
import time
import unittest
import uuid

import psycopg
from psycopg import errors
from psycopg.types.json import Jsonb

from tests.graph_lineage_fixture import seed_authored_graphs

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.approvals import (
    ApprovalCommandService,
    DecideApproval,
)
import control_plane_kit_operations.postgres as postgres
from control_plane_kit_operations.postgres import migration_inspection
from control_plane_kit_operations.postgres import migration_runner
from control_plane_kit_operations.postgres import schema as schema_module
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.records import ApprovalDecisionKind
from control_plane_kit_operations.workflows import IdempotencyKey


_V15_HISTORY = (
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
    (15, "approval-subject-evidence"),
)
_V16_IDENTITY = (16, "approval-scope-contracts")
_CURRENT_IDENTITY = (17, "graph-lineage-compatibility")
_V16_SHA256 = "301c05458431939355d7c835bbdd05dad221a8370a7fb6ed6b95cd086162497e"
_V16_STEP_SHA256 = (
    "9fa1ec3c562647985a4a0cd83af9cda2d34194df6fe0601a9b87453ec5b16fe9",
    "4578f21d03526be03cb3188bae81fab3fdf626f1a06f3ef62ef2b9873a085b23",
)
_CATEGORICAL_ERROR = "approval scope contract is not accepted"
_REQUESTS = (
    "cpk_approval_requests",
    "required_scope",
    "cpk_approval_requests_scope_check",
)
_DECISIONS = (
    "cpk_approval_decisions",
    "scope",
    "cpk_approval_decisions_scope_check",
)
_TARGETS = (_REQUESTS, _DECISIONS)
_CURRENT_SCOPES = (
    "hub:instance:create",
    "hub:instance:read",
    "instance:workspace:read",
    "instance:workspace:edit",
    "plan:request",
    "plan:approve",
    "plan:approve-destructive",
    "plan:execute",
    "execution:operate",
    "runtime-authority:register",
    "runtime-authority:read",
    "runtime-authority:revoke",
    "runtime-authority:use",
    "runtime-authority-delivery:register",
    "runtime-authority-delivery:read",
    "runtime-authority-delivery:revoke",
    "ingress-authority:register",
    "ingress-authority:read",
    "ingress-authority:revoke",
    "ingress-authority:use",
    "secret-provider:register",
    "secret-provider:read",
    "secret-provider:use",
    "secret-provider:revoke",
    "delegation-key:generate",
    "delegation-key:register",
    "delegation-key:read",
    "delegation-key:activate",
    "delegation-key:retire",
    "delegation-key:revoke",
    "delegation-key:use",
    "delegation-key:rotate",
    "delegation-key:rotate-approve",
    "gateway-probe:use",
)
_LEGACY_SCOPES = tuple(
    scope for scope in _CURRENT_SCOPES if scope != "delegation-key:rotate-approve"
)


def _values(scopes: tuple[str, ...]) -> str:
    return ", ".join(f"'{scope}'" for scope in scopes)


def _definition(column: str, scopes: tuple[str, ...]) -> str:
    values = ", ".join(f"'{scope}'::text" for scope in scopes)
    return f"CHECK (({column} = ANY (ARRAY[{values}])))"


class ApprovalScopeContractMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; use Docker-first tests"
            )
        self.database_url = database_url
        self.schema = f"apscope_{uuid.uuid4().hex}"
        self.admin = psycopg.connect(database_url, autocommit=True)
        self.admin.execute(f'CREATE SCHEMA "{self.schema}"')

    def tearDown(self) -> None:
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}_other" CASCADE')
        self.admin.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.admin.close()

    def test_registry_appends_frozen_two_step_v16_and_retires_helper(self) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS

        self.assertEqual(tuple(scope.value for scope in PolicyScope), _CURRENT_SCOPES)
        self.assertEqual(
            getattr(schema_module, "_POSTGRES_SCHEMA_V16_LEGACY_SCOPES"),
            _LEGACY_SCOPES,
        )
        self.assertEqual(
            getattr(schema_module, "_POSTGRES_SCHEMA_V16_CURRENT_SCOPES"),
            _CURRENT_SCOPES,
        )
        self.assertEqual(registry.target_version, 17)
        self.assertEqual(
            tuple((migration.version, migration.name) for migration in registry.migrations),
            (*_V15_HISTORY, _V16_IDENTITY, _CURRENT_IDENTITY),
        )
        migration = registry.migrations[15]
        self.assertIsNone(migration.sql)
        self.assertEqual(len(migration.steps), 2)
        self.assertTrue(
            all(type(step) is postgres.SqlMigrationStep for step in migration.steps)
        )
        self.assertEqual(
            tuple(step.checksum_sha256 for step in migration.steps),
            _V16_STEP_SHA256,
        )
        preflight = migration.steps[0].sql
        self.assertLess(
            preflight.index(
                "LOCK TABLE cpk_approval_requests IN ACCESS EXCLUSIVE MODE;"
            ),
            preflight.index(
                "LOCK TABLE cpk_approval_decisions IN ACCESS EXCLUSIVE MODE;"
            ),
        )
        for column in ("required_scope", "scope"):
            for scopes in (_LEGACY_SCOPES, _CURRENT_SCOPES):
                with self.subTest(column=column, scopes=len(scopes)):
                    self.assertIn(
                        _definition(column, scopes).replace("'", "''"),
                        preflight,
                    )
        self.assertNotIn("ALTER TABLE", preflight)
        self.assertIn(_CATEGORICAL_ERROR, preflight)
        self.assertIn("count(DISTINCT constraints.oid)", preflight)
        self.assertIn("count(DISTINCT constraints.conname)", preflight)
        self.assertIn("constraint_count > 2", preflight)
        self.assertEqual(migration.checksum_sha256, _V16_SHA256)
        self.assertEqual(
            migration.checksum_sha256,
            getattr(schema_module, "_POSTGRES_SCHEMA_V16_SHA256"),
        )
        self.assertFalse(hasattr(schema_module, "_upgrade_approval_scope_constraints"))
        runner_source = inspect.getsource(migration_runner)
        self.assertNotIn("_upgrade_approval_scope_constraints", runner_source)
        self.assertNotIn(
            "DROP CONSTRAINT cpk_approval_requests_scope_check",
            getattr(schema_module, "_CURRENT_POSTGRES_SCHEMA"),
        )
        self.assertNotIn(
            "DROP CONSTRAINT cpk_approval_decisions_scope_check",
            getattr(schema_module, "_CURRENT_POSTGRES_SCHEMA"),
        )

    def test_all_nine_accepted_constraint_state_combinations_converge(self) -> None:
        for request_state in ("absent", "legacy", "current"):
            for decision_state in ("absent", "legacy", "current"):
                with self.subTest(request=request_state, decision=decision_state):
                    self._reset_schema()
                    connection = self._connection()
                    try:
                        self._prepare(15, connection)
                        self._set_state(connection, _REQUESTS, request_state)
                        self._set_state(connection, _DECISIONS, decision_state)
                        before_rows = self._approval_rows(connection)
                        before = self._target_identities(connection)

                        postgres.install_postgres_schema(connection)

                        self.assertEqual(self._approval_rows(connection), before_rows)
                        after = self._target_identities(connection)
                        self.assertEqual(
                            set(after),
                            {_REQUESTS[2], _DECISIONS[2]},
                        )
                        for target, state in (
                            (_REQUESTS, request_state),
                            (_DECISIONS, decision_state),
                        ):
                            identity = after[target[2]]
                            self.assertEqual(identity[1], _definition(target[1], _CURRENT_SCOPES))
                            if state == "current":
                                self.assertEqual(identity, before[target[2]])
                            else:
                                self.assertNotEqual(
                                    identity[0],
                                    None if state == "absent" else before[target[2]][0],
                                )
                        self.assertEqual(
                            self._history(connection)[-1][:2], _CURRENT_IDENTITY
                        )
                        snapshot = self._snapshot(connection)
                        postgres.install_postgres_schema(connection)
                        self.assertEqual(self._snapshot(connection), snapshot)
                    finally:
                        connection.close()

    def test_invalid_second_target_prevents_first_target_mutation_and_reverse(self) -> None:
        for valid_target, invalid_target in (
            (_REQUESTS, _DECISIONS),
            (_DECISIONS, _REQUESTS),
        ):
            with self.subTest(invalid=invalid_target[2]):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(15, connection)
                    self._set_state(connection, valid_target, "legacy")
                    self._replace_constraint(
                        connection,
                        invalid_target,
                        f"CHECK ({invalid_target[1]} <> 'private-scope-material')",
                    )
                    before = self._snapshot(connection)

                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError, f"^{_CATEGORICAL_ERROR}$"
                    ) as raised:
                        postgres.install_postgres_schema(connection)

                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertNotIn("private-scope-material", repr(raised.exception))
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_wrong_type_unvalidated_subset_superset_and_reordering_reject(self) -> None:
        invalid_definitions = (
            "CHECK ({column} IN ('plan:approve'))",
            "CHECK ({column} IN (" + _values(_CURRENT_SCOPES) + ", 'future:scope'))",
            "CHECK ({column} IN (" + _values(tuple(reversed(_CURRENT_SCOPES))) + "))",
            "CHECK ({column} <> '') NOT VALID",
            "UNIQUE ({column})",
        )
        for target in _TARGETS:
            for template in invalid_definitions:
                with self.subTest(target=target[2], definition=template[:12]):
                    self._reset_schema()
                    connection = self._connection()
                    try:
                        self._prepare(15, connection)
                        self._replace_constraint(
                            connection, target, template.format(column=target[1])
                        )
                        before = self._snapshot(connection)
                        with self.assertRaisesRegex(
                            postgres.SchemaMigrationError, f"^{_CATEGORICAL_ERROR}$"
                        ):
                            postgres.install_postgres_schema(connection)
                        self.assertEqual(self._snapshot(connection), before)
                    finally:
                        connection.close()

    def test_other_relation_and_cross_schema_lookalikes_are_preserved(self) -> None:
        connection = self._connection()
        try:
            self._prepare(15, connection)
            for target in _TARGETS:
                connection.execute(f"ALTER TABLE {target[0]} DROP CONSTRAINT {target[2]}")
            connection.execute("CREATE TABLE scope_lookalike (value text)")
            connection.execute(
                "ALTER TABLE scope_lookalike ADD CONSTRAINT "
                "cpk_approval_requests_scope_check CHECK (value <> '')"
            )
            self.admin.execute(f'CREATE SCHEMA "{self.schema}_other"')
            other = psycopg.connect(self.database_url, autocommit=True)
            try:
                other.execute(f'SET search_path TO "{self.schema}_other"')
                other.execute("CREATE TABLE cpk_approval_decisions (scope text)")
                other.execute(
                    "ALTER TABLE cpk_approval_decisions ADD CONSTRAINT "
                    "cpk_approval_decisions_scope_check CHECK (scope <> '')"
                )
                lookalikes = self._lookalikes(connection)

                self._apply_v16(connection)

                self.assertEqual(self._lookalikes(connection), lookalikes)
                self.assertEqual(len(self._target_identities(connection)), 2)
            finally:
                other.close()
        finally:
            connection.close()

    def test_every_current_scope_survives_without_row_rewrite(self) -> None:
        connection = self._connection()
        try:
            self._prepare(15, connection)
            self._seed_all_scopes(connection)
            before = self._approval_rows(connection)

            postgres.install_postgres_schema(connection)

            self.assertEqual(self._approval_rows(connection), before)
            self.assertEqual(
                tuple(sorted(row[1] for row in before[0])),
                tuple(sorted(_CURRENT_SCOPES)),
            )
            self.assertEqual(
                tuple(sorted(row[1] for row in before[1])),
                tuple(sorted(_CURRENT_SCOPES)),
            )
        finally:
            connection.close()

    def test_unlisted_retained_scope_rejects_without_reflection_or_mutation(self) -> None:
        for target in _TARGETS:
            with self.subTest(target=target[0]):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(15, connection)
                    self._seed_invalid_scope(connection, target)
                    before = self._snapshot(connection)
                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError, f"^{_CATEGORICAL_ERROR}$"
                    ) as raised:
                        postgres.install_postgres_schema(connection)
                    self.assertNotIn("private-scope-material", repr(raised.exception))
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_caller_rollback_restores_v15_and_v16_effects(self) -> None:
        connection = self._connection(autocommit=False)
        try:
            self._prepare(15, connection)
            self._set_state(connection, _REQUESTS, "legacy")
            self._set_state(connection, _DECISIONS, "legacy")
            connection.commit()
            before = self._snapshot(connection)

            postgres.install_postgres_schema(connection)
            self.assertEqual(self._history(connection)[-1][:2], _CURRENT_IDENTITY)
            connection.rollback()

            self.assertEqual(self._snapshot(connection), before)
        finally:
            connection.close()

    def test_each_v16_phase_and_ledger_failure_restore_exact_v15_truth(self) -> None:
        for failure in ("preflight", "after-convergence", "ledger"):
            with self.subTest(failure=failure):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(15, connection)
                    self._set_state(connection, _REQUESTS, "legacy")
                    self._set_state(connection, _DECISIONS, "legacy")
                    self._seed_one_approval(connection)
                    before = self._snapshot(connection)
                    migration = postgres.POSTGRES_SCHEMA_MIGRATIONS.migrations[15]

                    class FailingConnection:
                        @property
                        def autocommit(self):
                            return connection.autocommit

                        def transaction(self):
                            return connection.transaction()

                        def execute(self, query, params=()):
                            if failure == "preflight" and query == migration.steps[0].sql:
                                raise RuntimeError("private preflight material")
                            if (
                                failure == "after-convergence"
                                and query == migration.steps[1].sql
                            ):
                                connection.execute(query, params)
                                raise RuntimeError("private convergence material")
                            if (
                                failure == "ledger"
                                and "INSERT INTO cpk_schema_migrations" in query
                                and params
                                and params[0] == 16
                            ):
                                raise RuntimeError("private ledger material")
                            return connection.execute(query, params)

                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError,
                        "^schema migration application failed$",
                    ) as raised:
                        postgres.install_postgres_schema(FailingConnection())

                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertNotIn("private", repr(raised.exception))
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_failed_v16_savepoint_releases_locks_but_preserves_outer_work(self) -> None:
        setup = self._connection()
        caller = self._connection(autocommit=False)
        observer = self._connection()
        try:
            self._prepare(15, setup)
            self._set_state(setup, _REQUESTS, "legacy")
            self._set_state(setup, _DECISIONS, "legacy")
            self._seed_one_approval(setup, include_decision=False)
            self._replace_constraint(
                setup,
                _REQUESTS,
                "CHECK (required_scope <> 'private-lock-material')",
            )
            caller.execute(
                "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
                "VALUES ('workspace-outer', 'Outer work', 'created')"
            )

            with self.assertRaisesRegex(
                postgres.SchemaMigrationError,
                f"^{_CATEGORICAL_ERROR}$",
            ):
                postgres.install_postgres_schema(caller)

            self.assertEqual(
                caller.execute(
                    "SELECT count(*) FROM cpk_workspaces "
                    "WHERE workspace_id = 'workspace-outer'"
                ).fetchone(),
                (1,),
            )
            self.assertEqual(
                observer.execute(
                    "SELECT count(*) FROM cpk_workspaces "
                    "WHERE workspace_id = 'workspace-outer'"
                ).fetchone(),
                (0,),
            )

            def package_factory():
                connection = self._package_connection()
                connection.execute("SET lock_timeout TO '500ms'")
                return connection

            result = self._decision_service(package_factory).execute(
                self._decision_command()
            )
            self.assertEqual(result.decision.decision_id, "package-decision")

            caller.rollback()
            self.assertEqual(
                observer.execute(
                    "SELECT count(*) FROM cpk_workspaces "
                    "WHERE workspace_id = 'workspace-outer'"
                ).fetchone(),
                (0,),
            )
        finally:
            caller.rollback()
            setup.close()
            caller.close()
            observer.close()

    def test_v15_success_then_v16_failure_restores_exact_v14_truth(self) -> None:
        connection = self._connection(autocommit=False)
        try:
            self._prepare(14, connection)
            self._downgrade_subject_contract(connection)
            self._seed_legacy_plan_approval(connection)
            self._replace_constraint(
                connection,
                _DECISIONS,
                "CHECK (scope <> 'private-scope-material')",
            )
            connection.commit()
            before = self._snapshot(connection)

            with self.assertRaisesRegex(
                postgres.SchemaMigrationError, f"^{_CATEGORICAL_ERROR}$"
            ):
                postgres.install_postgres_schema(connection)

            self.assertEqual(self._snapshot(connection), before)
            self.assertEqual(self._history(connection)[-1][:2], (14, "gateway-key-rotation-retirement-evidence"))
        finally:
            connection.close()

    def test_migration_locks_both_tables_until_outer_transaction_ends(self) -> None:
        migration = self._connection(autocommit=False)
        observer = self._connection()
        try:
            self._prepare(15, migration)
            migration.commit()
            postgres.install_postgres_schema(migration)
            observer.execute("SET lock_timeout = '150ms'")
            for table in (_REQUESTS[0], _DECISIONS[0]):
                with self.subTest(table=table), self.assertRaises(errors.LockNotAvailable):
                    observer.execute(f"SELECT count(*) FROM {table}").fetchone()
            migration.commit()
            self.assertEqual(
                tuple(
                    observer.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in (_REQUESTS[0], _DECISIONS[0])
                ),
                (0, 0),
            )
        finally:
            migration.rollback()
            migration.close()
            observer.close()

    def test_migration_first_serializes_real_package_decision(self) -> None:
        migration = self._connection(autocommit=False)
        try:
            self._prepare(15, migration)
            self._set_state(migration, _REQUESTS, "legacy")
            self._set_state(migration, _DECISIONS, "legacy")
            self._seed_one_approval(migration, include_decision=False)
            migration.commit()
            postgres.install_postgres_schema(migration)
            started = threading.Event()

            def decide():
                started.set()
                return self._decision_service(self._package_connection).execute(
                    self._decision_command()
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(decide)
                self.assertTrue(started.wait(timeout=2))
                time.sleep(0.1)
                self.assertFalse(future.done())
                migration.commit()
                result = future.result(timeout=5)
            self.assertEqual(result.decision.decision_id, "package-decision")
        finally:
            migration.rollback()
            migration.close()

    def test_real_package_decision_first_then_migration_converges(self) -> None:
        setup = self._connection()
        migration = self._connection(autocommit=False)
        package_at_commit = threading.Event()
        release_package = threading.Event()
        try:
            self._prepare(15, setup)
            self._set_state(setup, _REQUESTS, "legacy")
            self._set_state(setup, _DECISIONS, "legacy")
            self._seed_one_approval(setup, include_decision=False)

            def factory():
                return _HeldCommitConnection(
                    self._package_connection(),
                    package_at_commit,
                    release_package,
                )

            migration_started = threading.Event()

            def install() -> None:
                migration_started.set()
                postgres.install_postgres_schema(migration)

            with ThreadPoolExecutor(max_workers=2) as executor:
                package_future = executor.submit(
                    self._decision_service(factory).execute,
                    self._decision_command(),
                )
                self.assertTrue(package_at_commit.wait(timeout=5))
                migration_future = executor.submit(install)
                self.assertTrue(migration_started.wait(timeout=2))
                time.sleep(0.1)
                self.assertFalse(migration_future.done())
                release_package.set()
                package_result = package_future.result(timeout=5)
                migration_future.result(timeout=5)
            migration.commit()
            self.assertEqual(
                package_result.decision.decision_id,
                "package-decision",
            )
            self.assertEqual(self._history(setup)[-1][:2], _CURRENT_IDENTITY)
        finally:
            release_package.set()
            setup.close()
            migration.rollback()
            migration.close()

    def test_failed_package_decision_releases_locks_before_migration(self) -> None:
        setup = self._connection()
        migration = self._connection(autocommit=False)
        commit_attempted = threading.Event()
        try:
            self._prepare(15, setup)
            self._set_state(setup, _REQUESTS, "legacy")
            self._set_state(setup, _DECISIONS, "legacy")
            self._seed_one_approval(setup, include_decision=False)

            def factory():
                return _FailingCommitConnection(
                    self._package_connection(),
                    commit_attempted,
                )

            with self.assertRaisesRegex(RuntimeError, "private package commit"):
                self._decision_service(factory).execute(self._decision_command())
            self.assertTrue(commit_attempted.is_set())
            self.assertEqual(
                setup.execute("SELECT count(*) FROM cpk_approval_decisions").fetchone(),
                (0,),
            )

            postgres.install_postgres_schema(migration)
            migration.commit()
            self.assertEqual(self._history(setup)[-1][:2], _CURRENT_IDENTITY)
        finally:
            setup.close()
            migration.rollback()
            migration.close()

    def test_already_v16_scope_drift_rejects_without_repair_or_row_loss(self) -> None:
        for target in _TARGETS:
            with self.subTest(target=target[0]):
                self._reset_schema()
                connection = self._connection()
                try:
                    self._prepare(16, connection)
                    self._seed_one_approval(connection)
                    self._replace_constraint(
                        connection,
                        target,
                        f"CHECK ({target[1]} <> 'private-drift-material')",
                    )
                    before = self._snapshot(connection)

                    with self.assertRaisesRegex(
                        postgres.SchemaMigrationError,
                        "^approval scope schema is not current$",
                    ) as raised:
                        postgres.install_postgres_schema(connection)

                    self.assertIsNone(raised.exception.__context__)
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertNotIn("private-drift-material", repr(raised.exception))
                    self.assertEqual(self._snapshot(connection), before)
                finally:
                    connection.close()

    def test_final_verifier_is_relation_scoped_boolean_and_bounded(self) -> None:
        verifier = getattr(migration_inspection, "_verify_approval_scope_contracts")
        connection = _ScriptedConnection(
            [
                (
                    "cpk_approval_decisions",
                    "cpk_approval_decisions_scope_check",
                    "c",
                    True,
                    True,
                ),
                (
                    "cpk_approval_requests",
                    "cpk_approval_requests_scope_check",
                    "c",
                    True,
                    True,
                ),
            ]
        )

        verifier(connection)

        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        self.assertIn("namespace.nspname = current_schema()", query)
        self.assertIn("pg_get_constraintdef", query)
        self.assertIn("ORDER BY relation.relname", query)
        self.assertIn("LIMIT 3", query)
        self.assertNotIn("SELECT pg_get_constraintdef", query)
        self.assertEqual(
            parameters,
            (
                _definition("required_scope", _CURRENT_SCOPES),
                _definition("scope", _CURRENT_SCOPES),
            ),
        )

        accepted_rows = connection._rows
        for invalid_rows in (
            accepted_rows[:1],
            [*accepted_rows, accepted_rows[-1]],
            [(*accepted_rows[0][:-1], False), accepted_rows[1]],
        ):
            with self.subTest(rows=len(invalid_rows)):
                with self.assertRaisesRegex(
                    postgres.SchemaMigrationError,
                    "^approval scope schema is not current$",
                ):
                    verifier(_ScriptedConnection(invalid_rows))

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
    def _apply_v16(connection) -> None:
        registry = postgres.POSTGRES_SCHEMA_MIGRATIONS
        if registry.target_version < 16:
            raise AssertionError("V16 approval-scope migration is missing")
        migration_runner._apply_schema_migration(connection, registry.migrations[15])

    @staticmethod
    def _set_state(connection, target, state: str) -> None:
        table, column, constraint = target
        connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        if state == "absent":
            return
        scopes = _LEGACY_SCOPES if state == "legacy" else _CURRENT_SCOPES
        connection.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"CHECK ({column} IN ({_values(scopes)}))"
        )

    @staticmethod
    def _replace_constraint(connection, target, definition: str) -> None:
        table, _column, constraint = target
        connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        connection.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} {definition}"
        )

    def _seed_all_scopes(self, connection) -> None:
        self._seed_execution_truth(connection)
        for index, scope in enumerate(_CURRENT_SCOPES):
            subject = ActivityPlanApprovalSubject(f"plan-{index:03d}")
            connection.execute(
                "INSERT INTO cpk_activity_plans "
                "(plan_id, session_id, base_graph_id, desired_graph_id, status, "
                "created_at, payload) VALUES (%s, 'session-a', 'graph-a', 'graph-b', "
                "'planned', '2026-08-10T00:00:01Z', '{}'::jsonb)",
                (subject.plan_id,),
            )
            connection.execute(
                "INSERT INTO cpk_approval_requests "
                "(request_id, session_id, plan_id, subject_kind, subject_payload, "
                "review_digest, requested_by, requested_at, required_scope, max_risk, "
                "destructive) VALUES (%s, 'session-a', %s, 'activity-plan', %s, %s, "
                "'operator-a', '2026-08-10T00:00:02Z', %s, 'low', false)",
                (f"request-{index:03d}", subject.plan_id, Jsonb(subject.descriptor()), subject.review_digest, scope),
            )
            connection.execute(
                "INSERT INTO cpk_approval_decisions "
                "(decision_id, request_id, actor_id, decision, scope, decided_at) "
                "VALUES (%s, %s, 'operator-b', 'approved', %s, "
                "'2026-08-10T00:00:03Z')",
                (f"decision-{index:03d}", f"request-{index:03d}", scope),
            )

    def _seed_invalid_scope(self, connection, target) -> None:
        self._seed_one_approval(connection)
        table, column, constraint = target
        connection.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        identity = "request-a" if table == _REQUESTS[0] else "decision-a"
        key = "request_id" if table == _REQUESTS[0] else "decision_id"
        connection.execute(
            f"UPDATE {table} SET {column} = 'private-scope-material' WHERE {key} = %s",
            (identity,),
        )

    def _seed_one_approval(self, connection, *, include_decision: bool = True) -> None:
        self._seed_execution_truth(connection)
        subject = ActivityPlanApprovalSubject("plan-a")
        connection.execute(
            "INSERT INTO cpk_activity_plans "
            "(plan_id, session_id, base_graph_id, desired_graph_id, status, created_at, "
            "payload) VALUES ('plan-a', 'session-a', 'graph-a', 'graph-b', 'planned', "
            "'2026-08-10T00:00:01Z', '{}'::jsonb)"
        )
        connection.execute(
            "INSERT INTO cpk_approval_requests "
            "(request_id, session_id, plan_id, subject_kind, subject_payload, "
            "review_digest, requested_by, requested_at, required_scope, max_risk, "
            "destructive) VALUES ('request-a', 'session-a', 'plan-a', "
            "'activity-plan', %s, %s, 'operator-a', '2026-08-10T00:00:02Z', "
            "'plan:approve', 'low', false)",
            (Jsonb(subject.descriptor()), subject.review_digest),
        )
        if include_decision:
            connection.execute(
                "INSERT INTO cpk_approval_decisions "
                "(decision_id, request_id, actor_id, decision, scope, decided_at) "
                "VALUES ('decision-a', 'request-a', 'operator-b', 'approved', "
                "'plan:approve', '2026-08-10T00:00:03Z')"
            )

    def _package_connection(self):
        connection = psycopg.connect(self.database_url)
        connection.execute(f'SET search_path TO "{self.schema}"')
        connection.commit()
        return connection

    @staticmethod
    def _decision_service(connection_factory) -> ApprovalCommandService:
        identities = iter(("package-decision", "package-action"))
        return ApprovalCommandService(
            lambda: PostgresUnitOfWork(connection_factory),
            clock=lambda: "2026-08-10T00:00:04.000001Z",
            id_factory=lambda: next(identities),
        )

    @staticmethod
    def _decision_command() -> DecideApproval:
        return DecideApproval(
            session_id="session-a",
            request_id="request-a",
            actor_id="operator-b",
            actor_scopes=(PolicyScope.PLAN_APPROVE,),
            decision=ApprovalDecisionKind.APPROVED,
            idempotency_key=IdempotencyKey("package-decision-key"),
        )

    @staticmethod
    def _seed_execution_truth(connection) -> None:
        connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        seed_authored_graphs(
            connection,
            workspace_id="workspace-a",
            graph_ids=("graph-a", "graph-b"),
        )
        connection.execute(
            "INSERT INTO cpk_operation_sessions "
            "(session_id, workspace_id, actor_id, title, status, created_at) "
            "VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open', "
            "'2026-08-10T00:00:00Z')"
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

    def _seed_legacy_plan_approval(self, connection) -> None:
        self._seed_execution_truth(connection)
        connection.execute(
            "INSERT INTO cpk_activity_plans "
            "(plan_id, session_id, base_graph_id, desired_graph_id, status, created_at, "
            "payload) VALUES ('plan-a', 'session-a', 'graph-a', 'graph-b', 'planned', "
            "'2026-08-10T00:00:01Z', '{}'::jsonb)"
        )
        connection.execute(
            "INSERT INTO cpk_approval_requests "
            "(request_id, session_id, plan_id, requested_by, requested_at, "
            "required_scope, max_risk, destructive) VALUES "
            "('request-a', 'session-a', 'plan-a', 'operator-a', "
            "'2026-08-10T00:00:02Z', 'plan:approve', 'low', false)"
        )

    @staticmethod
    def _target_identities(connection):
        rows = connection.execute(
            """
            SELECT constraints.conname, constraints.oid,
                   pg_get_constraintdef(constraints.oid, false),
                   constraints.contype::text, constraints.convalidated
            FROM pg_constraint AS constraints
            JOIN pg_class AS relation ON relation.oid = constraints.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND (
                (relation.relname = 'cpk_approval_requests'
                 AND constraints.conname = 'cpk_approval_requests_scope_check')
                OR
                (relation.relname = 'cpk_approval_decisions'
                 AND constraints.conname = 'cpk_approval_decisions_scope_check')
              )
            ORDER BY constraints.conname, constraints.oid
            """
        ).fetchall()
        return {row[0]: row[1:] for row in rows}

    def _lookalikes(self, connection):
        return tuple(
            connection.execute(
                """
                SELECT namespace.nspname, relation.relname, constraints.conname,
                       pg_get_constraintdef(constraints.oid, false)
                FROM pg_constraint AS constraints
                JOIN pg_class AS relation ON relation.oid = constraints.conrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE (namespace.nspname = current_schema()
                       AND relation.relname = 'scope_lookalike')
                   OR namespace.nspname = %s
                ORDER BY namespace.nspname, relation.relname, constraints.conname
                """,
                (f"{self.schema}_other",),
            ).fetchall()
        )

    @staticmethod
    def _approval_rows(connection):
        requests = tuple(
            connection.execute(
                "SELECT request_id, required_scope, subject_kind, subject_payload, "
                "review_digest FROM cpk_approval_requests ORDER BY request_id"
            ).fetchall()
        )
        decisions = tuple(
            connection.execute(
                "SELECT decision_id, scope FROM cpk_approval_decisions "
                "ORDER BY decision_id"
            ).fetchall()
        )
        return requests, decisions

    @staticmethod
    def _history(connection):
        return tuple(
            connection.execute(
                "SELECT version, name, checksum_sha256, applied_at "
                "FROM cpk_schema_migrations ORDER BY version"
            ).fetchall()
        )

    def _snapshot(self, connection):
        columns = tuple(
            connection.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                ORDER BY table_name, ordinal_position
                """
            ).fetchall()
        )
        return (
            self._history(connection),
            tuple(
                connection.execute(
                    "SELECT to_jsonb(requests) FROM cpk_approval_requests AS requests "
                    "ORDER BY request_id"
                ).fetchall()
            ),
            tuple(
                connection.execute(
                    "SELECT to_jsonb(decisions) FROM cpk_approval_decisions AS decisions "
                    "ORDER BY decision_id"
                ).fetchall()
            ),
            tuple(sorted(self._target_identities(connection).items())),
            columns,
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


class _HeldCommitConnection:
    def __init__(self, delegate, at_commit, release):
        self._delegate = delegate
        self._at_commit = at_commit
        self._release = release

    def execute(self, query, params=()):
        return self._delegate.execute(query, params)

    def commit(self):
        self._at_commit.set()
        if not self._release.wait(timeout=5):
            raise TimeoutError("package commit was not released")
        self._delegate.commit()

    def rollback(self):
        self._delegate.rollback()

    def close(self):
        self._delegate.close()


class _FailingCommitConnection:
    def __init__(self, delegate, commit_attempted):
        self._delegate = delegate
        self._commit_attempted = commit_attempted

    def execute(self, query, params=()):
        return self._delegate.execute(query, params)

    def commit(self):
        self._commit_attempted.set()
        raise RuntimeError("private package commit")

    def rollback(self):
        self._delegate.rollback()

    def close(self):
        self._delegate.close()


class _ScriptedCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _ScriptedConnection:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def execute(self, query, parameters=()):
        self.calls.append((query, parameters))
        return _ScriptedCursor(self._rows)
