"""#1923 original protection survives direct outcomes and lawful linked retry."""
import unittest

from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.activity_run_retry_interpreter import ActivityRunRetryCommandService
from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt
from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt
from control_plane_kit_operations.effect_outcome_evidence import (
    ExecutionEffectOutcome, effect_outcome_failure, effect_outcome_transition,
)
from control_plane_kit_operations.lifecycle import FailActivityRun, RunLifecycleCommandService
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.activity_run_retry_interpreter_fixture import PostgresActivityRunRetryFixture
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds


class PostgresConfigurationEvolvedReplayTests(ConfigurationPreparationFixture, unittest.TestCase):
    def fold_direct_outcome(self, started, *, status):
        attempt = started.attempt
        result = getattr(RuntimeEffectResult, status)(attempt.original_start_event.event_id,
            RuntimeEffectFailure("configuration.fixture-outcome", "Recorded bounded test outcome."))
        outcome = ExecutionEffectOutcome(attempt.state.identity, attempt.state.request_fingerprint, result)
        failure = effect_outcome_failure(outcome)
        command = FoldEffectAttempt("request-a", effect_outcome_transition(outcome),
            self.authority(), self.fence(), failure, outcome)
        folded = EffectAttemptFoldService(self.unit_of_work,
            id_factory=GeneratedIds("configuration-fold")).execute(command)
        return folded.attempt, failure

    def test_failed_and_uncertain_original_replay_preserves_all_protection_after_expiry(self):
        for status in ("failed", "uncertain"):
            with self.subTest(status=status):
                self.reset_start_truth()
                command = self.configuration_command()
                started = self.start_service("configuration-start").execute(command)
                original_protection = self.protection_rows()
                evolved, _ = self.fold_direct_outcome(started, status=status)
                self.assertEqual(self.protection_rows(), original_protection)
                self.expire_claim()
                before = self.complete_start_snapshot()
                service, ids = self.start_service_with_sequence("must-not-be-used")
                with self.reject_database_observation("evolved replay sampled database time"):
                    replay = service.execute(command)
                self.assertEqual(replay, ExistingAttempt(evolved))
                self.assertEqual(replay.attempt.original_start_event, started.attempt.original_start_event)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.complete_start_snapshot(), before)

    def test_original_replay_after_real_linked_run_retry_keeps_original_refs(self):
        command = self.configuration_command()
        started = self.start_service("configuration-start").execute(command)
        evolved, failure = self.fold_direct_outcome(started, status="failed")
        RunLifecycleCommandService(self.unit_of_work, clock=lambda: "2030-01-01T00:01:00Z",
            id_factory=GeneratedIds("configuration-run-failure")).execute(FailActivityRun(
                "run-a", self.authority(), self.fence(), IdempotencyKey("configuration-run-failure"), failure))
        retry_command = PostgresActivityRunRetryFixture.retry_command(self)
        retried = ActivityRunRetryCommandService(self.unit_of_work,
            id_factory=GeneratedIds("configuration-retry")).execute(retry_command)
        self.assertEqual(retried.prior_run.run_id, "run-a")
        self.assertNotEqual(retried.run.run_id, "run-a")
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.reject_database_observation("historical replay sampled database time"):
            replay = service.execute(command)
        self.assertEqual(replay, ExistingAttempt(evolved))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)
