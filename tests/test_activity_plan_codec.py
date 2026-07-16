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
    FieldSubject,
    GraphSubject,
    LossyActivityPlanDescriptor,
    MalformedActivityPlanDescriptor,
    NodeTarget,
    PlannedActivity,
    ReviewChange,
    ReviewReason,
    RiskLevel,
    StartNode,
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


if __name__ == "__main__":
    unittest.main()
