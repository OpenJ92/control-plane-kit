from __future__ import annotations

from itertools import permutations
import unittest

from control_plane_kit_core.planning import (
    ActivityDependency,
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    AddSocketConnection,
    ChangeTarget,
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
    StopNode,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit_core.topology import GraphSubject


def activity(
    activity_id: str,
    operation: object,
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
    def test_valid_plan_is_canonically_topological_not_input_ordered(self) -> None:
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

    def test_fan_out_and_fan_in_order_is_invariant_across_permutations(self) -> None:
        runtime = activity("a-runtime", StartRuntime(RuntimeTarget("docker")))
        api = activity("b-api", StartNode(NodeTarget("api")), depends_on=("a-runtime",))
        auth = activity("c-auth", StartNode(NodeTarget("auth")), depends_on=("a-runtime",))
        connect = activity(
            "d-connect",
            AddSocketConnection(SocketConnectionTarget("auth-to-api")),
            depends_on=("b-api", "c-auth"),
        )

        observed = {
            tuple(value.activity_id.value for value in ActivityPlan(order).activities)
            for order in permutations((runtime, api, auth, connect))
        }

        self.assertEqual(observed, {("a-runtime", "b-api", "c-auth", "d-connect")})

    def test_multiple_structural_violations_are_deterministically_ordered(self) -> None:
        invalid = activity(
            "invalid",
            StartNode(NodeTarget("api")),
            depends_on=("missing", "missing"),
            risk=RiskLevel.LOW,
            impact=ActivityImpact.DESTRUCTIVE,
        )

        with self.assertRaises(InvalidActivityPlan) as raised:
            ActivityPlan((invalid,))

        codes = tuple(value.code.value for value in raised.exception.violations)
        self.assertEqual(codes, tuple(sorted(codes)))
        self.assertEqual(
            set(codes),
            {
                PlanViolationCode.DESTRUCTIVE_RISK.value,
                PlanViolationCode.DUPLICATE_DEPENDENCY.value,
                PlanViolationCode.MISSING_DEPENDENCY.value,
            },
        )

    def test_review_change_is_a_high_risk_plan_blocker(self) -> None:
        review = activity(
            "review",
            ReviewChange(ChangeTarget(GraphSubject()), ReviewReason.UNSUPPORTED_CHANGE),
            risk=RiskLevel.HIGH,
        )

        plan = ActivityPlan((review,))

        self.assertFalse(plan.ready_for_execution)
        self.assertIs(plan.activity(ActivityId("review")), review)

    def test_typed_operation_targets_are_enforced(self) -> None:
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
