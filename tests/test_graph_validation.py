import unittest
from dataclasses import replace

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentRecipe,
    DockerRuntime,
    Endpoint,
    GraphValidationError,
    LiteralAddress,
    PlanOnlyImplementation,
    Protocol,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
    ValidationCode,
    ValidationSeverity,
    compile_recipe,
    validate_graph,
)


def graph_with_requirement(*, required: bool = True, connected: bool = True):
    provider = ApplicationBlock(
        BlockSpec("provider"),
        PlanOnlyImplementation("provider", {"internal": "http://provider"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    consumer = ApplicationBlock(
        BlockSpec("consumer"),
        PlanOnlyImplementation("consumer"),
        BlockSockets(
            requirements=(
                RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",), required),
            )
        ),
    )
    children: tuple[object, ...] = (provider, consumer)
    if connected:
        children += (SocketConnection("provider", "internal", "consumer", "upstream"),)
    return compile_recipe(DeploymentRecipe("validation", DockerRuntime(children=children)))


class GraphValidationTests(unittest.TestCase):
    def test_valid_graph_returns_original_typed_value_without_effects(self):
        graph = graph_with_requirement()

        result = validate_graph(graph)

        self.assertTrue(result.valid)
        self.assertIs(result.require_valid(), graph)
        self.assertEqual(result.findings, ())

    def test_required_and_optional_socket_findings_are_structured(self):
        required = validate_graph(graph_with_requirement(connected=False))
        optional = validate_graph(
            graph_with_requirement(required=False, connected=False)
        )

        self.assertFalse(required.valid)
        self.assertEqual(required.errors[0].code, ValidationCode.MISSING_REQUIRED_CONNECTION)
        self.assertEqual(required.errors[0].subject.descriptor()["kind"], "socket")
        self.assertTrue(optional.valid)
        self.assertEqual(optional.warnings[0].severity, ValidationSeverity.WARNING)

    def test_runtime_and_endpoint_invariants_fail_closed(self):
        graph = graph_with_requirement()
        provider = graph.node("provider")
        malformed = graph.update_node(
            replace(provider, runtime_id="missing", endpoints={})
        )

        result = validate_graph(malformed)

        self.assertFalse(result.valid)
        self.assertIn(ValidationCode.MISSING_RUNTIME, {finding.code for finding in result.errors})
        self.assertIn(
            ValidationCode.MISSING_PROVIDER_ENDPOINT,
            {finding.code for finding in result.errors},
        )
        self.assertIn(ValidationCode.INVALID_DESCRIPTOR, {finding.code for finding in result.errors})

    def test_undeclared_endpoint_is_a_structured_failure_not_a_codec_exception(self):
        graph = graph_with_requirement()
        provider = graph.node("provider")
        malformed = graph.update_node(
            replace(
                provider,
                endpoints={
                    **provider.endpoints,
                    "orphan": Endpoint(
                        LiteralAddress("http://orphan"),
                        Protocol.HTTP,
                    ),
                },
            )
        )

        result = validate_graph(malformed)

        self.assertFalse(result.valid)
        self.assertIn(
            ValidationCode.UNDECLARED_PROVIDER_ENDPOINT,
            {finding.code for finding in result.errors},
        )
        self.assertIn(
            ValidationCode.INVALID_DESCRIPTOR,
            {finding.code for finding in result.errors},
        )

    def test_unregistered_block_spec_variant_fails_closed(self):
        class UnsupportedBlockSpec(BlockSpec):
            pass

        graph = graph_with_requirement()
        provider = graph.node("provider")
        malformed = graph.update_node(
            replace(provider, block_spec=UnsupportedBlockSpec("provider"))
        )

        result = validate_graph(malformed)

        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0].code, ValidationCode.INVALID_DESCRIPTOR)

    def test_multiple_connections_to_one_requirement_are_rejected(self):
        graph = graph_with_requirement()
        edge = next(iter(graph.edges.values()))
        graph = graph.add_edge(replace(edge, edge_id="duplicate"))

        result = validate_graph(graph)

        self.assertIn(
            ValidationCode.MULTIPLE_REQUIREMENT_CONNECTIONS,
            {finding.code for finding in result.errors},
        )

    def test_duplicate_socket_names_are_rejected(self):
        graph = graph_with_requirement()
        provider = graph.node("provider")
        consumer = graph.node("consumer")
        provider_socket = provider.sockets.providers[0]
        requirement_socket = consumer.sockets.requirements[0]
        malformed = graph.update_node(
            replace(
                provider,
                sockets=BlockSockets(
                    providers=(provider_socket, provider_socket),
                ),
            )
        ).update_node(
            replace(
                consumer,
                sockets=BlockSockets(
                    requirements=(requirement_socket, requirement_socket),
                ),
            )
        )

        result = validate_graph(malformed)

        self.assertIn(
            ValidationCode.DUPLICATE_PROVIDER_SOCKET,
            {finding.code for finding in result.errors},
        )
        self.assertIn(
            ValidationCode.DUPLICATE_REQUIREMENT_SOCKET,
            {finding.code for finding in result.errors},
        )

    def test_edge_assignments_must_match_requirement_and_provider(self):
        graph = graph_with_requirement()
        edge_id, edge = next(iter(graph.edges.items()))
        wrong_key = replace(edge, env_assignments={"WRONG_URL": "http://provider"})
        wrong_value = replace(edge, env_assignments={"UPSTREAM_URL": "http://wrong"})

        key_result = validate_graph(replace(graph, edges={edge_id: wrong_key}))
        value_result = validate_graph(replace(graph, edges={edge_id: wrong_value}))

        self.assertIn(
            ValidationCode.EDGE_ENV_BINDINGS,
            {finding.code for finding in key_result.errors},
        )
        self.assertIn(
            ValidationCode.EDGE_ENV_BINDINGS,
            {finding.code for finding in value_result.errors},
        )

    def test_consumer_environment_must_retain_edge_assignments(self):
        graph = graph_with_requirement()
        consumer = graph.node("consumer")
        malformed = graph.update_node(replace(consumer, environment={}))

        result = validate_graph(malformed)

        self.assertIn(
            ValidationCode.CONSUMER_ENVIRONMENT,
            {finding.code for finding in result.errors},
        )

    def test_findings_are_deterministic_and_renderable(self):
        graph = graph_with_requirement(connected=False)

        first = validate_graph(graph).descriptor()
        second = validate_graph(graph).descriptor()

        self.assertEqual(first, second)
        self.assertEqual(first["findings"][0]["code"], "missing-required-connection")

    def test_require_valid_raises_with_complete_result(self):
        result = validate_graph(graph_with_requirement(connected=False))

        with self.assertRaises(GraphValidationError) as raised:
            result.require_valid()

        self.assertIs(raised.exception.result, result)


if __name__ == "__main__":
    unittest.main()
