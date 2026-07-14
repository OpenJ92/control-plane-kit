from unittest import TestCase, main

from control_plane_kit import DeploymentRecipe, DockerRuntime, SocketConnection, compile_recipe
from control_plane_kit.servers import (
    HttpActiveRouterServer,
    HttpRequest,
    HttpResponse,
    hello_server_block,
    http_active_router_block,
)
from control_plane_kit.types import Protocol


class HttpActiveRouterServerBlockTests(TestCase):
    def test_active_router_block_advertises_provider_and_active_requirement(self):
        block = http_active_router_block("router")

        self.assertEqual(block.block_id, "router")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.sockets.requirement("active").env_bindings, ("ACTIVE_TARGET_URL",))

    def test_active_router_block_compiles_socket_connection_to_environment(self):
        recipe = DeploymentRecipe(
            "router-demo",
            DockerRuntime(children=(
                hello_server_block("app"),
                http_active_router_block("router"),
                SocketConnection("app", "internal", "router", "active"),
            )),
        )

        graph = compile_recipe(recipe)

        self.assertEqual(graph.node("router").environment["ACTIVE_TARGET_URL"], graph.node("app").endpoint("internal").url)

    def test_active_router_forwards_to_active_target(self):
        def first(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("first")

        def second(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("second")

        router = HttpActiveRouterServer(targets={"first": first, "second": second}, active_target="first")

        self.assertEqual(router.handle(HttpRequest()).body, b"first")
        router.set_active_target("second")

        self.assertEqual(router.handle(HttpRequest()).body, b"second")
        self.assertEqual(router.runtime.get("active_target"), "second")

    def test_active_router_rejects_unknown_active_target(self):
        router = HttpActiveRouterServer(targets={"first": lambda request: HttpResponse.text("first")}, active_target="first")

        with self.assertRaises(KeyError):
            router.set_active_target("missing")


if __name__ == "__main__":
    main()
