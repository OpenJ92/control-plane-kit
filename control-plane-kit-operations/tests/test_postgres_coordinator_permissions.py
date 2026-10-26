"""Current permission carriage through the existing coordinator and receipts."""

import asyncio
from dataclasses import replace
import inspect
import unittest
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.planning import ManagementBootstrapStage
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.coordinator import (
    ExecuteManagedActivityRun, ExecutionCoordinator, ExecutionCoordinatorDenied,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.records import ExecutionCommandReceiptStatus
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.postgres_effect_attempt_coordinator_fixture import PostgresEffectAttemptCoordinatorFixture
from tests.receiver_health_execution_fixture import ReceiverHealthExecutionFixture
from tests.test_managed_application_chain import ForbiddenRecovery, now


class _CurrentCoordinatorPermission:
    def configure_permissions(self, identity, *, installation):
        self.member = identity
        self.installation = installation
        admin = PrincipalIdentity("coordinator-test", "admin", PrincipalKind.OPERATOR)
        self.admin_policy = operations.WorkspaceAdministrationPolicy(installation, (admin,), (admin, identity))
        self.admin_context = self.admin_policy.authorize(AuthenticatedPrincipal(admin))
        self.permissions = operations.WorkspacePermissionCommandService(self.unit_of_work,
            administration_policy=self.admin_policy, clock=now, id_factory=lambda: uuid4().hex)
        self.permissions.initialize(self.admin_context, idempotency_key=IdempotencyKey("initialize"))
        self.permissions.grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", identity, tuple(PolicyScope), 0, IdempotencyKey("grant")))
        self.original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id=installation).resolve(AuthenticatedPrincipal(identity), "workspace-a")
        self.capabilities = operations.ManagedRuntimeCapabilityPolicy(installation,
            (operations.ManagedRuntimeCapability("local-docker"),))
        self.policy = operations.CurrentWorkspaceEffectPolicy(installation, self.capabilities)

    def require_interface(self):
        self.assertIn("permission_policy", inspect.signature(ExecutionCoordinator).parameters,
            "coordinator cannot carry current effect permission")

    def revoke_operate(self):
        self.permissions.revoke(operations.RevokeWorkspacePermissions(self.admin_context,
            "workspace-a", self.member, (PolicyScope.EXECUTION_OPERATE,), 1, IdempotencyKey("revoke")))

    def receipt(self, execution):
        with self.unit_of_work() as uow:
            return uow.stores.execution.command_receipt_for_idempotency(
                execution.run_id, execution.idempotency_key.value)

    def assert_denial(self, result, execution, activity):
        self.assertEqual(result.status.value, "authorization-denied")
        self.assertEqual(result.effects_attempted, 0)
        self.assertEqual(result.activity_id, activity)
        retained = self.receipt(execution)
        self.assertIs(retained.status, ExecutionCommandReceiptStatus.COMPLETED)
        self.assertEqual(retained.result.status, result.status)
        self.assertEqual(retained.result.effects_attempted, 0)
        self.assertEqual(retained.result.activity_id, activity)
        self.assertEqual(retained.managed_intent.actor, self.original.principal.identity)


class PostgresCoordinatorPermissionTests(
    _CurrentCoordinatorPermission, PostgresEffectAttemptCoordinatorFixture, unittest.TestCase,
):
    def setUp(self):
        super().setUp()
        self.configure_permissions(PrincipalIdentity("coordinator-test", "worker-a", PrincipalKind.WORKER),
            installation="coordinator-permission")

    def composition(self, *, start_policy=True):
        self.require_interface()
        harness = self.coordinator_harness()
        owner = ExecutionCoordinator(self.unit_of_work, lifecycle=harness.lifecycle,
            adapter=harness.adapter, start_service=EffectAttemptStartService(self.unit_of_work,
                id_factory=harness.start_ids, permission_policy=self.policy if start_policy is True else start_policy),
            fold_service=harness.fold, reconciliation_service=harness.reconciliation,
            clock=now, id_factory=harness.coordinator_ids, permission_policy=self.policy)
        command = ExecuteManagedActivityRun(self.coordinator_command(), self.original)
        return owner, harness.adapter, command

    def test_dynamic_composition_refuses_missing_or_different_start_policy(self):
        self.require_interface()
        for policy in (None, operations.CurrentWorkspaceEffectPolicy("other",
                operations.ManagedRuntimeCapabilityPolicy("other", ()))):
            with self.subTest(policy=policy), self.assertRaises(ExecutionCoordinatorDenied):
                self.composition(start_policy=policy)

    def test_dynamic_sync_without_trusted_context_refuses_new_work(self):
        owner, adapter, command = self.composition()
        before = self.attempt_snapshot()
        with self.assertRaises(ExecutionCoordinatorDenied):
            owner.execute(command.execution)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertIsNone(self.receipt(command.execution))

    def test_real_caller_and_receipt_reach_the_current_start_owner(self):
        owner, adapter, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(adapter.runtime_calls), 1)
        receipt = self.receipt(command.execution)
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)
        self.assertEqual(receipt.managed_intent.actor, self.member)

    def test_revoke_before_start_is_durable_denial_and_exact_replay_after_regrant(self):
        owner, adapter, command = self.composition()
        before = self.attempt_snapshot()
        self.revoke_operate()
        result = asyncio.run(owner.execute_managed(command))
        self.assert_denial(result, command.execution, "start-runtime")
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(self.attempt_snapshot(), before)
        self.permissions.grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", self.member, (PolicyScope.EXECUTION_OPERATE,), 2, IdempotencyKey("restore")))
        replay = asyncio.run(owner.execute_managed(command))
        self.assertEqual(replay, result)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_already_admitted_result_can_finish_after_current_revocation(self):
        owner, adapter, command = self.composition()
        from control_plane_kit_core.runtime_effects import RuntimeEffectResult
        def returned(context, request):
            self.revoke_operate()
            return RuntimeEffectResult.succeeded(request.effect_id)
        adapter.results.append(returned)
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(adapter.runtime_calls), 1)
        replay = asyncio.run(owner.execute_managed(command))
        self.assertEqual(replay, result)
        self.assertEqual(len(adapter.runtime_calls), 1)


class PostgresCoordinatorManagementPermissionTests(_CurrentCoordinatorPermission, ReceiverHealthExecutionFixture):
    def health_start_command(self):
        if self.health_activity.operation.stage is not ManagementBootstrapStage.CONNECTOR_CONNECTED:
            return super().health_start_command()
        from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind, RunId
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt
        from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
        context, command = self.execution_context()
        intent = _runtime_effect_intent_for_context(context, self.health_activity)
        identity = EffectAttemptIdentity(RunId(self.run_id), self.health_activity.activity_id.value, 1)
        return StartEffectAttempt(self.request_id, EffectAttemptTransition(EffectAttemptTransitionKind.STARTED,
            identity, runtime_effect_intent_fingerprint(intent)), intent, command.authority, command.fence)

    async def prepared(self, *, native=True):
        stage = (ManagementBootstrapStage.CONNECTOR_CONNECTED if native
            else ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        await self.prepare_health(stage=stage)
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
        self.require_interface()
        context, execution = self.execution_context()
        with self.unit_of_work() as uow:
            runtime = uow.stores.runtime_authorities.get_active_for_update(
                "workspace-a", self.graph.runtimes["docker"].authority_ref)
        self.assignments = operations.ManagedRuntimeAssignmentCommandService(self.unit_of_work,
            administration_policy=self.admin_policy, capability_policy=self.capabilities,
            clock=now, id_factory=lambda: uuid4().hex)
        self.assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", "docker", runtime.authority_ref, runtime.registration_id,
            "local-docker", 0, IdempotencyKey("management")))
        fold = EffectAttemptFoldService(self.unit_of_work, id_factory=self.ids["effect-fold"])
        owner = ExecutionCoordinator(self.unit_of_work,
            lifecycle=RunLifecycleCommandService(self.unit_of_work, clock=now, id_factory=self.ids["lifecycle"]),
            adapter=self.runtime, start_service=EffectAttemptStartService(self.unit_of_work,
                id_factory=self.ids["effect-start"], health_receiver_decoders=self.registry,
                permission_policy=self.policy), fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(self.unit_of_work, ForbiddenRecovery(), fold),
            clock=now, id_factory=self.ids["execution"], managed_health=self.health,
            health_signing_authority=self.reload_service(), permission_policy=self.policy)
        return owner, ExecuteManagedActivityRun(execution, self.original), runtime

    async def test_native_start_carries_current_selection_without_signing_scopes(self):
        owner, command, _ = await self.prepared()
        required = (PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE)
        from control_plane_kit_core.identity import WorkspaceGrant
        original = AuthenticatedPrincipal(self.member, (WorkspaceGrant("workspace-a", required),)).command_context("workspace-a")
        result = await owner.execute_managed(replace(command, context=original))
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(self.health.signed_reads, [])
        self.assertEqual(self.tracker.active, 0)

    async def test_revoked_assignment_prevents_native_start_and_port(self):
        owner, command, runtime = await self.prepared()
        before = self.history()
        self.assignments.revoke(operations.RevokeManagedRuntimeAccess(self.admin_context,
            "workspace-a", "docker", runtime.authority_ref, 1, IdempotencyKey("revoke-management")))
        result = await owner.execute_managed(command)
        self.assert_denial(result, command.execution, self.health_activity.activity_id.value)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.health.native_reads, [])

    async def test_signed_start_carries_original_context_and_selection(self):
        owner, command, _ = await self.prepared(native=False)
        result = await owner.execute_managed(command)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.health.signed_reads), 1)
        self.assertEqual(self.tracker.active, 0)

    async def test_native_completion_then_revoked_next_step_retains_actual_count(self):
        owner, command, _ = await self.prepared()
        observe = self.health.observe_connection
        async def returned(*args):
            value = await observe(*args)
            self.revoke_operate()
            return value
        self.health.observe_connection = returned
        command = replace(command, execution=replace(command.execution, max_effects=2))
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "authorization-denied")
        self.assertEqual(result.effects_attempted, 1)
        self.assertNotEqual(result.activity_id, self.health_activity.activity_id.value)
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(self.health.signed_reads, [])
        receipt = self.receipt(command.execution)
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)
        self.assertEqual(receipt.result.effects_attempted, 1)
        history = self.history()
        replay = await owner.execute_managed(command)
        self.assertEqual(replay, result)
        self.assertEqual(self.history(), history)
        self.assertEqual(len(self.health.native_reads), 1)

    async def test_native_scope_ceiling_does_not_authorize_signed_start(self):
        owner, command, _ = await self.prepared(native=False)
        from control_plane_kit_core.identity import WorkspaceGrant
        required = (PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE)
        original = AuthenticatedPrincipal(self.member, (WorkspaceGrant("workspace-a", required),)).command_context("workspace-a")
        before = self.history()
        result = await owner.execute_managed(replace(command, context=original))
        self.assert_denial(result, command.execution, self.health_activity.activity_id.value)
        self.assertEqual(self.health.signed_reads, [])
        self.assertEqual(self.history(), before)
