from unittest import TestCase, main

from control_plane_kit import (
    compile_recipe,
)
from control_plane_kit.runtimes import (
    CleanupPolicy,
    DryRunRuntime,
    RuntimeState,
)
from examples.app_with_postgres import recipe as app_recipe


class RuntimeStateTests(TestCase):
    def test_dry_run_runtime_returns_interpreted_node_state(self):
        graph = compile_recipe(app_recipe())
        state = DryRunRuntime().up(graph, "docker")

        api = state.node("orders-api")
        self.assertTrue(api.healthy)
        self.assertEqual(api.environment["DATABASE_URL"], graph.node("postgres").endpoint("internal").url)
        self.assertEqual(state.metadata["interpreter"], "dry-run")

    def test_runtime_state_descriptor_is_json_friendly(self):
        state = RuntimeState("docker", compile_recipe(app_recipe()).runtimes["docker"].kind)

        self.assertEqual(
            state.descriptor(),
            {
                "runtime_id": "docker",
                "kind": "docker",
                "cleanup_policy": CleanupPolicy.REMOVE_ON_STOP.value,
                "nodes": {},
                "metadata": {},
            },
        )

    def test_dry_run_plan_stop_is_idempotent_shape(self):
        graph = compile_recipe(app_recipe())
        runtime = DryRunRuntime(cleanup_policy=CleanupPolicy.PRESERVE_ON_STOP)
        state = runtime.up(graph, "docker")
        plan = runtime.plan_stop(state)
        stopped = runtime.down(state)

        self.assertEqual(plan.action, "stop")
        self.assertEqual(stopped.nodes, {})
        self.assertTrue(stopped.metadata["stopped"])


if __name__ == "__main__":
    main()
