from unittest import TestCase, main

from control_plane_kit import compile_recipe, diff_graphs, plan_migration
from examples.router_swap import recipe


class PlannerTests(TestCase):
    def test_router_swap_plans_runtime_target_switch(self):
        current = compile_recipe(recipe("api-v1"))
        desired = compile_recipe(recipe("api-v2"))

        diff = diff_graphs(current, desired)
        self.assertIn("api-router.active", diff.changed_edges)
        plan = plan_migration(current, desired)
        self.assertIn("SwitchRuntimeTarget(api-router.active)", plan.to_text())


if __name__ == "__main__":
    main()
