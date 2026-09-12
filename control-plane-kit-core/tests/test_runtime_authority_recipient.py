from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import BlockSockets
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.planning import (
    ActivityId, NodeTarget, ReconcileNode, RemoveNodeResource, RuntimeTarget,
    StartNode, StartRuntime, StopNode, StopRuntime,
)
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductReference, ProductRuntimeContract,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery, RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent, RuntimeEffectIntentSource, RuntimeEffectObservationRequest,
    runtime_effect_intent_fingerprint, runtime_effect_intent_for_request,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectContractError, RuntimeEffectKind, RuntimeEffectRequest,
    RuntimeEffectSource, RuntimeProductMaterial,
)
from control_plane_kit_core.types import RuntimeKind


AUTHORITY = RuntimeAuthorityReference("local-docker")
DELIVERY = RuntimeAuthorityAccessDelivery(
    AUTHORITY, RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
)


def material(*, node_id="controller", deliveries=(DELIVERY,)):
    product = ContainerServerProduct(
        ProductIdentity("example", "process", 1),
        OciImageReference("example.org", "process", "sha256:" + "a" * 64),
        ProductRuntimeContract(sockets=BlockSockets()),
    )
    return RuntimeProductMaterial(
        node_id=node_id, runtime_id="docker", product=product,
        reference=ProductReference.from_document(
            ProductDescriptorCodec().encode_document(product)),
        runtime_authority_deliveries=deliveries,
    )


def effect(constructor, **changes):
    source_fields = dict(
        workspace_id="workspace-a", request_id="request-a", run_id=RunId("run-a"),
        plan_id="plan-a", base_graph_id="base", desired_graph_id="desired",
    )
    fields = dict(
        kind=RuntimeEffectKind.REALIZE_ACTIVITY, runtime_kind=RuntimeKind.DOCKER,
        activity_id=ActivityId("start-controller"),
        operation=StartNode(NodeTarget("controller")), authority_ref=AUTHORITY,
        authority_deliveries=(DELIVERY,), products=(material(),),
    )
    if constructor is RuntimeEffectRequest:
        fields.update(
            effect_id="event-a",
            source=RuntimeEffectSource(**source_fields, intent_event_id="event-a"),
        )
    else:
        fields["source"] = RuntimeEffectIntentSource(**source_fields)
    fields.update(changes)
    return constructor(**fields)


class RuntimeAuthorityRecipientTests(unittest.TestCase):
    def test_start_and_reconcile_bind_exact_target_declaration(self):
        for constructor in (RuntimeEffectRequest, RuntimeEffectIntent):
            for operation in (StartNode(NodeTarget("controller")),
                              ReconcileNode(NodeTarget("controller"))):
                with self.subTest(constructor=constructor, operation=operation):
                    value = effect(constructor, operation=operation)
                    self.assertEqual(value.authority_deliveries,
                                     value.products[0].runtime_authority_deliveries)

    def test_invalid_recipient_compositions_fail_closed(self):
        cases = {
            "missing material": {"products": ()},
            "foreign material": {"products": (material(node_id="sibling"),)},
            "extra material": {"products": (material(), material(node_id="sibling"))},
            "undeclared delivery": {"products": (material(deliveries=()),)},
            "omitted delivery": {"authority_deliveries": ()},
            "foreign authority": {"authority_ref": RuntimeAuthorityReference("other")},
            "missing authority": {"authority_ref": None},
        }
        for constructor in (RuntimeEffectRequest, RuntimeEffectIntent):
            for label, changes in cases.items():
                with self.subTest(constructor=constructor, case=label):
                    with self.assertRaises(RuntimeEffectContractError):
                        effect(constructor, **changes)

    def test_runtime_and_teardown_operations_cannot_select_process_delivery(self):
        operations = (
            StartRuntime(RuntimeTarget("docker")), StopRuntime(RuntimeTarget("docker")),
            StopNode(NodeTarget("controller")), RemoveNodeResource(NodeTarget("controller")),
        )
        for constructor in (RuntimeEffectRequest, RuntimeEffectIntent):
            for operation in operations:
                with self.subTest(constructor=constructor, operation=operation):
                    with self.assertRaises(RuntimeEffectContractError):
                        effect(constructor, operation=operation)

    def test_teardown_preserves_declaration_without_selecting_delivery(self):
        for constructor in (RuntimeEffectRequest, RuntimeEffectIntent):
            for operation in (StopNode(NodeTarget("controller")),
                              RemoveNodeResource(NodeTarget("controller"))):
                with self.subTest(constructor=constructor, operation=operation):
                    value = effect(constructor, operation=operation, authority_deliveries=())
                    self.assertEqual(value.authority_deliveries, ())
                    self.assertEqual(value.products[0].runtime_authority_deliveries, (DELIVERY,))

    def test_no_power_request_remains_valid_without_product_material(self):
        for constructor in (RuntimeEffectRequest, RuntimeEffectIntent):
            value = effect(constructor, authority_ref=None, authority_deliveries=(), products=())
            self.assertEqual(value.products, ())

    def test_observation_retains_the_committed_delegation_and_fingerprint(self):
        request = effect(RuntimeEffectRequest)
        intent = runtime_effect_intent_for_request(request)
        observation = RuntimeEffectObservationRequest(request)
        self.assertEqual(observation.intent, intent)
        self.assertEqual(observation.request_fingerprint, runtime_effect_intent_fingerprint(intent))
        self.assertEqual(observation.intent.authority_deliveries, (DELIVERY,))
        self.assertEqual(observation.intent.products[0].runtime_authority_deliveries, (DELIVERY,))
        plain = replace(request, authority_deliveries=(), products=(material(deliveries=()),))
        self.assertNotEqual(runtime_effect_intent_fingerprint(intent),
                            runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(plain)))
