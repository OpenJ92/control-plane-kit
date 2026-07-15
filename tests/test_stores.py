import unittest

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    InstanceRecord,
    ObservationRecord,
    OperationActionRecord,
    OperationSessionRecord,
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
                status="open",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        store.add_action(
            OperationActionRecord(
                action_id="action-b",
                session_id="session-a",
                ordinal=2,
                action_type="connect_socket",
                actor_id="jacob",
            )
        )
        store.add_action(
            OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type="add_block",
                actor_id="jacob",
            )
        )

        self.assertEqual(
            [record.action_id for record in store.actions_for_session("session-a")],
            ["action-a", "action-b"],
        )

    def test_observed_state_is_separate_from_graph_truth(self):
        store = self.stores.observed_state
        store.put(
            ObservationRecord(
                observation_id="obs-1",
                workspace_id="workspace-a",
                subject_id="api",
                status="healthy",
                observed_at="2026-07-15T00:00:00Z",
            )
        )
        store.put(
            ObservationRecord(
                observation_id="obs-2",
                workspace_id="workspace-a",
                subject_id="api",
                status="stale",
                observed_at="2026-07-15T00:01:00Z",
                stale=True,
            )
        )

        latest = store.latest("workspace-a", "api")
        self.assertIsNotNone(latest)
        self.assertTrue(latest.stale)

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
