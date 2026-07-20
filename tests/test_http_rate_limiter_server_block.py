from unittest import TestCase, main

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.servers import (
    HttpRateLimiterServer,
    HttpRequest,
    HttpResponse,
    hello_server_block,
    http_rate_limiter_block,
)
from control_plane_kit.core.types import Protocol


class HttpRateLimiterServerBlockTests(TestCase):
    def test_rate_limiter_block_advertises_provider_and_target_requirement(self):
        block = http_rate_limiter_block("limiter")

        self.assertEqual(block.block_id, "limiter")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.sockets.requirement("target").env_bindings, ("RATE_LIMIT_TARGET_URL",))

    def test_rate_limiter_block_compiles_socket_connection_to_environment(self):
        recipe = DeploymentRecipe(
            "rate-limiter-demo",
            DockerRuntime(children=(
                hello_server_block("app"),
                http_rate_limiter_block("limiter"),
                SocketConnection("app", "internal", "limiter", "target"),
            )),
        )

        graph = compile_recipe(recipe)

        self.assertEqual(
            graph.node("limiter").non_secret_environment()["RATE_LIMIT_TARGET_URL"],
            graph.node("app").endpoint("internal").url,
        )

    def test_rate_limiter_allows_until_quota_is_exhausted_then_returns_429(self):
        seen: list[HttpRequest] = []

        def target(request: HttpRequest) -> HttpResponse:
            seen.append(request)
            return HttpResponse.text("allowed")

        limiter = HttpRateLimiterServer(targets={"app": target}, target="app", limit=2)

        self.assertEqual(limiter.handle(HttpRequest()).body, b"allowed")
        self.assertEqual(limiter.handle(HttpRequest()).body, b"allowed")
        rejected = limiter.handle(HttpRequest())

        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.body, b"Too Many Requests")
        self.assertEqual(len(seen), 2)
        self.assertEqual(limiter.runtime.get("remaining"), 0)

    def test_rate_limiter_reset_reloads_quota(self):
        limiter = HttpRateLimiterServer(
            targets={"app": lambda request: HttpResponse.text("allowed")},
            target="app",
            limit=1,
        )

        self.assertEqual(limiter.handle(HttpRequest()).status_code, 200)
        self.assertEqual(limiter.handle(HttpRequest()).status_code, 429)
        limiter.reset(limit=2)

        self.assertEqual(limiter.runtime.get("remaining"), 2)
        self.assertEqual(limiter.handle(HttpRequest()).status_code, 200)


if __name__ == "__main__":
    main()
