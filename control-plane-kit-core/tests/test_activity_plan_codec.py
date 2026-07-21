from __future__ import annotations

import json
import unittest

from control_plane_kit_core.planning import (
    ACTIVITY_PLAN_SCHEMA,
    ACTIVITY_PLAN_VERSION,
    ActivityDependency,
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    ActivityPlanDescriptorCodec,
    AddSocketConnection,
    ChangeTarget,
    DataResourceTarget,
    DestroyDataResource,
    LossyActivityPlanDescriptor,
    MalformedActivityPlanDescriptor,
    NodeTarget,
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
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    UnknownActivityPlanVariant,
    WaitForHealthy,
)
from control_plane_kit_core.topology import (
    EdgeSubject,
    FieldSubject,
    GraphSubject,
    NodeSubject,
    RuntimeSubject,
    StructuralField,
)


class ActivityPlanDescriptorCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = ActivityPlanDescriptorCodec()

    def test_round_trip_preserves_closed_plan_semantics(self) -> None:
        start = PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api")))
        wait = PlannedActivity(
            ActivityId("wait-api"),
            WaitForHealthy(NodeTarget("api")),
            dependencies=(ActivityDependency(start.activity_id),),
            risk=RiskLevel.MEDIUM,
        )
        plan = ActivityPlan((wait, start))

        descriptor = self.codec.encode(plan)

        self.assertEqual(descriptor["schema"], ACTIVITY_PLAN_SCHEMA)
        self.assertEqual(descriptor["version"], ACTIVITY_PLAN_VERSION)
        self.assertEqual(self.codec.decode(descriptor), plan)

    def test_json_rendering_is_deterministic(self) -> None:
        first = PlannedActivity(ActivityId("b"), StartNode(NodeTarget("b")))
        second = PlannedActivity(ActivityId("a"), StartNode(NodeTarget("a")))

        rendered = self.codec.dumps(ActivityPlan((first, second)))

        self.assertEqual(rendered, self.codec.dumps(ActivityPlan((second, first))))
        self.assertEqual(json.loads(rendered), self.codec.encode(ActivityPlan((first, second))))

    def test_dependency_and_activity_permutations_have_one_descriptor(self) -> None:
        first = PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api")))
        second = PlannedActivity(ActivityId("b"), StartNode(NodeTarget("auth")))
        joined_ab = PlannedActivity(
            ActivityId("c"),
            WaitForHealthy(NodeTarget("auth")),
            dependencies=(
                ActivityDependency(first.activity_id),
                ActivityDependency(second.activity_id),
            ),
        )
        joined_ba = PlannedActivity(
            ActivityId("c"),
            WaitForHealthy(NodeTarget("auth")),
            dependencies=(
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

    def test_codec_boundary_rejects_arbitrary_payload_mapping(self) -> None:
        with self.assertRaises(MalformedActivityPlanDescriptor):
            self.codec.encode({"activities": ["StartNode(api)"]})  # type: ignore[arg-type]

    def test_every_closed_operation_and_target_variant_round_trips(self) -> None:
        operations = (
            StartNode(NodeTarget("api")),
            StopNode(NodeTarget("api")),
            RemoveNodeResource(NodeTarget("api")),
            WaitForHealthy(NodeTarget("api")),
            AddSocketConnection(SocketConnectionTarget("api.database")),
            SwitchSocketConnection(SocketConnectionTarget("router.active")),
            RemoveSocketConnection(SocketConnectionTarget("api.database")),
            ReconcileNode(NodeTarget("api")),
            ReconcileRuntime(RuntimeTarget("docker")),
            StartRuntime(RuntimeTarget("docker")),
            StopRuntime(RuntimeTarget("docker")),
            RemoveRuntimeResource(RuntimeTarget("docker")),
            DestroyDataResource(DataResourceTarget("postgres", "pgdata")),
        )

        for ordinal, operation in enumerate(operations):
            with self.subTest(operation=operation.__class__.__name__):
                risk = RiskLevel.CRITICAL if isinstance(operation, DestroyDataResource) else RiskLevel.LOW
                impact = (
                    ActivityImpact.DESTRUCTIVE
                    if isinstance(operation, DestroyDataResource)
                    else ActivityImpact.NON_DESTRUCTIVE
                )
                plan = ActivityPlan(
                    (
                        PlannedActivity(
                            ActivityId(f"activity-{ordinal}"),
                            operation,
                            risk=risk,
                            impact=impact,
                        ),
                    )
                )
                self.assertEqual(self.codec.decode(self.codec.encode(plan)), plan)

    def test_every_review_subject_variant_round_trips(self) -> None:
        subjects = (
            GraphSubject(),
            RuntimeSubject("docker"),
            NodeSubject("api"),
            EdgeSubject("api.database"),
            FieldSubject(GraphSubject(), StructuralField.GRAPH_NAME),
            FieldSubject(RuntimeSubject("docker"), StructuralField.RUNTIME_METADATA, "region"),
            FieldSubject(NodeSubject("api"), StructuralField.PUBLIC_ENVIRONMENT, "DATABASE_URL"),
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

    def test_review_diagnostic_carries_subject_not_changed_secret_value(self) -> None:
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("review-secret-change"),
                    ReviewChange(
                        ChangeTarget(
                            FieldSubject(
                                GraphSubject(),
                                StructuralField.PUBLIC_ENVIRONMENT,
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

    def test_risk_and_destructive_markers_survive_the_descriptor_boundary(self) -> None:
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

    def test_unknown_schema_operation_review_reason_and_non_json_fail_closed(self) -> None:
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

        descriptor = self.codec.encode(
            ActivityPlan(
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
        )
        descriptor["activities"][0]["operation"]["reason"] = "operator-feeling"
        with self.assertRaises(UnknownActivityPlanVariant):
            self.codec.decode(descriptor)

        descriptor = self.codec.encode(ActivityPlan(()))
        descriptor["extra"] = object()
        with self.assertRaises(MalformedActivityPlanDescriptor):
            self.codec.decode(descriptor)

    def test_wrong_shapes_lossy_fields_and_invalid_dag_fail_closed(self) -> None:
        with self.assertRaises(MalformedActivityPlanDescriptor):
            self.codec.decode(
                {
                    "schema": ACTIVITY_PLAN_SCHEMA,
                    "version": ACTIVITY_PLAN_VERSION,
                    "activities": {},
                }
            )

        descriptor = self.codec.encode(ActivityPlan(()))
        descriptor["operator_note"] = "not part of the closed language"
        with self.assertRaises(LossyActivityPlanDescriptor):
            self.codec.decode(descriptor)

        descriptor = self.codec.encode(
            ActivityPlan((PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api"))),))
        )
        descriptor["activities"][0]["dependencies"] = ["missing"]
        with self.assertRaisesRegex(MalformedActivityPlanDescriptor, "missing activity"):
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

        start = PlannedActivity(ActivityId("a"), StartNode(NodeTarget("api")))
        wait = PlannedActivity(
            ActivityId("b"),
            WaitForHealthy(NodeTarget("api")),
            dependencies=(ActivityDependency(start.activity_id),),
        )
        descriptor = self.codec.encode(ActivityPlan((start, wait)))
        descriptor["activities"][1]["dependencies"] = ["a", "a"]
        with self.assertRaisesRegex(MalformedActivityPlanDescriptor, "repeats a dependency edge"):
            self.codec.decode(descriptor)


if __name__ == "__main__":
    unittest.main()
