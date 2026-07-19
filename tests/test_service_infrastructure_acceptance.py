from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    PackageServerProduct,
    PackageServerSpec,
    Protocol,
    SocketConnection,
    StartNode,
    ValidationCode,
    WaitForHealthy,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from examples.service_infrastructure import service_infrastructure_recipe


class ServiceInfrastructureAcceptanceTests(unittest.TestCase):
    def test_recipe_preserves_exact_products_protocols_and_secret_references(self) -> None:
        graph = compile_recipe(service_infrastructure_recipe())
        result = validate_graph(graph)

        self.assertTrue(result.valid, result.descriptor())
        self.assertEqual(
            {
                node.block_spec.product
                for node in graph.nodes.values()
                if isinstance(node.block_spec, PackageServerSpec)
            },
            {
                PackageServerProduct.SERVICE_DISCOVERY,
                PackageServerProduct.OPENTELEMETRY_COLLECTOR,
                PackageServerProduct.WEBHOOK_DELIVERY,
            },
        )
        self.assertEqual(
            {
                edge.protocol
                for edge in graph.edges.values()
            },
            {Protocol.POSTGRES},
        )
        descriptor = DEFAULT_GRAPH_CODEC.encode(graph)
        self.assertEqual(
            DEFAULT_GRAPH_CODEC.encode(DEFAULT_GRAPH_CODEC.decode(descriptor)),
            descriptor,
        )
        encoded = str(descriptor)
        self.assertIn("secret://service-acceptance/discovery-identity", encoded)
        self.assertIn("secret://service-acceptance/webhook-identity", encoded)
        self.assertIn("secret://service-acceptance/webhook-signing", encoded)

    def test_application_owned_database_requirements_are_independently_wired(self) -> None:
        graph = compile_recipe(service_infrastructure_recipe())
        assignments = {
            edge.consumer_role: (
                edge.provider_role,
                edge.requirement_socket,
                tuple(sorted(edge.env_assignments)),
            )
            for edge in graph.edges.values()
        }

        self.assertEqual(
            assignments,
            {
                "service-discovery": (
                    "discovery-postgres",
                    "database",
                    ("DISCOVERY_DATABASE_URL",),
                ),
                "webhook-delivery": (
                    "webhook-postgres",
                    "database",
                    ("WEBHOOK_DATABASE_URL",),
                ),
            },
        )
        webhook = graph.node("webhook-delivery")
        self.assertEqual(webhook.sockets.requirement_names(), ("database",))
        self.assertNotIn(
            "webhook-receiver",
            {edge.provider_role for edge in graph.edges.values()},
        )

    def test_plan_orders_provider_health_before_each_database_consumer(self) -> None:
        desired = validate_graph(compile_recipe(service_infrastructure_recipe()))
        plan = compile_activity_plan(
            diff_graphs(validate_graph(DeploymentGraph("empty")), desired)
        )
        starts = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, StartNode)
        }
        health = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForHealthy)
        }

        self.assertTrue(plan.ready_for_execution)
        for provider, consumer in (
            ("discovery-postgres", "service-discovery"),
            ("webhook-postgres", "webhook-delivery"),
        ):
            self.assertIn(
                health[provider].activity_id,
                {
                    dependency.predecessor
                    for dependency in starts[consumer].dependencies
                },
            )

    def test_missing_and_incompatible_database_edges_fail_at_pure_boundaries(self) -> None:
        recipe = service_infrastructure_recipe()
        root = recipe.root
        self.assertIsInstance(root, DockerRuntime)
        without_webhook_database = replace(
            root,
            children=tuple(
                child
                for child in root.children
                if not (
                    isinstance(child, SocketConnection)
                    and child.consumer_role == "webhook-delivery"
                )
            ),
        )
        invalid = validate_graph(
            compile_recipe(
                DeploymentRecipe(recipe.name, without_webhook_database)
            )
        )
        self.assertIn(
            ValidationCode.MISSING_REQUIRED_CONNECTION,
            {finding.code for finding in invalid.errors},
        )

        incompatible = replace(
            root,
            children=tuple(
                child
                for child in root.children
                if not (
                    isinstance(child, SocketConnection)
                    and child.consumer_role == "webhook-delivery"
                )
            )
            + (
                SocketConnection(
                    "opentelemetry-collector",
                    "otlp-http",
                    "webhook-delivery",
                    "database",
                ),
            ),
        )
        with self.assertRaisesRegex(
            ValueError,
            "expects postgres, connection provides otlp-http",
        ):
            compile_recipe(DeploymentRecipe(recipe.name, incompatible))


if __name__ == "__main__":
    unittest.main()
