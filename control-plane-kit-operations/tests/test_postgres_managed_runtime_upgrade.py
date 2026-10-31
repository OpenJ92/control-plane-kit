"""#2038 one exact retained transition with both workspace and management seeds."""
from contextlib import contextmanager
from dataclasses import replace
import unittest
from uuid import uuid4

from control_plane_kit_core.identity import AuthenticatedPrincipal, IdentityContractError
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
import control_plane_kit_operations as operations
from control_plane_kit_operations import postgres
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority, RegisterRuntimeAuthorityCommand, RuntimeAuthorityRegistrationService,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests import test_postgres_workspace_permission_upgrade as upgrade_fixtures


class PostgresManagedRuntimeUpgradeTests(unittest.TestCase):
    def setUp(self):
        upgrade_fixtures.PostgresWorkspacePermissionUpgradeTests.setUp(self)
        WorkspaceCommandService(self.unit_of_work, clock=self.clock, id_factory=lambda: uuid4().hex).create(
            CreateWorkspace("workspace-b", "Workspace B", "admin", IdempotencyKey("create-b")))
        self.reference = RuntimeAuthorityReference("local-docker")
        self.authorities = {}
        for workspace in ("workspace-a", "workspace-b"):
            self.authorities[workspace] = RuntimeAuthorityRegistrationService(self.unit_of_work).register(
                RegisterRuntimeAuthorityCommand(workspace_id=workspace, authority_ref=self.reference,
                    runtime_kind=RuntimeKind.DOCKER, authority=LocalDockerSocketAuthority(),
                    admitted_by="admin", admitted_at=self.clock(),
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,)))
        self.capabilities = operations.ManagedRuntimeCapabilityPolicy("installation-a",
            (operations.ManagedRuntimeCapability("installer-local-docker"),))

    @staticmethod
    def clock():
        return "2026-10-25T20:00:00Z"

    def unit_of_work(self):
        return upgrade_fixtures.PostgresWorkspacePermissionUpgradeTests.unit_of_work(self)

    def old_rows(self):
        return upgrade_fixtures.PostgresWorkspacePermissionUpgradeTests.old_rows(self)

    def old_objects(self):
        return upgrade_fixtures.PostgresWorkspacePermissionUpgradeTests.old_objects(self)

    def managed_seed(self):
        return operations.GrantManagedRuntimeAccess(self.context, "workspace-a", "docker", self.reference,
            self.authorities["workspace-a"].registration_id, "installer-local-docker", 0, IdempotencyKey("seed-managed-a"))

    def plan(self):
        workspace_seed = operations.GrantWorkspacePermissions(self.context, "workspace-a", self.member,
            (PolicyScope.INSTANCE_WORKSPACE_READ,), 0, IdempotencyKey("seed-workspace-a"))
        try:
            return postgres.WorkspacePermissionUpgradePlan(context=self.context, grants=(workspace_seed,),
                managed_runtime_grants=(self.managed_seed(),), idempotency_key=IdempotencyKey("combined-upgrade"))
        except TypeError as error:
            if "unexpected keyword argument 'managed_runtime_grants'" not in str(error):
                raise
            self.fail("combined retained upgrade has no managed_runtime_grants input")

    def upgrade(self, plan, *, connection=None, policy=None, capabilities=None, id_factory=None):
        return postgres.upgrade_workspace_permissions(self.connection if connection is None else connection,
            plan, administration_policy=self.policy if policy is None else policy,
            capability_policy=self.capabilities if capabilities is None else capabilities,
            clock=self.clock, id_factory=id_factory or (lambda: uuid4().hex))

    def selector(self):
        return operations.ManagedRuntimeSelectionService(self.unit_of_work, capability_policy=self.capabilities)

    def assert_no_policy_tables(self):
        names = ["cpk_workspace_permission_installation", "cpk_workspace_permissions",
                 "cpk_workspace_permission_receipts", "cpk_workspace_administration_receipts",
                 "cpk_managed_runtime_assignments", "cpk_managed_runtime_assignment_receipts"]
        self.assertEqual(self.connection.execute("SELECT relname FROM pg_class "
            "WHERE relnamespace=current_schema()::regnamespace AND relname=ANY(%s)", (names,)).fetchall(), [])

    def test_exact_combined_seeds_preserve_history_and_do_not_enroll_unlisted_authorities(self):
        plan = self.plan()
        # Snapshot AFTER legitimate authority/workspace fixture creation.
        before, objects = self.old_rows(), self.old_objects()
        result = self.upgrade(plan)
        self.assertFalse(result.replayed)
        self.assertEqual(self.old_rows(), before)
        self.assertEqual(self.old_objects(), objects)
        self.assertEqual(result.receipt.managed_seed_count, 1)
        self.assertIsNotNone(self.selector().select(workspace_id="workspace-a", runtime_id="docker",
                                                    authority=self.authorities["workspace-a"]))
        self.assertIsNone(self.selector().select(workspace_id="workspace-b", runtime_id="docker",
                                                authority=self.authorities["workspace-b"]))
        resolved = operations.WorkspaceContextResolver(self.unit_of_work, installation_id="installation-a").resolve(
            AuthenticatedPrincipal(self.member), "workspace-a")
        self.assertEqual(resolved.granted_scopes, (PolicyScope.INSTANCE_WORKSPACE_READ,))
        postgres.install_schema(self.connection)

    def test_combined_replay_with_removed_identity_and_capability_cannot_restore_either_grant(self):
        plan = self.plan()
        original = self.upgrade(plan)
        operations.WorkspacePermissionCommandService(self.unit_of_work, administration_policy=self.policy,
            clock=self.clock, id_factory=lambda: uuid4().hex).revoke(operations.RevokeWorkspacePermissions(
                self.context, "workspace-a", self.member, (PolicyScope.INSTANCE_WORKSPACE_READ,),
                1, IdempotencyKey("revoke-workspace")))
        operations.ManagedRuntimeAssignmentCommandService(self.unit_of_work, administration_policy=self.policy,
            capability_policy=self.capabilities, clock=self.clock, id_factory=lambda: uuid4().hex).revoke(
                operations.RevokeManagedRuntimeAccess(self.context, "workspace-a", "docker", self.reference,
                                                     1, IdempotencyKey("revoke-managed")))
        removed = operations.WorkspaceAdministrationPolicy("installation-a", (self.admin,), (self.admin,))
        replay = self.upgrade(plan, policy=removed,
                             capabilities=operations.ManagedRuntimeCapabilityPolicy("installation-a", ()))
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, original.receipt)
        with self.assertRaises(IdentityContractError):
            operations.WorkspaceContextResolver(self.unit_of_work, installation_id="installation-a").resolve(
                AuthenticatedPrincipal(self.member), "workspace-a")
        self.assertIsNone(self.selector().select(workspace_id="workspace-a", runtime_id="docker",
                                                authority=self.authorities["workspace-a"]))
        changed = replace(plan, managed_runtime_grants=(replace(self.managed_seed(), runtime_id="other-runtime"),))
        with self.assertRaises(postgres.WorkspacePermissionUpgradeError):
            self.upgrade(changed)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_managed_runtime_assignment_receipts")
                         .fetchone()[0], 2)

    def test_failed_management_receipt_rolls_back_both_owners_after_a_real_workspace_seed(self):
        plan = self.plan()
        before, objects = self.old_rows(), self.old_objects()
        identifiers = iter(("workspace-seed-receipt", ""))
        calls, observed_workspace_receipts = [], []

        def next_id():
            value = next(identifiers)
            calls.append(value)
            if not value:
                observed_workspace_receipts.append(self.connection.execute(
                    "SELECT count(*) FROM cpk_workspace_permission_receipts").fetchone()[0])
            return value

        with self.assertRaises(postgres.WorkspacePermissionUpgradeError):
            self.upgrade(plan, id_factory=next_id)
        self.assertEqual(len(calls), 2)
        self.assertEqual(observed_workspace_receipts, [1])
        self.assert_no_policy_tables()
        self.assertEqual(self.old_rows(), before)
        self.assertEqual(self.old_objects(), objects)

    def test_combined_transition_remains_provisional_until_outer_caller_commits(self):
        plan = self.plan()
        before = self.old_rows()
        self.connection.autocommit = False
        try:
            self.connection.execute("SELECT 1")
            result = self.upgrade(plan)
            self.assertTrue(result.provisional)
            inspection = postgres.inspect_workspace_permission_upgrade(
                self.connection, plan, administration_policy=self.policy)
            self.assertEqual(inspection.status, "unknown")
            self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_managed_runtime_assignment_receipts")
                             .fetchone()[0], 1)
        finally:
            self.connection.rollback()
            self.connection.autocommit = True
        self.assert_no_policy_tables()
        self.assertEqual(self.old_rows(), before)

    def test_combined_commit_acknowledgement_loss_is_inspected_without_redispatch(self):
        plan = self.plan()
        original = self.connection

        class LostAcknowledgement:
            def __getattr__(self, name):
                return getattr(original, name)

            @contextmanager
            def transaction(self):
                with original.transaction():
                    yield
                raise OSError("synthetic lost combined commit acknowledgement")

        with self.assertRaises(postgres.WorkspacePermissionUpgradeUncertain):
            self.upgrade(plan, connection=LostAcknowledgement())
        inspection = postgres.inspect_workspace_permission_upgrade(original, plan, administration_policy=self.policy)
        self.assertEqual(inspection.status, "matching-committed")
        self.assertEqual(inspection.receipt.managed_seed_count, 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_workspace_permission_receipts")
                         .fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_managed_runtime_assignment_receipts")
                         .fetchone()[0], 1)

    def test_new_management_seeds_require_installer_admission_and_exact_registration(self):
        plan = self.plan()
        before = self.old_rows()
        with self.assertRaises(postgres.WorkspacePermissionUpgradeError):
            postgres.upgrade_workspace_permissions(self.connection, plan, administration_policy=self.policy,
                                                    clock=self.clock, id_factory=lambda: uuid4().hex)
        self.assert_no_policy_tables()
        with self.assertRaises(postgres.WorkspacePermissionUpgradeError):
            self.upgrade(plan, capabilities=operations.ManagedRuntimeCapabilityPolicy("installation-a", ()))
        self.assert_no_policy_tables()
        wrong_registration = replace(plan, managed_runtime_grants=(replace(self.managed_seed(),
            registration_id=self.authorities["workspace-b"].registration_id),))
        with self.assertRaises(postgres.WorkspacePermissionUpgradeError):
            self.upgrade(wrong_registration)
        self.assert_no_policy_tables()
        self.assertEqual(self.old_rows(), before)
        with self.assertRaises(postgres.WorkspacePermissionUpgradeError):
            replace(plan, managed_runtime_grants=(self.managed_seed(), self.managed_seed()))

    def test_management_seed_correlation_is_strict_on_decode_and_independent_inspection(self):
        plan = self.plan()
        self.upgrade(plan)
        self.connection.execute("UPDATE cpk_workspace_permission_installation "
                                "SET receipt=receipt || '{\"managed_seed_count\":true}'::jsonb")
        with self.assertRaises(postgres.SchemaInstallationError):
            postgres.install_schema(self.connection)
        self.assertEqual(postgres.inspect_workspace_permission_upgrade(
            self.connection, plan, administration_policy=self.policy).status, "unknown")
        self.connection.execute("UPDATE cpk_workspace_permission_installation SET receipt="
            "receipt || jsonb_build_object('managed_seed_count',1,'managed_seed_fingerprint',repeat('0',64))")
        self.assertEqual(postgres.inspect_workspace_permission_upgrade(
            self.connection, plan, administration_policy=self.policy).status, "conflicting-receipt")
        with self.assertRaises(postgres.WorkspacePermissionUpgradeError):
            self.upgrade(plan)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_managed_runtime_assignment_receipts")
                         .fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
