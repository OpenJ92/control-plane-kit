"""Remote lifecycle targets reuse the actual prepared-start owners."""

from dataclasses import replace
import unittest

from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, PlannedActivity, ReconcileRuntime, RuntimeTarget,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import DeploymentGraph, RuntimeRecord
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from tests.test_postgres_prepared_runtime_invocations import _PreparedRuntimeInvocationFixture


class PostgresRuntimeLifecycleInvocationTargets(_PreparedRuntimeInvocationFixture, unittest.TestCase):
    def reset_start_truth(self, *, compensation=False, plan=None, desired=None):
        plan = ActivityPlan((PlannedActivity(ActivityId("reconcile-runtime"),
            ReconcileRuntime(RuntimeTarget("runtime-a"))),))
        base = DeploymentGraph("current", runtimes={"runtime-a": RuntimeRecord(
            "runtime-a", RuntimeKind.DOCKER, authority_ref=RuntimeAuthorityReference("prepared-runtime"))})
        super().reset_start_truth(compensation=compensation, plan=plan, desired=desired, base=base)

    def test_remote_reconcile_start_retains_its_atomic_prepared_marker(self):
        bound = self.binding(generation=self.runtime_fence.generation)
        intent = replace(self.intent(activity_id="reconcile-runtime",
            operation=ReconcileRuntime(RuntimeTarget("runtime-a"))), authority_ref=self.reference)
        transition = self.transition(identity=self.identity(activity_id="reconcile-runtime"), intent=intent)
        command = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence)
        started = self.fresh_start().execute(command, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(original.intent, intent)
            self.assertEqual(original.original_start_event, started.attempt.original_start_event)
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=intent.activity_id.value, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertIsNotNone(marker, "remote ReconcileRuntime lacks its atomic prepared dispatch marker")
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
