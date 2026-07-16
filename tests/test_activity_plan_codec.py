import json
import unittest

from control_plane_kit import (
    ACTIVITY_PLAN_SCHEMA,
    ACTIVITY_PLAN_VERSION,
    ActivityDependency,
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    ActivityPlanDescriptorCodec,
    ChangeTarget,
    EdgeSubject,
    FieldSubject,
    GraphSubject,
    LossyActivityPlanDescriptor,
    MalformedActivityPlanDescriptor,
    NodeTarget,
    NodeSubject,
    PlannedActivity,
    ReviewChange,
    ReviewReason,
    RiskLevel,
    RuntimeSubject,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    AddSocketConnection,
    ReconcileNode,
    ReconcileRuntime,
    RemoveSocketConnection,
    RuntimeTarget,
    SocketConnectionTarget,
    SwitchSocketConnection,
    StructuralField,
    UnknownActivityPlanVariant,
    WaitForHealthy,
)
from control_plane_kit.stores import ActivityPlanRecord


class ActivityPlanDescriptorCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = ActivityPlanDescriptorCodec()

    def test_round_trip_preserves_closed_plan_semantics(self):
        start = PlannedActivity(
            ActivityId("start-api"),
            StartNode(NodeTarget("api-v2")),
        )
        healthy = PlannedActivity(
            ActivityId("wait-api"),
            WaitForHealthy(NodeTarget("api-v2")),
            (ActivityDependency(start.activity_id),),
            RiskLevel.MEDIUM,
            ActivityImpact.NON_DESTRUCTIVE,
        )
        plan = ActivityPlan((healthy, start))

        descriptor = self.codec.encode(plan)

        self.assertEqual(descriptor["schema"], ACTIVITY_PLAN_SCHEMA)
        self.assertEqual(descriptor["version"], ACTIVITY_PLAN_VERSION)
        self.assertEqual(self.codec.decode(descriptor), plan)

    def test_json_rendering_is_deterministic(self):
        plan = ActivityPlan(
            (
                PlannedActivity(ActivityId("b"), StartNode(NodeTarget("b"))),
                PlannedActivity(ActivityId("a"), StartNode(NodeTarget("a"))),
            )
        )

        rendered = self.codec.dumps(plan)

        self.assertEqual(rendered, self.codec.dumps(ActivityPlan(tuple(reversed(plan.activities)))))
        self.assertEqual(json.loads(rendered), self.codec.encode(plan))

    def test_review_diagnostic_carries_subject_not_changed_secret_value(self):
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("review-secret-change"),
                    ReviewChange(
                        ChangeTarget(
                            FieldSubject(
                                GraphSubject(),
                                StructuralField.ENVIRONMENT,
                                "DATABASE_PASSWORD",
                            )
                        ),
                        ReviewReason.UNSUPPORTED_CHANGE,
                    ),
                    risk=RiskLevel.HIGH,
                ),
            )
        )

        rendered = self.codec.dumps(plan)

        self.assertIn("DATABASE_PASSWORD", rendered)
        self.assertNotIn("postgres://secret", rendered)
        self.assertNotIn("before", rendered)
        self.assertNotIn("after", rendered)

    def test_unknown_schema_and_operation_fail_closed(self):
        descriptor = self.codec.encode(ActivityPlan(()))
        descriptor["schema"] = "other"
        with self.assertRaises(UnknownActivityPlanVariant):
            self.codec.decode(descriptor)

        descriptor = self.codec.encode(
            ActivityPlan((PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api"))),))
        )
        descriptor["activities"][0]["operation"]["kind"] = "shell-command"
        with self.assertRaises(UnknownActivityPlanVariant):
            self.codec.decode(descriptor)

    def test_wrong_shapes_and_unrepresentable_extra_fields_fail(self):
        with self.assertRaises(MalformedActivityPlanDescriptor):
            self.codec.decode({"schema": ACTIVITY_PLAN_SCHEMA, "version": 1, "activities": {}})

        descriptor = self.codec.encode(ActivityPlan(()))
        descriptor["operator_note"] = "not part of the closed language"
        with self.assertRaises(LossyActivityPlanDescriptor):
            self.codec.decode(descriptor)

    def test_durable_record_rejects_arbitrary_payload_mapping(self):
        with self.assertRaisesRegex(TypeError, "typed ActivityPlan"):
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-16T00:00:00Z",
                plan={"activities": ["StartNode(api)"]},
            )

    def test_every_closed_operation_and_target_variant_round_trips(self):
        operations = (
            StartNode(NodeTarget("api")),
            StopNode(NodeTarget("api")),
            WaitForHealthy(NodeTarget("api")),
            AddSocketConnection(SocketConnectionTarget("auth-api")),
            SwitchSocketConnection(SocketConnectionTarget("auth-api")),
            RemoveSocketConnection(SocketConnectionTarget("auth-api")),
            ReconcileNode(NodeTarget("api")),
            ReconcileRuntime(RuntimeTarget("docker")),
            StartRuntime(RuntimeTarget("docker")),
            StopRuntime(RuntimeTarget("docker")),
        )
        for ordinal, operation in enumerate(operations):
            with self.subTest(operation=type(operation).__name__):
                plan = ActivityPlan(
                    (PlannedActivity(ActivityId(f"activity-{ordinal}"), operation),)
                )
                self.assertEqual(self.codec.decode(self.codec.encode(plan)), plan)

    def test_every_review_subject_variant_round_trips(self):
        subjects = (
            GraphSubject(),
            RuntimeSubject("docker"),
            NodeSubject("api"),
            EdgeSubject("auth-api"),
            FieldSubject(GraphSubject(), StructuralField.GRAPH_NAME),
            FieldSubject(
                RuntimeSubject("docker"),
                StructuralField.RUNTIME_METADATA,
                "region",
            ),
            FieldSubject(
                NodeSubject("api"),
                StructuralField.ENVIRONMENT,
                "DATABASE_URL",
            ),
        )
        for ordinal, subject in enumerate(subjects):
            with self.subTest(subject=subject):
                plan = ActivityPlan(
                    (
                        PlannedActivity(
                            ActivityId(f"review-{ordinal}"),
                            ReviewChange(
                                ChangeTarget(subject),
                                ReviewReason.AMBIGUOUS_CHANGE,
                            ),
                            risk=RiskLevel.HIGH,
                        ),
                    )
                )
                self.assertEqual(self.codec.decode(self.codec.encode(plan)), plan)

    def test_dependency_and_activity_permutations_have_one_descriptor(self):
        first = PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api")))
        second = PlannedActivity(ActivityId("b"), StartNode(NodeTarget("auth")))
        joined_ab = PlannedActivity(
            ActivityId("c"),
            WaitForHealthy(NodeTarget("auth")),
            (
                ActivityDependency(first.activity_id),
                ActivityDependency(second.activity_id),
            ),
        )
        joined_ba = PlannedActivity(
            ActivityId("c"),
            WaitForHealthy(NodeTarget("auth")),
            (
                ActivityDependency(second.activity_id),
                ActivityDependency(first.activity_id),
            ),
        )

        descriptors = {
            self.codec.dumps(ActivityPlan(activities))
            for activities in (
                (first, second, joined_ab),
                (joined_ab, second, first),
                (second, joined_ba, first),
            )
        }

        self.assertEqual(len(descriptors), 1)

    def test_invalid_dag_and_target_shapes_are_descriptor_errors(self):
        descriptor = self.codec.encode(
            ActivityPlan((PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api"))),))
        )
        descriptor["activities"][0]["dependencies"] = ["missing"]
        with self.assertRaisesRegex(
            MalformedActivityPlanDescriptor,
            "depends on missing activity",
        ):
            self.codec.decode(descriptor)

        descriptor = self.codec.encode(
            ActivityPlan((PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api"))),))
        )
        descriptor["activities"][0]["operation"]["target"] = {
            "kind": "runtime",
            "runtime_id": "docker",
        }
        with self.assertRaisesRegex(MalformedActivityPlanDescriptor, "expected 'node' target"):
            self.codec.decode(descriptor)

        descriptor = self.codec.encode(
            ActivityPlan((PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api"))),))
        )
        del descriptor["activities"][0]["operation"]["target"]
        with self.assertRaisesRegex(MalformedActivityPlanDescriptor, "operation.target"):
            self.codec.decode(descriptor)

        start = PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api")))
        wait = PlannedActivity(
            ActivityId("b"),
            WaitForHealthy(NodeTarget("api")),
            (ActivityDependency(start.activity_id),),
        )
        descriptor = self.codec.encode(ActivityPlan((start, wait)))
        descriptor["activities"][1]["dependencies"] = ["a", "a"]
        with self.assertRaisesRegex(
            MalformedActivityPlanDescriptor,
            "repeats a dependency edge",
        ):
            self.codec.decode(descriptor)

    def test_risk_and_destructive_markers_survive_the_descriptor_boundary(self):
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("stop-api"),
                    StopNode(NodeTarget("api")),
                    risk=RiskLevel.CRITICAL,
                    impact=ActivityImpact.DESTRUCTIVE,
                ),
            )
        )

        descriptor = self.codec.encode(plan)

        self.assertEqual(descriptor["activities"][0]["risk"], "critical")
        self.assertEqual(descriptor["activities"][0]["impact"], "destructive")
        self.assertEqual(self.codec.decode(descriptor), plan)

    def test_unknown_review_reason_and_non_json_values_fail_closed(self):
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("review"),
                    ReviewChange(
                        ChangeTarget(GraphSubject()),
                        ReviewReason.UNSUPPORTED_CHANGE,
                    ),
                    risk=RiskLevel.HIGH,
                ),
            )
        )
        descriptor = self.codec.encode(plan)
        descriptor["activities"][0]["operation"]["reason"] = "operator-feeling"
        with self.assertRaises(UnknownActivityPlanVariant):
            self.codec.decode(descriptor)

        descriptor = self.codec.encode(ActivityPlan(()))
        descriptor["extra"] = object()
        with self.assertRaises(MalformedActivityPlanDescriptor):
            self.codec.decode(descriptor)


if __name__ == "__main__":
    unittest.main()
