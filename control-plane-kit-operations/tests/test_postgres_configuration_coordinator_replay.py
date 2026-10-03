"""#1923 original-intent-first caller law, ending at observation handoff."""
import unittest
from unittest import mock

from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning.saga import derive_schedule, project_activity_journal
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests import postgres_effect_attempt_coordinator_fixture as coordinator_fixture
from tests import test_postgres_configuration_evolved_replay as evolved_fixture


class PostgresConfigurationCoordinatorReplayTests(ConfigurationPreparationFixture, unittest.TestCase):
    coordinator_command = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_command
    coordinator_harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness
    fold_direct_outcome = evolved_fixture.PostgresConfigurationEvolvedReplayTests.fold_direct_outcome

    def test_nonactionable_history_precedes_independent_ready_configuration_proposal(self):
        for status in ("uncertain", "failed"):
            with self.subTest(status=status):
                self.configuration_history_count = 2
                self.reset_start_truth()
                started = self.start_service("nonactionable-start").execute(self.configuration_command())
                self.fold_direct_outcome(started, status=status)
                protection = self.protection_rows()
                with self.unit_of_work() as uow:
                    plan = uow.stores.activity_history.get_plan("plan-a")
                    events = uow.stores.execution.events_for_run("run-a")
                    run = uow.stores.execution.get_run("run-a")
                self.assertIs(run.status, ActivityRunStatus.RUNNING)
                projection = project_activity_journal(plan.plan, activity_journal_events(events))
                schedule = derive_schedule(plan.plan, projection.state)
                self.assertEqual(tuple(value.activity_id.value for value in schedule.ready), ("history-use-001",))
                self.assertTrue(projection.uncertain if status == "uncertain" else schedule.failed)
                harness = self.coordinator_harness()
                with mock.patch.object(ConfigurationPreparationStore, "_proposal",
                        side_effect=AssertionError("nonactionable history selected fresh configuration")) as proposal:
                    result = harness.coordinator.execute(self.coordinator_command())
                proposal.assert_not_called()
                self.assertIs(result.status, CoordinatorStatus.UNCERTAIN if status == "uncertain" else CoordinatorStatus.FAILED)
                self.assertIs(result.run.status, ActivityRunStatus.RUNNING if status == "uncertain" else ActivityRunStatus.FAILED)
                self.assertEqual(result.effects_attempted, 0)
                self.assertEqual(harness.start.commands, [])
                self.assertEqual(harness.start_ids.calls, [])
                self.assertEqual(harness.adapter.runtime_calls, [])
                self.assertEqual(self.protection_rows(), protection)

    def test_existing_attempt_uses_original_before_translation_and_only_hands_off_to_observation(self):
        command = self.configuration_command()
        original = self.start_service("original-configuration-start").execute(command)
        before = self.complete_start_snapshot()
        adapter = coordinator_fixture.RecordingRuntimeAdapter(
            AssertionError("existing configuration attempt redispatched"))
        harness = self.coordinator_harness(adapter=adapter)
        boundary = RuntimeError("observation handoff reached")

        def stop_at_observation():
            raise boundary

        harness.reconciliation.before_execute = stop_at_observation
        with mock.patch("control_plane_kit_operations.coordinator._runtime_effect_intent_for_context",
                side_effect=AssertionError("existing attempt selected current material before original intent")):
            with self.assertRaises(RuntimeError) as caught:
                harness.coordinator.execute(self.coordinator_command())
        self.assertIs(caught.exception, boundary)
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(harness.start.commands[0].intent, command.intent)
        self.assertEqual(len(harness.reconciliation.commands), 1)
        self.assertEqual(harness.reconciliation.commands[0].identity, original.attempt.state.identity)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(harness.start_ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)
