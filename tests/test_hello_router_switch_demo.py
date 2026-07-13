from unittest import TestCase, main

from control_plane_kit import compile_recipe
from examples.hello_router_switch_demo import EARTH_ID, MARS_ID, ROUTER_ID, recipe


class HelloRouterSwitchDemoTests(TestCase):
    def test_demo_recipe_compiles_runtime_target_edges(self):
        graph = compile_recipe(recipe())

        self.assertEqual(graph.node(EARTH_ID).metadata["hello_world"], "earth")
        self.assertEqual(graph.node(MARS_ID).metadata["hello_world"], "mars")
        self.assertEqual(graph.node(ROUTER_ID).requirement_socket("targets").name, "targets")
        earth_edge = graph.edges[f"{EARTH_ID}.internal-to-{ROUTER_ID}.targets"]
        mars_edge = graph.edges[f"{MARS_ID}.internal-to-{ROUTER_ID}.targets"]
        self.assertEqual(earth_edge.control_route_set, "targets")
        self.assertEqual(mars_edge.control_route_set, "targets")
        self.assertIn(EARTH_ID, earth_edge.runtime_assignments)
        self.assertIn(MARS_ID, mars_edge.runtime_assignments)


if __name__ == "__main__":
    main()
