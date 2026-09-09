from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import BlockSockets, DeploymentTopology, DockerRuntime
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductInstanceConfiguration, ProductInstantiationError, ProductReference,
    ProductRuntimeContract, ProductIdentity, instantiate_product,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery, RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.runtime_effects import RuntimeProductMaterial
from control_plane_kit_core.planning import ReconcileNode, compile_activity_plan
from control_plane_kit_core.topology import (
    GraphDescriptorCodec, ModifiedChange, NodeSubject, compile_topology,
    diff_graphs, validate_graph,
)


AUTHORITY = RuntimeAuthorityReference("local-docker")
DELIVERY = RuntimeAuthorityAccessDelivery(
    AUTHORITY, RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
)


def product():
    return ContainerServerProduct(
        ProductIdentity("example", "process", 1),
        OciImageReference("example.org", "process", "sha256:" + "a" * 64),
        ProductRuntimeContract(sockets=BlockSockets()),
    )


def graph(*, deliveries=None):
    value = product()
    config = ProductInstanceConfiguration.from_contract(value.runtime_contract)
    if deliveries is not None:
        config = replace(config, runtime_authority_deliveries=deliveries)
    nodes = tuple(
        instantiate_product(value, name, config if name == "controller" else
                            ProductInstanceConfiguration.from_contract(value.runtime_contract))
        for name in ("controller", "database", "custody")
    )
    return compile_topology(DeploymentTopology(
        "recipient-example", DockerRuntime(runtime_id="docker", authority_ref=AUTHORITY,
                                            children=nodes),
    ))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class NodeAuthorityDeliveryTests(unittest.TestCase):
    def test_existing_empty_graph_canonical_baseline(self):
        descriptor = GraphDescriptorCodec().encode(graph())
        self.assertNotIn("runtime_authority_deliveries", canonical(descriptor))
        fixture = Path(__file__).parent / "fixtures" / "node_authority_empty_graph.json"
        # Captured from unchanged production at 0c53845 during the owning red gate.
        frozen = fixture.read_text().strip()
        self.assertEqual(canonical(descriptor), frozen)
        self.assertEqual(canonical(GraphDescriptorCodec().encode(
            GraphDescriptorCodec().decode(json.loads(frozen)))), frozen)
        self.assertEqual(hashlib.sha256(canonical(descriptor).encode()).digest(),
                         hashlib.sha256(frozen.encode()).digest())

    def test_only_explicit_instance_receives_delivery(self):
        desired = graph(deliveries=(DELIVERY,))
        self.assertEqual(desired.node("controller").runtime_authority_deliveries, (DELIVERY,))
        for name in ("database", "custody"):
            self.assertEqual(desired.node(name).runtime_authority_deliveries, ())
        self.assertNotIn("runtime_authority_deliveries", product().runtime_contract.descriptor())

    def test_missing_declaration_defaults_to_empty(self):
        desired = graph()
        for node in desired.nodes.values():
            self.assertEqual(node.runtime_authority_deliveries, ())
        restored = GraphDescriptorCodec().decode(GraphDescriptorCodec().encode(desired))
        self.assertEqual(restored.node("controller").runtime_authority_deliveries, ())

    def test_delivery_round_trip_and_explicit_empty_canonicalization(self):
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph(deliveries=(DELIVERY,)))
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)
        self.assertEqual(codec.decode(descriptor).node("controller").runtime_authority_deliveries,
                         (DELIVERY,))
        descriptor["nodes"]["controller"]["runtime_authority_deliveries"] = []
        self.assertNotIn("runtime_authority_deliveries",
                         codec.encode(codec.decode(descriptor))["nodes"]["controller"])

    def test_invalid_or_duplicate_declarations_are_rejected(self):
        for values in ((object(),), (DELIVERY, DELIVERY)):
            with self.subTest(values=values), self.assertRaises((ProductInstantiationError, ValueError)):
                ProductInstanceConfiguration(runtime_authority_deliveries=values)
        desired = graph()
        with self.assertRaises(ValueError):
            replace(desired.node("controller"), runtime_authority_deliveries=(DELIVERY, DELIVERY))

    def test_graph_codec_rejects_unknown_delivery_payload(self):
        descriptor = GraphDescriptorCodec().encode(graph())
        descriptor["nodes"]["controller"]["runtime_authority_deliveries"] = [
            {**DELIVERY.descriptor(), "socket_path": "/private/material"},
        ]
        with self.assertRaises(ValueError):
            GraphDescriptorCodec().decode(descriptor)

    def test_add_and_remove_delivery_change_identity_and_reconcile_only_recipient(self):
        before, after = graph(), graph(deliveries=(DELIVERY,))
        codec = GraphDescriptorCodec()
        self.assertNotEqual(hashlib.sha256(canonical(codec.encode(before)).encode()).digest(),
                            hashlib.sha256(canonical(codec.encode(after)).encode()).digest())
        for current, desired in ((before, after), (after, before)):
            with self.subTest(add=current is before):
                changes = diff_graphs(validate_graph(current), validate_graph(desired))
                relevant = [c for c in changes.changes if isinstance(c, ModifiedChange)
                            and isinstance(c.subject.owner, NodeSubject)
                            and c.subject.field.value == "runtime-authority-deliveries"]
                self.assertEqual(len(relevant), 1)
                self.assertEqual(relevant[0].subject.owner.node_id, "controller")
                plan = compile_activity_plan(changes)
                self.assertTrue(plan.ready_for_execution)
                self.assertEqual([a.operation.target.node_id for a in plan.activities
                                  if isinstance(a.operation, ReconcileNode)], ["controller"])

    def test_runtime_material_carries_exact_recipient_declaration(self):
        value = product()
        material = RuntimeProductMaterial(
            node_id="controller", runtime_id="docker", product=value,
            reference=ProductReference.from_document(ProductDescriptorCodec().encode_document(value)),
            runtime_authority_deliveries=(DELIVERY,),
        )
        descriptor = material.descriptor()
        self.assertEqual(RuntimeProductMaterial.from_descriptor(descriptor), material)
        self.assertEqual(descriptor["runtime_authority_deliveries"], [DELIVERY.descriptor()])
        plain = replace(material, runtime_authority_deliveries=())
        self.assertNotIn("runtime_authority_deliveries", plain.descriptor())
        self.assertEqual(RuntimeProductMaterial.from_descriptor(plain.descriptor()), plain)
