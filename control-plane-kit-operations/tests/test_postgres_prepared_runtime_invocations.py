"""Prepared runtime targets use a real ordinary start and retained TLS authority."""

from dataclasses import replace
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_core.planning import ActivityId, ActivityPlan, PlannedActivity, RuntimeTarget, StartRuntime
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.topology import DeploymentGraph, RuntimeRecord
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_effects import required_secret_uses_for_runtime_effect
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_execution_coordinator as coordinator_fixture
from tests.test_postgres_current_effect_permissions import CurrentEffectPermissionFixture
from tests.postgres_effect_attempt_reconciliation_fixture import UnitOfWorkLedger


class PostgresPreparedRuntimeInvocationTests(CurrentEffectPermissionFixture, unittest.TestCase):
    def reset_start_truth(self, *, compensation=False):
        self.assertFalse(compensation)
        self.reference = RuntimeAuthorityReference("prepared-runtime")
        plan = ActivityPlan((PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("runtime-a"))),))
        desired = DeploymentGraph("desired", runtimes={"runtime-a": RuntimeRecord(
            "runtime-a", RuntimeKind.DOCKER, authority_ref=self.reference)})
        # Reuse the actual graph, approval, request and lifecycle fixture owners.
        # The ledger retains this fixture's isolated owning-suite schema.
        engine = coordinator_fixture.ExecutionCoordinatorTests()
        engine.connection = self.connection
        engine.tracker = UnitOfWorkLedger(self.unit_of_work)
        engine.reset_execution_request(plan=plan, base_graph=DeploymentGraph("current"), desired_graph=desired)
        with self.unit_of_work() as uow:
            self.runtime = uow.stores.runtime_authorities.register(
                workspace_id="workspace-a", authority_ref=self.reference, runtime_kind=RuntimeKind.DOCKER,
                authority=RemoteDockerTlsAuthority("tcp://runtime.example.test:2376",
                    SecretReference("secret://runtime/ca"), SecretReference("secret://runtime/cert"),
                    SecretReference("secret://runtime/key")),
                admitted_by="operator-a", admitted_at=self.clock())
            uow.commit()
        authority = self.authority()
        opened = engine.lifecycle_with_ids("run-a", "prepared-open", "prepared-claim").execute(
            ClaimAndOpenActivityRun("request-a", authority, ExecutionLeaseDuration(600), IdempotencyKey("prepared-claim")))
        self.runtime_fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        engine.lifecycle_with_ids("prepared-run-start", "prepared-run-action").execute(
            StartActivityRun("run-a", authority, self.runtime_fence, IdempotencyKey("prepared-run-start")))

    def admitted(self):
        bound = self.binding(generation=self.runtime_fence.generation)
        start = self.start_command(intent=replace(self.intent(), authority_ref=self.reference), fence=self.runtime_fence)
        started = self.fresh_start().execute(start, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            retained = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(retained.intent, start.intent)
            self.assertEqual(retained.original_start_event, started.attempt.original_start_event)
            self.assertEqual(uow.stores.effect_attempts.get(started.attempt.state.identity), started.attempt)
        request = runtime_effect_request_for_intent(start.intent,
            effect_id=started.attempt.original_start_event.event_id, secret_resolution_grants=())
        self.assertEqual(len(required_secret_uses_for_runtime_effect(request, self.runtime)), 3)
        return bound, start, started

    def test_actual_prepared_start_retains_planned_final_with_exact_registration(self):
        bound, _, started = self.admitted()
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=started.attempt.state.identity.activity_id,
                kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertIsNotNone(marker, "ordinary prepared start lacks its atomic final-dispatch marker")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.outcome)

    def test_original_runtime_binding_reads_history_without_new_admission(self):
        bound, start, started = self.admitted()
        ledger = UnitOfWorkLedger(self.unit_of_work)
        service = operations.ExecutionInvocationService(ledger, permission_policy=self.policy(), clock=self.clock)
        bind = getattr(service, "bind_runtime", None)
        self.assertTrue(callable(bind), "invocation service lacks original prepared-runtime binding")
        before = self.attempt_snapshot()
        port = bind(command_context=bound, start=start, started=started)
        self.assertEqual(ledger.active, 0)
        marker = service.inspect_runtime_dispatch(port)
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(ledger.active, 0)
