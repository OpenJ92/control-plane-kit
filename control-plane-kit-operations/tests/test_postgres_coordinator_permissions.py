"""Current permission carriage through the existing coordinator and receipts."""

import asyncio
from dataclasses import replace
import inspect
import threading
import unittest
from unittest import mock
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.planning import AllocatePublicIngress, ManagementBootstrapStage
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.coordinator import (
    ExecuteManagedActivityRun, ExecutionCoordinator, ExecutionCoordinatorDenied, ExecutionCoordinatorConflict,
    ReobserveConnectorConnection,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.records import ExecutionCommandReceiptStatus
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.postgres_effect_attempt_coordinator_fixture import PostgresEffectAttemptCoordinatorFixture
from tests.receiver_health_execution_fixture import ReceiverHealthExecutionFixture
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests.postgres_effect_attempt_reconciliation_fixture import UnitOfWorkLedger
from tests import test_managed_reobservation_admission as reobservation_fixtures
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

    def composition(self, *, start_policy=True, invocation_service=None, managed_health=None):
        self.require_interface()
        harness = self.coordinator_harness()
        owner = ExecutionCoordinator(self.unit_of_work, lifecycle=harness.lifecycle,
            adapter=harness.adapter, start_service=EffectAttemptStartService(self.unit_of_work,
                id_factory=harness.start_ids, permission_policy=self.policy if start_policy is True else start_policy),
            fold_service=harness.fold, reconciliation_service=harness.reconciliation,
            clock=now, id_factory=harness.coordinator_ids, permission_policy=self.policy,
            invocation_service=invocation_service, managed_health=managed_health)
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

    def test_invocation_composition_requires_matching_policy_and_exact_registration(self):
        other = operations.CurrentWorkspaceEffectPolicy("other",
            operations.ManagedRuntimeCapabilityPolicy("other", ()))
        service = operations.ExecutionInvocationService(self.unit_of_work,
            permission_policy=other, clock=now)
        before = self.attempt_snapshot()
        with self.assertRaises(ExecutionCoordinatorDenied):
            self.composition(invocation_service=service)
        class RegistrationSubclass(operations.ManagedHealthAdapterRegistration):
            pass
        with self.assertRaises(ExecutionCoordinatorDenied):
            self.composition(managed_health=RegistrationSubclass(object(), "cpk.semantic-invocations.v1"))
        self.assertEqual(self.attempt_snapshot(), before)

    def test_real_caller_and_receipt_reach_the_current_start_owner(self):
        owner, adapter, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(adapter.runtime_calls), 1)
        receipt = self.receipt(command.execution)
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)
        self.assertEqual(receipt.managed_intent.actor, self.member)

    def test_dynamic_application_retains_initial_scope_ceiling_through_actual_async_execution(self):
        from control_plane_kit_core.operations import ControlPlaneServiceRole
        from tests.test_postgres_application_initial_context import Request, CountingResolver
        owner, adapter, command = self.composition()
        self.assertIsNone(self.receipt(command.execution))
        self.permissions.revoke(operations.RevokeWorkspacePermissions(self.admin_context,
            "workspace-a", self.member, (PolicyScope.PLAN_APPROVE,), 1, IdempotencyKey("drop-approve")))
        resolver = CountingResolver(self.unit_of_work, installation=self.installation)
        class GrantAfterAdmission(operations.CpkServerExecutionService):
            async def handle_async(inner_self, request):
                self.assertEqual(len(resolver.calls), 1)
                self.permissions.grant(operations.GrantWorkspacePermissions(self.admin_context,
                    "workspace-a", self.member, (PolicyScope.PLAN_APPROVE,), 2, IdempotencyKey("restore-approve")))
                return await super().handle_async(request)
        services = {role: operations.CpkServerUnsupportedService(role) for role in ControlPlaneServiceRole}
        services[ControlPlaneServiceRole.EXECUTION] = GrantAfterAdmission(owner)
        app = operations.CpkServerOperationsApplication(services, authority_mode="dynamic",
            workspace_context_resolver=resolver)
        request = Request("http", "command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
            {"workspace_id": "workspace-a", "run_id": command.execution.run_id},
            {"idempotency_key": command.execution.idempotency_key.value,
             "claim_generation": command.execution.fence.generation,
             "max_effects": command.execution.max_effects}, AuthenticatedPrincipal(self.member))
        result = asyncio.run(app.handle_async(request))
        self.assertEqual((result["effects_attempted"], result["coordinator_status"]), (1, "completed"))
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(resolver.calls, [(request.principal, "workspace-a")])
        receipt = self.receipt(command.execution)
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)
        self.assertEqual(receipt.managed_intent.actor, self.member)
        self.assertEqual(set(receipt.managed_intent.actor_scopes), set(PolicyScope) - {PolicyScope.PLAN_APPROVE})
        current = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id=self.installation).resolve(request.principal, "workspace-a")
        self.assertIn(PolicyScope.PLAN_APPROVE, current.granted_scopes)

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
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(result.run.status.value, "succeeded")
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(self.start_command().transition.identity)
            self.assertEqual(attempt.state.status.value, "succeeded")
            retained = uow.stores.effect_outcomes.get(attempt.state.identity, attempt.latest_transition_event.event_id)
            self.assertEqual(retained.outcome.result, RuntimeEffectResult.succeeded(attempt.original_start_event.event_id))
        replay = asyncio.run(owner.execute_managed(command))
        self.assertEqual(replay, result)
        self.assertEqual(len(adapter.runtime_calls), 1)


class _CoordinatorManagementPermissionFixture(_CurrentCoordinatorPermission, ReceiverHealthExecutionFixture):
    def integral_observations(self):
        # The actual async coordinator waits for grants; preserve real DB time
        # so a newly sampled native result is not in a rounded clock's future.
        pass

    def prepare_health_journal(self):
        # Advance only preceding ready activities in the existing journal
        # premise; the selected target itself must remain unstarted.
        super().prepare_health_journal(first_ready=True)

    def health_start_command(self):
        if type(self.health_activity.operation) is AllocatePublicIngress:
            return None  # Legacy admission owns a journal event, not an attempt command.
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

    async def prepared(self, *, native=True, legacy=False, stage=None):
        stage = stage or (ManagementBootstrapStage.CONNECTOR_CONNECTED if native
            else ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        if legacy:
            await self.prepare_and_start()
            self.health_activity, = (value for value in self.plan.activities
                if type(value.operation) is AllocatePublicIngress)
            self.prepare_health_journal()
        else:
            await self.prepare_health(stage=stage)
        # Counterpart of the fixture's recorded predecessor journal premise.
        # This is a controlled port value, not a provider deployment claim.
        self.runtime.containers["connector"] = "a" * 64
        self.configure_permissions(self.worker.identity, installation="coordinator-permission")
        self.require_interface()
        context, execution = self.execution_context()
        if legacy:
            execution = replace(execution, authority=replace(execution.authority, scopes=self.original.granted_scopes))
        with self.unit_of_work() as uow:
            runtime = uow.stores.runtime_authorities.get_active_for_update(
                "workspace-a", self.graph.runtimes["docker"].authority_ref)
        self.assignments = operations.ManagedRuntimeAssignmentCommandService(self.unit_of_work,
            administration_policy=self.admin_policy, capability_policy=self.capabilities,
            clock=now, id_factory=lambda: uuid4().hex)
        if not legacy:
            self.assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
                "workspace-a", "docker", runtime.authority_ref, runtime.registration_id,
                "local-docker", 0, IdempotencyKey("management")))
        return self.permission_coordinator(), ExecuteManagedActivityRun(execution, self.original), runtime

    def permission_coordinator(self, factory=None, *, invocation_service=True,
            invocation_profile="cpk.semantic-invocations.v1", ingress_invocations=None, qualify_ingress=True):
        factory = factory or self.unit_of_work
        fold = EffectAttemptFoldService(factory, id_factory=self.ids["effect-fold"])
        from control_plane_kit_operations.coordinator import ActivityExecutionDispatcher
        from control_plane_kit_operations.ingress_authorities import IngressAuthorityProviderKind
        from control_plane_kit_operations.ingress_realization import IngressRealizationAdapter, IngressProviderRegistration
        from control_plane_kit_operations.secret_providers import SecretUseAuthorizationService
        adapter = ActivityExecutionDispatcher(runtime=self.runtime,
            ingress=IngressRealizationAdapter(factory,
                {IngressAuthorityProviderKind.CLOUDFLARE: IngressProviderRegistration(self.ingress,
                    "cpk.ingress-invocations.v1") if ingress_invocations is not None and qualify_ingress else self.ingress}, now,
                SecretUseAuthorizationService(factory), invocation_service=ingress_invocations))
        health = (self.health if type(self.health) is _ControlledInvocationHealth
            else _ControlledInvocationHealth(self, self.health))
        if invocation_service is True:
            invocation_service = operations.ExecutionInvocationService(factory,
                permission_policy=self.policy, clock=now, health_receiver_decoders=self.registry)
        return ExecutionCoordinator(factory,
            lifecycle=RunLifecycleCommandService(factory, clock=now, id_factory=self.ids["lifecycle"]),
            adapter=adapter, start_service=EffectAttemptStartService(factory,
                id_factory=self.ids["effect-start"], health_receiver_decoders=self.registry,
                permission_policy=self.policy), fold_service=fold,
            reconciliation_service=EffectAttemptReconciliationService(factory, ForbiddenRecovery(), fold),
            clock=now, id_factory=self.ids["execution"],
            managed_health=operations.ManagedHealthAdapterRegistration(health, invocation_profile),
            health_signing_authority=self.reload_service(factory), permission_policy=self.policy,
            invocation_service=invocation_service)


class PostgresCoordinatorManagementPermissionTests(_CoordinatorManagementPermissionFixture):
    async def test_legacy_initial_denial_has_no_start_event_or_ingress_call(self):
        owner, command, _ = await self.prepared(legacy=True)
        self.revoke_operate()
        before = self.history()
        result = await owner.execute_managed(command)
        self.assert_denial(result, command.execution, self.health_activity.activity_id.value)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.ingress.create_allocation_names, [])

    async def test_ingress_admitted_custody_result_survives_current_revocation(self):
        owner, command, _ = await self.prepared(legacy=True)
        from tests.ingress_invocation_fixture import StagedIngressPort
        from control_plane_kit_operations.ingress_invocation_service import IngressInvocationService
        self.ingress = StagedIngressPort(self)
        self.ingress.after_custody = self.revoke_operate
        owner = self.permission_coordinator(ingress_invocations=IngressInvocationService(
            self.unit_of_work, permission_policy=self.policy, clock=now))
        result = await owner.execute_managed(command)
        self.assertEqual(result.status.value, "progressed")
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.ingress.create_allocation_names), 1)
        self.assertEqual(self.ingress.create_active_counts, [0])
        events = [event for event in self.history() if event.activity_id == self.health_activity.activity_id.value]
        self.assertEqual([event.kind for event in events], [ActivityEventKind.STEP_STARTED, ActivityEventKind.STEP_SUCCEEDED])
        self.assertEqual(len(self.rows("cpk_cloudflare_ingress_resources")), 1)
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.ingress.create_allocation_names), 1)

    async def test_native_start_carries_current_selection_without_signing_scopes(self):
        owner, command, _ = await self.prepared()
        required = (PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE)
        from control_plane_kit_core.identity import WorkspaceGrant
        original = AuthenticatedPrincipal(self.member, (WorkspaceGrant("workspace-a", required),)).command_context("workspace-a")
        result = await owner.execute_managed(replace(command, context=original))
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(self.health.native_reads[0].activity_id, self.health_activity.activity_id)
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
        self.assertEqual(self.health.signed_reads[0][0].activity_id, self.health_activity.activity_id)
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
        self.assertEqual(self.health.native_reads, [])


class PostgresNativeReobservationPermissionTests(_CoordinatorManagementPermissionFixture, LifecycleLockFixture):
    async def waiting(self):
        owner, command, runtime = await self.prepared()
        result = await owner.execute_managed(command)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.health.native_reads), 1)
        prior = self.native_attempt(1)
        self.assertEqual(prior[0].state.status.value, "not_ready")
        reobserve = ReobserveConnectorConnection(replace(command.execution,
            idempotency_key=IdempotencyKey("native-reobserve-permission")), command.context,
            prior[0].state.identity)
        return owner, reobserve, runtime, prior

    def assert_no_successor(self, command, before, prior, *, receipt_expected=True):
        self.assertEqual(self.history(), before)
        self.assertEqual(self.native_attempt(1), prior)
        self.assertEqual(len(self.health.native_reads), 1)
        successor = replace(command.predecessor, attempt=2)
        with self.unit_of_work() as uow:
            with self.assertRaises(KeyError):
                uow.stores.effect_attempts.get(successor)
            with self.assertRaises(KeyError):
                uow.stores.effect_attempt_intents.get(successor)
        receipt = self.receipt(command.execution)
        if not receipt_expected:
            self.assertIsNone(receipt)
            return
        self.assertEqual(receipt.managed_intent.predecessor, command.predecessor)
        self.assertEqual(receipt.managed_intent.successor, successor)

    async def test_current_permission_denial_preserves_predecessor_and_replays_after_regrant(self):
        owner, command, _, prior = await self.waiting()
        before = self.history()
        self.revoke_operate()
        denied = await owner.reobserve(command)
        self.assert_denial(denied, command.execution, command.predecessor.activity_id)
        self.assert_no_successor(command, before, prior)
        self.permissions.grant(operations.GrantWorkspacePermissions(self.admin_context,
            "workspace-a", self.member, (PolicyScope.EXECUTION_OPERATE,), 2, IdempotencyKey("restore")))
        self.assertEqual(await owner.reobserve(command), denied)
        self.assert_no_successor(command, before, prior)
        with self.assertRaises(ExecutionCoordinatorConflict):
            await owner.reobserve(replace(command, predecessor=replace(command.predecessor, attempt=2)))
        self.assert_no_successor(command, before, prior)

    async def test_current_assignment_denial_preserves_predecessor_and_replays_after_regrant(self):
        owner, command, runtime, prior = await self.waiting()
        before = self.history()
        self.assignments.revoke(operations.RevokeManagedRuntimeAccess(self.admin_context,
            "workspace-a", "docker", runtime.authority_ref, 1, IdempotencyKey("revoke-management")))
        denied = await owner.reobserve(command)
        self.assert_denial(denied, command.execution, command.predecessor.activity_id)
        self.assert_no_successor(command, before, prior)
        self.assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", "docker", runtime.authority_ref, runtime.registration_id,
            "local-docker", 2, IdempotencyKey("restore-management")))
        self.assertEqual(await owner.reobserve(command), denied)
        self.assert_no_successor(command, before, prior)

    async def test_current_native_successor_uses_the_existing_admission_and_replays_without_io(self):
        owner, command, _, prior = await self.waiting()
        result = await owner.reobserve(command)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.health.native_reads), 2)
        successor, retained = self.native_attempt(2)
        self.assertEqual(successor.state.prior_attempt, command.predecessor)
        self.assertEqual(successor.state.status.value, "not_ready")
        self.assertEqual(retained.attempt, successor)
        self.assertEqual(self.native_attempt(1), prior)
        before = self.history()
        self.assertEqual(await owner.reobserve(command), result)
        self.assertEqual(self.history(), before)
        self.assertEqual(len(self.health.native_reads), 2)

    async def revoke_admission_race(self, *, assignment, admission_first):
        _, command, runtime, prior = await self.waiting()
        before = self.history()
        revoked = threading.Event()
        instrumentation = []

        def revoke(factory):
            if assignment:
                service = operations.ManagedRuntimeAssignmentCommandService(factory,
                    administration_policy=self.admin_policy, capability_policy=self.capabilities,
                    clock=now, id_factory=lambda: uuid4().hex)
                result = service.revoke(operations.RevokeManagedRuntimeAccess(self.admin_context,
                    "workspace-a", "docker", runtime.authority_ref, 1, IdempotencyKey("race-revoke")))
            else:
                service = operations.WorkspacePermissionCommandService(factory,
                    administration_policy=self.admin_policy, clock=now, id_factory=lambda: uuid4().hex)
                result = service.revoke(operations.RevokeWorkspacePermissions(self.admin_context,
                    "workspace-a", self.member, (PolicyScope.EXECUTION_OPERATE,), 1, IdempotencyKey("race-revoke")))
            revoked.set()
            return result

        def admit(factory):
            ledger = UnitOfWorkLedger(factory)
            owner = self.permission_coordinator(ledger)
            observe = self.health.observe_connection
            async def returned(*args):
                if ledger.active:
                    instrumentation.append("native capability inside admission transaction")
                if admission_first and not revoked.wait(8):
                    instrumentation.append("revocation did not commit before native result")
                return await observe(*args)
            with mock.patch.object(self.health, "observe_connection", returned):
                return asyncio.run(owner.reobserve(command))

        def permission(query, _):
            return "FROM cpk_workspace_permissions" in query and "FOR UPDATE" in query

        def authority(query, _):
            return "FROM cpk_runtime_authorities" in query and "FOR UPDATE" in query

        if not assignment:
            pause = select = permission
        elif not admission_first:
            # Revoke holds the authority row before the real preselection read.
            pause = select = authority
        else:
            # Ignore preliminary selection transactions; identify the admission
            # connection at A, and the revoker by its original assignment read.
            local = threading.local()
            def select(query, parameters):
                if permission(query, parameters):
                    local.admission = True
                    return True
                if "FROM cpk_managed_runtime_assignments" in query and "FOR UPDATE" not in query:
                    local.administration = True
                return authority(query, parameters) and getattr(local, "administration", False)
            def pause(query, parameters):
                return authority(query, parameters) and getattr(local, "admission", False)

        first, second = self.opposing_commands(admit if admission_first else revoke,
            revoke if admission_first else admit, pause, connection_query=select)
        result = (first if admission_first else second).result()
        (second if admission_first else first).result()
        self.assertEqual(instrumentation, [])
        self.assertTrue(revoked.is_set())
        if admission_first:
            self.assertEqual(result.effects_attempted, 1)
            successor, retained = self.native_attempt(2)
            self.assertEqual(successor.state.prior_attempt, command.predecessor)
            self.assertEqual(successor.state.status.value, "not_ready")
            self.assertEqual(retained.attempt, successor)
            self.assertEqual(self.native_attempt(1), prior)
            self.assertEqual(len(self.health.native_reads), 2)
        else:
            self.assert_denial(result, command.execution, command.predecessor.activity_id)
            self.assert_no_successor(command, before, prior)

    async def test_permission_revoke_wins_real_reobservation_admission_lock(self):
        await self.revoke_admission_race(assignment=False, admission_first=False)

    async def test_reobservation_admission_wins_permission_revoke_and_retains_result(self):
        await self.revoke_admission_race(assignment=False, admission_first=True)

    async def test_assignment_revoke_wins_real_reobservation_selection_lock(self):
        await self.revoke_admission_race(assignment=True, admission_first=False)

    async def test_reobservation_admission_wins_assignment_revoke_and_retains_result(self):
        await self.revoke_admission_race(assignment=True, admission_first=True)

    async def test_regranted_assignment_cannot_replace_the_expected_selected_revision(self):
        owner, command, runtime, prior = await self.waiting()
        before = self.history()
        select = owner._current_management_selection
        selections = []
        def changed(context, intent):
            selected = select(context, intent)
            self.assertEqual(self.tracker.active, 0)
            selections.append(selected)
            self.assignments.revoke(operations.RevokeManagedRuntimeAccess(self.admin_context,
                "workspace-a", "docker", runtime.authority_ref, 1, IdempotencyKey("stale-revoke")))
            self.assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
                "workspace-a", "docker", runtime.authority_ref, runtime.registration_id,
                "local-docker", 2, IdempotencyKey("stale-regrant")))
            return selected
        with mock.patch.object(owner, "_current_management_selection", changed):
            denied = await owner.reobserve(command)
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0].revision, 1)
        current = operations.ManagedRuntimeSelectionService(self.unit_of_work,
            capability_policy=self.capabilities).select(workspace_id="workspace-a", runtime_id="docker", authority=runtime)
        self.assertEqual(current.revision, 3)
        self.assert_denial(denied, command.execution, command.predecessor.activity_id)
        self.assert_no_successor(command, before, prior)

    async def test_independently_allowed_assignment_must_match_the_pinned_native_target(self):
        owner, command, runtime, prior = await self.waiting()
        self.assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", "different-runtime", runtime.authority_ref, runtime.registration_id,
            "local-docker", 0, IdempotencyKey("other-runtime")))
        selected = operations.ManagedRuntimeSelectionService(self.unit_of_work,
            capability_policy=self.capabilities).select(workspace_id="workspace-a",
                runtime_id="different-runtime", authority=runtime)
        with self.unit_of_work() as uow:
            self.assertTrue(self.policy.evaluate_in_unit_of_work(uow, self.original,
                required_scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ,
                    PolicyScope.NODE_CONTROL_EXECUTE), managed_target=selected).allowed)
        before = self.history()
        with mock.patch.object(owner, "_current_management_selection", return_value=selected):
            denied = await owner.reobserve(command)
        self.assert_denial(denied, command.execution, command.predecessor.activity_id)
        self.assert_no_successor(command, before, prior)

    async def test_original_invocation_ceiling_denies_before_successor_admission(self):
        owner, command, _, prior = await self.waiting()
        from control_plane_kit_core.identity import WorkspaceGrant
        scopes = tuple(scope for scope in self.original.granted_scopes if scope is not PolicyScope.NODE_CONTROL_EXECUTE)
        original = AuthenticatedPrincipal(self.member, (WorkspaceGrant("workspace-a", scopes),)).command_context("workspace-a")
        before = self.history()
        command = replace(command, context=original)
        denied = await owner.reobserve(command)
        self.assert_denial(denied, command.execution, command.predecessor.activity_id)
        self.assert_no_successor(command, before, prior)

    async def test_denial_receipt_rollback_and_lost_ack_never_create_a_successor(self):
        owner, command, _, prior = await self.waiting()
        self.revoke_operate()
        before = self.history()
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                fault, fired = reobservation_fixtures.ManagedReobservationAdmissionTests.admission_commit_fault(
                    self, command.execution.idempotency_key.value,
                    after_commit=after_commit, receipt_status="completed")
                with fault, self.assertRaises(reobservation_fixtures.InterruptedAdmission):
                    await owner.reobserve(command)
                self.assertEqual(len(fired), 1)
                self.assert_no_successor(command, before, prior, receipt_expected=after_commit)
                if after_commit:
                    replay = await owner.reobserve(command)
                    self.assert_denial(replay, command.execution, command.predecessor.activity_id)
                    self.assert_no_successor(command, before, prior)

    async def test_lost_admission_ack_retains_uncertainty_without_native_io(self):
        owner, command, _, prior = await self.waiting()
        fault, fired = reobservation_fixtures.ManagedReobservationAdmissionTests.admission_commit_fault(
            self, command.execution.idempotency_key.value, after_commit=True)
        with fault, self.assertRaises(reobservation_fixtures.InterruptedAdmission):
            await owner.reobserve(command)
        self.assertEqual(len(fired), 1)
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(self.native_attempt(1), prior)
        with self.unit_of_work() as uow:
            successor = uow.stores.effect_attempts.get(replace(command.predecessor, attempt=2))
            self.assertEqual(successor.state.status.value, "started")
            self.assertEqual(successor.state.prior_attempt, command.predecessor)
        self.assertIs(self.receipt(command.execution).status, ExecutionCommandReceiptStatus.INCOMPLETE)
        before = self.history()
        replay = await owner.reobserve(command)
        self.assertEqual(replay.status.value, "uncertain")
        self.assertEqual(self.history(), before)
        self.assertEqual(len(self.health.native_reads), 1)


class _ControlledInvocationHealth:
    """Fixed controlled capabilities using the real Operations admission port."""
    def __init__(self, test, delegate, *, refuse_preparation=False):
        self.test, self.delegate = test, delegate
        self.refuse_preparation = refuse_preparation
        self.invocations = None
        self.preparation_calls = []
        self.final_calls = []
        self.control = None
        self.after_preparation = None
        self.before_final = None
        self.after_final_admission = None
        self.preparation_error = None
        self.final_error = None
        self.classify_final = True
        self.transform_result = None

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def observe_signed(self, realization, request, authority, *, invocations=None):
        self.invocations = invocations
        if invocations is None:
            return None  # Target observes the missing carriage after actual start.
        try:
            return await self._observe(realization, request, authority, invocations)
        except invocations.control_error as error:
            self.control = error
            raise

    async def _observe(self, realization, request, authority, invocations):
        preparation = authority.preparation
        for authorization in (preparation.transit_authorization_id, preparation.workload_authorization_id):
            permit = invocations.admit("secret-resolution", authorization_id=authorization)
            self.test.assertEqual(self.test.tracker.active, 0)
            self.preparation_calls.append(authorization)
            invocations.record_outcome(permit, "returned")
            if len(self.preparation_calls) == 1 and self.after_preparation is not None:
                self.after_preparation(invocations, authorization)
            if self.preparation_error is not None:
                raise self.preparation_error
            if self.refuse_preparation:
                return None
        if self.before_final is not None:
            self.before_final()
        permit = invocations.admit("signed-health-transport")
        self.test.assertEqual(self.test.tracker.active, 0)
        if self.after_final_admission is not None:
            self.after_final_admission()
        self.final_calls.append(request.effect_id)
        try:
            if self.final_error is not None:
                raise self.final_error
            observed = await self.delegate.observe_signed(realization, request, authority)
            if self.transform_result is not None:
                observed = self.transform_result(observed)
        except invocations.control_error:
            raise
        except Exception:
            if self.classify_final:
                invocations.record_outcome(permit, "unknown")
            raise
        if self.classify_final:
            invocations.record_outcome(permit, "returned")
        return observed


class PostgresCoordinatorHealthInvocationTests(_CoordinatorManagementPermissionFixture):
    async def invocation_case(self, *, refuse_preparation=False, native=False, stage=None):
        _, command, _ = await self.prepared(native=native, stage=stage)
        self.health = _ControlledInvocationHealth(self, self.health,
            refuse_preparation=refuse_preparation)
        return self.permission_coordinator(), command

    def actual_final(self, command):
        from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
        receipt = self.receipt(command.execution)
        reference = operations.ExecutionCommandReference(command.execution.run_id,
            command.execution.idempotency_key.value, receipt.intent_fingerprint)
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(reference,
                activity_id=self.health_activity.activity_id.value,
                kind=ExecutionInvocationKind.SIGNED_HEALTH_TRANSPORT)
            attempt = uow.stores.effect_attempts.get(EffectAttemptIdentity(
                RunId(command.execution.run_id), self.health_activity.activity_id.value, 1))
        self.assertIsNotNone(marker)
        self.assertEqual(marker.original_event_id, attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, attempt.state.request_fingerprint)
        return marker, attempt, receipt

    async def test_original_bound_invocations_reach_signed_adapter_after_actual_start(self):
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationOutcome
        owner, command = await self.invocation_case()
        result = await owner.execute_managed(command)
        final, attempt, _ = self.actual_final(command)
        self.assertIsNotNone(self.health.invocations,
            "coordinator did not pass original bound signed-health invocations")
        self.assertEqual(len(self.health.preparation_calls), 2)
        self.assertNotEqual(*self.health.preparation_calls)
        self.assertEqual(len(self.health.signed_reads), 1)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(attempt.state.status.value, "succeeded")
        self.assertIs(final.outcome, ExecutionInvocationOutcome.RETURNED)
        self.assertEqual(self.tracker.active, 0)

    async def test_preparation_refusal_with_planned_final_is_inspection_only(self):
        from control_plane_kit_operations.execution_invocations import (
            ExecutionInvocationControlError, ExecutionInvocationPlanned, ExecutionInvocationDisposition,
        )
        owner, command = await self.invocation_case(refuse_preparation=True)
        control = None
        try:
            await owner.execute_managed(command)
        except ExecutionInvocationControlError as error:
            control = error
        final, attempt, receipt = self.actual_final(command)
        self.assertIsNotNone(self.health.invocations,
            "coordinator did not pass original bound signed-health invocations")
        self.assertIs(type(control), ExecutionInvocationPlanned)
        self.assertIs(final.disposition, ExecutionInvocationDisposition.PLANNED)
        self.assertEqual(attempt.state.status.value, "started")
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.INCOMPLETE)
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(self.health.signed_reads, [])
        with self.unit_of_work() as uow, self.assertRaises(KeyError):
            uow.stores.effect_outcomes.get(attempt.state.identity, attempt.latest_transition_event.event_id)
        replay = await owner.execute_managed(command)
        self.assertEqual(replay.status.value, "uncertain")
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(self.health.signed_reads, [])

    def assert_unfolded(self, command, *, disposition="planned"):
        final, attempt, receipt = self.actual_final(command)
        self.assertEqual(final.disposition.value, disposition)
        self.assertEqual(attempt.state.status.value, "started")
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.INCOMPLETE)
        with self.unit_of_work() as uow, self.assertRaises(KeyError):
            uow.stores.effect_outcomes.get(attempt.state.identity, attempt.latest_transition_event.event_id)
        return final, attempt, receipt

    async def test_preparation_exception_preserves_planned_state_without_fold(self):
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationPlanned
        owner, command = await self.invocation_case()
        self.health.preparation_error = RuntimeError("controlled preparation unavailable")
        with self.assertRaises(ExecutionInvocationPlanned):
            await owner.execute_managed(command)
        self.assert_unfolded(command)
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(self.health.final_calls, [])

    async def pending_return(self, *, error=False):
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationConflict
        owner, command = await self.invocation_case()
        self.health.classify_final = False
        if error:
            self.health.final_error = RuntimeError("controlled final failure")
        with self.assertRaises(ExecutionInvocationConflict):
            await owner.execute_managed(command)
        final, _, _ = self.assert_unfolded(command, disposition="admitted")
        self.assertEqual(final.outcome.value, "pending")
        self.assertEqual(len(self.health.final_calls), 1)
        replay = await owner.execute_managed(command)
        self.assertEqual(replay.status.value, "uncertain")
        self.assertEqual(len(self.health.final_calls), 1)

    async def test_ordinary_return_with_pending_final_is_conflict_not_a_fold(self):
        await self.pending_return()

    async def test_ordinary_exception_with_pending_final_is_conflict_not_a_fold(self):
        await self.pending_return(error=True)

    async def test_classified_final_exception_uses_existing_uncertain_fold(self):
        owner, command = await self.invocation_case()
        self.health.final_error = RuntimeError("controlled final failure")
        result = await owner.execute_managed(command)
        final, attempt, receipt = self.actual_final(command)
        self.assertEqual((result.status.value, result.effects_attempted), ("uncertain", 1))
        self.assertEqual(attempt.state.status.value, "uncertain")
        self.assertEqual(final.outcome.value, "unknown")
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)
        self.assertEqual(len(self.health.final_calls), 1)

    async def test_admitted_final_result_folds_after_current_revocation(self):
        owner, command = await self.invocation_case()
        self.health.after_final_admission = self.revoke_operate
        result = await owner.execute_managed(command)
        final, attempt, receipt = self.actual_final(command)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(attempt.state.status.value, "succeeded")
        self.assertEqual(final.outcome.value, "returned")
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)

    async def test_revocation_between_uses_denies_without_final_and_replays_exactly(self):
        owner, command = await self.invocation_case()
        self.health.after_preparation = lambda *_: self.revoke_operate()
        result = await owner.execute_managed(command)
        final, attempt, receipt = self.actual_final(command)
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 1))
        self.assertEqual(attempt.state.status.value, "started")
        self.assertEqual(final.disposition.value, "planned")
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(self.health.final_calls, [])
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.health.preparation_calls), 1)

    async def test_prior_native_and_denied_started_signed_attempt_are_both_counted(self):
        owner, command = await self.invocation_case(native=True)
        self.health.after_preparation = lambda *_: self.revoke_operate()
        command = replace(command, execution=replace(command.execution, max_effects=2))
        result = await owner.execute_managed(command)
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 2))
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(self.health.final_calls, [])
        receipt = self.receipt(command.execution)
        self.assertEqual(receipt.result.effects_attempted, 2)
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.health.native_reads), 1)

    async def test_existing_control_survives_without_inspection_rescue_or_fold(self):
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationExisting
        owner, command = await self.invocation_case()
        self.health.after_preparation = lambda port, authorization: port.admit(
            "secret-resolution", authorization_id=authorization)
        with self.assertRaises(ExecutionInvocationExisting) as caught:
            await owner.execute_managed(command)
        self.assertIs(caught.exception, self.health.control)
        self.assert_unfolded(command)
        self.assertEqual(self.health.final_calls, [])

    async def cancellation(self, *, after_final):
        owner, command = await self.invocation_case()
        cancellation = asyncio.CancelledError("controlled cancellation")
        if after_final:
            self.health.final_error = cancellation
        else:
            self.health.preparation_error = cancellation
        with self.assertRaises(asyncio.CancelledError) as caught:
            await owner.execute_managed(command)
        self.assertIs(caught.exception, cancellation)
        final, attempt, receipt = self.actual_final(command)
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.INCOMPLETE)
        if after_final:
            self.assertEqual((final.disposition.value, final.outcome.value), ("admitted", "pending"))
            self.assertEqual(attempt.state.status.value, "uncertain")
            self.assertEqual(len(self.health.final_calls), 1)
        else:
            self.assert_unfolded(command)
            self.assertEqual(self.health.final_calls, [])

    async def test_cancellation_before_final_preserves_planned_and_propagates(self):
        await self.cancellation(after_final=False)

    async def test_cancellation_after_final_preserves_existing_uncertainty_and_propagates(self):
        await self.cancellation(after_final=True)

    async def test_final_denial_preserves_attempt_and_actual_preparation_history(self):
        owner, command = await self.invocation_case()
        self.health.before_final = self.revoke_operate
        result = await owner.execute_managed(command)
        final, attempt, receipt = self.actual_final(command)
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 1))
        self.assertEqual(final.disposition.value, "denied")
        self.assertEqual(attempt.state.status.value, "started")
        self.assertEqual(len(self.health.preparation_calls), 2)
        self.assertEqual(self.health.final_calls, [])
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.COMPLETED)

    async def test_regranted_assignment_does_not_replace_the_original_bound_selection(self):
        owner, command = await self.invocation_case()
        with self.unit_of_work() as uow:
            runtime = uow.stores.runtime_authorities.get_active_for_update(
                "workspace-a", self.graph.runtimes["docker"].authority_ref)
        def replace_assignment(*_):
            self.assignments.revoke(operations.RevokeManagedRuntimeAccess(self.admin_context,
                "workspace-a", "docker", runtime.authority_ref, 1, IdempotencyKey("revoke-management")))
            self.assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
                "workspace-a", "docker", runtime.authority_ref, runtime.registration_id,
                "local-docker", 2, IdempotencyKey("restore-management")))
        self.health.after_preparation = replace_assignment
        result = await owner.execute_managed(command)
        final, attempt, _ = self.actual_final(command)
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 1))
        self.assertEqual(final.disposition.value, "planned")
        self.assertEqual(attempt.state.status.value, "started")
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(self.health.final_calls, [])

    async def test_lost_final_commit_ack_control_cannot_be_rescued_by_admitted_row(self):
        import psycopg
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationConflict
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        _, command = await self.invocation_case()
        armed, connections = [], []
        def connect():
            connection = psycopg.connect(self.database_url)
            if armed:
                connection = _CommitThenRaiseConnection(connection, RuntimeError("final admission ack lost"))
                connections.append(connection)
            return connection
        service = operations.ExecutionInvocationService(lambda: PostgresUnitOfWork(connect),
            permission_policy=self.policy, clock=now, health_receiver_decoders=self.registry)
        owner = self.permission_coordinator(invocation_service=service)
        self.health.before_final = lambda: armed.append(True)
        with self.assertRaises(ExecutionInvocationConflict) as caught:
            await owner.execute_managed(command)
        self.assertIs(caught.exception, self.health.control)
        final, _, _ = self.assert_unfolded(command, disposition="admitted")
        self.assertEqual(final.outcome.value, "pending")
        self.assertEqual([connection.commits for connection in connections], [1])
        self.assertEqual(len(self.health.preparation_calls), 2)
        self.assertEqual(self.health.final_calls, [])
        self.assertEqual((await owner.execute_managed(command)).status.value, "uncertain")
        self.assertEqual(self.health.final_calls, [])

    async def test_missing_signed_profile_or_service_refuses_before_start(self):
        _, command = await self.invocation_case()
        before = self.history()
        for index, options in enumerate((dict(invocation_profile=None), dict(invocation_service=None))):
            with self.subTest(options=tuple(options)):
                selected = replace(command, execution=replace(command.execution,
                    idempotency_key=IdempotencyKey("unsupported-profile-" + str(index))))
                result = await self.permission_coordinator(**options).execute_managed(selected)
                self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
                self.assertEqual(self.receipt(selected.execution).result.effects_attempted, 0)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.health.preparation_calls, [])
        self.assertEqual(self.health.final_calls, [])
        self.assertEqual(self.rows("cpk_execution_command_invocations"), ())

    async def test_native_observation_does_not_require_signed_invocation_profile(self):
        _, command, _ = await self.prepared()
        result = await self.permission_coordinator(invocation_profile=None,
            invocation_service=None).execute_managed(command)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.health.native_reads), 1)
        self.assertEqual(self.rows("cpk_execution_command_invocations"), ())

    async def classified_response(self, response):
        from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome
        owner, command = await self.invocation_case(stage=ManagementBootstrapStage.GATEWAY_INGRESS_READY)
        if response == "unhealthy":
            self.health.transform_result = lambda value: replace(value, outcome=NodeHealthReadOutcome.UNHEALTHY)
        elif response == "invalid":
            self.health.transform_result = lambda _: None
        result = await owner.execute_managed(command)
        final, attempt, _ = self.actual_final(command)
        self.assertEqual(attempt.state.status.value,
            {"healthy": "succeeded", "unhealthy": "failed", "invalid": "uncertain"}[response])
        self.assertEqual(final.outcome.value, "returned")
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(self.health.final_calls), 1)

    async def test_classified_actual_healthy_result_uses_existing_fold(self):
        await self.classified_response("healthy")

    async def test_classified_actual_unhealthy_result_is_not_promoted_to_success(self):
        await self.classified_response("unhealthy")

    async def test_classified_invalid_adapter_result_preserves_uncertainty(self):
        await self.classified_response("invalid")

    async def test_missing_final_marker_after_preparation_refuses_without_repair_or_fold(self):
        from control_plane_kit_operations.execution_invocations import ExecutionInvocationConflict
        owner, command = await self.invocation_case(refuse_preparation=True)
        original_attempts = []
        def remove_marker(*_):
            final, attempt, _ = self.actual_final(command)
            original_attempts.append(attempt)
            self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s",
                (final.invocation_id,))
        self.health.after_preparation = remove_marker
        with self.assertRaises(ExecutionInvocationConflict):
            await owner.execute_managed(command)
        receipt = self.receipt(command.execution)
        self.assertIs(receipt.status, ExecutionCommandReceiptStatus.INCOMPLETE)
        self.assertEqual(self.health.final_calls, [])
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(len(self.rows("cpk_execution_command_invocations")), 1)
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(original_attempts[0].state.identity)
            self.assertEqual(attempt, original_attempts[0])
            self.assertEqual(attempt.state.status.value, "started")
            with self.assertRaises(KeyError):
                uow.stores.effect_outcomes.get(attempt.state.identity, attempt.latest_transition_event.event_id)


class ManagedHealthAdapterRegistrationTests(unittest.TestCase):
    def test_unknown_profile_is_not_advertised_support(self):
        for profile in ("unknown", True, 1):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                operations.ManagedHealthAdapterRegistration(object(), profile)

    def test_registration_hides_the_adapter_and_validates_the_closed_profile(self):
        adapter = object()
        registration = operations.ManagedHealthAdapterRegistration(adapter, "cpk.semantic-invocations.v1")
        self.assertIs(registration.adapter, adapter)
        self.assertNotIn(repr(adapter), repr(registration))
