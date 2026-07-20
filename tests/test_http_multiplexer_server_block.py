from unittest import TestCase, main

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    SocketConnection,
    compile_recipe,
    validate_graph,
)
from control_plane_kit.servers import (
    HttpMultiplexerServer,
    hello_server_block,
    http_multiplexer_block,
)
from control_plane_kit.products.servers.support.http_messages import HttpRequest, HttpResponse
from control_plane_kit.core.types import Protocol


class HttpMultiplexerServerBlockTests(TestCase):
    def test_multiplexer_block_advertises_provider_and_target_requirements(self):
        block = http_multiplexer_block("multiplexer")

        self.assertEqual(block.block_id, "multiplexer")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.sockets.requirement("primary").env_bindings, ("MULTIPLEXER_PRIMARY_URL",))
        self.assertEqual(block.sockets.requirement("observer-a").env_bindings, ("MULTIPLEXER_OBSERVER_A_URL",))
        self.assertEqual(block.sockets.requirement("observer-b").env_bindings, ("MULTIPLEXER_OBSERVER_B_URL",))
        self.assertFalse(block.sockets.requirement("observer-a").required)
        self.assertFalse(block.sockets.requirement("observer-b").required)

    def test_multiplexer_block_compiles_socket_connections_to_environment(self):
        recipe = DeploymentRecipe(
            "multiplexer-demo",
            DockerRuntime(children=(
                hello_server_block("app"),
                hello_server_block("observer"),
                http_multiplexer_block("multiplexer"),
                SocketConnection("app", "internal", "multiplexer", "primary"),
                SocketConnection("observer", "internal", "multiplexer", "observer-a"),
            )),
        )

        graph = compile_recipe(recipe)

        self.assertTrue(validate_graph(graph).valid)
        self.assertEqual(
            graph.node("multiplexer").non_secret_environment()["MULTIPLEXER_PRIMARY_URL"],
            graph.node("app").endpoint("internal").url,
        )
        self.assertEqual(
            graph.node("multiplexer").non_secret_environment()["MULTIPLEXER_OBSERVER_A_URL"],
            graph.node("observer").endpoint("internal").url,
        )

    def test_multiplexer_returns_primary_response_and_copies_to_observers(self):
        seen: list[HttpRequest] = []

        def primary(request: HttpRequest) -> HttpResponse:
            seen.append(request)
            return HttpResponse.text("primary")

        def observer(request: HttpRequest) -> HttpResponse:
            seen.append(request)
            return HttpResponse.text("observed")

        multiplexer = HttpMultiplexerServer(
            targets={"primary": primary},
            primary_target="primary",
            observers={"audit-log": observer},
        )
        request = HttpRequest(method="POST", path="/wax", body=b"payload")

        response = multiplexer.handle(request)

        self.assertEqual(response.body, b"primary")
        self.assertEqual(seen, [request, request])

    def test_multiplexer_observer_failure_is_recorded_but_primary_response_wins(self):
        def primary(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("primary")

        def observer(_request: HttpRequest) -> HttpResponse:
            raise RuntimeError("observer down")

        multiplexer = HttpMultiplexerServer(
            targets={"primary": primary},
            primary_target="primary",
            observers={"analytics": observer},
        )

        response = multiplexer.handle(HttpRequest())

        self.assertEqual(response.body, b"primary")
        self.assertEqual(multiplexer.observer_errors, ["analytics: observer down"])


if __name__ == "__main__":
    main()
