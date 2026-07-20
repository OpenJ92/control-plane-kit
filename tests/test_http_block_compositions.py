from unittest import TestCase, main

from examples.http_block_compositions import (
    active_router_recipe,
    compile_all_http_block_graphs,
    multiplexer_recipe,
    proxy_recipe,
    rate_limiter_recipe,
    weighted_balancer_recipe,
)
from control_plane_kit import compile_recipe


class HttpBlockCompositionTests(TestCase):
    def test_all_http_block_composition_examples_compile(self):
        graphs = compile_all_http_block_graphs()

        self.assertEqual(len(graphs), 5)
        for graph in graphs:
            self.assertEqual(len(graph.runtimes), 1)

    def test_proxy_composition_binds_target_environment(self):
        graph = compile_recipe(proxy_recipe())

        self.assertEqual(graph.node("proxy").non_secret_environment()["PROXY_TARGET_URL"], graph.node("app").endpoint("internal").url)

    def test_active_router_composition_binds_selected_target(self):
        graph = compile_recipe(active_router_recipe("app-v2"))

        self.assertEqual(
            graph.node("router").non_secret_environment()["ACTIVE_TARGET_URL"],
            graph.node("app-v2").endpoint("internal").url,
        )

    def test_weighted_balancer_composition_binds_two_targets(self):
        graph = compile_recipe(weighted_balancer_recipe())

        self.assertEqual(
            graph.node("balancer").non_secret_environment()["BALANCER_TARGET_A_URL"],
            graph.node("app-a").endpoint("internal").url,
        )
        self.assertEqual(
            graph.node("balancer").non_secret_environment()["BALANCER_TARGET_B_URL"],
            graph.node("app-b").endpoint("internal").url,
        )

    def test_multiplexer_composition_binds_primary_and_observer(self):
        graph = compile_recipe(multiplexer_recipe())

        self.assertEqual(
            graph.node("multiplexer").non_secret_environment()["MULTIPLEXER_PRIMARY_URL"],
            graph.node("primary").endpoint("internal").url,
        )
        self.assertEqual(
            graph.node("multiplexer").non_secret_environment()["MULTIPLEXER_OBSERVER_A_URL"],
            graph.node("observer").endpoint("internal").url,
        )

    def test_rate_limiter_composition_binds_target(self):
        graph = compile_recipe(rate_limiter_recipe())

        self.assertEqual(
            graph.node("limiter").non_secret_environment()["RATE_LIMIT_TARGET_URL"],
            graph.node("app").endpoint("internal").url,
        )


if __name__ == "__main__":
    main()
