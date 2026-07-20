from unittest import TestCase, main

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.servers import (
    HttpRequest,
    HttpResponse,
    HttpWeightedLoadBalancerServer,
    hello_server_block,
    http_weighted_load_balancer_block,
)
from control_plane_kit.core.types import Protocol


class HttpWeightedLoadBalancerServerBlockTests(TestCase):
    def test_weighted_balancer_block_advertises_provider_and_target_requirements(self):
        block = http_weighted_load_balancer_block("balancer")

        self.assertEqual(block.block_id, "balancer")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.sockets.requirement("target-a").env_bindings, ("BALANCER_TARGET_A_URL",))
        self.assertEqual(block.sockets.requirement("target-b").env_bindings, ("BALANCER_TARGET_B_URL",))

    def test_weighted_balancer_block_compiles_socket_connections_to_environment(self):
        recipe = DeploymentRecipe(
            "weighted-balancer-demo",
            DockerRuntime(children=(
                hello_server_block("app-a"),
                hello_server_block("app-b"),
                http_weighted_load_balancer_block("balancer"),
                SocketConnection("app-a", "internal", "balancer", "target-a"),
                SocketConnection("app-b", "internal", "balancer", "target-b"),
            )),
        )

        graph = compile_recipe(recipe)

        self.assertEqual(
            graph.node("balancer").non_secret_environment()["BALANCER_TARGET_A_URL"],
            graph.node("app-a").endpoint("internal").url,
        )
        self.assertEqual(
            graph.node("balancer").non_secret_environment()["BALANCER_TARGET_B_URL"],
            graph.node("app-b").endpoint("internal").url,
        )

    def test_weighted_balancer_routes_by_deterministic_weighted_cycle(self):
        seen: list[str] = []

        def first(_request: HttpRequest) -> HttpResponse:
            seen.append("first")
            return HttpResponse.text("first")

        def second(_request: HttpRequest) -> HttpResponse:
            seen.append("second")
            return HttpResponse.text("second")

        balancer = HttpWeightedLoadBalancerServer(
            targets={"first": first, "second": second},
            weights={"first": 2, "second": 1},
        )

        responses = [balancer.handle(HttpRequest()).body for _ in range(4)]

        self.assertEqual(responses, [b"first", b"first", b"second", b"first"])
        self.assertEqual(seen, ["first", "first", "second", "first"])

    def test_weighted_balancer_rejects_no_positive_weights(self):
        with self.assertRaises(ValueError):
            HttpWeightedLoadBalancerServer(
                targets={"first": lambda request: HttpResponse.text("first")},
                weights={"first": 0},
            )


if __name__ == "__main__":
    main()
