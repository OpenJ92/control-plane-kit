"""#2038 separate management grants through their real durable owners."""

from dataclasses import replace
import unittest
from uuid import uuid4

import psycopg

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority, RegisterRuntimeAuthorityCommand,
    RegisteredRuntimeAuthorityStatus, RevokeRuntimeAuthorityCommand,
    RuntimeAuthorityRegistrationService,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests import test_postgres_workspace_permissions as permission_fixtures


class PostgresManagedRuntimeAssignmentTests(LifecycleLockFixture, unittest.TestCase):
    def setUp(self):
        # Reuse established isolated schema/workspace setup, without inheriting
        # its TestCase and accidentally collecting another copy of its laws.
        permission_fixtures.PostgresWorkspacePermissionTests.setUp(self)
        self.admin_policy = operations.WorkspaceAdministrationPolicy(
            "installation-a", (self.admin,), (self.admin, self.member))
        self.context = self.admin_policy.authorize(AuthenticatedPrincipal(self.admin))
        operations.WorkspacePermissionCommandService(self.unit_of_work,
            administration_policy=self.admin_policy, clock=self.clock,
            id_factory=lambda: uuid4().hex).initialize(
                self.context, idempotency_key=IdempotencyKey("bind-installation"))
        self.reference = RuntimeAuthorityReference("local-docker")
        self.authorities = {}
        for workspace in ("workspace-a", "workspace-b"):
            self.authorities[workspace] = self.register(workspace)

    @staticmethod
    def clock():
        return "2026-10-25T20:00:00Z"

    def connect(self):
        return psycopg.connect(self.database_url, options=f"-c search_path={self.schema}")

    def unit_of_work(self):
        return PostgresUnitOfWork(self.connect)

    def public(self, name):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, "durable managed-runtime assignment is unavailable: " + name)
        return value

    def register(self, workspace):
        return RuntimeAuthorityRegistrationService(self.unit_of_work).register(
            RegisterRuntimeAuthorityCommand(workspace_id=workspace, authority_ref=self.reference,
                runtime_kind=RuntimeKind.DOCKER, authority=LocalDockerSocketAuthority(),
                admitted_by=self.admin.subject_id, admitted_at=self.clock(),
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,)))

    def capability_policy(self, *, empty=False):
        capability = self.public("ManagedRuntimeCapability")("installer-local-docker")
        return self.public("ManagedRuntimeCapabilityPolicy")(
            "installation-a", () if empty else (capability,))

    def service(self, factory=None, *, empty=False):
        return self.public("ManagedRuntimeAssignmentCommandService")(
            factory or self.unit_of_work, administration_policy=self.admin_policy,
            capability_policy=self.capability_policy(empty=empty),
            clock=self.clock, id_factory=lambda: uuid4().hex)

    def reads(self):
        return self.public("ManagedRuntimeAssignmentReadService")(
            self.unit_of_work, administration_policy=self.admin_policy)

    def selector(self, *, empty=False):
        return self.public("ManagedRuntimeSelectionService")(
            self.unit_of_work, capability_policy=self.capability_policy(empty=empty))

    def grant(self, *, workspace="workspace-a", runtime="docker", revision=0, key="grant-a"):
        return self.public("GrantManagedRuntimeAccess")(
            context=self.context, workspace_id=workspace, runtime_id=runtime, authority_ref=self.reference,
            registration_id=self.authorities[workspace].registration_id,
            capability_id="installer-local-docker", expected_revision=revision,
            idempotency_key=IdempotencyKey(key))

    def revoke(self, *, revision=1, key="revoke-a"):
        return self.public("RevokeManagedRuntimeAccess")(
            context=self.context, workspace_id="workspace-a", runtime_id="docker",
            authority_ref=self.reference, expected_revision=revision, idempotency_key=IdempotencyKey(key))

    def inspect(self):
        return self.reads().inspect(self.context, workspace_id="workspace-a", runtime_id="docker",
                                    authority_ref=self.reference)

    def history(self):
        return self.reads().history(self.context, workspace_id="workspace-a", runtime_id="docker",
                                    authority_ref=self.reference, limit=10).items

    def select(self, *, workspace="workspace-a", runtime="docker", empty=False):
        return self.selector(empty=empty).select(workspace_id=workspace, runtime_id=runtime,
                                                 authority=self.authorities[workspace])

    @staticmethod
    def authority_lock(query, parameters):
        return "FROM cpk_runtime_authorities" in query and "FOR UPDATE" in query

    def revoke_authority(self, factory=None):
        return RuntimeAuthorityRegistrationService(factory or self.unit_of_work).revoke(
            RevokeRuntimeAuthorityCommand("workspace-a", self.reference,
                                          (PolicyScope.RUNTIME_AUTHORITY_REVOKE,)))

    def test_registered_authority_needs_exact_separate_assignment_and_installer_capability(self):
        service = self.service()
        self.assertIsNone(self.select())
        service.grant(self.grant())
        selected = self.select()
        self.assertEqual(selected.capability_id, "installer-local-docker")
        self.assertEqual(selected.registration_id, self.authorities["workspace-a"].registration_id)
        self.assertEqual(selected.revision, 1)
        self.assertIsNone(self.select(workspace="workspace-b"))
        self.assertIsNone(self.select(runtime="other-runtime"))
        self.assertIsNone(self.select(empty=True))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_runtime_authority_deliveries")
                         .fetchone()[0], 0)
        rendered = selected.descriptor()
        self.assertNotIn("socket_path", rendered)
        self.assertNotIn("endpoint", rendered)
        self.assertNotIn("credential", rendered)

    def test_old_grant_replay_does_not_restore_revoked_assignment_or_require_removed_capability(self):
        service = self.service()
        command = self.grant()
        original = service.grant(command)
        service.revoke(self.revoke())
        replay = self.service(empty=True).grant(command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, original.receipt)
        current = self.inspect()
        self.assertEqual((current.status, current.revision), ("revoked", 2))
        self.assertIsNone(self.select())
        self.assertEqual(len(self.history()), 2)

    def test_current_admin_capability_registration_and_revision_are_required(self):
        service = self.service()
        command = self.grant()
        error = self.public("ManagedRuntimeAssignmentError")
        with self.assertRaises(error):
            service.grant(replace(command, capability_id="unadmitted"))
        with self.assertRaises(error):
            service.grant(replace(command, registration_id=self.authorities["workspace-b"].registration_id))
        ordinary = operations.WorkspaceAdministrationContext(self.member, "installation-a")
        with self.assertRaises(operations.WorkspacePermissionDenied):
            service.grant(replace(command, context=ordinary))
        service.grant(command)
        with self.assertRaises(error):
            service.grant(replace(command, idempotency_key=IdempotencyKey("stale")))
        with self.assertRaises(error):
            service.grant(replace(command, capability_id="unadmitted"))
        with self.assertRaises(operations.WorkspacePermissionDenied):
            service.grant(replace(command, context=ordinary))
        self.assertEqual(self.inspect().revision, 1)
        self.assertEqual(len(self.history()), 1)

    def test_authority_revoke_preserves_tombstone_and_truthful_cause_without_lifecycle_repair(self):
        service = self.service()
        command = self.grant()
        original = service.grant(command)
        self.revoke_authority()
        current = self.inspect()
        self.assertEqual((current.status, current.revision), ("revoked", 2))
        causal = self.history()[-1]
        self.assertEqual(causal.operation, "authority-revoked")
        self.assertIsNone(causal.actor)
        self.assertEqual(causal.registration_id, self.authorities["workspace-a"].registration_id)
        self.assertTrue(service.grant(command).replayed)
        self.assertEqual(service.grant(command).receipt, original.receipt)
        self.assertIsNone(self.select())
        # Existing owner INSERT cannot reactivate its retained deterministic PK.
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.register("workspace-a")
        self.assertEqual(self.inspect(), current)
        # Pure selection law, not a manufactured DB reactivation or live claim:
        # even this same-ID active value cannot erase the durable tombstone.
        self.assertIsNone(self.public("select_managed_runtime_assignment")(
            installation_id="installation-a", workspace_id="workspace-a", runtime_id="docker",
            authority=self.authorities["workspace-a"], assignment=current,
            capability_policy=self.capability_policy()))
        self.revoke_authority()
        self.assertEqual(self.inspect(), current)
        self.assertEqual(len(self.history()), 2)

    def test_grant_before_authority_revoke_is_then_invalidated_in_observable_order(self):
        command = self.grant()
        first, second = self.opposing_commands(
            lambda factory: self.service(factory).grant(command),
            lambda factory: self.revoke_authority(factory), self.authority_lock)
        first.result()
        second.result()
        self.assertEqual((self.inspect().status, self.inspect().revision), ("revoked", 2))
        self.assertIsNone(self.select())
        self.assertEqual(len(self.history()), 2)

    def test_authority_revoke_before_new_grant_denies_without_resurrection(self):
        self.service().grant(self.grant())
        # The leader commits invalidation revision2 before releasing its lock.
        # Matching that revision makes revoked authority, not stale revision,
        # the required reason this otherwise-valid later grant is refused.
        later = self.grant(revision=2, key="later-grant")
        first, second = self.opposing_commands(
            lambda factory: self.revoke_authority(factory),
            lambda factory: self.service(factory).grant(later), self.authority_lock)
        first.result()
        with self.assertRaises(self.public("ManagedRuntimeAssignmentError")):
            second.result()
        self.assertEqual((self.inspect().status, self.inspect().revision), ("revoked", 2))
        self.assertIsNone(self.select())
        self.assertEqual(len(self.history()), 2)

    def test_failed_invalidation_receipt_rolls_back_authority_and_assignment_together(self):
        self.service().grant(self.grant())
        before, history = self.inspect(), self.history()
        self.connection.execute("ALTER TABLE cpk_managed_runtime_assignment_receipts "
                                "ADD CONSTRAINT reject_new_receipt CHECK (false) NOT VALID")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.revoke_authority()
        with self.unit_of_work() as uow:
            authority = uow.stores.runtime_authorities.get("workspace-a", self.reference)
        self.assertIs(authority.status, RegisteredRuntimeAuthorityStatus.ACTIVE)
        self.assertEqual(self.inspect(), before)
        self.assertEqual(self.history(), history)
        self.assertIsNotNone(self.select())

    def test_competing_revision_has_one_winner_and_ordinary_revoke_survives_capability_removal(self):
        self.service().grant(self.grant())
        first, second = self.opposing_commands(
            lambda factory: self.service(factory).grant(self.grant(revision=1, key="first")),
            lambda factory: self.service(factory).grant(self.grant(revision=1, key="second")),
            self.authority_lock)
        first.result()
        with self.assertRaises(self.public("ManagedRuntimeAssignmentError")):
            second.result()
        self.assertEqual(self.inspect().revision, 2)
        self.service(empty=True).revoke(self.revoke(revision=2))
        self.assertEqual((self.inspect().status, self.inspect().revision), ("revoked", 3))
        self.assertIsNone(self.select())


if __name__ == "__main__":
    unittest.main()
