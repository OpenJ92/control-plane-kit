from unittest import TestCase, main

from control_plane_kit import DeploymentGraph, Edge, Endpoint, Node, diff_graphs, plan_migration
from control_plane_kit.core.activities import StartNode, SwitchEdge, VerifyNode
from control_plane_kit.runtimes import DryRunRuntime


class CoreGraphTests(TestCase):
    def test_graph_adds_nodes_and_edges_without_mutating_original(self) -> None:
        empty = DeploymentGraph("demo")
        with_auth = empty.add_node(
            Node("auth", "fastapi", {"default": Endpoint("http://auth:8010")})
        )

        self.assertEqual({}, empty.nodes)
        self.assertIn("auth", with_auth.nodes)

    def test_edge_validation_requires_existing_endpoints(self) -> None:
        graph = DeploymentGraph("demo").add_node(
            Node("auth", "fastapi", {"default": Endpoint("http://auth:8010")})
        )

        with self.assertRaisesRegex(KeyError, "missing target"):
            graph.add_edge(Edge("auth-to-api", "auth", "api", "http"))

    def test_diff_and_plan_blue_green_switch(self) -> None:
        before = (
            DeploymentGraph("before")
            .add_node(Node("auth", "fastapi", {"default": Endpoint("http://auth:8010")}))
            .add_node(
                Node(
                    "router",
                    "http-router",
                    {"default": Endpoint("http://router:8080")},
                    frozenset({"switch-target"}),
                )
            )
            .add_node(
                Node(
                    "api-v1",
                    "fastapi",
                    {"default": Endpoint("http://api-v1:8000")},
                    frozenset({"health"}),
                )
            )
            .add_edge(Edge("auth-to-router", "auth", "router", "http"))
            .add_edge(Edge("router-active", "router", "api-v1", "http", mutable=True))
        )
        after = before.add_node(
            Node(
                "api-v2",
                "fastapi",
                {"default": Endpoint("http://api-v2:8000")},
                frozenset({"health"}),
            )
        ).replace_edge(Edge("router-active", "router", "api-v2", "http", mutable=True))

        diff = diff_graphs(before, after)
        self.assertEqual(["api-v2"], [node.node_id for node in diff.added_nodes])
        self.assertEqual(["router-active"], [edge.edge_id for _, edge in diff.changed_edges])

        plan = plan_migration(before, after)
        self.assertIsInstance(plan.activities[0], StartNode)
        self.assertIsInstance(plan.activities[1], VerifyNode)
        self.assertIsInstance(plan.activities[2], SwitchEdge)
        self.assertIn("SwitchEdge(router-active: api-v1 -> api-v2)", plan.to_text())

    def test_dry_run_runtime_reports_planned_activities(self) -> None:
        before = DeploymentGraph("before")
        after = before.add_node(Node("api", "fastapi"))

        results = DryRunRuntime().execute(plan_migration(before, after))

        self.assertEqual("planned", results[0].status)
        self.assertEqual("StartNode(api)", results[0].message)


if __name__ == "__main__":
    main()
