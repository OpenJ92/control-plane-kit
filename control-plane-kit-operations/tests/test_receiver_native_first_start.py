"""Real successor node first-start after explicitly assumed predecessor history."""

from dataclasses import replace
import unittest

from control_plane_kit_core.planning import StartNode
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError, ExistingAttempt, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.admission import ExecutionAdmissionError
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds, RecordingRuntimeAdapter
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture
from tests.receiver_recorded_completion_fixture import retain_completion_inputs


class ReceiverNativeFirstStartTests(ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def test_fresh_execution_admission_requires_original_receiver_binding(self):
        self.desired_receiver("admission", graph=self.canonical_receiver_graph)
        _, plan, approval = self.plan_and_approve("admission")
        with self.mismatched_receiver_binding():
            before = self.acceptance_truth()
            with self.assertRaises(ExecutionAdmissionError):
                self.admit_approved("admission", plan, approval)
            self.assertEqual(self.acceptance_truth(), before)
        admitted = self.admit_approved("admission", plan, approval)
        self.assertFalse(admitted.replayed)

    def receiver_start(self):
        self.desired_receiver("native", graph=self.canonical_receiver_graph)
        _, plan, _ = self.plan_and_admit("native")
        claimed = self.ready_run("native")
        context = self.coordinator(self.unit_of_work, RecordingRuntimeAdapter(), "native")._load_context(
            self.execution_command(claimed, "native"))
        position = next(index for index, activity in enumerate(plan.plan.activities)
            if type(activity.operation) is StartNode and activity.operation.target.node_id == "api")
        # Predecessor health/ingress/runtime completion is assumed. The node's
        # own first start below enters the actual public permission owner.
        retain_completion_inputs(self, context, activities=plan.plan.activities[:position])
        return self.native_start_command(claimed, "native",
            activity_id=plan.plan.activities[position].activity_id.value)

    def service(self):
        return EffectAttemptStartService(self.unit_of_work, id_factory=GeneratedIds("receiver-start"))

    def test_successor_node_first_start_is_real_and_exact_replay_grants_no_new_dispatch(self):
        command = self.receiver_start()
        started = self.service().execute(command)
        self.assertIsInstance(started, NewlyStarted)
        self.assertEqual(started.attempt.state.identity, command.transition.identity)
        before = self.acceptance_truth()
        replay = self.service().execute(command)
        self.assertIsInstance(replay, ExistingAttempt)
        self.assertEqual(replay.attempt, started.attempt)
        self.assertEqual(self.acceptance_truth(), before)
        self.assertIsNone(self.receiver_origin().first_accepted_action_id)

    def test_substituted_receiver_configuration_cannot_use_original_scope_and_plan(self):
        command = self.receiver_start()
        material, = command.intent.products
        other = self.receiver_graph(node_id="api", receiver="b" * 32)[0].node("api")
        product = replace(material.product, runtime_contract=replace(material.product.runtime_contract,
            configuration_artifacts=other.configuration_artifacts))
        intent = replace(command.intent, products=(replace(material, product=product),))
        candidate = replace(command, intent=intent,
            transition=replace(command.transition, request_fingerprint=runtime_effect_intent_fingerprint(intent)))
        before = self.acceptance_truth()
        with self.assertRaises(EffectAttemptStartError):
            self.service().execute(candidate)
        self.assertEqual(self.acceptance_truth(), before)

    def test_actual_material_cannot_move_outside_original_runtime_coverage(self):
        command = self.receiver_start()
        material, = command.intent.products
        intent = replace(command.intent, products=(replace(material, runtime_id="foreign-runtime"),))
        candidate = replace(command, intent=intent,
            transition=replace(command.transition, request_fingerprint=runtime_effect_intent_fingerprint(intent)))
        before = self.acceptance_truth()
        with self.assertRaises(EffectAttemptStartError):
            self.service().execute(candidate)
        self.assertEqual(self.acceptance_truth(), before)

    def test_mismatched_original_receiver_binding_refuses_before_first_start(self):
        command = self.receiver_start()
        with self.mismatched_receiver_binding():
            before = self.acceptance_truth()
            with self.assertRaises(EffectAttemptStartError):
                self.service().execute(command)
            self.assertEqual(self.acceptance_truth(), before)
        self.assertIsInstance(self.service().execute(command), NewlyStarted)
