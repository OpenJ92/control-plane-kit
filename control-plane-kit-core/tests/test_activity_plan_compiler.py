from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
)
from control_plane_kit_core.planning import (
    ActivityImpact,
    AllocatePublicIngress,
    AddSocketConnection,
    ReconcileNode,
    RemovePublicIngress,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    ReviewChange,
    RiskLevel,
    RuntimeTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForPublicIngressReady,
    WaitForHealthy,
    compile_activity_plan,
)
from control_plane_kit_core.topology import (
    AddedChange,
    AmbiguityReason,
    AmbiguousChange,
    DeploymentGraph,
    FieldSubject,
    GraphDiff,
    GraphSubject,
    ModifiedChange,
    NodeSubject,
    RemovedChange,
    RuntimeRecord,
    RuntimeSubject,
    RuntimeValue,
    StructuralField,
    TextValue,
    UnsupportedChange,
    UnsupportedReason,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import Protocol, RuntimeKind, SocketBinding

from tests.test_kernel_pipeline import (
    PureImplementation,
    app_with_database_topology,
    split_service_topology,
)
from tests.test_graph_codec import public_ingress_graph


class ActivityPlanCompilerTests(unittest.TestCase):
    def test_empty_and_graph_metadata_only_diffs_need_no_runtime_activity(self) -> None:
        self.assertEqual(
            compile_activity_plan(GraphDiff("same", "same", ())).activities,
            (),
        )

        rename = GraphDiff(
            "before",
            "after",
            (
                ModifiedChange(
                    FieldSubject(GraphSubject(), StructuralField.GRAPH_NAME),
                    TextValue("before"),
                    TextValue("after"),
                ),
            ),
        )
        plan = compile_activity_plan(rename)

        self.assertEqual(plan.activities, ())

    def test_startup_dependencies_follow_runtime_node_health_connection_order(self) -> None:
        desired = validate_graph(compile_topology(split_service_topology()))
        current = validate_graph(DeploymentGraph(desired.graph.name))

        plan = compile_activity_plan(diff_graphs(current, desired))

        runtime_start = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StartRuntime)
        )
        node_starts = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, StartNode)
        }
        health = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForHealthy)
        }

        self.assertEqual(set(node_starts), {"api", "inventory-service", "postgres"})
        for activity in node_starts.values():
            self.assertIn(
                runtime_start.activity_id,
                {dependency.predecessor for dependency in activity.dependencies},
            )
        for node_id, activity in health.items():
            self.assertIn(
                node_starts[node_id].activity_id,
                {dependency.predecessor for dependency in activity.dependencies},
            )

    def test_environment_connections_are_startup_material_not_socket_effects(self) -> None:
        desired = validate_graph(compile_topology(app_with_database_topology()))
        current = validate_graph(DeploymentGraph(desired.graph.name))

        plan = compile_activity_plan(diff_graphs(current, desired))

        self.assertFalse(
            any(
                isinstance(
                    activity.operation,
                    (
                        AddSocketConnection,
                        SwitchSocketConnection,
                        RemoveSocketConnection,
                    ),
                )
                for activity in plan.activities
            )
        )

    def test_public_ingress_allocation_waits_for_target_before_connector_start(self) -> None:
        desired = validate_graph(public_ingress_graph())
        current = validate_graph(DeploymentGraph(desired.graph.name))

        plan = compile_activity_plan(diff_graphs(current, desired))

        allocation = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, AllocatePublicIngress)
        )
        connector_start = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StartNode)
            and activity.operation.target.node_id == "cloudflared-gateway"
        )
        target_health = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForHealthy)
            and activity.operation.target.node_id == "gateway"
        )
        connector_health = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForHealthy)
            and activity.operation.target.node_id == "cloudflared-gateway"
        )
        readiness = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForPublicIngressReady)
        )

        self.assertEqual(allocation.operation.target.ingress_id, "gateway-public")
        self.assertIn(
            target_health.activity_id,
            {dependency.predecessor for dependency in allocation.dependencies},
        )
        self.assertIn(
            allocation.activity_id,
            {dependency.predecessor for dependency in connector_start.dependencies},
        )
        self.assertEqual(readiness.operation.target.ingress_id, "gateway-public")
        self.assertEqual(
            {dependency.predecessor for dependency in readiness.dependencies},
            {allocation.activity_id, connector_health.activity_id},
        )

    def test_public_ingress_readiness_around_existing_connector_waits_for_allocation(self) -> None:
        desired_graph = public_ingress_graph()
        current_graph = replace(desired_graph, public_ingresses=())

        plan = compile_activity_plan(
            diff_graphs(validate_graph(current_graph), validate_graph(desired_graph))
        )

        allocation = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, AllocatePublicIngress)
        )
        readiness = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForPublicIngressReady)
        )
        self.assertEqual(
            {dependency.predecessor for dependency in readiness.dependencies},
            {allocation.activity_id},
        )
        self.assertFalse(
            any(isinstance(activity.operation, StartNode) for activity in plan.activities)
        )

    def test_public_ingress_teardown_stops_connector_before_removal(self) -> None:
        populated = validate_graph(public_ingress_graph())
        empty = validate_graph(DeploymentGraph(populated.graph.name))

        plan = compile_activity_plan(diff_graphs(populated, empty))

        removal = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, RemovePublicIngress)
        )
        gateway_stop = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StopNode)
            and activity.operation.target.node_id == "gateway"
        )
        connector_stop = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StopNode)
            and activity.operation.target.node_id == "cloudflared-gateway"
        )

        self.assertEqual(removal.operation.target.ingress_id, "gateway-public")
        self.assertFalse(
            any(
                isinstance(activity.operation, WaitForPublicIngressReady)
                for activity in plan.activities
            )
        )
        self.assertIn(
            removal.activity_id,
            {dependency.predecessor for dependency in gateway_stop.dependencies},
        )
        self.assertIn(
            connector_stop.activity_id,
            {dependency.predecessor for dependency in removal.dependencies},
        )

    def test_teardown_dependencies_remove_connections_before_nodes_and_runtime(self) -> None:
        populated = validate_graph(runtime_control_graph("blue"))
        empty = validate_graph(DeploymentGraph(populated.graph.name))

        plan = compile_activity_plan(diff_graphs(populated, empty))

        connection_removal = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, RemoveSocketConnection)
        )
        node_stops = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, StopNode)
        }
        node_removals = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, RemoveNodeResource)
        }
        runtime_stop = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StopRuntime)
        )
        runtime_remove = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, RemoveRuntimeResource)
        )

        for node_id, stop in node_stops.items():
            self.assertIn(
                connection_removal.activity_id,
                {dependency.predecessor for dependency in stop.dependencies},
            )
            self.assertEqual(
                {dependency.predecessor for dependency in node_removals[node_id].dependencies},
                {stop.activity_id},
            )
        self.assertEqual(
            {dependency.predecessor for dependency in runtime_stop.dependencies},
            {activity.activity_id for activity in node_removals.values()},
        )
        self.assertEqual(
            {dependency.predecessor for dependency in runtime_remove.dependencies},
            {runtime_stop.activity_id},
        )
        self.assertEqual(runtime_remove.risk, RiskLevel.HIGH)
        self.assertEqual(runtime_remove.impact, ActivityImpact.DESTRUCTIVE)

    def test_router_change_compiles_to_typed_switch_and_is_deterministic(self) -> None:
        current = validate_graph(runtime_control_graph("blue"))
        desired = validate_graph(runtime_control_graph("green"))
        diff = diff_graphs(current, desired)

        first = compile_activity_plan(diff)
        second = compile_activity_plan(diff)

        self.assertEqual(first, second)
        switches = [
            activity
            for activity in first.activities
            if isinstance(activity.operation, SwitchSocketConnection)
            and activity.operation.target.edge_id == "router.active"
        ]
        self.assertEqual(len(switches), 1)
        self.assertFalse(
            any(isinstance(activity.operation, ReconcileNode) for activity in first.activities)
        )

    def test_environment_reconcile_precedes_removed_endpoint_stop(self) -> None:
        current = validate_graph(service_graph("provider-a"))
        desired = validate_graph(service_graph("provider-b"))

        plan = compile_activity_plan(diff_graphs(current, desired))

        reconcile = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, ReconcileNode)
            and activity.operation.target.node_id == "consumer"
        )
        old_provider_stop = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StopNode)
            and activity.operation.target.node_id == "provider-a"
        )
        self.assertIn(
            reconcile.activity_id,
            {dependency.predecessor for dependency in old_provider_stop.dependencies},
        )

    def test_reconciled_node_is_checked_healthy_before_transition_advancement(self) -> None:
        current = validate_graph(service_graph("provider-a"))
        desired = validate_graph(service_graph("provider-b"))

        plan = compile_activity_plan(diff_graphs(current, desired))

        reconcile = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, ReconcileNode)
            and activity.operation.target.node_id == "consumer"
        )
        healthy = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForHealthy)
            and activity.operation.target.node_id == "consumer"
        )

        self.assertIn(
            reconcile.activity_id,
            {dependency.predecessor for dependency in healthy.dependencies},
        )

    def test_runtime_move_orders_start_reconcile_and_stop(self) -> None:
        before = RuntimeRecord("old", RuntimeKind.DOCKER)
        after = RuntimeRecord("new", RuntimeKind.DOCKER)
        move = FieldSubject(NodeSubject("api"), StructuralField.RUNTIME_MEMBERSHIP)
        plan = compile_activity_plan(
            GraphDiff(
                "before",
                "after",
                (
                    AddedChange(RuntimeSubject("new"), RuntimeValue(after)),
                    ModifiedChange(move, TextValue("old"), TextValue("new")),
                    RemovedChange(RuntimeSubject("old"), RuntimeValue(before)),
                ),
            )
        )

        start = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StartRuntime)
        )
        reconcile = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, ReconcileNode)
        )
        stop = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, StopRuntime)
        )

        self.assertEqual(start.operation.target, RuntimeTarget("new"))
        self.assertIn(start.activity_id, {dependency.predecessor for dependency in reconcile.dependencies})
        self.assertIn(reconcile.activity_id, {dependency.predecessor for dependency in stop.dependencies})

    def test_unsupported_and_ambiguous_forms_become_review_blockers(self) -> None:
        unsupported = UnsupportedChange(
            FieldSubject(RuntimeSubject("runtime"), StructuralField.RUNTIME_KIND),
            TextValue("docker"),
            TextValue("external"),
            UnsupportedReason.RUNTIME_KIND_TRANSITION,
        )
        ambiguous = AmbiguousChange(
            GraphSubject(),
            AmbiguityReason.BLOCK_SPEC_LANGUAGE_MISMATCH,
        )

        plan = compile_activity_plan(GraphDiff("before", "after", (unsupported, ambiguous)))

        self.assertFalse(plan.ready_for_execution)
        self.assertEqual(len(plan.activities), 2)
        self.assertTrue(
            all(isinstance(activity.operation, ReviewChange) for activity in plan.activities)
        )
        self.assertTrue(all(activity.risk is RiskLevel.HIGH for activity in plan.activities))


def runtime_control_graph(target: str) -> DeploymentGraph:
    router = ApplicationBlock(
        BlockSpec("router"),
        PureImplementation("router", {"public": "http://router"}),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "active",
                    Protocol.HTTP,
                    (),
                    binding=SocketBinding.RUNTIME_CONTROL,
                ),
            ),
            providers=(ProviderSocket("public", Protocol.HTTP),),
        ),
    )
    backend = ApplicationBlock(
        BlockSpec(target),
        PureImplementation("application", {"internal": f"http://{target}"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    return compile_topology(
        DeploymentTopology(
            "runtime-control",
            DockerRuntime(
                children=(
                    router,
                    backend,
                    SocketConnection(
                        target,
                        "internal",
                        "router",
                        "active",
                        edge_id="router.active",
                    ),
                )
            ),
        )
    )


def service_graph(provider_id: str) -> DeploymentGraph:
    provider = ApplicationBlock(
        BlockSpec(provider_id),
        PureImplementation("provider", {"internal": f"http://{provider_id}"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    consumer = ApplicationBlock(
        BlockSpec("consumer"),
        PureImplementation("consumer", {}),
        BlockSockets(
            requirements=(RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",)),)
        ),
    )
    return compile_topology(
        DeploymentTopology(
            "service",
            DockerRuntime(
                children=(
                    provider,
                    consumer,
                    SocketConnection(provider_id, "internal", "consumer", "upstream"),
                )
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
