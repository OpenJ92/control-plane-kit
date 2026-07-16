import unittest

from control_plane_kit import (
    DeploymentGraph,
    RecoveryDisposition,
    RECOVERY_CANDIDATE_SCHEMA,
    RECOVERY_CANDIDATE_VERSION,
    RecoveryLimitationCode,
    RecoveryMode,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    compile_recipe,
    plan_reconstruction,
    plan_recovery_transition,
    validate_graph,
)
from examples.router_swap import recipe as router_recipe


class RecoveryPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.populated = validate_graph(compile_recipe(router_recipe("api-v1")))
        self.empty = validate_graph(DeploymentGraph(self.populated.graph.name))

    def test_reverse_transition_is_a_fresh_canonical_plan_with_limitations(self):
        candidate = plan_recovery_transition(self.populated, self.empty)

        self.assertEqual(candidate.mode, RecoveryMode.REVERSE_TRANSITION)
        self.assertTrue(
            any(
                isinstance(activity.operation, StopNode | StopRuntime)
                for activity in candidate.plan.activities
            )
        )
        self.assertTrue(candidate.approval.destructive)
        self.assertEqual(candidate.approval.required_scope, "plan:approve-destructive")
        self.assertIn(
            RecoveryLimitationCode.GRAPH_STATE_ONLY,
            {value.code for value in candidate.limitations},
        )
        self.assertIn(
            RecoveryLimitationCode.DESTRUCTIVE_ACTIVITY,
            {value.code for value in candidate.limitations},
        )

    def test_reconstruction_uses_empty_baseline_without_claiming_absence(self):
        candidate = plan_reconstruction(self.populated)

        self.assertEqual(candidate.mode, RecoveryMode.RECONSTRUCTION)
        self.assertIsNone(candidate.source_graph_name)
        self.assertTrue(
            any(
                isinstance(activity.operation, StartNode | StartRuntime)
                for activity in candidate.plan.activities
            )
        )
        self.assertIn(
            RecoveryLimitationCode.SOURCE_STATE_UNKNOWN,
            {value.code for value in candidate.limitations},
        )
        self.assertTrue(
            any(
                value.disposition is RecoveryDisposition.COMPENSATION_REQUIRED
                for value in candidate.assessments
            )
        )

    def test_router_recovery_uses_same_compiler_and_is_deterministic(self):
        version_one = validate_graph(compile_recipe(router_recipe("api-v1")))
        version_two = validate_graph(compile_recipe(router_recipe("api-v2")))

        first = plan_recovery_transition(version_two, version_one)
        second = plan_recovery_transition(version_two, version_one)

        self.assertEqual(first, second)
        self.assertEqual(first.descriptor(), second.descriptor())
        self.assertEqual(first.descriptor()["schema"], RECOVERY_CANDIDATE_SCHEMA)
        self.assertEqual(first.descriptor()["version"], RECOVERY_CANDIDATE_VERSION)
        self.assertEqual(first.plan.__class__.__module__, "control_plane_kit.planning.activity_plan")
        self.assertNotIn("rollback", str(first.descriptor()).lower())

    def test_invalid_inputs_fail_before_planning(self):
        with self.assertRaises(TypeError):
            plan_recovery_transition(self.populated.graph, self.empty)
        with self.assertRaises(TypeError):
            plan_reconstruction(self.populated.graph)


if __name__ == "__main__":
    unittest.main()
