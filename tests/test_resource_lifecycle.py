from dataclasses import replace
import unittest

from control_plane_kit import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataResourceSpec,
    DataResourceTarget,
    DeploymentGraph,
    DeploymentRecipe,
    DestroyDataResource,
    DockerImageImplementation,
    DockerRuntime,
    ExternalHttpImplementation,
    ExternalRuntime,
    GraphDescriptorCodec,
    InvalidActivityPlan,
    NodeTarget,
    PlanViolationCode,
    PlannedActivity,
    Protocol,
    ProviderSocket,
    RemoveNodeResource,
    RemoveRuntimeResource,
    ResourceLifecycle,
    ResourceOwnership,
    ResourcePersistence,
    RiskLevel,
    ReviewChange,
    StopNode,
    StopRuntime,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from examples.app_with_postgres import recipe as app_with_postgres


class ResourceLifecycleTests(unittest.TestCase):
    def test_lifecycle_is_a_closed_product_with_independent_data_resources(self):
        lifecycle = ResourceLifecycle.owned_with_retained_data(
            "postgres-data",
            "postgres-backups",
        )

        self.assertIs(lifecycle.ownership, ResourceOwnership.OWNED)
        self.assertIs(lifecycle.compute, ResourcePersistence.EPHEMERAL)
        self.assertEqual(
            lifecycle.data,
            (
                DataResourceSpec("postgres-backups"),
                DataResourceSpec("postgres-data"),
            ),
        )
        with self.assertRaisesRegex(ValueError, "attached and external"):
            ResourceLifecycle(
                ResourceOwnership.EXTERNAL,
                ResourcePersistence.EPHEMERAL,
            )

    def test_graph_codec_preserves_lifecycle_without_string_inference(self):
        graph = compile_recipe(app_with_postgres())
        codec = GraphDescriptorCodec()

        restored = codec.decode(codec.encode(graph))

        self.assertEqual(restored, graph)
        postgres = restored.node("postgres")
        self.assertEqual(
            postgres.lifecycle,
            ResourceLifecycle.owned_with_retained_data("postgres-data"),
        )

    def test_topology_removal_deletes_ephemeral_compute_but_never_data(self):
        current = validate_graph(compile_recipe(app_with_postgres()))
        desired = validate_graph(DeploymentGraph("empty"))

        plan = compile_activity_plan(diff_graphs(current, desired))
        operations = tuple(activity.operation for activity in plan.activities)

        self.assertTrue(
            any(
                isinstance(operation, StopNode)
                and operation.target == NodeTarget("postgres")
                for operation in operations
            )
        )
        self.assertTrue(
            any(
                isinstance(operation, RemoveNodeResource)
                and operation.target == NodeTarget("postgres")
                for operation in operations
            )
        )
        self.assertTrue(any(isinstance(operation, StopRuntime) for operation in operations))
        self.assertTrue(
            any(isinstance(operation, RemoveRuntimeResource) for operation in operations)
        )
        self.assertFalse(any(isinstance(operation, DestroyDataResource) for operation in operations))

    def test_retained_compute_stops_without_resource_removal(self):
        service = ApplicationBlock(
            BlockSpec("retained-api", "Retained API"),
            DockerImageImplementation(
                "retained-api:latest",
                ports={"internal": 8000},
                lifecycle=ResourceLifecycle(
                    ResourceOwnership.OWNED,
                    ResourcePersistence.RETAINED,
                ),
            ),
            BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
        )
        current = validate_graph(
            compile_recipe(
                DeploymentRecipe(
                    "retained",
                    DockerRuntime(children=(service,)),
                )
            )
        )
        desired = validate_graph(
            DeploymentGraph(
                "without-retained-api",
                nodes={},
                edges={},
                runtimes={
                    runtime_id: replace(
                        runtime,
                        children=tuple(
                            child
                            for child in runtime.children
                            if child != "retained-api"
                        ),
                    )
                    for runtime_id, runtime in current.graph.runtimes.items()
                },
            )
        )

        plan = compile_activity_plan(diff_graphs(current, desired))

        self.assertTrue(
            any(
                isinstance(activity.operation, StopNode)
                and activity.operation.target.node_id == "retained-api"
                for activity in plan.activities
            )
        )
        self.assertFalse(
            any(
                isinstance(activity.operation, RemoveNodeResource)
                and activity.operation.target.node_id == "retained-api"
                for activity in plan.activities
            )
        )

    def test_external_resources_are_topology_only_and_never_gain_lifecycle_work(self):
        service = ApplicationBlock(
            BlockSpec("external-api", "External API"),
            ExternalHttpImplementation("https://example.invalid"),
            BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
        )
        desired = validate_graph(
            compile_recipe(
                DeploymentRecipe(
                    "external",
                    ExternalRuntime("external", children=(service,)),
                )
            )
        )
        empty = validate_graph(DeploymentGraph("empty"))

        self.assertEqual(
            compile_activity_plan(diff_graphs(empty, desired)).activities,
            (),
        )
        self.assertEqual(
            compile_activity_plan(diff_graphs(desired, empty)).activities,
            (),
        )

    def test_data_destruction_requires_explicit_critical_destructive_activity(self):
        target = DataResourceTarget("postgres", "postgres-data")
        with self.assertRaises(InvalidActivityPlan) as raised:
            ActivityPlan(
                (
                    PlannedActivity(
                        ActivityId("destroy-postgres-data"),
                        DestroyDataResource(target),
                    ),
                )
            )

        self.assertIn(
            PlanViolationCode.DATA_DESTRUCTION_SAFETY,
            {violation.code for violation in raised.exception.violations},
        )
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("destroy-postgres-data"),
                    DestroyDataResource(target),
                    risk=RiskLevel.CRITICAL,
                    impact=ActivityImpact.DESTRUCTIVE,
                ),
            )
        )
        self.assertEqual(plan.activities[0].operation, DestroyDataResource(target))

    def test_lifecycle_policy_changes_are_review_blockers_not_reconciliation(self):
        current_graph = compile_recipe(app_with_postgres())
        postgres = current_graph.node("postgres")
        desired_graph = current_graph.update_node(
            replace(postgres, lifecycle=ResourceLifecycle.owned_ephemeral())
        )

        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(current_graph),
                validate_graph(desired_graph),
            )
        )

        self.assertEqual(len(plan.activities), 1)
        self.assertIsInstance(plan.activities[0].operation, ReviewChange)
        self.assertEqual(plan.activities[0].risk, RiskLevel.HIGH)
        self.assertFalse(plan.ready_for_execution)


if __name__ == "__main__":
    unittest.main()
