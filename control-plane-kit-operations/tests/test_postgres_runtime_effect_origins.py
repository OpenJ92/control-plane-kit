"""Fresh local dispatch retains its admitted-path target atomically."""

import unittest

from control_plane_kit_core.planning import ActivityId
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver
from tests.test_postgres_workload_invocations import _WorkloadInvocationFixture


class PostgresRuntimeEffectOriginTargets(_WorkloadInvocationFixture, unittest.TestCase):
    workload_preparation = False

    def test_local_no_secret_start_retains_atomic_invocation_without_legacy_origin(self):
        bound = self.binding(generation=self.runtime_fence.generation)
        context = self.engine.coordinator(FailIfObserver())._load_context(
            self.engine.command(generation=self.runtime_fence.generation))
        intent = _runtime_effect_intent_for_context(
            context, context.plan.activity(ActivityId(self.activity_id)))
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        started = self.fresh_start().execute(self.start_command(
            intent=intent, transition=transition, fence=self.runtime_fence), command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(original.intent, intent)
            self.assertEqual(original.original_start_event, started.attempt.original_start_event)
            self.assertEqual(uow.stores.effect_attempts.get(started.attempt.state.identity), started.attempt)
            registration = uow.stores.runtime_authorities.get("workspace-a", self.reference)
            self.assertEqual(registration, self.runtime)
            page = uow.stores.execution.command_invocations(bound.receipt, limit=10)
            marker, = page.items
            self.assertIsNone(page.next_cursor)
            self.assertIsNone(uow.stores.runtime_effect_origins.get(started.attempt.state.identity))
        self.assertEqual(marker.kind, ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertEqual(marker.command, bound.receipt)
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.outcome)
