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
from examples.router_swap import recipe as router_recipe


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
        populated = validate_graph(compile_recipe(router_recipe("api-v1")))
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

    def test_teardown_dependencies_remove_connections_before_nodes_and_runtime(self):
        populated = validate_graph(compile_recipe(router_recipe("api-v1")))
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
        graph = populated.graph
        for node_id, activity in stops.items():
            expected = {
                removals[edge.edge_id].activity_id
                for edge in graph.edges.values()
                if node_id in (edge.provider_role, edge.consumer_role)
            }
            self.assertEqual(
                {dependency.predecessor for dependency in activity.dependencies},
                expected,
            )
        self.assertEqual(
            {dependency.predecessor for dependency in runtime_stop.dependencies},
            {activity.activity_id for activity in stops.values()},
        )
        self.assertEqual(runtime_stop.risk, RiskLevel.CRITICAL)
        self.assertEqual(runtime_stop.impact, ActivityImpact.DESTRUCTIVE)

    def test_router_change_compiles_to_typed_switch_and_is_deterministic(self):
        current = validate_graph(compile_recipe(router_recipe("api-v1")))
        desired = validate_graph(compile_recipe(router_recipe("api-v2")))
        diff = diff_graphs(current, desired)

        first = compile_activity_plan(diff)
        second = compile_activity_plan(diff)

        self.assertEqual(first, second)
        self.assertTrue(
            any(
                isinstance(activity.operation, SwitchSocketConnection)
                for activity in first.activities
            )
        )
        self.assertTrue(
            any(isinstance(activity.operation, ReconcileNode) for activity in first.activities)
        )
        reconciles = [
            activity
            for activity in first.activities
            if isinstance(activity.operation, ReconcileNode)
            and activity.operation.target.node_id == "api-router"
        ]
        self.assertEqual(len(reconciles), 1)

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
