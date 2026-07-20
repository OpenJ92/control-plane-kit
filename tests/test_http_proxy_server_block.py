from unittest import TestCase, main

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.servers import HttpProxyServer, HttpRequest, HttpResponse, hello_server_block, http_proxy_block
from control_plane_kit.core.types import Protocol


class HttpProxyServerBlockTests(TestCase):
    def test_proxy_block_advertises_provider_and_target_requirement(self):
        block = http_proxy_block("proxy")

        self.assertEqual(block.block_id, "proxy")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.sockets.requirement("target").env_bindings, ("PROXY_TARGET_URL",))

    def test_proxy_block_compiles_socket_connection_to_environment(self):
        recipe = DeploymentRecipe(
            "proxy-demo",
            DockerRuntime(children=(
                hello_server_block("app"),
                http_proxy_block("proxy"),
                SocketConnection("app", "internal", "proxy", "target"),
            )),
        )

        graph = compile_recipe(recipe)

        self.assertEqual(graph.node("proxy").non_secret_environment()["PROXY_TARGET_URL"], graph.node("app").endpoint("internal").url)

    def test_proxy_forwards_request_to_active_target(self):
        seen: list[HttpRequest] = []

        def target(request: HttpRequest) -> HttpResponse:
            seen.append(request)
            return HttpResponse.text("proxied")

        proxy = HttpProxyServer(targets={"app": target}, target="app")
        request = HttpRequest(
            method="POST",
            path="/orders",
            query="page=1",
            headers={"x-trace": "abc"},
            body=b"payload",
        )

        response = proxy.handle(request)

        self.assertEqual(response.body, b"proxied")
        self.assertEqual(seen, [request])

    def test_proxy_runtime_target_change_affects_later_requests(self):
        def first(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("first")

        def second(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("second")

        proxy = HttpProxyServer(targets={"first": first, "second": second}, target="first")

        self.assertEqual(proxy.handle(HttpRequest()).body, b"first")
        proxy.set_target("second")

        self.assertEqual(proxy.handle(HttpRequest()).body, b"second")
        self.assertEqual(proxy.runtime.get("target"), "second")


if __name__ == "__main__":
    main()
