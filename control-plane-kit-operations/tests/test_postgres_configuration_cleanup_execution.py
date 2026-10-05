"""#1936 public-owner targets; simulated adapter results are not provider proof."""
import unittest

from control_plane_kit_core.configuration_instances import (
    ConfigurationCleanupOutcome, ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.runtime_effects import configuration_cleanup_result, configuration_cleanup_outcomes
from control_plane_kit_operations.coordinator import CoordinatorStatus
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter


class PostgresConfigurationCleanupExecutionTests(ConfigurationCleanupExecutionFixture, unittest.TestCase):
    def test_public_k1_admission_lifecycle_start_fold_and_no_dispatch_replay(self):
        self.prepare_cleanup_execution()
        admitted = self.admit_cleanup()
        self.assertEqual(admitted.request.identity.plan_id, self.plan.plan_id)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_configuration_cleanup_reservations").fetchone(), (0,))
        claimed = self.ready_run("cleanup-execution")
        expected = ConfigurationCleanupOutcomeSet((ConfigurationCleanupOutcome(
            self.selected_ref, ConfigurationCleanupStatus.REMOVED, None),))

        def removed(_context, request):
            self.assertEqual(request.operation.instances, (self.selected_ref,))
            self.assertEqual(request.authority_ref, self.registration.authority_ref)
            identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
            # An independent transaction must see the complete committed start
            # before the coordinator is permitted to invoke its runtime adapter.
            with self.unit_of_work() as uow:
                reservation = uow.stores.configuration_cleanup_ownership.get(identity)
                self.assertIsNotNone(reservation)
                self.assertIs(reservation.status, EffectAttemptStatus.STARTED)
                self.assertEqual(tuple(member.ref for member in reservation.members), (self.selected_ref,))
                self.assertEqual(len(reservation.claims), 1)
                self.assertEqual(reservation.completions, (self.completion,))
            return configuration_cleanup_result(request, expected)

        adapter = RecordingRuntimeAdapter(removed)
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        result = coordinator.execute(command)
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(adapter.legacy_calls, [])
        request = adapter.runtime_calls[0][1]
        identity = EffectAttemptIdentity(RunId(claimed.run.run_id), request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertIs(reservation.status, EffectAttemptStatus.SUCCEEDED)
            self.assertEqual(reservation.outcomes, expected)
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
            self.assertEqual(configuration_cleanup_outcomes(request, outcome.outcome.result), expected)
        before = self.ceiling_truth()
        replay = coordinator.execute(command)
        self.assertIs(replay.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(self.ceiling_truth(), before)
