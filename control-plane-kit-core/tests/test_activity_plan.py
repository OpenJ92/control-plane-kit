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
    DataResourceTarget,
    DestroyDataResource,
    InvalidActivityPlan,
    NodeTarget,
    PlanViolationCode,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    ReviewChange,
    ReviewReason,
    RiskLevel,
    RuntimeTarget,
    StopRuntime,
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

    def test_empty_plan_and_missing_lookup_are_explicit(self) -> None:
        plan = ActivityPlan(())

        self.assertEqual(plan.activities, ())
        self.assertTrue(plan.ready_for_execution)
        with self.assertRaisesRegex(KeyError, "missing"):
            plan.activity(ActivityId("missing"))

    def test_every_closed_operation_accepts_only_its_typed_target(self) -> None:
        valid_operations = (
            (StartNode(NodeTarget("node")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (StopNode(NodeTarget("node")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (RemoveNodeResource(NodeTarget("node")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (WaitForHealthy(NodeTarget("node")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (AddSocketConnection(SocketConnectionTarget("edge")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (SwitchSocketConnection(SocketConnectionTarget("edge")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (RemoveSocketConnection(SocketConnectionTarget("edge")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (ReconcileNode(NodeTarget("node")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (ReconcileRuntime(RuntimeTarget("runtime")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (StartRuntime(RuntimeTarget("runtime")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (StopRuntime(RuntimeTarget("runtime")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (RemoveRuntimeResource(RuntimeTarget("runtime")), RiskLevel.LOW, ActivityImpact.NON_DESTRUCTIVE),
            (
                DestroyDataResource(DataResourceTarget("postgres", "volume")),
                RiskLevel.CRITICAL,
                ActivityImpact.DESTRUCTIVE,
            ),
            (
                ReviewChange(ChangeTarget(GraphSubject()), ReviewReason.UNSUPPORTED_CHANGE),
                RiskLevel.HIGH,
                ActivityImpact.NON_DESTRUCTIVE,
            ),
        )

        for index, (operation, risk, impact) in enumerate(valid_operations):
            with self.subTest(operation=operation.__class__.__name__):
                plan = ActivityPlan((activity(f"op-{index}", operation, risk=risk, impact=impact),))
                self.assertIs(plan.activities[0].operation, operation)

        invalid_operations = (
            StartNode(RuntimeTarget("runtime")),  # type: ignore[arg-type]
            StopNode(RuntimeTarget("runtime")),  # type: ignore[arg-type]
            RemoveNodeResource(RuntimeTarget("runtime")),  # type: ignore[arg-type]
            WaitForHealthy(RuntimeTarget("runtime")),  # type: ignore[arg-type]
            AddSocketConnection(NodeTarget("node")),  # type: ignore[arg-type]
            SwitchSocketConnection(NodeTarget("node")),  # type: ignore[arg-type]
            RemoveSocketConnection(NodeTarget("node")),  # type: ignore[arg-type]
            ReconcileNode(RuntimeTarget("runtime")),  # type: ignore[arg-type]
            ReconcileRuntime(NodeTarget("node")),  # type: ignore[arg-type]
            StartRuntime(NodeTarget("node")),  # type: ignore[arg-type]
            StopRuntime(NodeTarget("node")),  # type: ignore[arg-type]
            RemoveRuntimeResource(NodeTarget("node")),  # type: ignore[arg-type]
            DestroyDataResource(NodeTarget("node")),  # type: ignore[arg-type]
            ReviewChange(ChangeTarget(GraphSubject()), "unsupported-change"),  # type: ignore[arg-type]
        )

        for index, operation in enumerate(invalid_operations):
            with self.subTest(operation=operation.__class__.__name__):
                with self.assertRaises(TypeError):
                    PlannedActivity(ActivityId(f"invalid-{index}"), operation)

    def test_destructive_activity_requires_high_or_critical_risk(self) -> None:
        for risk in (RiskLevel.INFORMATIONAL, RiskLevel.LOW, RiskLevel.MEDIUM):
            with self.subTest(risk=risk):
                with self.assertRaises(InvalidActivityPlan) as raised:
                    ActivityPlan(
                        (
                            activity(
                                f"destroy-{risk.value}",
                                DestroyDataResource(
                                    target=DataResourceTarget("postgres", "volume"),
                                ),
                                risk=risk,
                                impact=ActivityImpact.DESTRUCTIVE,
                            ),
                        )
                    )
                self.assertIn(
                    PlanViolationCode.DESTRUCTIVE_RISK,
                    {violation.code for violation in raised.exception.violations},
                )

        self.assertEqual(
            ActivityPlan(
                (
                    activity(
                        "critical-destroy",
                        DestroyDataResource(
                            target=DataResourceTarget("postgres", "volume"),
                        ),
                        risk=RiskLevel.CRITICAL,
                        impact=ActivityImpact.DESTRUCTIVE,
                    ),
                )
            ).activities[0].risk,
            RiskLevel.CRITICAL,
        )

    def test_missing_duplicate_self_and_cycle_dependencies_fail_structurally(self) -> None:
        missing_duplicate_self = activity(
            "invalid",
            StartNode(NodeTarget("api")),
            depends_on=("invalid", "missing", "missing"),
        )

        with self.assertRaises(InvalidActivityPlan) as raised:
            ActivityPlan((missing_duplicate_self,))

        self.assertEqual(
            {violation.code for violation in raised.exception.violations},
            {
                PlanViolationCode.DUPLICATE_DEPENDENCY,
                PlanViolationCode.MISSING_DEPENDENCY,
                PlanViolationCode.SELF_DEPENDENCY,
            },
        )

        first = activity("first", StartNode(NodeTarget("first")), depends_on=("second",))
        second = activity("second", StartNode(NodeTarget("second")), depends_on=("first",))
        with self.assertRaises(InvalidActivityPlan) as cycle:
            ActivityPlan((first, second))
        self.assertEqual(
            {violation.code for violation in cycle.exception.violations},
            {PlanViolationCode.DEPENDENCY_CYCLE},
        )

    def test_independent_activities_use_id_as_deterministic_tie_breaker(self) -> None:
        beta = activity("beta", StartNode(NodeTarget("beta")))
        alpha = activity("alpha", StartNode(NodeTarget("alpha")))

        self.assertEqual(
            tuple(value.activity_id.value for value in ActivityPlan((beta, alpha)).activities),
            ("alpha", "beta"),
        )

    def test_review_change_cannot_be_downgraded_to_low_risk(self) -> None:
        review = activity(
            "review",
            ReviewChange(ChangeTarget(GraphSubject()), ReviewReason.AMBIGUOUS_CHANGE),
            risk=RiskLevel.LOW,
        )

        with self.assertRaises(InvalidActivityPlan) as raised:
            ActivityPlan((review,))

        self.assertEqual(
            {violation.code for violation in raised.exception.violations},
            {PlanViolationCode.REVIEW_RISK},
        )

    def test_runtime_lifecycle_operations_use_runtime_targets(self) -> None:
        for operation in (
            StartRuntime(RuntimeTarget("docker")),
            StopRuntime(RuntimeTarget("docker")),
            ReconcileRuntime(RuntimeTarget("docker")),
        ):
            with self.subTest(operation=operation.__class__.__name__):
                plan = ActivityPlan((activity("runtime", operation),))
                self.assertIsInstance(plan.activities[0].operation.target, RuntimeTarget)

        for operation in (
            lambda: StartRuntime(NodeTarget("docker")),  # type: ignore[arg-type]
            lambda: StopRuntime(NodeTarget("docker")),  # type: ignore[arg-type]
            lambda: ReconcileRuntime(NodeTarget("docker")),  # type: ignore[arg-type]
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(TypeError):
                    PlannedActivity(ActivityId("invalid"), operation())


if __name__ == "__main__":
    unittest.main()
