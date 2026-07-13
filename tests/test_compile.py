from unittest import TestCase, main

from control_plane_kit import (
    AppSpec,
    ApplicationBlock,
    DockerImageImplementation,
    DeploymentRecipe,
    DockerRuntime,
    Protocol,
    ProviderSocket,
    RoleSockets,
    SocketConnection,
    compile_recipe,
    http_active_router_block,
)
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

    def test_split_service_wires_http_and_postgres(self):
        graph = compile_recipe(split_recipe())

        api = graph.node("api")
        inventory = graph.node("inventory-service")
        postgres = graph.node("postgres")
        self.assertEqual(api.environment["INVENTORY_SERVICE_URL"], inventory.endpoint("internal").url)
        self.assertEqual(inventory.environment["DATABASE_URL"], postgres.endpoint("internal").url)

    def test_runtime_requirement_connection_records_control_route_assignment(self):
        hello = ApplicationBlock(
            AppSpec("hello-v2"),
            DockerImageImplementation("hello:v2", ports={"internal": 8000}),
            sockets=RoleSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
        )
        graph = compile_recipe(
            DeploymentRecipe(
                "router-target-demo",
                DockerRuntime(children=(
                    hello,
                    http_active_router_block("api-router"),
                    SocketConnection("hello-v2", "internal", "api-router", "targets"),
                )),
            )
        )

        router = graph.node("api-router")
        self.assertNotIn("hello-v2", router.environment)
        edge = graph.edges["hello-v2.internal-to-api-router.targets"]
        self.assertEqual(edge.runtime_assignments, {"hello-v2": graph.node("hello-v2").endpoint("internal").url})
        self.assertEqual(edge.control_route_set, "targets")

    def test_protocol_mismatch_fails(self):
        source = app_recipe()
        bad = SocketConnection("postgres", "internal", "orders-api", "DATABASE_URL", protocol=Protocol.HTTP)
        broken = type(source)(source.name, type(source.root)(children=(*source.root.children[:-1], bad)))

        with self.assertRaises(ValueError):
            compile_recipe(broken)


if __name__ == "__main__":
    main()
