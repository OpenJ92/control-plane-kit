"""#1936 public-owner targets; simulated adapter results are not provider proof."""
import unittest

from control_plane_kit_core.configuration_instances import (
    ConfigurationCleanupOutcome, ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus,
    ConfigurationCleanupReason,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure, RuntimeEffectResult, configuration_cleanup_result, configuration_cleanup_outcomes,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus, RuntimeInterpreterDispatcher
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

    def assert_uncertain_cleanup(self, producer, *, reason=ConfigurationCleanupReason.PROVIDER_UNCERTAIN):
        claimed = self.ready_cleanup()
        adapter = RecordingRuntimeAdapter(producer)
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        result = coordinator.execute(command)
        self.assertIs(result.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        request = adapter.runtime_calls[0][1]
        expected = ConfigurationCleanupOutcomeSet((ConfigurationCleanupOutcome(
            self.selected_ref, ConfigurationCleanupStatus.UNKNOWN, reason),))
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            reservation = uow.stores.configuration_cleanup_ownership.get(identity)
            self.assertIs(reservation.status, EffectAttemptStatus.UNCERTAIN)
            self.assertEqual(reservation.outcomes, expected)
            self.assertEqual(len(reservation.members), 1)
            self.assertEqual(len(reservation.claims), 1)
            self.assertEqual(reservation.completions, (self.completion,))
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
            self.assertEqual(configuration_cleanup_outcomes(request, outcome.outcome.result), expected)
        before = self.ceiling_truth()
        self.assertNotIn("PROVIDER-CANARY", repr(before))
        replay = coordinator.execute(command)
        self.assertIs(replay.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(self.ceiling_truth(), before)

    def test_adapter_exception_retains_total_unknown_and_never_redispatches(self):
        self.assert_uncertain_cleanup(RuntimeError("PROVIDER-CANARY"))

    def test_wrong_adapter_result_type_retains_total_unknown(self):
        self.assert_uncertain_cleanup({"provider": "PROVIDER-CANARY"})

    def test_wrong_effect_id_retains_total_unknown(self):
        self.assert_uncertain_cleanup(RuntimeEffectResult.succeeded("foreign-event"))

    def test_invoked_adapter_unsupported_is_provider_uncertain_not_no_call(self):
        self.assert_uncertain_cleanup(lambda _context, request: RuntimeEffectResult.unsupported(
            request.effect_id, RuntimeEffectFailure("provider.unsupported", "PROVIDER-CANARY")))

    def test_missing_interpreter_is_known_not_attempted(self):
        dispatcher = RuntimeInterpreterDispatcher({})
        self.assert_uncertain_cleanup(dispatcher.execute_runtime,
            reason=ConfigurationCleanupReason.NOT_ATTEMPTED)

    def test_inner_interpreter_exception_retains_total_unknown(self):
        calls = []

        class FailingInterpreter:
            def execute(self, request):
                raise AssertionError("registered authority must use execute_with_authority")

            def execute_with_authority(self, request, authority):
                calls.append((request, authority))
                raise RuntimeError("PROVIDER-CANARY")

        def dispatch(context, request):
            return RuntimeInterpreterDispatcher({request.runtime_kind: FailingInterpreter()}).execute_runtime(
                context, request)

        self.assert_uncertain_cleanup(dispatch)
        self.assertEqual(len(calls), 1)
