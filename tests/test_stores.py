import unittest

from control_plane_kit import (
    ActivityPlan,
    ActivityRunStatus,
    ObservationFreshness,
    ObservationStatus,
)
from control_plane_kit.topology.graph import DeploymentGraph
from control_plane_kit.stores import (
    ActivityPlanRecord,
    ActivityRunRecord,
    GraphVersionRecord,
    InstanceRecord,
    ObservationRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    POSTGRES_SCHEMA,
    SecretReferenceRecord,
    WorkspaceLifecycle,
    WorkspaceRecord,
)
from tests.postgres_case import PostgresStoreTestCase


class StoreContractTests(PostgresStoreTestCase):
    def test_workspace_tracks_current_and_desired_graph_pointers(self):
        store = self.stores.workspace
        store.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))

        store.set_current_graph("workspace-a", "graph-current")
        record = store.set_desired_graph("workspace-a", "graph-desired")

        self.assertEqual(record.current_graph_id, "graph-current")
        self.assertEqual(record.desired_graph_id, "graph-desired")

    def test_graph_topology_store_keeps_latest_workspace_version(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        store = self.stores.graph_topology
        first = GraphVersionRecord(
            graph_id="graph-1",
            workspace_id="workspace-a",
            version=1,
            graph_descriptor=DeploymentGraph(name="first").descriptor(),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        second = GraphVersionRecord.from_graph(
            graph_id="graph-2",
            workspace_id="workspace-a",
            version=2,
            graph=DeploymentGraph(name="second"),
            created_by="jacob",
            created_at="2026-07-15T00:01:00Z",
        )

        store.save(first)
        store.save(second)

        latest = store.latest_for_workspace("workspace-a")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.graph_id, "graph-2")

    def test_activity_history_preserves_action_order(self):
        store = self.stores.activity_history
        store.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Swap API",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-15T00:00:00Z",
            )
        )
        store.add_action(
            OperationActionRecord(
                action_id="action-b",
                session_id="session-a",
                ordinal=2,
                action_type=OperationActionKind.CONNECT_SOCKET,
                actor_id="jacob",
            )
        )
        store.add_action(
            OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type=OperationActionKind.ADD_BLOCK,
                actor_id="jacob",
            )
        )

        self.assertEqual(
            [record.action_id for record in store.actions_for_session("session-a")],
            ["action-a", "action-b"],
        )

    def test_activity_history_supports_workspace_timeline_queries(self):
        store = self.stores.activity_history
        store.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Swap API",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-15T00:00:00Z",
            )
        )
        store.add_session(
            OperationSessionRecord(
                session_id="session-b",
                workspace_id="workspace-b",
                actor_id="jacob",
                title="Other workspace",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-15T00:01:00Z",
            )
        )
        store.add_plan(
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-15T00:02:00Z",
                plan=ActivityPlan(()),
            )
        )
        store.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                status=ActivityRunStatus.RUNNING,
                started_at="2026-07-15T00:03:00Z",
            )
        )

        self.assertEqual(
            [record.session_id for record in store.sessions_for_workspace("workspace-a")],
            ["session-a"],
        )
        self.assertEqual([record.plan_id for record in store.plans_for_session("session-a")], ["plan-a"])
        self.assertEqual([record.run_id for record in store.runs_for_plan("plan-a")], ["run-a"])

    def test_observed_state_is_separate_from_graph_truth(self):
        self.stores.workspace.create(
            WorkspaceRecord(workspace_id="workspace-a", name="Demo")
        )
        store = self.stores.observed_state
        store.put(
            ObservationRecord(
                observation_id="obs-1",
                workspace_id="workspace-a",
                subject_id="api",
                status=ObservationStatus.HEALTHY,
                observed_at="2026-07-15T00:00:00Z",
            )
        )
        store.put(
            ObservationRecord(
                observation_id="obs-2",
                workspace_id="workspace-a",
                subject_id="api",
                status=ObservationStatus.UNKNOWN,
                observed_at="2026-07-15T00:01:00Z",
                freshness=ObservationFreshness.STALE,
            )
        )

        latest = store.latest("workspace-a", "api")
        self.assertIsNotNone(latest)
        self.assertIs(latest.freshness, ObservationFreshness.STALE)

    def test_observed_state_lists_latest_per_workspace_subject(self):
        self.stores.workspace.create(
            WorkspaceRecord(workspace_id="workspace-a", name="Demo")
        )
        store = self.stores.observed_state
        store.put(
            ObservationRecord(
                observation_id="obs-api-1",
                workspace_id="workspace-a",
                subject_id="api",
                status=ObservationStatus.STARTING,
                observed_at="2026-07-15T00:00:00Z",
            )
        )
        store.put(
            ObservationRecord(
                observation_id="obs-api-2",
                workspace_id="workspace-a",
                subject_id="api",
                status=ObservationStatus.HEALTHY,
                observed_at="2026-07-15T00:01:00Z",
            )
        )
        store.put(
            ObservationRecord(
                observation_id="obs-router",
                workspace_id="workspace-a",
                subject_id="router",
                status=ObservationStatus.UNKNOWN,
                observed_at="2026-07-15T00:02:00Z",
                freshness=ObservationFreshness.STALE,
            )
        )

        self.assertEqual(
            [
                (record.subject_id, record.status.value, record.freshness.value)
                for record in store.latest_for_workspace("workspace-a")
            ],
            [("api", "healthy", "fresh"), ("router", "unknown", "stale")],
        )

    def test_instance_registry_lists_by_owner_and_updates_lifecycle(self):
        store = self.stores.instance_registry
        store.register(
            InstanceRecord(
                instance_id="instance-a",
                owner_id="jacob",
                lifecycle=WorkspaceLifecycle.CREATED,
            )
        )
        updated = store.set_lifecycle("instance-a", WorkspaceLifecycle.RUNNING)

        self.assertEqual(updated.lifecycle, WorkspaceLifecycle.RUNNING)
        self.assertEqual([record.instance_id for record in store.list_for_owner("jacob")], ["instance-a"])

    def test_secret_reference_store_never_accepts_secret_values(self):
        store = self.stores.secret_references
        record = store.assign(
            SecretReferenceRecord(
                secret_ref="secret://cloudflare-token",
                owner_id="jacob",
                purpose="cloudflare tunnel",
                assigned_at="2026-07-15T00:00:00Z",
            )
        )

        self.assertTrue(store.exists("secret://cloudflare-token"))
        self.assertFalse(hasattr(record, "secret_value"))

    def test_postgres_schema_is_the_durable_target_and_excludes_secret_values(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS cpk_workspaces", POSTGRES_SCHEMA)
        self.assertIn("CREATE TABLE IF NOT EXISTS cpk_graph_versions", POSTGRES_SCHEMA)
        self.assertIn("CREATE TABLE IF NOT EXISTS cpk_secret_references", POSTGRES_SCHEMA)
        self.assertNotIn("secret_value", POSTGRES_SCHEMA)


if __name__ == "__main__":
    unittest.main()
