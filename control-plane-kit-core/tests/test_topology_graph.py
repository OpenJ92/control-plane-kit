from dataclasses import replace
import unittest

from control_plane_kit_core.topology import (
    DeploymentGraph,
    Edge,
    GraphConstructionCode,
    GraphConstructionError,
    GraphIdentityKind,
    Node,
    RuntimeRecord,
)


class DeploymentGraphConstructionTests(unittest.TestCase):
    def test_add_operations_reject_duplicates_without_erasing_first_values(self) -> None:
        original_node = Node("api", kind="application")
        original_edge = Edge(
            edge_id="database-edge",
            provider_role="database",
            provider_socket="postgres",
            consumer_role="api",
            requirement_socket="database",
        )
        original_runtime = RuntimeRecord("docker", kind="docker")
        graph = (
            DeploymentGraph("orders")
            .add_node(original_node)
            .add_edge(original_edge)
            .add_runtime(original_runtime)
        )

        cases = (
            (
                GraphIdentityKind.NODE,
                lambda: graph.add_node(
                    replace(original_node, kind="replacement-must-not-win")
                ),
            ),
            (
                GraphIdentityKind.EDGE,
                lambda: graph.add_edge(
                    replace(original_edge, consumer_role="replacement-must-not-win")
                ),
            ),
            (
                GraphIdentityKind.RUNTIME,
                lambda: graph.add_runtime(
                    replace(original_runtime, metadata={"replacement": "must-not-win"})
                ),
            ),
        )

        for identity_kind, insert in cases:
            with self.subTest(identity_kind=identity_kind):
                with self.assertRaises(GraphConstructionError) as caught:
                    insert()
                self.assertIs(
                    caught.exception.code,
                    GraphConstructionCode.DUPLICATE_IDENTITY,
                )
                self.assertIs(caught.exception.identity_kind, identity_kind)
                self.assertNotIn("replacement-must-not-win", str(caught.exception))

        self.assertIs(graph.node("api"), original_node)
        self.assertIs(graph.edges[original_edge.edge_id], original_edge)
        self.assertIs(graph.runtimes["docker"], original_runtime)


if __name__ == "__main__":
    unittest.main()

