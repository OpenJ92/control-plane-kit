import unittest

from control_plane_kit import (
    ActivityDependency,
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    AddSocketConnection,
    ChangeTarget,
    GraphSubject,
    InvalidActivityPlan,
    NodeTarget,
    PlanViolationCode,
    PlannedActivity,
    ReconcileRuntime,
    ReviewChange,
    ReviewReason,
    RiskLevel,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)


def activity(
    activity_id: str,
    operation,
    *,
    depends_on: tuple[str, ...] = (),
    risk: RiskLevel = RiskLevel.LOW,
    impact: ActivityImpact = ActivityImpact.NON_DESTRUCTIVE,
) -> PlannedActivity:
    return PlannedActivity(
        ActivityId(activity_id),
        operation,
        tuple(ActivityDependency(ActivityId(value)) for value in depends_on),
        risk,
        impact,
    )


class ActivityPlanTests(unittest.TestCase):
    def test_valid_plan_is_canonically_topological_not_input_ordered(self):
        start = activity("1-start", StartNode(NodeTarget("api-v2")))
        healthy = activity(
            "2-healthy",
            WaitForHealthy(NodeTarget("api-v2")),
            depends_on=("1-start",),
            risk=RiskLevel.MEDIUM,
        )
        connect = activity(
            "3-connect",
            AddSocketConnection(SocketConnectionTarget("router.active")),
            depends_on=("2-healthy",),
            risk=RiskLevel.MEDIUM,
        )

        plan = ActivityPlan((connect, healthy, start))

        self.assertEqual(
            tuple(value.activity_id.value for value in plan.activities),
            ("1-start", "2-healthy", "3-connect"),
        )
        self.assertTrue(plan.ready_for_execution)

    def test_independent_activities_use_id_as_deterministic_tie_breaker(self):
        second = activity("b", ReconcileRuntime(RuntimeTarget("runtime-b")))
        first = activity("a", ReconcileRuntime(RuntimeTarget("runtime-a")))

        plan = ActivityPlan((second, first))

        self.assertEqual(
            tuple(value.activity_id.value for value in plan.activities),
            ("a", "b"),
        )

    def test_runtime_lifecycle_operations_use_runtime_targets(self):
        start = activity("start", StartRuntime(RuntimeTarget("docker")))
        stop = activity(
            "stop",
            StopRuntime(RuntimeTarget("docker")),
            depends_on=("start",),
            risk=RiskLevel.MEDIUM,
            impact=ActivityImpact.DISRUPTIVE,
        )

        plan = ActivityPlan((stop, start))

        self.assertEqual(
            tuple(value.operation for value in plan.activities),
            (start.operation, stop.operation),
        )

    def test_missing_duplicate_self_and_cycle_dependencies_fail_structurally(self):
        cases = (
            (
                (
                    activity("a", StartNode(NodeTarget("api-a"))),
                    activity(
                        "b",
                        StartNode(NodeTarget("api-b")),
                        depends_on=("a", "a"),
                    ),
                ),
                PlanViolationCode.DUPLICATE_DEPENDENCY,
            ),
            (
                (
                    activity(
                        "a",
                        StartNode(NodeTarget("api")),
                        depends_on=("missing",),
                    ),
                ),
                PlanViolationCode.MISSING_DEPENDENCY,
            ),
            (
                (
                    activity("a", StartNode(NodeTarget("api-a"))),
                    activity("a", StartNode(NodeTarget("api-b"))),
                ),
                PlanViolationCode.DUPLICATE_ACTIVITY_ID,
            ),
            (
                (
                    activity(
                        "a",
                        StartNode(NodeTarget("api")),
                        depends_on=("a",),
                    ),
                ),
                PlanViolationCode.SELF_DEPENDENCY,
            ),
            (
                (
                    activity(
                        "a",
                        StartNode(NodeTarget("api-a")),
                        depends_on=("b",),
                    ),
                    activity(
                        "b",
                        StartNode(NodeTarget("api-b")),
                        depends_on=("a",),
                    ),
                ),
                PlanViolationCode.DEPENDENCY_CYCLE,
            ),
        )
        for activities, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(InvalidActivityPlan) as raised:
                    ActivityPlan(activities)
                self.assertIn(code, {value.code for value in raised.exception.violations})

    def test_destructive_activity_requires_high_or_critical_risk(self):
        destructive = activity(
            "switch",
            SwitchSocketConnection(SocketConnectionTarget("database.active")),
            risk=RiskLevel.MEDIUM,
            impact=ActivityImpact.DESTRUCTIVE,
        )

        with self.assertRaises(InvalidActivityPlan) as raised:
            ActivityPlan((destructive,))

        self.assertEqual(
            raised.exception.violations[0].code,
            PlanViolationCode.DESTRUCTIVE_RISK,
        )

    def test_unsupported_or_ambiguous_change_is_a_high_risk_plan_blocker(self):
        review = activity(
            "review",
            ReviewChange(
                ChangeTarget(GraphSubject()),
                ReviewReason.UNSUPPORTED_CHANGE,
            ),
            risk=RiskLevel.HIGH,
        )

        plan = ActivityPlan((review,))

        self.assertFalse(plan.ready_for_execution)
        self.assertIs(plan.activity(ActivityId("review")), review)

    def test_review_change_cannot_be_downgraded_to_low_risk(self):
        review = activity(
            "review",
            ReviewChange(
                ChangeTarget(GraphSubject()),
                ReviewReason.AMBIGUOUS_CHANGE,
            ),
        )

        with self.assertRaises(InvalidActivityPlan) as raised:
            ActivityPlan((review,))

        self.assertEqual(
            raised.exception.violations[0].code,
            PlanViolationCode.REVIEW_RISK,
        )

    def test_invalid_operation_and_target_shapes_cannot_enter_plan(self):
        with self.assertRaises(TypeError):
            ActivityDependency("invalid")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ChangeTarget(object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PlannedActivity(ActivityId("invalid"), object())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PlannedActivity(
                ActivityId("invalid-target"),
                StartNode(RuntimeTarget("runtime")),  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
