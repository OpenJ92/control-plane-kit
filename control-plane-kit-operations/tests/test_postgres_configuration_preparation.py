"""#1923 first target batch; new laws at Operations' durable start boundary."""
from dataclasses import replace
import unittest

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRefCodec, ConfigurationInstanceSelection,
)
from control_plane_kit_core.planning import ActivityId
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
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict) as caught:
            service.execute(self.configuration_command(forged))
        self.assert_safe_error(caught.exception, "caller-chosen-allocation")
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_public_intent_insert_cannot_bypass_configuration_preparation(self):
        intent = self.intent()
        attempt, evidence = self.intent_attempt(activity_id="start-api", intent=intent)
        before = self.complete_start_snapshot()
        with self.assertRaises(OperationsRecordError):
            with self.unit_of_work() as uow:
                uow.stores.execution.add_event(attempt.original_start_event)
                uow.stores.effect_attempt_intents.insert(evidence)
                uow.commit()
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_self_consistent_material_drift_cannot_replace_approved_selection(self):
        original = self.intent()
        material = original.products[0]
        contract = material.product.runtime_contract
        artifact, *rest = contract.configuration_artifacts
        altered = replace(artifact, content='{"selection":"unapproved-material"}')
        changed_product = replace(material.product, runtime_contract=replace(contract,
            configuration_artifacts=(altered, *rest)))
        changed = replace(original, products=(replace(material, product=changed_product),),
            kind=type(original.kind).REALIZE_ACTIVITY, configuration_instances=None)
        changed = replace(changed, kind=original.kind,
            configuration_instances=self.configuration_selection(changed))
        # Both Core material and its new deterministic refs agree. Only the
        # durable owner can discover disagreement with the approved graph.
        command = self.configuration_command(changed)
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict) as caught:
            service.execute(command)
        self.assert_safe_error(caught.exception, "unapproved-material")
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_private_intent_writer_without_owner_guard_refuses_configuration(self):
        attempt, evidence = self.intent_attempt(activity_id="start-api", intent=self.intent())
        before = self.complete_start_snapshot()
        with self.assertRaises(OperationsRecordError):
            with self.unit_of_work() as uow:
                uow.stores.execution.add_event(attempt.original_start_event)
                uow.stores.effect_attempt_intents._insert(evidence)
                uow.commit()
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_existing_configured_node_without_b2_truth_cannot_fall_back_to_birth(self):
        self.configuration_existing_node = True
        self.reset_start_truth()
        command = self.configuration_command()
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict):
            service.execute(command)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_another_approved_activity_cannot_remint_an_unresolved_slot(self):
        self.configuration_history_count = 2
        self.reset_start_truth()
        original = self.configuration_command()
        self.start_service("unresolved-configuration-start").execute(original)
        other = replace(original.intent, activity_id=ActivityId("history-use-001"))
        other = replace(other, configuration_instances=self.configuration_selection(other))
        self.assertNotEqual(other.configuration_instances, original.intent.configuration_instances)
        command = self.command(intent=other, transition=self.transition(
            identity=self.identity(activity_id="history-use-001"), intent=other))
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict):
            service.execute(command)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_exact_replay_keeps_original_refs_claims_and_attempt_without_ids(self):
        command = self.configuration_command()
        first = self.start_service("configuration-start").execute(command)
        self.expire_claim()
        before = self.attempt_snapshot(), self.protection_rows()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.reject_database_observation("original replay sampled database time"):
            replay = service.execute(command)
        self.assertIsInstance(replay, ExistingAttempt)
        self.assertEqual(replay.attempt, first.attempt)
        self.assertEqual(ids.calls, [])
        self.assertEqual((self.attempt_snapshot(), self.protection_rows()), before)
