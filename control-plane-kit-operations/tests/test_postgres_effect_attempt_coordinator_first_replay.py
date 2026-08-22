from __future__ import annotations

import unittest
from unittest import mock

from control_plane_kit_core.operations import EffectRecoveryResolution
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
)
from control_plane_kit_operations.coordinator import (
    CoordinatorStatus,
    ExecutionCoordinatorConflict,
)
from control_plane_kit_operations.effect_attempt_fold import (
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
)
from control_plane_kit_operations.effect_attempt_start import ExistingAttempt
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from control_plane_kit_operations.records import ActivityEventRecord
from tests.postgres_effect_attempt_coordinator_fixture import (
    PostgresEffectAttemptCoordinatorFixture,
    RecordingRuntimeAdapter,
)


RECOVERY_ERROR = "effect attempt recovery requires explicit recovery authority"


class PostgresEffectAttemptCoordinatorFirstReplayTests(
    PostgresEffectAttemptCoordinatorFixture,
    unittest.TestCase,
):
    def test_control_accepted_start_reconciliation_and_guarded_fold_services(self) -> None:
        started = self.persisted_started()
        replay = self.start_service("unused-start-id").execute(self.start_command())
        self.assertEqual(replay, ExistingAttempt(started))

        self.reset_start_truth()
        story = self.observed_story()
        current, intent, _record, authority, observer = (
            self.seed_running_reconciliation(story)
        )
        reconciled = self.reconciliation_service(observer, story).execute(
            self.reconciliation_command(
                current,
                scopes=self.coordinator_command().authority.scopes,
            )
        )
        self.assertIs(type(reconciled), NewlyFolded)

        self.reset_start_truth()
        current, intent, _record = self.seed_guarded_source(story)
        authority = self.register_runtime_authority(intent)
        guarded = self.expected_observed_fold(story, current, intent, authority)
        fold_ids = self.fold_ids_for_story("control-observed-fold", story)
        service, sequence = self.observed_service(*fold_ids)
        folded = service.execute_observed(guarded)
        self.assertIs(type(folded), NewlyFolded)
        self.assertEqual(sequence.calls, list(fold_ids))

    def test_running_forward_attempt_reconciles_without_provider_redispatch(self) -> None:
        story = self.observed_story()
        current, _intent, _record, _authority, observer = (
            self.seed_running_reconciliation(story)
        )
        adapter = RecordingRuntimeAdapter(
            AssertionError("existing attempt redispatched provider")
        )
        harness = self.coordinator_harness(adapter=adapter, observer=observer)

        result = harness.coordinator.execute(self.coordinator_command())

        self.assertIn(result.status, (CoordinatorStatus.COMPLETED, CoordinatorStatus.FAILED))
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(len(harness.reconciliation.commands), 1)
        self.assertEqual(
            harness.reconciliation.commands[0].identity,
            current.state.identity,
        )

    def test_running_recovery_bearing_attempt_conflicts_before_reconciliation(self) -> None:
        current, _intent, _record, _authority, _observer = (
            self.seed_running_reconciliation()
        )
        recovered = self.transition_record(
            current,
            "recovered-succeeded",
            event_id="recovered-running-canary",
            ordinal=current.latest_transition_event.ordinal + 1,
        )
        adapter = RecordingRuntimeAdapter(
            AssertionError("recovery-bearing attempt dispatched provider")
        )
        harness = self.coordinator_harness(adapter=adapter)
        original = EffectAttemptStartService.execute

        def recovery_replay(service, command):
            result = original(service, command)
            self.assertIs(type(result), ExistingAttempt)
            self.assertEqual(result.attempt, current)
            return ExistingAttempt(recovered)

        with mock.patch.object(EffectAttemptStartService, "execute", recovery_replay):
            with self.assertRaises(ExecutionCoordinatorConflict) as caught:
                harness.coordinator.execute(self.coordinator_command())

        self.assertEqual(str(caught.exception), RECOVERY_ERROR)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(current.state.identity, recovered.state.identity)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(harness.reconciliation.commands, [])

    def test_step_started_without_attempt_never_appends_another_start(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_event(
                ActivityEventRecord(
                    "orphan-step-start",
                    "run-a",
                    3,
                    ActivityEventKind.STEP_STARTED,
                    "2030-01-01T00:00:00Z",
                    activity_id="start-runtime",
                )
            )
            unit_of_work.commit()
        before = self.coordinator_snapshot()
        adapter = RecordingRuntimeAdapter(
            AssertionError("orphan running activity dispatched provider")
        )
        harness = self.coordinator_harness(adapter=adapter)

        with self.assertRaises(ExecutionCoordinatorConflict) as caught:
            harness.coordinator.execute(self.coordinator_command())

        self.assertEqual(
            str(caught.exception),
            "effect attempt start truth is invalid",
        )
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(harness.reconciliation.commands, [])
        self.assertEqual(self.coordinator_snapshot(), before)

    def test_terminal_direct_replay_is_provider_and_reconciliation_free(self) -> None:
        story = self.outcome_story("execution-succeeded")
        attempt, outcome = self.persist_terminal(story)
        adapter = RecordingRuntimeAdapter(
            AssertionError("terminal attempt redispatched provider")
        )
        harness = self.coordinator_harness(adapter=adapter)

        first = harness.coordinator.execute(self.coordinator_command())
        second = harness.coordinator.execute(self.coordinator_command())

        self.assertIs(first.status, CoordinatorStatus.COMPLETED)
        self.assertIs(second.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(first.effects_attempted, 0)
        self.assertEqual(second.effects_attempted, 0)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(harness.start.commands, [])
        self.assertEqual(harness.reconciliation.commands, [])
        self.assertEqual(attempt.state.identity, outcome.attempt.state.identity)

    def test_recovered_terminal_success_and_failure_are_lifecycle_only(self) -> None:
        for resolution, expected_status in (
            (EffectRecoveryResolution.SUCCEEDED, ActivityRunStatus.SUCCEEDED),
            (EffectRecoveryResolution.FAILED, ActivityRunStatus.FAILED),
        ):
            with self.subTest(resolution=resolution.value):
                self.reset_start_truth()
                terminal = self.persist_recovery_resolution(resolution)
                adapter = RecordingRuntimeAdapter(
                    AssertionError("terminal recovery dispatched provider")
                )
                harness = self.coordinator_harness(adapter=adapter)

                result = harness.coordinator.execute(self.coordinator_command())

                self.assertIs(self.run_status(), expected_status)
                self.assertEqual(result.effects_attempted, 0)
                self.assertEqual(adapter.runtime_calls, [])
                self.assertEqual(harness.start.commands, [])
                self.assertEqual(harness.reconciliation.commands, [])
                self.assertIsNotNone(terminal.state.recovery_decision)


if __name__ == "__main__":
    unittest.main()
