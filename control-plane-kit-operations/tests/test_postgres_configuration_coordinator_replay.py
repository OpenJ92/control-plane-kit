"""#1923 original-intent-first caller law, ending at observation handoff."""
import unittest
from unittest import mock

from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests import postgres_effect_attempt_coordinator_fixture as coordinator_fixture


class PostgresConfigurationCoordinatorReplayTests(ConfigurationPreparationFixture, unittest.TestCase):
    coordinator_command = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_command
    coordinator_harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness

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
