"""Exact admitted capability data at the real coordinator's adapter boundary."""
from dataclasses import replace
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_request_for_intent, runtime_effect_intent_fingerprint,
)
from control_plane_kit_operations.coordinator import ReobserveConnectorConnection
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.test_managed_application_chain import now
from tests.test_postgres_coordinator_permissions import (
    _CoordinatorManagementPermissionFixture, _ControlledInvocationHealth,
)


class PostgresManagedHealthDispatchBindingTests(_CoordinatorManagementPermissionFixture):
    async def dispatch_case(self, *, native=True):
        _, command, runtime = await self.prepared(native=native)
        self.capabilities = operations.ManagedRuntimeCapabilityPolicy(self.installation,
            (operations.ManagedRuntimeCapability("local-docker"),
             operations.ManagedRuntimeCapability("other-installed-docker")))
        self.policy = operations.CurrentWorkspaceEffectPolicy(self.installation, self.capabilities)
        self.assignments = operations.ManagedRuntimeAssignmentCommandService(self.unit_of_work,
            administration_policy=self.admin_policy, capability_policy=self.capabilities,
            clock=now, id_factory=lambda: uuid4().hex)
        self.selection_service = operations.ManagedRuntimeSelectionService(self.unit_of_work,
            capability_policy=self.capabilities)
        self.health = _ControlledInvocationHealth(self, self.health)
        return self.permission_coordinator(), command, runtime, self.selection(runtime)

    def selection(self, runtime):
        return self.selection_service.select(workspace_id="workspace-a", runtime_id="docker", authority=runtime)

    def regrant(self, runtime, capability):
        current = self.selection(runtime)
        self.assertIsNotNone(current)
        revoked = self.assignments.revoke(operations.RevokeManagedRuntimeAccess(self.admin_context,
            "workspace-a", "docker", runtime.authority_ref, current.revision, IdempotencyKey(uuid4().hex)))
        self.assignments.grant(operations.GrantManagedRuntimeAccess(self.admin_context,
            "workspace-a", "docker", runtime.authority_ref, runtime.registration_id, capability,
            revoked.receipt.resulting_assignment.revision, IdempotencyKey(uuid4().hex)))

    def assert_dispatch(self, index, expected, *, attempt=1):
        realization, request, authority, target = self.health.managed_dispatches[index]
        self.assertEqual(target, expected, "dispatch lost the exact admitted management selection")
        self.assertEqual(self.health.dispatch_active_transactions[index], 0)
        self.assertEqual(self.tracker.active, 0)
        self.assertEqual(target.installation_id, self.installation)
        self.assertEqual(target.workspace_id, request.source.workspace_id)
        self.assertEqual(target.runtime_id, request.operation.target.runtime_id)
        self.assertEqual(target.authority_ref, request.authority_ref)
        if type(authority) is RegisteredRuntimeAuthority:
            self.assertEqual((target.registration_id, target.workspace_id, target.authority_ref),
                (authority.registration_id, authority.workspace_id, authority.authority_ref))
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, attempt)
        with self.unit_of_work() as uow:
            retained = uow.stores.effect_attempts.get(identity)
            intent = uow.stores.effect_attempt_intents.get(identity)
        self.assertEqual(realization.intent_event, retained.original_start_event)
        self.assertEqual(request, runtime_effect_request_for_intent(intent.intent,
            effect_id=retained.original_start_event.event_id, secret_resolution_grants=()))
        self.assertEqual(retained.state.request_fingerprint, runtime_effect_intent_fingerprint(intent.intent))

    async def test_initial_native_dispatch_preserves_admitted_a_after_regrant_b(self):
        owner, command, runtime, admitted = await self.dispatch_case()
        self.health.before_native = lambda: self.regrant(runtime, "other-installed-docker")
        result = await owner.execute_managed(command)
        self.assert_dispatch(0, admitted)
        self.assertEqual(self.selection(runtime).capability_id, "other-installed-docker")
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(self.native_attempt(1)[0].state.status.value, "not_ready")
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.health.managed_dispatches), 1)

    async def test_successor_carries_its_admitted_b_after_regrant_a_without_redispatch(self):
        owner, command, runtime, first = await self.dispatch_case()
        await owner.execute_managed(command)
        self.assert_dispatch(0, first)
        predecessor = self.native_attempt(1)
        self.regrant(runtime, "other-installed-docker")
        successor_target = self.selection(runtime)
        successor = ReobserveConnectorConnection(replace(command.execution,
            idempotency_key=IdempotencyKey("dispatch-successor")), command.context,
            predecessor[0].state.identity)
        self.health.before_native = lambda: self.regrant(runtime, "local-docker")
        result = await owner.reobserve(successor)
        self.assert_dispatch(1, successor_target, attempt=2)
        self.assertEqual(self.selection(runtime).capability_id, "local-docker")
        self.assertEqual(self.native_attempt(1), predecessor)
        self.assertEqual(self.native_attempt(2)[0].state.prior_attempt, predecessor[0].state.identity)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(await owner.reobserve(successor), result)
        self.assertEqual(len(self.health.managed_dispatches), 2)

    async def test_signed_dispatch_carries_a_and_later_unit_refuses_regranted_b(self):
        owner, command, runtime, admitted = await self.dispatch_case(native=False)
        self.health.after_preparation = lambda *_: self.regrant(runtime, "other-installed-docker")
        result = await owner.execute_managed(command)
        self.assert_dispatch(0, admitted)
        self.assertEqual(self.selection(runtime).capability_id, "other-installed-docker")
        self.assertEqual((result.status.value, result.effects_attempted), ("authorization-denied", 1))
        self.assertEqual(len(self.health.preparation_calls), 1)
        self.assertEqual(self.health.final_calls, [])
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(len(self.health.managed_dispatches), 1)
