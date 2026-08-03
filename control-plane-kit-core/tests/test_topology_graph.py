from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Edge,
    GraphConstructionCode,
    GraphConstructionError,
    GraphIdentityKind,
    Node,
    RuntimeRecord,
    compile_topology,
)
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind, SocketBinding


class PureImplementation:
    def __init__(self, kind: str, endpoints: dict[str, str] | None = None) -> None:
        self.kind = kind
        self.endpoints = endpoints or {}

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: object) -> object:
        class Materialized:
            kind = self.kind
            endpoints = {
                name: Endpoint(LiteralAddress(address), sockets.provider(name).protocol)
                for name, address in self.endpoints.items()
            }
            public_environment = ()
            metadata = {}
            lifecycle = None
            configuration_artifacts = ()
            secret_deliveries = ()

        return Materialized()


class DeploymentGraphConstructionTests(unittest.TestCase):
    def test_add_operations_reject_duplicates_without_erasing_first_values(self) -> None:
        original_node = Node(
            node_id="api",
            block_family=BlockFamily.APPLICATION,
            block_spec=BlockSpec("api"),
            kind="application",
            runtime_id="docker",
            sockets=BlockSockets(),
        )
        original_edge = Edge(
            edge_id="database-edge",
            provider_role="database",
            provider_socket="postgres",
            consumer_role="api",
            requirement_socket="database",
            protocol=Protocol.POSTGRES,
            binding=SocketBinding.ENVIRONMENT,
            env_assignments={"DATABASE_URL": "postgres://database/app"},
        )
        original_runtime = RuntimeRecord("docker", kind=RuntimeKind.DOCKER)
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

    def test_topology_compilation_rejects_duplicate_block_identity(self) -> None:
        block = _application("api")
        duplicate = _application("api")

        with self.assertRaises(GraphConstructionError) as caught:
            compile_topology(
                DeploymentTopology(
                    "duplicate-node",
                    DockerRuntime(children=(block, duplicate)),
                )
            )

        self.assertIs(caught.exception.identity_kind, GraphIdentityKind.NODE)
        self.assertEqual(caught.exception.identity, "api")

    def test_nested_runtimes_cannot_reuse_node_or_runtime_identity(self) -> None:
        with self.assertRaises(GraphConstructionError) as node_error:
            compile_topology(
                DeploymentTopology(
                    "nested-node",
                    DockerRuntime(
                        runtime_id="outer",
                        children=(
                            _application("api"),
                            DockerRuntime(
                                runtime_id="inner",
                                children=(_application("api"),),
                            ),
                        ),
                    ),
                )
            )
        self.assertIs(node_error.exception.identity_kind, GraphIdentityKind.NODE)

        with self.assertRaises(GraphConstructionError) as runtime_error:
            compile_topology(
                DeploymentTopology(
                    "nested-runtime",
                    DockerRuntime(
                        runtime_id="same",
                        children=(DockerRuntime(runtime_id="same"),),
                    ),
                )
            )
        self.assertIs(runtime_error.exception.identity_kind, GraphIdentityKind.RUNTIME)
        self.assertEqual(runtime_error.exception.identity, "same")

    def test_topology_compilation_rejects_duplicate_edge_identity(self) -> None:
        provider = _application("provider", provides=True)
        consumer = ApplicationBlock(
            BlockSpec("consumer"),
            PureImplementation("application"),
            BlockSockets(
                requirements=(
                    RequirementSocket("first", Protocol.HTTP, ("FIRST_URL",)),
                    RequirementSocket("second", Protocol.HTTP, ("SECOND_URL",)),
                )
            ),
        )

        with self.assertRaises(GraphConstructionError) as caught:
            compile_topology(
                DeploymentTopology(
                    "duplicate-edge",
                    DockerRuntime(
                        children=(
                            provider,
                            consumer,
                            SocketConnection(
                                "provider",
                                "internal",
                                "consumer",
                                "first",
                                edge_id="duplicate",
                            ),
                            SocketConnection(
                                "provider",
                                "internal",
                                "consumer",
                                "second",
                                edge_id="duplicate",
                            ),
                        )
                    ),
                )
            )

        self.assertIs(caught.exception.identity_kind, GraphIdentityKind.EDGE)
        self.assertEqual(caught.exception.identity, "duplicate")

    def test_update_node_remains_explicit_and_requires_existing_identity(self) -> None:
        original = Node(
            node_id="api",
            block_family=BlockFamily.APPLICATION,
            block_spec=BlockSpec("api"),
            kind="application",
            runtime_id="docker",
            sockets=BlockSockets(),
        )
        replacement = replace(original, kind="explicit-replacement")
        graph = DeploymentGraph("orders").add_node(original)

        updated = graph.update_node(replacement)

        self.assertEqual(updated.node("api").kind, "explicit-replacement")
        self.assertEqual(graph.node("api").kind, "application")
        with self.assertRaisesRegex(KeyError, "cannot update missing node"):
            DeploymentGraph("empty").update_node(replacement)


def _application(block_id: str, *, provides: bool = False) -> ApplicationBlock:
    return ApplicationBlock(
        BlockSpec(block_id),
        PureImplementation(
            "application",
            {"internal": f"http://{block_id}"} if provides else {},
        ),
        BlockSockets(
            providers=(ProviderSocket("internal", Protocol.HTTP),) if provides else (),
        ),
    )


if __name__ == "__main__":
    unittest.main()
