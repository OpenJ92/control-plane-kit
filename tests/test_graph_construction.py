from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentGraph,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    GraphConstructionCode,
    GraphConstructionError,
    GraphIdentityKind,
    Protocol,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
)
from examples.app_with_postgres import recipe


class GraphConstructionTests(unittest.TestCase):
    def test_add_operations_reject_duplicates_without_erasing_first_values(self) -> None:
        compiled = compile_recipe(recipe())
        original_node = compiled.node("orders-api")
        original_edge = next(iter(compiled.edges.values()))
        original_runtime = compiled.runtimes["docker"]
        cases = (
            (
                GraphIdentityKind.NODE,
                lambda: compiled.add_node(
                    replace(original_node, kind="replacement-must-not-win")
                ),
            ),
            (
                GraphIdentityKind.EDGE,
                lambda: compiled.add_edge(
                    replace(original_edge, consumer_role="replacement-must-not-win")
                ),
            ),
            (
                GraphIdentityKind.RUNTIME,
                lambda: compiled.add_runtime(
                    replace(original_runtime, metadata={"replacement": "must-not-win"})
                ),
            ),
        )

        for identity_kind, insert in cases:
            with self.subTest(identity_kind=identity_kind), self.assertRaises(
                GraphConstructionError
            ) as caught:
                insert()
            self.assertIs(caught.exception.code, GraphConstructionCode.DUPLICATE_IDENTITY)
            self.assertIs(caught.exception.identity_kind, identity_kind)
            self.assertNotIn("replacement-must-not-win", str(caught.exception))

        self.assertIs(compiled.node("orders-api"), original_node)
        self.assertIs(compiled.edges[original_edge.edge_id], original_edge)
        self.assertIs(compiled.runtimes["docker"], original_runtime)

    def test_recipe_compilation_rejects_duplicate_block_identity(self) -> None:
        block = _application("api")
        duplicate = replace(block, implementation=DockerImageImplementation("other:latest"))

        with self.assertRaises(GraphConstructionError) as caught:
            compile_recipe(
                DeploymentRecipe(
                    "duplicate-node",
                    DockerRuntime(children=(block, duplicate)),
                )
            )

        self.assertIs(caught.exception.identity_kind, GraphIdentityKind.NODE)
        self.assertEqual(caught.exception.identity, "api")

    def test_nested_runtimes_cannot_reuse_node_or_runtime_identity(self) -> None:
        with self.assertRaises(GraphConstructionError) as node_error:
            compile_recipe(
                DeploymentRecipe(
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
            compile_recipe(
                DeploymentRecipe(
                    "nested-runtime",
                    DockerRuntime(
                        runtime_id="same",
                        children=(DockerRuntime(runtime_id="same"),),
                    ),
                )
            )
        self.assertIs(runtime_error.exception.identity_kind, GraphIdentityKind.RUNTIME)
        self.assertEqual(runtime_error.exception.identity, "same")

    def test_recipe_compilation_rejects_duplicate_edge_identity(self) -> None:
        provider = _application("provider", provides=True)
        consumer = ApplicationBlock(
            BlockSpec("consumer"),
            DockerImageImplementation("consumer:latest"),
            BlockSockets(
                requirements=(
                    RequirementSocket("first", Protocol.HTTP, ("FIRST_URL",)),
                    RequirementSocket("second", Protocol.HTTP, ("SECOND_URL",)),
                )
            ),
        )
        connections = (
            SocketConnection(
                "provider", "internal", "consumer", "first", edge_id="duplicate"
            ),
            SocketConnection(
                "provider", "internal", "consumer", "second", edge_id="duplicate"
            ),
        )

        with self.assertRaises(GraphConstructionError) as caught:
            compile_recipe(
                DeploymentRecipe(
                    "duplicate-edge",
                    DockerRuntime(children=(provider, consumer, *connections)),
                )
            )

        self.assertIs(caught.exception.identity_kind, GraphIdentityKind.EDGE)
        self.assertEqual(caught.exception.identity, "duplicate")

    def test_update_node_remains_explicit_and_requires_existing_identity(self) -> None:
        graph = compile_recipe(recipe())
        original = graph.node("orders-api")
        replacement = replace(original, kind="explicit-replacement")

        updated = graph.update_node(replacement)

        self.assertEqual(updated.node("orders-api").kind, "explicit-replacement")
        self.assertNotEqual(graph.node("orders-api").kind, "explicit-replacement")
        with self.assertRaisesRegex(KeyError, "cannot update missing node"):
            DeploymentGraph("empty").update_node(replacement)


def _application(
    block_id: str,
    *,
    provides: bool = False,
) -> ApplicationBlock:
    providers = (
        (ProviderSocket("internal", Protocol.HTTP),)
        if provides
        else ()
    )
    return ApplicationBlock(
        BlockSpec(block_id),
        DockerImageImplementation(
            f"{block_id}:latest",
            ports={"internal": 8080} if provides else {},
        ),
        BlockSockets(providers=providers),
    )


if __name__ == "__main__":
    unittest.main()
