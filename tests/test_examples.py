from unittest import TestCase, main

from examples.api_blue_green import graph_v1 as api_graph_v1
from examples.api_blue_green import graph_v2 as api_graph_v2
from examples.local_cloudflare_auth import graph as cloudflare_auth_graph
from examples.postgres_switch import graph_v1 as postgres_graph_v1
from examples.postgres_switch import graph_v2 as postgres_graph_v2
from control_plane_kit import diff_graphs, plan_migration


class ExampleTests(TestCase):
    def test_api_blue_green_example_has_switch_plan(self) -> None:
        plan = plan_migration(api_graph_v1(), api_graph_v2())

        self.assertIn("SwitchEdge(api-router-active: api-v1 -> api-v2)", plan.to_text())

    def test_postgres_switch_example_has_database_switch_plan(self) -> None:
        plan = plan_migration(postgres_graph_v1(), postgres_graph_v2())

        self.assertIn(
            "SwitchEdge(postgres-switch.active: postgres-v1 -> postgres-v2)",
            plan.to_text(),
        )

    def test_cloudflare_auth_example_is_serializable(self) -> None:
        descriptor = cloudflare_auth_graph().descriptor()

        self.assertEqual("local-cloudflare-auth", descriptor["name"])
        self.assertEqual("cloudflare-tunnel", descriptor["nodes"]["cloudflare"]["kind"])

    def test_examples_have_diffs(self) -> None:
        self.assertFalse(diff_graphs(api_graph_v1(), api_graph_v2()).is_empty())
        self.assertFalse(diff_graphs(postgres_graph_v1(), postgres_graph_v2()).is_empty())


if __name__ == "__main__":
    main()
