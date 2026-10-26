"""#2037 exact retained-schema transition through its PostgreSQL owner."""

from dataclasses import replace
from contextlib import contextmanager
from hashlib import sha256
import os
import unittest
from uuid import uuid4

import psycopg

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, IdentityContractError, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations import postgres
from control_plane_kit_operations.postgres import schema
from control_plane_kit_operations.postgres.current_data_validation import validate_current_rows
from control_plane_kit_operations.postgres.current_schema_contract import PRE_WORKSPACE_PERMISSION_SCHEMA_CONTRACT
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests import test_postgres_schema as schema_fixtures


class PostgresWorkspacePermissionUpgradeTests(LifecycleLockFixture, unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker PostgreSQL")
        self.schema = "permission_upgrade_" + uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        old_sql = schema._CURRENT_SCHEMA_SQL.split("CREATE TABLE cpk_workspace_permission_installation (", 1)[0]
        old_sql = old_sql.rstrip() + "\n"
        self.assertEqual(sha256(old_sql.encode()).hexdigest(),
                         "a38cb3590078ab33454ff26c14a9e1a3f3705aff01834533209aada14aa03dc1")
        self.connection.execute(old_sql)
        schema_fixtures.PostgresSchemaFoundationTests._seed_minimal_execution_truth(self)
        validate_current_rows(self.connection)
        self.admin = PrincipalIdentity("issuer-a", "admin", PrincipalKind.OPERATOR)
        self.member = PrincipalIdentity("issuer-a", "member", PrincipalKind.OPERATOR)
        self.policy = operations.WorkspaceAdministrationPolicy("installation-a", (self.admin,), (self.admin, self.member))
        self.context = self.policy.authorize(AuthenticatedPrincipal(self.admin))

    @staticmethod
    def clock():
        return "2026-10-25T20:00:00Z"

    def unit_of_work(self):
        return postgres.PostgresUnitOfWork(lambda: psycopg.connect(
            self.database_url, options=f"-c search_path={self.schema}"))

    def public(self, name):
        value = getattr(postgres, name, None)
        self.assertIsNotNone(value, "explicit retained workspace-permission upgrade is unavailable: " + name)
        return value

    def seed(self, workspace="workspace-a"):
        return operations.GrantWorkspacePermissions(self.context, workspace, self.member,
            (PolicyScope.INSTANCE_WORKSPACE_READ,), 0, IdempotencyKey("seed-" + workspace))

    def plan(self, grants=None):
        return self.public("WorkspacePermissionUpgradePlan")(
            context=self.context, grants=(self.seed(),) if grants is None else grants,
            idempotency_key=IdempotencyKey("upgrade-workspace-permissions"),
        )

    def upgrade(self, plan=None, *, connection=None, policy=None, id_factory=None):
        plan = self.plan() if plan is None else plan
        return self.public("upgrade_workspace_permissions")(
            self.connection if connection is None else connection, plan,
            administration_policy=self.policy if policy is None else policy,
            clock=self.clock, id_factory=id_factory or (lambda: uuid4().hex),
        )

    def old_rows(self):
        return {relation.name: self.connection.execute(
            f'SELECT to_jsonb(retained) FROM "{relation.name}" AS retained ORDER BY to_jsonb(retained)::text'
        ).fetchall() for relation in PRE_WORKSPACE_PERMISSION_SCHEMA_CONTRACT.relations}

    def old_objects(self):
        names = [item.name for item in PRE_WORKSPACE_PERMISSION_SCHEMA_CONTRACT.relations]
        names += [item.name for item in PRE_WORKSPACE_PERMISSION_SCHEMA_CONTRACT.indexes]
        return self.connection.execute("SELECT relname,oid FROM pg_class WHERE relnamespace=current_schema()::regnamespace "
                                       "AND relname=ANY(%s) ORDER BY relname", (names,)).fetchall()

    def assert_no_policy_tables(self):
        names = ["cpk_workspace_permission_installation", "cpk_workspace_permissions",
                 "cpk_workspace_permission_receipts", "cpk_workspace_administration_receipts"]
        self.assertEqual(self.connection.execute("SELECT relname FROM pg_class "
            "WHERE relnamespace=current_schema()::regnamespace AND relname=ANY(%s)", (names,)).fetchall(), [])

    def test_exact_upgrade_preserves_populated_history_and_old_object_identity(self):
        plan = self.plan()
        before, objects = self.old_rows(), self.old_objects()
        for table in ("cpk_graph_versions", "cpk_approval_decisions", "cpk_activity_runs", "cpk_activity_events"):
            self.assertTrue(before[table], "preservation fixture must contain actual history")
        result = self.upgrade(plan)
        self.assertFalse(result.replayed)
        self.assertEqual(self.old_rows(), before)
        self.assertEqual(self.old_objects(), objects)
        postgres.install_schema(self.connection)
        context = operations.WorkspaceContextResolver(self.unit_of_work, installation_id="installation-a").resolve(
            AuthenticatedPrincipal(self.member), "workspace-a")
        self.assertEqual(context.granted_scopes, (PolicyScope.INSTANCE_WORKSPACE_READ,))

    def test_exact_seed_replay_after_revoke_and_identity_removal_does_not_restore_grants(self):
        plan = self.plan()
        original = self.upgrade(plan)
        service = operations.WorkspacePermissionCommandService(self.unit_of_work, administration_policy=self.policy,
                                                               clock=self.clock, id_factory=lambda: uuid4().hex)
        service.revoke(operations.RevokeWorkspacePermissions(self.context, "workspace-a", self.member,
            (PolicyScope.INSTANCE_WORKSPACE_READ,), 1, IdempotencyKey("revoke-after-seed")))
        removed = operations.WorkspaceAdministrationPolicy("installation-a", (self.admin,), (self.admin,))
        replay = self.upgrade(plan, policy=removed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, original.receipt)
        with self.assertRaises(IdentityContractError):
            operations.WorkspaceContextResolver(self.unit_of_work, installation_id="installation-a").resolve(
                AuthenticatedPrincipal(self.member), "workspace-a")
        changed = replace(plan, grants=(replace(self.seed(), scopes=(PolicyScope.PLAN_REQUEST,)),))
        with self.assertRaises(self.public("WorkspacePermissionUpgradeError")):
            self.upgrade(changed)

    def test_unknown_schema_and_stale_plan_refuse_without_changes(self):
        plan = self.plan()
        before = self.old_rows()
        with self.assertRaises(self.public("WorkspacePermissionUpgradeError")):
            self.upgrade(replace(plan, target_contract_sha256="0" * 64))
        self.connection.execute("CREATE TABLE unexpected_retained_table (value text)")
        with self.assertRaises(self.public("WorkspacePermissionUpgradeError")):
            self.upgrade(plan)
        self.assertEqual(self.old_rows(), before)
        self.assert_no_policy_tables()
        self.assertIsNotNone(self.connection.execute("SELECT to_regclass('unexpected_retained_table')").fetchone()[0])

    def test_failed_second_seed_rolls_back_ddl_binding_and_first_seed(self):
        WorkspaceCommandService(self.unit_of_work, clock=self.clock, id_factory=lambda: uuid4().hex).create(
            CreateWorkspace("workspace-b", "Workspace B", "admin", IdempotencyKey("create-b")))
        plan = self.plan((self.seed(), self.seed("workspace-b")))
        before, objects = self.old_rows(), self.old_objects()
        identifiers = iter(("first-seed-receipt", ""))
        calls, observed_receipts = [], []

        def next_id():
            value = next(identifiers)
            calls.append(value)
            if not value:
                observed_receipts.append(self.connection.execute(
                    "SELECT count(*) FROM cpk_workspace_permission_receipts").fetchone()[0])
            return value

        with self.assertRaises(self.public("WorkspacePermissionUpgradeError")):
            self.upgrade(plan, id_factory=next_id)
        self.assertEqual(len(calls), 2)
        self.assertEqual(observed_receipts, [1])
        self.assert_no_policy_tables()
        self.assertEqual(self.old_rows(), before)
        self.assertEqual(self.old_objects(), objects)

    def test_caller_owned_outer_transaction_can_roll_back_upgrade(self):
        plan = self.plan()
        before = self.old_rows()
        self.connection.autocommit = False
        try:
            self.connection.execute("SELECT 1")
            result = self.upgrade(plan)
            self.assertTrue(result.provisional)
            inspection = self.public("inspect_workspace_permission_upgrade")(
                self.connection, plan, administration_policy=self.policy)
            self.assertEqual(inspection.status, "unknown")
            self.assertIsNotNone(self.connection.execute("SELECT to_regclass('cpk_workspace_permissions')").fetchone()[0])
            self.connection.rollback()
        finally:
            self.connection.autocommit = True
        self.assert_no_policy_tables()
        self.assertEqual(self.old_rows(), before)

    def test_authoritative_old_row_validation_follows_write_excluding_locks(self):
        plan = self.plan()

        def writer(factory):
            with factory() as uow:
                uow.stores.connection.execute("UPDATE cpk_graph_versions SET graph_descriptor='{}'::jsonb WHERE graph_id='graph-a'")
                uow.commit()

        def upgrader(factory):
            with factory() as uow:
                uow.stores.connection.execute("SELECT 1")
                result = self.upgrade(plan, connection=uow.stores.connection)
                uow.commit()
                return result

        first, second = self.opposing_commands(writer, upgrader,
            lambda query, parameters: query.startswith("UPDATE cpk_graph_versions"))
        first.result()
        with self.assertRaises(self.public("WorkspacePermissionUpgradeError")):
            second.result()
        self.assert_no_policy_tables()
        self.assertEqual(self.connection.execute("SELECT graph_descriptor FROM cpk_graph_versions WHERE graph_id='graph-a'")
                         .fetchone()[0], {})

    def test_lost_commit_acknowledgement_is_uncertain_and_inspected_without_redispatch(self):
        plan = self.plan()
        original = self.connection

        class LostAcknowledgement:
            def __getattr__(self, name):
                return getattr(original, name)

            @contextmanager
            def transaction(self):
                with original.transaction():
                    yield
                raise OSError("synthetic lost commit acknowledgement")

        with self.assertRaises(self.public("WorkspacePermissionUpgradeUncertain")):
            self.upgrade(plan, connection=LostAcknowledgement())
        inspection = self.public("inspect_workspace_permission_upgrade")(
            original, plan, administration_policy=self.policy)
        self.assertEqual(inspection.status, "matching-committed")
        self.assertEqual(inspection.receipt.installation_id, "installation-a")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_workspace_permission_receipts")
                         .fetchone()[0], 1)

    def test_inspection_does_not_treat_a_busy_upgrade_or_outer_transaction_as_noncommit(self):
        plan = self.plan()
        inspect = self.public("inspect_workspace_permission_upgrade")
        with self.lock_connection() as blocker:
            blocker.execute(schema._ADVISORY_LOCK_SQL)
            result = inspect(self.connection, plan, administration_policy=self.policy)
            self.assertEqual(result.status, "unknown")
        result = inspect(self.connection, plan, administration_policy=self.policy)
        self.assertEqual(result.status, "supported-prior")
        self.assert_no_policy_tables()


if __name__ == "__main__":
    unittest.main()
