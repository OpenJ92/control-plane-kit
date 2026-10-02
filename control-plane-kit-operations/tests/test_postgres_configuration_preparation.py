"""#1923 first target batch; new laws at Operations' durable start boundary."""
from dataclasses import replace
import unittest

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRefCodec, ConfigurationInstanceSelection,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartConflict, ExistingAttempt, NewlyStarted,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture


class PostgresConfigurationPreparationTests(ConfigurationPreparationFixture, unittest.TestCase):
    def test_new_start_freezes_every_exact_birth_ref_and_protective_claim(self):
        command = self.configuration_command()
        result = self.start_service("configuration-start").execute(command)
        self.assertIsInstance(result, NewlyStarted)
        refs, claims = self.protection_rows()
        selected = sorted(command.intent.configuration_instances.instances, key=lambda ref: ref.artifact_id)
        expected_keys = [("run-a", "start-api", 1, ref.artifact_id, ref.workspace_id, ref.allocation_id)
                         for ref in selected]
        self.assertEqual(claims, expected_keys)
        self.assertEqual([row[:6] for row in refs], expected_keys)
        self.assertEqual([row[6:] for row in refs], [(
            ref.runtime_id, ref.node_id, ConfigurationInstanceRefCodec().encode_canonical_bytes(ref),
            runtime_effect_intent_fingerprint(command.intent), "configuration-start", True,
        ) for ref in selected])
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(command.transition.identity)
            self.assertEqual(original.intent, command.intent)
            self.assertEqual(original.original_start_event, result.attempt.original_start_event)

    def test_caller_chosen_allocation_is_refused_before_ids_or_durable_writes(self):
        intent = self.intent()
        proposed = intent.configuration_instances.instances
        forged = replace(intent, configuration_instances=ConfigurationInstanceSelection((
            replace(proposed[0], allocation_id="caller-chosen-allocation"), *proposed[1:])))
        before = self.attempt_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict) as caught:
            service.execute(self.configuration_command(forged))
        self.assert_safe_error(caught.exception, "caller-chosen-allocation")
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_public_intent_insert_cannot_bypass_configuration_preparation(self):
        intent = self.intent()
        attempt, evidence = self.intent_attempt(activity_id="start-api", intent=intent)
        before = self.attempt_snapshot()
        with self.assertRaises(OperationsRecordError):
            with self.unit_of_work() as uow:
                uow.stores.execution.add_event(attempt.original_start_event)
                uow.stores.effect_attempt_intents.insert(evidence)
                uow.commit()
        self.assertEqual(self.attempt_snapshot(), before)

    def test_exact_replay_keeps_original_refs_claims_and_attempt_without_ids(self):
        command = self.configuration_command()
        first = self.start_service("configuration-start").execute(command)
        before = self.attempt_snapshot(), self.protection_rows()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        replay = service.execute(command)
        self.assertIsInstance(replay, ExistingAttempt)
        self.assertEqual(replay.attempt, first.attempt)
        self.assertEqual(ids.calls, [])
        self.assertEqual((self.attempt_snapshot(), self.protection_rows()), before)
