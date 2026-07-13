from unittest import TestCase, main

from examples.hello_router_graph_demo import HelloRouterGraphRunner
from examples.hello_router_switch_demo import EARTH_ID, MARS_ID, ROUTER_ID


class HelloRouterGraphDemoTests(TestCase):
    def test_graph_demo_stops_at_compiled_graph(self):
        runner = HelloRouterGraphRunner()
        summary = runner.summary()

        self.assertEqual(summary["name"], "hello-router-switch-demo")
        self.assertEqual(summary["runtime_ids"], ["docker"])
        self.assertEqual(summary["node_ids"], [EARTH_ID, MARS_ID, ROUTER_ID])
        self.assertEqual(
            summary["edge_ids"],
            [
                f"{EARTH_ID}.internal-to-{ROUTER_ID}.targets",
                f"{MARS_ID}.internal-to-{ROUTER_ID}.targets",
            ],
        )
        self.assertEqual(summary["hello_worlds"][EARTH_ID], "earth")
        self.assertEqual(summary["hello_worlds"][MARS_ID], "mars")
        self.assertEqual(
            summary["router_runtime_targets"][EARTH_ID],
            {EARTH_ID: "http://docker-hello-earth:8000"},
        )
        self.assertEqual(
            summary["router_runtime_targets"][MARS_ID],
            {MARS_ID: "http://docker-hello-mars:8000"},
        )


if __name__ == "__main__":
    main()
