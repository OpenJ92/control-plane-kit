"""Workload preparation retains original truth through the actual start owner."""

from dataclasses import replace
import unittest

from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, StartNode
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from tests.effect_attempt_intent_fixture import product_material
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver
from tests.test_postgres_prepared_runtime_invocations import _PreparedRuntimeInvocationFixture
from tests.test_runtime_interpreter_dispatcher import _registered_product, graph_with_node


class PostgresWorkloadInvocationTargets(_PreparedRuntimeInvocationFixture, unittest.TestCase):
    def reset_start_truth(self, **_options):
        material = product_material()
        document = ProductDescriptorCodec().encode_document(material.product)
        product = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=document, source=_registered_product().source,
            imported_by="operator-a", imported_at=self.clock())
        desired = graph_with_node(RuntimeKind.DOCKER,
            authority_ref=RuntimeAuthorityReference("prepared-runtime"),
            registered_product=product, secret_selections=material.secret_selections)
        desired = replace(desired, nodes={"api": replace(desired.nodes["api"],
            sockets=material.product.runtime_contract.sockets,
            block_spec=replace(desired.nodes["api"].block_spec,
                verification=material.product.runtime_contract.verification))})
        self.activity_id = "start-workload"
        plan = ActivityPlan((PlannedActivity(ActivityId(self.activity_id), StartNode(NodeTarget("api"))),))
        base = replace(desired, name="current", nodes={},
            runtimes={key: replace(runtime, children=()) for key, runtime in desired.runtimes.items()})
        self.engine = super().reset_start_truth(plan=plan,
            base=base, desired=replace(desired, name="desired"),
            product_document=document, runtime_authority=LocalDockerSocketAuthority())

    def test_registered_local_consumer_start_retains_atomic_prepared_marker(self):
        bound = self.binding(generation=self.runtime_fence.generation)
        owner = self.engine.coordinator(FailIfObserver())
        context = owner._load_context(self.engine.command(generation=self.runtime_fence.generation))
        intent = _runtime_effect_intent_for_context(context, context.plan.activity(ActivityId(self.activity_id)))
        material, = intent.products
        self.assertEqual(material.secret_selections, product_material().secret_selections)
        self.assertEqual(intent.authority_ref, self.reference)
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        command = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence)
        started = self.fresh_start().execute(command, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(original.intent, intent)
            self.assertEqual(original.original_start_event, started.attempt.original_start_event)
            self.assertEqual(uow.stores.effect_attempts.get(started.attempt.state.identity), started.attempt)
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=self.activity_id, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertIsNotNone(marker, "registered local consumer start lacks its atomic prepared dispatch marker")
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.outcome)
