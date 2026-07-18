from __future__ import annotations

from unittest import TestCase, main

from control_plane_kit.planning import compile_activity_plan
from control_plane_kit.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    ValidationCode,
    diff_graphs,
    validate_graph,
)
from examples.generated_hello_graphs import (
    CorruptEnvironmentAssignment,
    DuplicateRequirementConnection,
    HelloGraphShape,
    MissingDatabaseConnection,
    MissingHttpConnection,
    generated_hello_graph,
)


class GeneratedHelloGraphTests(TestCase):
    def test_shape_has_closed_resource_bounds(self):
        self.assertEqual(HelloGraphShape(0, 0).application_count, 1)
        self.assertEqual(HelloGraphShape(2, 2).application_count, 7)
        self.assertEqual(HelloGraphShape(2, 2).database_count, 6)
        self.assertEqual(HelloGraphShape(2, 2).edge_count, 12)

        for values in ((-1, 0), (5, 1), (1, -1), (1, 5), (0, 1), (4, 4)):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    HelloGraphShape(*values)

    def test_valid_generation_is_deterministic_and_counted_by_shape(self):
        for shape in (
            HelloGraphShape(0, 0),
            HelloGraphShape(1, 3),
            HelloGraphShape(2, 1),
            HelloGraphShape(2, 2),
            HelloGraphShape(3, 2),
        ):
            with self.subTest(shape=shape):
                first = generated_hello_graph(shape)
                second = generated_hello_graph(shape)

                self.assertEqual(first.descriptor(), second.descriptor())
                self.assertEqual(len(first.nodes), shape.application_count + shape.database_count)
                self.assertEqual(len(first.edges), shape.edge_count)
                self.assertTrue(validate_graph(first).valid)

    def test_every_application_dependency_has_paired_edges(self):
        graph = generated_hello_graph(HelloGraphShape(3, 2))

        for node in graph.nodes.values():
            for requirement in node.sockets.requirements:
                matches = tuple(
                    edge
                    for edge in graph.edges.values()
                    if edge.consumer_role == node.node_id
                    and edge.requirement_socket == requirement.name
                )
                self.assertEqual(len(matches), 1)
                self.assertIs(matches[0].protocol, requirement.protocol)

    def test_generated_graph_round_trips_and_compiles_from_empty(self):
        graph = generated_hello_graph(HelloGraphShape(2, 2))
        encoded = DEFAULT_GRAPH_CODEC.encode(graph)
        decoded = DEFAULT_GRAPH_CODEC.decode(encoded)

        first = compile_activity_plan(
            diff_graphs(validate_graph(DeploymentGraph("empty")), validate_graph(graph))
        )
        second = compile_activity_plan(
            diff_graphs(validate_graph(DeploymentGraph("empty")), validate_graph(decoded))
        )

        self.assertEqual(DEFAULT_GRAPH_CODEC.encode(decoded), encoded)
        self.assertEqual(first, second)
        self.assertTrue(first.ready_for_execution)
        self.assertGreater(len(first.activities), len(graph.nodes))

    def test_invalidities_produce_exact_structured_findings(self):
        shape = HelloGraphShape(2, 1)
        cases = (
            (MissingHttpConnection(), {ValidationCode.MISSING_REQUIRED_CONNECTION}),
            (MissingDatabaseConnection(), {ValidationCode.MISSING_REQUIRED_CONNECTION}),
            (
                DuplicateRequirementConnection(),
                {ValidationCode.MULTIPLE_REQUIREMENT_CONNECTIONS},
            ),
            (
                CorruptEnvironmentAssignment(),
                {
                    ValidationCode.EDGE_ENV_BINDINGS,
                    ValidationCode.CONSUMER_ENVIRONMENT,
                },
            ),
        )

        for invalidity, expected in cases:
            with self.subTest(invalidity=type(invalidity).__name__):
                result = validate_graph(generated_hello_graph(shape, invalidity))

                self.assertFalse(result.valid)
                self.assertEqual({finding.code for finding in result.errors}, expected)


if __name__ == "__main__":
    main()
