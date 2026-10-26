"""Ordinary verification retains original history through the real start owner."""

from dataclasses import replace
import unittest

from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, WaitForHealthy
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.verification import VerificationContract
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from tests.effect_attempt_intent_fixture import product_material
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver
from tests.test_postgres_prepared_runtime_invocations import _PreparedRuntimeInvocationFixture
from tests.test_runtime_interpreter_dispatcher import _registered_product, graph_with_node


class _ProbeInvocationFixture(_PreparedRuntimeInvocationFixture):
    def reset_start_truth(self, **_options):
        material = product_material()
        check, = material.product.runtime_contract.verification.checks
        contract = replace(material.product.runtime_contract, secret_deliveries=(),
            verification=VerificationContract((replace(check, check_id="database-first"),
                replace(check, check_id="database-second"))))
        document = ProductDescriptorCodec().encode_document(replace(material.product, runtime_contract=contract))
        product = RegisteredProduct.from_document(workspace_id="workspace-a", descriptor_document=document,
            source=_registered_product().source, imported_by="operator-a", imported_at=self.clock())
        desired = graph_with_node(RuntimeKind.DOCKER,
            authority_ref=RuntimeAuthorityReference("prepared-runtime"), registered_product=product)
        node = desired.nodes["api"]
        desired = replace(desired, nodes={"api": replace(node, sockets=contract.sockets,
            block_spec=replace(node.block_spec, verification=contract.verification))})
        self.activity_id = "verify-workload"
        plan = ActivityPlan((PlannedActivity(ActivityId(self.activity_id), WaitForHealthy(NodeTarget("api"))),))
        self.engine = super().reset_start_truth(plan=plan, base=replace(desired, name="current"),
            desired=replace(desired, name="desired"), product_document=document,
            runtime_authority=LocalDockerSocketAuthority())


class PostgresProbeInvocationTargets(_ProbeInvocationFixture, unittest.TestCase):
    def test_registered_verification_start_retains_atomic_acquisition_marker(self):
        bound = self.binding(generation=self.runtime_fence.generation)
        context = self.engine.coordinator(FailIfObserver())._load_context(
            self.engine.command(generation=self.runtime_fence.generation))
        intent = _runtime_effect_intent_for_context(context, context.plan.activity(ActivityId(self.activity_id)))
        material, = intent.products
        first, second = material.product.runtime_contract.verification.checks
        self.assertNotEqual(first.check_id, second.check_id)
        self.assertEqual(first.authentication.password_reference, second.authentication.password_reference)
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        started = self.fresh_start().execute(self.start_command(intent=intent, transition=transition,
            fence=self.runtime_fence), command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(original.intent, intent)
            self.assertEqual(original.original_start_event, started.attempt.original_start_event)
            self.assertEqual(uow.stores.effect_attempts.get(started.attempt.state.identity), started.attempt)
            page = uow.stores.execution.command_invocations(bound.receipt, limit=10)
        self.assertIsNone(page.next_cursor)
        markers = tuple(row for row in page.items
            if row.activity_id == self.activity_id and row.kind.value == "ordinary-verification-acquisition")
        self.assertEqual(len(markers), 1, "ordinary verification start lacks its atomic acquisition marker")
        marker, = markers
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.outcome)
