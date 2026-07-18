import unittest

from control_plane_kit.planning import (
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ActivityId,
    ActivityPlan,
    AddSocketConnection,
    Compensate,
    CompensationMaterialSource,
    DestroyDataResource,
    DataResourceTarget,
    NoCompensationRequired,
    NonCompensatable,
    NonCompensatableReason,
    NodeTarget,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
    compensation_for_operation,
)
from control_plane_kit.execution import ActivityEventKind, ActivityEventRecord
from control_plane_kit.saga import compensation_candidates
from control_plane_kit.projections.saga_journal import (
    initial_state_for_plan,
    project_activity_journal,
)


class CompensationPlanningTests(unittest.TestCase):
    def test_operation_matrix_has_one_explicit_compensation_meaning(self):
        node = NodeTarget("api")
        runtime = RuntimeTarget("docker")
        edge = SocketConnectionTarget("auth-api")
        cases = (
            (StartNode(node), StopNode(node), CompensationMaterialSource.DESIRED_GRAPH),
            (StopNode(node), StartNode(node), CompensationMaterialSource.BASE_GRAPH),
            (
                StartRuntime(runtime),
                StopRuntime(runtime),
                CompensationMaterialSource.DESIRED_GRAPH,
            ),
            (
                StopRuntime(runtime),
                StartRuntime(runtime),
                CompensationMaterialSource.BASE_GRAPH,
            ),
            (
                AddSocketConnection(edge),
                RemoveSocketConnection(edge),
                CompensationMaterialSource.DESIRED_GRAPH,
            ),
            (
                RemoveSocketConnection(edge),
                AddSocketConnection(edge),
                CompensationMaterialSource.BASE_GRAPH,
            ),
            (
                SwitchSocketConnection(edge),
                SwitchSocketConnection(edge),
                CompensationMaterialSource.BASE_GRAPH,
            ),
            (
                ReconcileNode(node),
                ReconcileNode(node),
                CompensationMaterialSource.BASE_GRAPH,
            ),
            (
                ReconcileRuntime(runtime),
                ReconcileRuntime(runtime),
                CompensationMaterialSource.BASE_GRAPH,
            ),
        )
        for forward, inverse, source in cases:
            with self.subTest(operation=type(forward).__name__):
                self.assertEqual(
                    compensation_for_operation(forward),
                    Compensate(inverse, source),
                )

    def test_observation_and_destruction_are_not_given_fake_inverses(self):
        self.assertEqual(
            compensation_for_operation(WaitForHealthy(NodeTarget("api"))),
            NoCompensationRequired(),
        )
        self.assertEqual(
            compensation_for_operation(RemoveNodeResource(NodeTarget("api"))),
            NonCompensatable(NonCompensatableReason.RESOURCE_REMOVAL),
        )
        self.assertEqual(
            compensation_for_operation(RemoveRuntimeResource(RuntimeTarget("docker"))),
            NonCompensatable(NonCompensatableReason.RESOURCE_REMOVAL),
        )
        self.assertEqual(
            compensation_for_operation(
                DestroyDataResource(DataResourceTarget("postgres", "data"))
            ),
            NonCompensatable(NonCompensatableReason.DATA_DESTRUCTION),
        )

    def test_plan_codec_persists_exact_compensation_and_source(self):
        plan = ActivityPlan(
            (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),)
        )
        descriptor = DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan)

        self.assertEqual(descriptor["version"], 1)
        self.assertEqual(
            descriptor["activities"][0]["compensation"],
            {
                "kind": "compensate",
                "operation": {
                    "kind": "stop-node",
                    "target": {"kind": "node", "node_id": "api"},
                },
                "material_source": "desired-graph",
            },
        )
        self.assertEqual(DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor), plan)

        descriptor["activities"][0]["compensation"]["operation"]["kind"] = "start-node"
        with self.assertRaisesRegex(ValueError, "canonical operation"):
            DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor)

    def test_version_one_requires_compensation_without_legacy_upgrade(self):
        incomplete = {
            "schema": "control-plane-kit.activity-plan",
            "version": 1,
            "activities": [
                {
                    "activity_id": "start-api",
                    "operation": {
                        "kind": "start-node",
                        "target": {"kind": "node", "node_id": "api"},
                    },
                    "dependencies": [],
                    "risk": "low",
                    "impact": "non-destructive",
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "fields"):
            DEFAULT_ACTIVITY_PLAN_CODEC.decode(incomplete)

        malformed = dict(incomplete)
        malformed["invented"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            DEFAULT_ACTIVITY_PLAN_CODEC.decode(malformed)

    def test_saga_availability_comes_from_persisted_plan_meaning(self):
        plan = ActivityPlan(
            (
                PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),
                PlannedActivity(
                    ActivityId("health-api"),
                    WaitForHealthy(NodeTarget("api")),
                ),
            )
        )

        state = initial_state_for_plan(plan)

        by_id = {step.step_id.value: step for step in state.steps}
        self.assertTrue(by_id["start-api"].compensation_available)
        self.assertFalse(by_id["health-api"].compensation_available)

    def test_candidates_follow_reverse_durable_completion_order(self):
        plan = ActivityPlan(
            (
                PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),
                PlannedActivity(
                    ActivityId("start-worker"),
                    StartNode(NodeTarget("worker")),
                ),
            )
        )
        events = tuple(
            ActivityEventRecord(
                event_id=f"event-{ordinal}",
                run_id="run-a",
                ordinal=ordinal,
                kind=kind,
                occurred_at=f"time-{ordinal}",
                activity_id=activity_id,
            )
            for ordinal, kind, activity_id in (
                (1, ActivityEventKind.STEP_STARTED, "start-api"),
                (2, ActivityEventKind.STEP_SUCCEEDED, "start-api"),
                (3, ActivityEventKind.STEP_STARTED, "start-worker"),
                (4, ActivityEventKind.STEP_SUCCEEDED, "start-worker"),
            )
        )

        projection = project_activity_journal(plan, events)

        self.assertEqual(
            tuple(value.value for value in compensation_candidates(projection.state)),
            ("start-worker", "start-api"),
        )


if __name__ == "__main__":
    unittest.main()
