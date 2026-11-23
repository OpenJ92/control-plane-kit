"""#2117 exact predecessor, preserved truth, and transaction acknowledgement."""
from contextlib import contextmanager
from dataclasses import replace
import os
import unittest
from uuid import uuid4

import psycopg
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_operations import postgres
from control_plane_kit_operations.postgres import current_schema_contract as contracts
from control_plane_kit_operations.postgres.schema import _ADVISORY_LOCK_SQL
from tests.historical_schema_fixture import historical_sql, catalog_sha256, namespace_state
from tests import test_postgres_schema as schema_fixtures


class PostgresCommandDiagnosticUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        self.schema = "command_upgrade_" + uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        self.connection.execute(historical_sql("pre_command_diagnostics"))
        self.assertEqual(catalog_sha256(self.connection),
            "75b6b900f70a170376c1133af7a066df11b27a147c29075230c26a94ab219d25")
        schema_fixtures.PostgresSchemaFoundationTests._seed_minimal_execution_truth(self)
        self.admin = PrincipalIdentity("issuer-a", "admin", PrincipalKind.OPERATOR)
        self.principal = AuthenticatedPrincipal(self.admin)

    def public(self, name):
        value = getattr(postgres, name, None)
        self.assertIsNotNone(value, "missing explicit diagnostic schema transition: " + name)
        return value

    def policy_and_plan(self):
        policy = self.public("CommandDiagnosticUpgradePolicy")("installation-a",
            self.connection.execute("SELECT current_database()").fetchone()[0], self.schema, (self.admin,))
        plan = policy.plan(self.principal)
        return policy, plan

    def upgrade(self, policy, plan, connection=None, principal=None):
        return self.public("upgrade_command_diagnostics")(
            self.connection if connection is None else connection, plan,
            principal=self.principal if principal is None else principal, administration_policy=policy)

    def inspect(self, policy, plan):
        return self.public("inspect_command_diagnostic_upgrade")(self.connection, plan,
            principal=self.principal, administration_policy=policy)

    def test_command_upgrade_exact_predecessor_preserves_rows_and_replays(self):
        policy, plan = self.policy_and_plan()
        before_objects, before_rows, _ = namespace_state(self.connection)
        self.assertEqual(self.inspect(policy, plan).state, "source")
        result = self.upgrade(policy, plan)
        self.assertFalse(result.replayed)
        self.assertFalse(result.provisional)
        self.assertEqual(result.plan_fingerprint, plan.fingerprint)
        self.assertEqual(self.inspect(policy, plan).state, "target")
        self.assertTrue(self.upgrade(policy, plan).replayed)
        after_objects, after_rows, digest = namespace_state(self.connection)
        self.assertEqual(before_objects, after_objects)
        for row, in after_rows["cpk_execution_command_receipts"]:
            self.assertIsNone(row.pop("diagnostic"))
        self.assertEqual(before_rows, after_rows)
        self.assertEqual(digest, "47db4dbe188b09d0a60a0bdee0fc678a49d463335e631fd359ca35d7c6091415")
        self.assertEqual(contracts.COMMAND_DIAGNOSTIC_UPGRADE_TARGET_SCHEMA_CONTRACT_SHA256, digest)
        postgres.install_schema(self.connection)

    def test_command_upgrade_wrong_identity_target_or_source_refuses_without_mutation(self):
        policy, plan = self.policy_and_plan()
        before = namespace_state(self.connection)
        error = self.public("CommandDiagnosticUpgradeError")
        with self.assertRaises(error):
            self.upgrade(policy, plan, principal=AuthenticatedPrincipal(
                PrincipalIdentity("issuer-a", "foreign", PrincipalKind.OPERATOR)))
        for change in ({"database_name": "foreign"}, {"schema_name": "foreign"},
                {"source_contract_sha256": "0" * 64}, {"actor": PrincipalIdentity("issuer-a", "foreign", PrincipalKind.OPERATOR)}):
            with self.subTest(change=change), self.assertRaises(error):
                self.upgrade(policy, replace(plan, **change))
        self.assertEqual(namespace_state(self.connection), before)
        self.connection.execute("CREATE TABLE unexpected(value text)")
        before = namespace_state(self.connection)
        with self.assertRaises(error):
            self.upgrade(policy, plan)
        self.assertEqual(namespace_state(self.connection), before)

    def test_command_upgrade_outer_transaction_is_provisional_and_rollback_preserves_source(self):
        policy, plan = self.policy_and_plan()
        self.connection.autocommit = False
        try:
            self.connection.execute("SELECT 1")
            result = self.upgrade(policy, plan)
            self.assertTrue(result.provisional)
            self.connection.rollback()
        finally:
            self.connection.autocommit = True
        self.assertEqual(self.inspect(policy, plan).state, "source")

    def test_command_upgrade_lost_commit_acknowledgement_requires_inspection(self):
        policy, plan = self.policy_and_plan()
        connection = self.connection
        class LostAcknowledgement:
            def __getattr__(self, name):
                return getattr(connection, name)
            @contextmanager
            def transaction(self):
                with connection.transaction():
                    yield
                raise RuntimeError("private-commit-message")
        with self.assertRaises(self.public("CommandDiagnosticUpgradeUncertain")) as raised:
            self.upgrade(policy, plan, LostAcknowledgement())
        self.assertNotIn("private-commit-message", str(raised.exception))
        self.assertEqual(self.inspect(policy, plan).state, "target")

    def test_command_upgrade_ddl_failure_rolls_back_and_preserves_primary_category(self):
        policy, plan = self.policy_and_plan()
        connection = self.connection
        class RefuseDdl:
            def __getattr__(self, name):
                return getattr(connection, name)
            def execute(self, query, *args, **kwargs):
                if "ALTER TABLE" in str(query):
                    raise RuntimeError("private-ddl-message")
                return connection.execute(query, *args, **kwargs)
        before = namespace_state(connection)
        with self.assertRaises(self.public("CommandDiagnosticUpgradeError")) as raised:
            self.upgrade(policy, plan, RefuseDdl())
        self.assertEqual(raised.exception.category, "transition-failed")
        self.assertNotIn("private-ddl-message", str(raised.exception))
        self.assertEqual(namespace_state(connection), before)

    def test_command_upgrade_namespace_lock_respects_stricter_timeout(self):
        policy, plan = self.policy_and_plan()
        blocker = psycopg.connect(self.database_url, options=f"-c search_path={self.schema}")
        self.addCleanup(blocker.close)
        blocker.execute(_ADVISORY_LOCK_SQL)
        self.connection.execute("SET lock_timeout='50ms'")
        try:
            with self.assertRaises(self.public("CommandDiagnosticUpgradeError")):
                self.upgrade(policy, plan)
            self.assertEqual(self.connection.execute("SHOW lock_timeout").fetchone()[0], "50ms")
        finally:
            blocker.rollback()
        self.assertEqual(self.inspect(policy, plan).state, "source")

    def test_command_upgrade_concurrent_callers_converge_without_duplicate_transition(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        policy, plan = self.policy_and_plan()
        barrier = Barrier(2, timeout=10)
        def attempt():
            with psycopg.connect(self.database_url, autocommit=True,
                    options=f"-c search_path={self.schema}") as connection:
                barrier.wait()
                return self.upgrade(policy, plan, connection)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(attempt), pool.submit(attempt)
            results = (first.result(timeout=30), second.result(timeout=30))
        self.assertEqual(sorted(result.replayed for result in results), [False, True])
        self.assertTrue(all(not result.provisional for result in results))
        self.assertEqual(self.inspect(policy, plan).state, "target")

    def test_command_upgrade_post_ddl_failure_and_failed_savepoint_exit_keep_primary_cause(self):
        policy, plan = self.policy_and_plan()
        connection = self.connection
        error_type = self.public("CommandDiagnosticUpgradeError")
        class FailAfterDdl:
            def __getattr__(self, name):
                return getattr(connection, name)
            def execute(self, query, *args, **kwargs):
                result = connection.execute(query, *args, **kwargs)
                if "ALTER TABLE" in str(query):
                    raise error_type("schema-mismatch")
                return result
        class LostSavepointExit(FailAfterDdl):
            @contextmanager
            def transaction(self):
                try:
                    with connection.transaction():
                        yield
                except Exception:
                    raise RuntimeError("private-savepoint-exit") from None
        connection.autocommit = False
        try:
            # A caller-owned row outside the namespace contract survives an inner
            # refusal. Put it in pg_temp so the upgrade's namespace stays exact.
            connection.execute("CREATE TEMP TABLE outer_caller_truth(value text)")
            connection.execute("INSERT INTO outer_caller_truth VALUES ('retained')")
            for wrapper, expected in ((FailAfterDdl(), error_type),
                    (LostSavepointExit(), self.public("CommandDiagnosticUpgradeUncertain"))):
                with self.subTest(wrapper=type(wrapper).__name__), self.assertRaises(expected) as raised:
                    self.upgrade(policy, plan, wrapper)
                self.assertEqual(raised.exception.category, "schema-mismatch")
                self.assertIsNone(raised.exception.__context__)
                self.assertEqual(connection.execute("SELECT value FROM outer_caller_truth").fetchall(), [("retained",)])
                self.assertEqual(catalog_sha256(connection),
                    "75b6b900f70a170376c1133af7a066df11b27a147c29075230c26a94ab219d25")
            connection.rollback()
        finally:
            connection.autocommit = True

    def test_command_upgrade_success_restores_stricter_caller_timeouts(self):
        policy, plan = self.policy_and_plan()
        self.connection.autocommit = False
        try:
            self.connection.execute("SET LOCAL lock_timeout='4s'")
            self.connection.execute("SET LOCAL statement_timeout='20s'")
            self.assertTrue(self.upgrade(policy, plan).provisional)
            self.assertEqual(self.connection.execute("SHOW lock_timeout").fetchone()[0], "4s")
            self.assertEqual(self.connection.execute("SHOW statement_timeout").fetchone()[0], "20s")
            self.connection.rollback()
        finally:
            self.connection.autocommit = True
