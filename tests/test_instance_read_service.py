from unittest import main

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.read_services import InstanceReadService, ReadModelError
from control_plane_kit.stores import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRecord,
    GraphVersionRecord,
    ObservationRecord,
    OperationActionRecord,
    OperationSessionRecord,
    WorkspaceRecord,
)
from examples.app_with_postgres import recipe
from tests.postgres_case import PostgresStoreTestCase
from control_plane_kit import compile_recipe


class InstanceReadServiceTests(PostgresStoreTestCase):
    def test_workspace_read_model_includes_current_and_desired_graphs(self):
        service = self._service_with_workspace_and_graphs()

        payload = service.workspace("workspace-a").descriptor()

        self.assertEqual(payload["workspace"]["workspace_id"], "workspace-a")
        self.assertEqual(payload["current_graph"]["graph_id"], "graph-current")
        self.assertEqual(payload["current_graph"]["graph_name"], "current")
        self.assertEqual(payload["desired_graph"]["graph_id"], "graph-desired")
        self.assertEqual(payload["desired_graph"]["graph_name"], "desired")

    def test_graph_descriptor_redacts_addresses_and_environment_values(self):
        service = self._service_with_workspace_and_graphs()

        payload = service.current_graph("workspace-a").descriptor()

        descriptor = payload["graph_descriptor"]
        postgres = descriptor["nodes"]["postgres"]
        api = descriptor["nodes"]["orders-api"]
        edge = descriptor["edges"]["postgres.internal-to-orders-api.DATABASE_URL"]
        self.assertEqual(postgres["endpoints"]["internal"]["url"], "<redacted>")
        self.assertEqual(api["environment"], "<redacted>")
        self.assertEqual(edge["env_assignments"], "<redacted>")
        self.assertNotIn("postgres:postgres", str(descriptor))

    def test_missing_workspace_fails_at_service_boundary(self):
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        with self.assertRaisesRegex(ReadModelError, "missing workspace 'missing'"):
            service.workspace("missing")

    def test_unassigned_graph_pointers_are_explicit(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        payload = service.workspace("workspace-a").descriptor()

        self.assertEqual(payload["current_graph"]["pointer"], "current")
        self.assertFalse(payload["current_graph"]["assigned"])
        self.assertEqual(payload["desired_graph"]["pointer"], "desired")
        self.assertFalse(payload["desired_graph"]["assigned"])

    def test_operator_graph_uses_shared_projection(self):
        service = self._service_with_workspace_and_graphs()

        payload = service.operator_graph("workspace-a").descriptor()

        self.assertTrue(payload["assigned"])
        self.assertIn("operator_graph", payload)
        self.assertEqual(payload["operator_graph"]["name"], "current")
        self.assertEqual(
            [edge["edge_id"] for edge in payload["operator_graph"]["edges"]],
            ["postgres.internal-to-orders-api.DATABASE_URL"],
        )

    def test_unknown_graph_pointer_fails_loudly(self):
        service = self._service_with_workspace_and_graphs()

        with self.assertRaisesRegex(ReadModelError, "unknown graph pointer 'future'"):
            service.operator_graph("workspace-a", pointer="future")

    def test_activity_timeline_is_bounded_and_redacted(self):
        service = self._service_with_activity()

        payload = service.activity_timeline("workspace-a", limit=1).descriptor()

        self.assertEqual(payload["limit"], 1)
        self.assertEqual(len(payload["sessions"]), 1)
        session = payload["sessions"][0]
        self.assertEqual([action["action_id"] for action in session["actions"]], ["action-a"])
        self.assertEqual(session["actions"][0]["payload"]["api_token"], "<redacted>")
        self.assertEqual(session["plans"][0]["payload"]["target_url"], "<redacted>")
        self.assertEqual(session["plans"][0]["runs"][0]["events"][0]["payload"]["password"], "<redacted>")

    def test_activity_timeline_rejects_invalid_limits(self):
        service = self._service_with_activity()

        with self.assertRaisesRegex(ReadModelError, "limit must be positive, got 0"):
            service.activity_timeline("workspace-a", limit=0)

    def test_activity_timeline_requires_workspace_truth(self):
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            observed_state_store=self.stores.observed_state,
        )

        with self.assertRaisesRegex(ReadModelError, "missing workspace 'missing'"):
            service.activity_timeline("missing")

    def test_activity_timeline_requires_configured_activity_store(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        with self.assertRaisesRegex(ReadModelError, "activity history store is not configured"):
            service.activity_timeline("workspace-a")

    def test_activity_timeline_does_not_cross_workspace_boundary(self):
        service = self._service_with_activity()

        payload = service.activity_timeline("workspace-a", limit=10).descriptor()

        self.assertEqual([session["session_id"] for session in payload["sessions"]], ["session-a"])
        self.assertNotIn("session-other", str(payload))

    def test_observed_state_reports_latest_and_stale_markers(self):
        service = self._service_with_observations()

        payload = service.observed_state("workspace-a").descriptor()

        self.assertEqual(
            [(record["subject_id"], record["status"], record["stale"]) for record in payload["observations"]],
            [("api", "healthy", False), ("router", "unknown", True)],
        )
        self.assertEqual(payload["observations"][0]["payload"]["token"], "<redacted>")
        self.assertEqual(payload["observations"][1]["payload"]["details"], "not checked yet")

    def test_observed_state_requires_configured_observed_state_store(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )

        with self.assertRaisesRegex(ReadModelError, "observed state store is not configured"):
            service.observed_state("workspace-a")

    def test_observed_state_does_not_cross_workspace_boundary(self):
        service = self._service_with_observations()

        payload = service.observed_state("workspace-a").descriptor()

        self.assertNotIn("workspace-b", str(payload))
        self.assertNotIn("other-api", str(payload))

    def test_nested_payload_redaction_reaches_lists_and_mappings(self):
        service = self._service_with_nested_payloads()

        payload = service.activity_timeline("workspace-a").descriptor()

        event_payload = payload["sessions"][0]["plans"][0]["runs"][0]["events"][0]["payload"]
        self.assertEqual(event_payload["nested"]["client_secret"], "<redacted>")
        self.assertEqual(event_payload["items"][0]["callback_url"], "<redacted>")
        self.assertEqual(event_payload["items"][0]["label"], "visible")

    def _service_with_workspace_and_graphs(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        current = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=_compiled_graph_named("current"),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        desired = GraphVersionRecord(
            graph_id="graph-desired",
            workspace_id="workspace-a",
            version=2,
            graph_descriptor=DeploymentGraph("desired").descriptor(),
            created_by="jacob",
            created_at="2026-07-15T00:01:00Z",
        )
        self.stores.graph_topology.save(current)
        self.stores.graph_topology.save(desired)
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-desired")
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            observed_state_store=self.stores.observed_state,
        )

    def _service_with_activity(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-b", name="Other"))
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Swap API",
                status="open",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-other",
                workspace_id="workspace-b",
                actor_id="jacob",
                title="Other workspace",
                status="open",
                created_at="2026-07-15T00:00:01Z",
            )
        )
        self.stores.activity_history.add_action(
            OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type="patch_variable",
                actor_id="jacob",
                payload={"api_token": "secret", "note": "visible"},
                created_at="2026-07-15T00:01:00Z",
            )
        )
        self.stores.activity_history.add_action(
            OperationActionRecord(
                action_id="action-b",
                session_id="session-a",
                ordinal=2,
                action_type="check_health",
                actor_id="jacob",
                payload={"note": "bounded away"},
                created_at="2026-07-15T00:02:00Z",
            )
        )
        self.stores.activity_history.add_approval(
            ApprovalRecord(
                approval_id="approval-a",
                session_id="session-a",
                target_id="plan-a",
                actor_id="manager",
                decision="approved",
                scope="admin",
                decided_at="2026-07-15T00:02:30Z",
            )
        )
        self.stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-15T00:03:00Z",
                payload={"target_url": "http://private"},
            )
        )
        self.stores.activity_history.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                status="running",
                started_at="2026-07-15T00:04:00Z",
                metadata={"worker_token": "secret"},
            )
        )
        self.stores.activity_history.add_event(
            ActivityEventRecord(
                event_id="event-a",
                run_id="run-a",
                ordinal=1,
                event_type="step",
                occurred_at="2026-07-15T00:05:00Z",
                payload={"password": "secret"},
            )
        )
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            observed_state_store=self.stores.observed_state,
        )

    def _service_with_observations(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-b", name="Other"))
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-api-old",
                workspace_id="workspace-a",
                subject_id="api",
                status="starting",
                observed_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-api-new",
                workspace_id="workspace-a",
                subject_id="api",
                status="healthy",
                observed_at="2026-07-15T00:01:00Z",
                payload={"token": "secret"},
            )
        )
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-router",
                workspace_id="workspace-a",
                subject_id="router",
                status="unknown",
                observed_at="2026-07-15T00:01:00Z",
                payload={"details": "not checked yet"},
                stale=True,
            )
        )
        self.stores.observed_state.put(
            ObservationRecord(
                observation_id="obs-other",
                workspace_id="workspace-b",
                subject_id="other-api",
                status="healthy",
                observed_at="2026-07-15T00:02:00Z",
            )
        )
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            observed_state_store=self.stores.observed_state,
        )

    def _service_with_nested_payloads(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Nested",
                status="open",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-a",
                desired_graph_id="graph-b",
                status="planned",
                created_at="2026-07-15T00:01:00Z",
            )
        )
        self.stores.activity_history.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                status="running",
                started_at="2026-07-15T00:02:00Z",
            )
        )
        self.stores.activity_history.add_event(
            ActivityEventRecord(
                event_id="event-a",
                run_id="run-a",
                ordinal=1,
                event_type="nested",
                occurred_at="2026-07-15T00:03:00Z",
                payload={
                    "nested": {"client_secret": "secret"},
                    "items": [{"callback_url": "http://private", "label": "visible"}],
                },
            )
        )
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            observed_state_store=self.stores.observed_state,
        )


def _compiled_graph_named(name: str) -> DeploymentGraph:
    graph = compile_recipe(recipe())
    return DeploymentGraph(
        name=name,
        nodes=graph.nodes,
        edges=graph.edges,
        runtimes=graph.runtimes,
    )


if __name__ == "__main__":
    main()
