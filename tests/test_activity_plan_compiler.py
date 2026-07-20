from dataclasses import replace
import unittest

from control_plane_kit import (
    ActivityImpact,
    AddSocketConnection,
    AddedChange,
    AmbiguityReason,
    AmbiguousChange,
    DeploymentGraph,
    FieldSubject,
    GraphDiff,
    GraphSubject,
    ModifiedChange,
    NodeSubject,
    ReconcileNode,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemovedChange,
    RemoveSocketConnection,
    ReviewChange,
    RiskLevel,
    RuntimeSubject,
    RuntimeKind,
    RuntimeRecord,
    RuntimeValue,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    StructuralField,
    SwitchSocketConnection,
    TextValue,
    UnsupportedChange,
    UnsupportedReason,
    WaitForHealthy,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from examples.gate_d_live_smoke import router_recipe
from examples.generated_hello_graphs import HelloGraphShape, generated_hello_graph
from examples.scenarios import operation_expectation, switch_database_endpoint


class ActivityPlanCompilerTests(unittest.TestCase):
    def test_empty_and_graph_metadata_only_diffs_need_no_runtime_activity(self):
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

        self.assertEqual(compile_activity_plan(rename).activities, ())

    def test_startup_dependencies_follow_runtime_node_health_connection_order(self):
        populated = validate_graph(compile_recipe(router_recipe("hello-blue")))
        empty = validate_graph(DeploymentGraph(populated.graph.name))

        plan = compile_activity_plan(diff_graphs(empty, populated))

        starts_runtime = [
            activity for activity in plan.activities if isinstance(activity.operation, StartRuntime)
        ]
        starts_node = [
            activity for activity in plan.activities if isinstance(activity.operation, StartNode)
        ]
        health = [
            activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForHealthy)
        ]
        connections = [
            activity
            for activity in plan.activities
            if isinstance(activity.operation, AddSocketConnection)
        ]

        self.assertEqual(len(starts_runtime), 1)
        self.assertEqual(len(starts_node), len(health))
        self.assertTrue(connections)
        runtime_id = starts_runtime[0].activity_id
        self.assertTrue(
            all(
                runtime_id in {dependency.predecessor for dependency in activity.dependencies}
                for activity in starts_node
            )
        )
        start_ids = {
            activity.operation.target.node_id: activity.activity_id
            for activity in starts_node
        }
        self.assertTrue(
            all(
                start_ids[activity.operation.target.node_id]
                in {dependency.predecessor for dependency in activity.dependencies}
                for activity in health
            )
        )
        health_ids = {
            activity.operation.target.node_id: activity.activity_id
            for activity in health
        }
        self.assertTrue(
            all(
                {dependency.predecessor for dependency in activity.dependencies}
                <= set(health_ids.values())
                for activity in connections
            )
        )

    def test_environment_connections_are_startup_material_not_socket_effects(self):
        desired = validate_graph(generated_hello_graph(HelloGraphShape(2, 1)))
        current = validate_graph(DeploymentGraph("generated-empty"))

        plan = compile_activity_plan(diff_graphs(current, desired))

        self.assertFalse(
            any(
                isinstance(
                    activity.operation,
                    (AddSocketConnection, SwitchSocketConnection, RemoveSocketConnection),
                )
                for activity in plan.activities
            )
        )

    def test_teardown_dependencies_remove_connections_before_nodes_and_runtime(self):
        populated = validate_graph(compile_recipe(router_recipe("hello-blue")))
        empty = validate_graph(DeploymentGraph(populated.graph.name))

        plan = compile_activity_plan(diff_graphs(populated, empty))

        removals = {
            activity.operation.target.edge_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, RemoveSocketConnection)
        }
        stops = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, StopNode)
        }
        runtime_stop = next(
            activity for activity in plan.activities if isinstance(activity.operation, StopRuntime)
        )
        node_removals = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, RemoveNodeResource)
        }
        runtime_remove = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, RemoveRuntimeResource)
        )
        graph = populated.graph
        for node_id, activity in stops.items():
            expected = {
                removals[edge.edge_id].activity_id
                for edge in graph.edges.values()
                if edge.edge_id in removals
                and node_id in (edge.provider_role, edge.consumer_role)
            }
            self.assertEqual(
                {dependency.predecessor for dependency in activity.dependencies},
                expected,
            )
            self.assertEqual(
                {dependency.predecessor for dependency in node_removals[node_id].dependencies},
                {activity.activity_id},
            )
        self.assertEqual(
            {dependency.predecessor for dependency in runtime_stop.dependencies},
            {activity.activity_id for activity in node_removals.values()},
        )
        self.assertEqual(
            {dependency.predecessor for dependency in runtime_remove.dependencies},
            {runtime_stop.activity_id},
        )
        self.assertEqual(runtime_stop.risk, RiskLevel.HIGH)
        self.assertEqual(runtime_stop.impact, ActivityImpact.DISRUPTIVE)
        self.assertEqual(runtime_remove.risk, RiskLevel.HIGH)
        self.assertEqual(runtime_remove.impact, ActivityImpact.DESTRUCTIVE)

    def test_router_change_compiles_to_typed_switch_and_is_deterministic(self):
        current = validate_graph(compile_recipe(router_recipe("hello-blue")))
        desired = validate_graph(compile_recipe(router_recipe("hello-green")))
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

    def test_environment_reconcile_precedes_removed_endpoint_stop(self):
        scenario = switch_database_endpoint()
        current = self._without_node(scenario.current_graph, "postgres-b")
        desired = self._without_node(scenario.desired_graph, "postgres-a")
        plan = compile_activity_plan(
            diff_graphs(
                validate_graph(current),
                validate_graph(desired),
            )
        )
        activities = {
            operation_expectation(activity.operation): activity
            for activity in plan.activities
        }
        reconcile = next(
            activity
            for expectation, activity in activities.items()
            if expectation.operation_type is ReconcileNode
            and expectation.target_id == "api"
        )
        old_provider_stop = next(
            activity
            for expectation, activity in activities.items()
            if expectation.operation_type is StopNode
            and expectation.target_id == "postgres-a"
        )

        self.assertIn(
            reconcile.activity_id,
            {
                dependency.predecessor
                for dependency in old_provider_stop.dependencies
            },
        )

    @staticmethod
    def _without_node(graph: DeploymentGraph, node_id: str) -> DeploymentGraph:
        return DeploymentGraph(
            graph.name,
            nodes={
                existing_id: node
                for existing_id, node in graph.nodes.items()
                if existing_id != node_id
            },
            edges=graph.edges,
            runtimes={
                runtime_id: replace(
                    runtime,
                    children=tuple(
                        child for child in runtime.children if child != node_id
                    ),
                )
                for runtime_id, runtime in graph.runtimes.items()
            },
        )

    def test_runtime_move_orders_start_reconcile_and_stop(self):
        before = RuntimeValue(RuntimeRecord("old", RuntimeKind.DOCKER))
        after = RuntimeValue(RuntimeRecord("new", RuntimeKind.DOCKER))
        move = ModifiedChange(
            FieldSubject(
                NodeSubject("api"),
                StructuralField.RUNTIME_MEMBERSHIP,
            ),
            TextValue("old"),
            TextValue("new"),
        )
        plan = compile_activity_plan(
            GraphDiff(
                "before",
                "after",
                (
                    AddedChange(RuntimeSubject("new"), after),
                    move,
                    RemovedChange(RuntimeSubject("old"), before),
                ),
            )
        )
        start = next(
            activity for activity in plan.activities if isinstance(activity.operation, StartRuntime)
        )
        reconcile = next(
            activity
            for activity in plan.activities
            if isinstance(activity.operation, ReconcileNode)
        )
        stop = next(
            activity for activity in plan.activities if isinstance(activity.operation, StopRuntime)
        )

        self.assertIn(
            start.activity_id,
            {dependency.predecessor for dependency in reconcile.dependencies},
        )
        self.assertIn(
            reconcile.activity_id,
            {dependency.predecessor for dependency in stop.dependencies},
        )

    def test_unsupported_and_unrecognized_forms_become_review_blockers(self):
        unsupported = UnsupportedChange(
            FieldSubject(RuntimeSubject("runtime"), StructuralField.RUNTIME_KIND),
            TextValue("docker"),
            TextValue("external"),
            UnsupportedReason.RUNTIME_KIND_TRANSITION,
        )
        unrecognized = AddedChange(GraphSubject(), TextValue("unexpected"))
        ambiguous = AmbiguousChange(
            GraphSubject(),
            AmbiguityReason.BLOCK_SPEC_LANGUAGE_MISMATCH,
        )

        plan = compile_activity_plan(
            GraphDiff("before", "after", (unsupported, unrecognized, ambiguous))
        )

        self.assertFalse(plan.ready_for_execution)
        self.assertEqual(len(plan.activities), 3)
        self.assertTrue(
            all(isinstance(activity.operation, ReviewChange) for activity in plan.activities)
        )
        self.assertTrue(all(activity.risk is RiskLevel.HIGH for activity in plan.activities))


if __name__ == "__main__":
    unittest.main()
