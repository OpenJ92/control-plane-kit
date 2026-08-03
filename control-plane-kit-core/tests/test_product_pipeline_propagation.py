from __future__ import annotations

import unittest

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductCatalog,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    instantiate_catalog_product,
)
from control_plane_kit_core.planning import ReconcileNode, StartNode, compile_activity_plan
from control_plane_kit_core.topology import (
    DeploymentGraph,
    GraphDescriptorCodec,
    MetadataValue,
    ModifiedChange,
    NodeSubject,
    StructuralField,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import Protocol


def digest(char: str) -> str:
    return "sha256:" + char * 64


class ProductPipelinePropagationTests(unittest.TestCase):
    def product(
        self,
        *,
        revision: int = 1,
        image_digest: str = digest("f"),
    ) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "hello", revision),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/hello",
                image_digest,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name="Hello server",
        )

    def graph(self, product: ContainerServerProduct) -> tuple[DeploymentGraph, str]:
        document = ProductDescriptorCodec().encode_document(product)
        catalog = ProductCatalog.from_documents((document,))
        block = instantiate_catalog_product(
            catalog,
            product.identity,
            role_id="hello-blue",
            configuration=ProductInstanceConfiguration.from_contract(
                product.runtime_contract
            ),
        )
        return (
            compile_topology(
                DeploymentTopology("hello", DockerRuntime(children=(block,)))
            ),
            document.content_digest,
        )

    def test_product_truth_survives_graph_descriptor_round_trip(self) -> None:
        graph, descriptor_digest = self.graph(self.product())
        codec = GraphDescriptorCodec()

        restored = codec.decode(codec.encode(graph))
        node = restored.node("hello-blue")

        self.assertEqual(node.metadata["product_identity"], "cpk-servers/hello/1")
        self.assertEqual(node.metadata["product_descriptor_digest"], descriptor_digest)
        self.assertEqual(
            node.metadata["oci_image"],
            f"ghcr.io/openj92/control-plane-kit-servers/hello@{digest('f')}",
        )

    def test_product_image_digest_change_is_explicit_structural_diff(self) -> None:
        before, before_descriptor_digest = self.graph(self.product(image_digest=digest("f")))
        after, after_descriptor_digest = self.graph(self.product(image_digest=digest("a")))

        diff = diff_graphs(validate_graph(before), validate_graph(after))

        metadata_changes = [
            change
            for change in diff.changes
            if isinstance(change, ModifiedChange)
            and isinstance(change.subject.owner, NodeSubject)
            and change.subject.owner.node_id == "hello-blue"
            and change.subject.field is StructuralField.NODE_METADATA
        ]
        self.assertEqual(len(metadata_changes), 1)
        change = metadata_changes[0]
        self.assertIsInstance(change.before, MetadataValue)
        self.assertIsInstance(change.after, MetadataValue)
        self.assertEqual(
            change.before.values["product_descriptor_digest"],
            before_descriptor_digest,
        )
        self.assertEqual(
            change.after.values["product_descriptor_digest"],
            after_descriptor_digest,
        )
        self.assertNotEqual(before_descriptor_digest, after_descriptor_digest)

        plan = compile_activity_plan(diff)
        self.assertTrue(
            any(isinstance(activity.operation, ReconcileNode) for activity in plan.activities)
        )
        self.assertTrue(plan.ready_for_execution)

    def test_product_revision_change_is_not_erased_by_same_role_id(self) -> None:
        before, _ = self.graph(self.product(revision=1))
        after, _ = self.graph(self.product(revision=2))

        diff = diff_graphs(validate_graph(before), validate_graph(after))

        self.assertTrue(
            any(
                isinstance(change, ModifiedChange)
                and isinstance(change.before, MetadataValue)
                and change.before.values["product_identity"] == "cpk-servers/hello/1"
                and change.after.values["product_identity"] == "cpk-servers/hello/2"
                for change in diff.changes
            )
        )

    def test_initial_external_product_deployment_uses_normal_start_activities(self) -> None:
        desired, _ = self.graph(self.product())
        diff = diff_graphs(validate_graph(DeploymentGraph("hello")), validate_graph(desired))

        plan = compile_activity_plan(diff)

        self.assertTrue(
            any(isinstance(activity.operation, StartNode) for activity in plan.activities)
        )
        self.assertTrue(plan.ready_for_execution)


if __name__ == "__main__":
    unittest.main()
