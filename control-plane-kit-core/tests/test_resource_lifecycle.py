from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.lifecycle import (
    DataResourceSpec,
    ResourceLifecycle,
    ResourceOwnership,
    ResourcePersistence,
)
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    DataResourceTarget,
    DestroyDataResource,
    InvalidActivityPlan,
    NodeTarget,
    PlanViolationCode,
    PlannedActivity,
    RemoveNodeResource,
    RemoveRuntimeResource,
    ReviewChange,
    RiskLevel,
    RuntimeTarget,
    StopNode,
    StopRuntime,
    compile_activity_plan,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    GraphDescriptorCodec,
    Node,
    RuntimeRecord,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import BlockFamily, RuntimeKind


def _graph(
    *,
    node_lifecycle: ResourceLifecycle,
    runtime_lifecycle: ResourceLifecycle | None = None,
) -> DeploymentGraph:
    node = Node(
        node_id="service",
        block_family=BlockFamily.APPLICATION,
        block_spec=BlockSpec("service"),
        kind="fixture",
        runtime_id="runtime",
        sockets=BlockSockets(),
        lifecycle=node_lifecycle,
    )
    runtime = RuntimeRecord(
        "runtime",
        RuntimeKind.DOCKER,
        children=(node.node_id,),
        lifecycle=runtime_lifecycle or ResourceLifecycle.owned_ephemeral(),
    )
    return DeploymentGraph(
        "lifecycle",
        nodes={node.node_id: node},
        runtimes={runtime.runtime_id: runtime},
    )


class ResourceLifecycleSuccessorTests(unittest.TestCase):
    def test_lifecycle_is_a_closed_product_with_independent_data_resources(self) -> None:
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

    def test_graph_codec_preserves_lifecycle_without_string_inference(self) -> None:
        graph = _graph(
            node_lifecycle=ResourceLifecycle.owned_with_retained_data(
                "postgres-data"
            )
        )
        codec = GraphDescriptorCodec()

        restored = codec.decode(codec.encode(graph))

        self.assertEqual(restored, graph)
        self.assertEqual(
            restored.node("service").lifecycle,
            ResourceLifecycle.owned_with_retained_data("postgres-data"),
        )

    def test_topology_removal_deletes_ephemeral_compute_but_never_data(self) -> None:
        current = validate_graph(
            _graph(
                node_lifecycle=ResourceLifecycle.owned_with_retained_data(
                    "postgres-data"
                )
            )
        )
        desired = validate_graph(DeploymentGraph("lifecycle"))

        operations = tuple(
            activity.operation
            for activity in compile_activity_plan(
                diff_graphs(current, desired)
            ).activities
        )

        self.assertIn(StopNode(NodeTarget("service")), operations)
        self.assertIn(RemoveNodeResource(NodeTarget("service")), operations)
        self.assertIn(StopRuntime(RuntimeTarget("runtime")), operations)
        self.assertIn(RemoveRuntimeResource(RuntimeTarget("runtime")), operations)
        self.assertFalse(any(isinstance(value, DestroyDataResource) for value in operations))

    def test_retained_compute_stops_without_resource_removal(self) -> None:
        current_graph = _graph(
            node_lifecycle=ResourceLifecycle(
                ResourceOwnership.OWNED,
                ResourcePersistence.RETAINED,
            )
        )
        desired_graph = replace(
            current_graph,
            nodes={},
            runtimes={
                "runtime": replace(
                    current_graph.runtimes["runtime"],
                    children=(),
                )
            },
        )

        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(current_graph),
                validate_graph(desired_graph),
            )
        )

        self.assertTrue(
            any(isinstance(value.operation, StopNode) for value in plan.activities)
        )
        self.assertFalse(
            any(
                isinstance(value.operation, RemoveNodeResource)
                for value in plan.activities
            )
        )

    def test_external_resources_are_topology_only_and_never_gain_lifecycle_work(self) -> None:
        external = _graph(
            node_lifecycle=ResourceLifecycle.external(),
            runtime_lifecycle=ResourceLifecycle.external(),
        )
        empty = DeploymentGraph("lifecycle")

        self.assertEqual(
            compile_activity_plan(
                diff_graphs(validate_graph(empty), validate_graph(external))
            ).activities,
            (),
        )
        self.assertEqual(
            compile_activity_plan(
                diff_graphs(validate_graph(external), validate_graph(empty))
            ).activities,
            (),
        )

    def test_data_destruction_requires_explicit_critical_destructive_activity(self) -> None:
        target = DataResourceTarget("service", "postgres-data")

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
            {value.code for value in raised.exception.violations},
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

    def test_lifecycle_policy_changes_are_review_blockers_not_reconciliation(self) -> None:
        current_graph = _graph(
            node_lifecycle=ResourceLifecycle.owned_with_retained_data(
                "postgres-data"
            )
        )
        desired_graph = replace(
            current_graph,
            nodes={
                "service": replace(
                    current_graph.node("service"),
                    lifecycle=ResourceLifecycle.owned_ephemeral(),
                )
            },
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
