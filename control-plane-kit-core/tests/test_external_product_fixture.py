from __future__ import annotations

from pathlib import Path
import unittest

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.products import (
    ProductCatalog,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_catalog_product,
)
from control_plane_kit_core.planning import StartNode, compile_activity_plan
from control_plane_kit_core.topology import (
    DeploymentGraph,
    GraphDescriptorCodec,
    compile_topology,
    diff_graphs,
    validate_graph,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "external-products"
    / "proxy"
    / "product.cpk.json"
)


class ExternalProductFixtureTests(unittest.TestCase):
    def test_descriptor_fixture_traverses_the_pure_pipeline_without_importing_product_code(self) -> None:
        document = ProductDescriptorCodec().decode_document(FIXTURE.read_bytes())
        catalog = ProductCatalog.empty().add(document)
        block = instantiate_catalog_product(
            catalog,
            ProductIdentity("fixture-products", "proxy", 1),
            role_id="proxy-blue",
            configuration=ProductInstanceConfiguration.from_contract(
                document.product.runtime_contract
            ),
        )

        graph = compile_topology(
            DeploymentTopology("fixture", DockerRuntime(children=(block,)))
        )
        restored = GraphDescriptorCodec().decode(GraphDescriptorCodec().encode(graph))
        diff = diff_graphs(
            validate_graph(DeploymentGraph("fixture")),
            validate_graph(restored),
        )
        plan = compile_activity_plan(diff)

        node = restored.node("proxy-blue")
        self.assertEqual(node.metadata["product_identity"], "fixture-products/proxy/1")
        self.assertEqual(
            node.metadata["product_descriptor_digest"],
            document.content_digest,
        )
        self.assertEqual(
            node.metadata["oci_image"],
            "ghcr.io/openj92/control-plane-kit-fixtures/proxy@"
            "sha256:3333333333333333333333333333333333333333333333333333333333333333",
        )
        self.assertTrue(
            any(isinstance(activity.operation, StartNode) for activity in plan.activities)
        )
        self.assertTrue(plan.ready_for_execution)


if __name__ == "__main__":
    unittest.main()
