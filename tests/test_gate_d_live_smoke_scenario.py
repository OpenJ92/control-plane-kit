from __future__ import annotations

import unittest

from control_plane_kit import compile_recipe
from control_plane_kit.core.planning import SwitchSocketConnection, compile_activity_plan
from control_plane_kit.core.topology import diff_graphs, validate_graph
from examples.gate_d_live_smoke import router_recipe
from control_plane_kit.docker_runtime import docker_container_name


class GateDLiveSmokeScenarioTests(unittest.TestCase):
    def test_initial_graph_is_valid_and_wires_both_targets(self) -> None:
        graph = compile_recipe(router_recipe("hello-blue"))
        result = validate_graph(graph)

        self.assertTrue(result.valid)
        self.assertEqual(
            set(graph.edges),
            {"router.target-blue", "router.target-green", "router.active"},
        )
        self.assertEqual(graph.edges["router.active"].provider_role, "hello-blue")

    def test_blue_to_green_compiles_to_one_typed_active_edge_switch(self) -> None:
        blue = validate_graph(compile_recipe(router_recipe("hello-blue")))
        green = validate_graph(compile_recipe(router_recipe("hello-green")))
        plan = compile_activity_plan(diff_graphs(blue, green))

        switches = [
            activity.operation
            for activity in plan.activities
            if isinstance(activity.operation, SwitchSocketConnection)
        ]
        self.assertEqual(len(switches), 1)
        self.assertEqual(switches[0].target.edge_id, "router.active")
        self.assertEqual(len(plan.activities), 1)

    def test_unprefixed_live_container_name_matches_compiled_docker_dns(self) -> None:
        graph = compile_recipe(router_recipe("hello-blue"))
        host = graph.node("hello-blue").endpoint("internal").url.split("//", 1)[1].split(":", 1)[0]

        self.assertEqual(
            docker_container_name("", "gate-d-runtime", "hello-blue"),
            host,
        )


if __name__ == "__main__":
    unittest.main()
