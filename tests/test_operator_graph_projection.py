from unittest import TestCase, main

from control_plane_kit import compile_recipe
from control_plane_kit.projections import project_operator_graph
from examples.app_with_postgres import recipe as app_recipe
from examples.http_block_compositions import active_router_recipe


class OperatorGraphProjectionTests(TestCase):
    def test_operator_graph_projects_runtime_nodes_sockets_and_edges(self):
        projection = project_operator_graph(compile_recipe(app_recipe()))
        descriptor = projection.descriptor()

        self.assertEqual(descriptor["name"], "app-with-postgres")
        self.assertEqual(
            descriptor["runtimes"][0],
            {
                "runtime_id": "docker",
                "kind": "docker",
                "children": ["orders-api", "postgres"],
                "metadata": {"network_name": "control-plane-kit-network"},
            },
        )
        nodes = {node["node_id"]: node for node in descriptor["nodes"]}
        self.assertEqual(nodes["orders-api"]["display_name"], "Orders API")
        self.assertEqual(
            nodes["orders-api"]["requirements"],
            [
                {
                    "name": "DATABASE_URL",
                    "direction": "requirement",
                    "protocol": "postgres",
                    "required": True,
                    "env_bindings": ["DATABASE_URL"],
                }
            ],
        )
        self.assertEqual(
            nodes["postgres"]["providers"],
            [{"name": "internal", "direction": "provider", "protocol": "postgres"}],
        )

        self.assertEqual(
            descriptor["edges"],
            [
                {
                    "edge_id": "postgres.internal-to-orders-api.DATABASE_URL",
                    "provider": {"node_id": "postgres", "socket": "internal"},
                    "consumer": {"node_id": "orders-api", "socket": "DATABASE_URL"},
                    "protocol": "postgres",
                    "env_bindings": ["DATABASE_URL"],
                }
            ],
        )

    def test_operator_graph_redacts_addresses_by_default(self):
        descriptor = project_operator_graph(compile_recipe(app_recipe())).descriptor()
        nodes = {node["node_id"]: node for node in descriptor["nodes"]}

        postgres_endpoint = nodes["postgres"]["endpoints"][0]
        self.assertTrue(postgres_endpoint["address_available"])
        self.assertNotIn("url", postgres_endpoint)
        self.assertNotIn("env_assignments", descriptor["edges"][0])

    def test_operator_graph_can_include_addresses_explicitly(self):
        graph = compile_recipe(app_recipe())
        descriptor = project_operator_graph(graph, include_addresses=True).descriptor()
        nodes = {node["node_id"]: node for node in descriptor["nodes"]}

        self.assertEqual(
            nodes["postgres"]["endpoints"][0]["url"],
            graph.node("postgres").endpoint("internal").url,
        )
        self.assertEqual(
            descriptor["edges"][0]["env_assignments"],
            {"DATABASE_URL": graph.node("postgres").endpoint("internal").url},
        )

    def test_operator_graph_exposes_capability_hints(self):
        descriptor = project_operator_graph(compile_recipe(active_router_recipe())).descriptor()
        router = {node["node_id"]: node for node in descriptor["nodes"]}["router"]

        capability_names = {capability["name"] for capability in router["capabilities"]}
        self.assertIn("health-checkable", capability_names)
        self.assertIn("target-mutable", capability_names)


if __name__ == "__main__":
    main()
