from unittest import TestCase, main

from control_plane_kit import (
    EdgeSubject,
    compile_recipe,
    diff_graphs,
    plan_migration,
    validate_graph,
)
from examples.router_swap import recipe


class PlannerTests(TestCase):
    def test_router_swap_changes_active_edge(self):
        current = compile_recipe(recipe("api-v1"))
        desired = compile_recipe(recipe("api-v2"))

        diff = diff_graphs(validate_graph(current), validate_graph(desired))
        self.assertIn(
            "api-router.active",
            {
                change.subject.edge_id
                for change in diff.changes
                if isinstance(change.subject, EdgeSubject)
            },
        )
        plan = plan_migration(diff)
        self.assertIn("SwitchSocketConnection(api-router.active)", plan.to_text())


if __name__ == "__main__":
    main()
