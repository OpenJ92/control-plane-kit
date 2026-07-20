from __future__ import annotations

import unittest

from control_plane_kit.core.planning import compile_activity_plan
from control_plane_kit.saga import SagaState, SagaStepId, SagaStepState
from control_plane_kit.scheduling import BlockReason, derive_schedule
from control_plane_kit.core.topology import diff_graphs, validate_graph
from examples.scenarios import planning_scenarios


class SchedulingScenarioTests(unittest.TestCase):
    def test_every_planning_scenario_has_a_deterministic_initial_schedule(self):
        for scenario in planning_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                current = validate_graph(scenario.current_graph)
                desired = validate_graph(scenario.desired_graph)
                current.require_valid()
                desired.require_valid()
                plan = compile_activity_plan(diff_graphs(current, desired))
                evidence = SagaState(
                    tuple(
                        SagaStepState(SagaStepId(value.activity_id.value))
                        for value in plan.activities
                    )
                )

                first = derive_schedule(plan, evidence)
                second = derive_schedule(plan, evidence)

                self.assertEqual(first, second)
                if plan.ready_for_execution:
                    self.assertFalse(first.blocked)
                    if plan.activities:
                        self.assertTrue(first.ready)
                else:
                    self.assertFalse(first.ready)
                    reasons = {value.reason for value in first.blocked}
                    self.assertIn(BlockReason.REVIEW_REQUIRED, reasons)
                    self.assertTrue(
                        reasons.issubset({
                            BlockReason.REVIEW_REQUIRED,
                            BlockReason.BLOCKED_PREDECESSOR,
                            BlockReason.PLAN_REVIEW_REQUIRED,
                        }),
                    )


if __name__ == "__main__":
    unittest.main()
