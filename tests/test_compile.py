from unittest import TestCase, main

from control_plane_kit import Protocol, SocketConnection, compile_recipe
from examples.app_with_postgres import recipe as app_recipe
from examples.split_service import recipe as split_recipe


class CompileTests(TestCase):
    def test_app_with_postgres_injects_database_url(self):
        graph = compile_recipe(app_recipe())

        api = graph.node("orders-api")
        postgres = graph.node("postgres")
        self.assertEqual(
            api.environment["DATABASE_URL"],
            postgres.endpoint("internal").url,
        )
        edge = graph.edges["postgres.internal-to-orders-api.DATABASE_URL"]
        self.assertEqual(edge.protocol, Protocol.POSTGRES)

    def test_graph_descriptor_uses_provider_requirement_vocabulary(self):
        descriptor = compile_recipe(app_recipe()).descriptor()

        api = descriptor["nodes"]["orders-api"]
        postgres = descriptor["nodes"]["postgres"]
        edge = descriptor["edges"]["postgres.internal-to-orders-api.DATABASE_URL"]

        self.assertIn("requirements", api)
        self.assertIn("providers", postgres)
        self.assertNotIn("inputs", api)
        self.assertNotIn("outputs", postgres)
        self.assertEqual(edge["provider"], {"role": "postgres", "socket": "internal"})
        self.assertEqual(edge["consumer"], {"role": "orders-api", "requirement": "DATABASE_URL"})

    def test_split_service_wires_http_and_postgres(self):
        graph = compile_recipe(split_recipe())

        api = graph.node("api")
        inventory = graph.node("inventory-service")
        postgres = graph.node("postgres")
        self.assertEqual(api.environment["INVENTORY_SERVICE_URL"], inventory.endpoint("internal").url)
        self.assertEqual(inventory.environment["DATABASE_URL"], postgres.endpoint("internal").url)

    def test_protocol_mismatch_fails(self):
        source = app_recipe()
        bad = SocketConnection("postgres", "internal", "orders-api", "DATABASE_URL", protocol=Protocol.HTTP)
        broken = type(source)(source.name, type(source.root)(children=(*source.root.children[:-1], bad)))

        with self.assertRaises(ValueError):
            compile_recipe(broken)


if __name__ == "__main__":
    main()
