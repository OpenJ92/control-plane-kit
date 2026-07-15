from unittest import main

from control_plane_kit import (
    InstanceReadService,
    compile_recipe,
)
from control_plane_kit.stores import GraphVersionRecord, WorkspaceRecord
from control_plane_kit.stores.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRecord,
    ObservationRecord,
    OperationActionRecord,
    OperationSessionRecord,
)
from examples.app_with_postgres import recipe as app_recipe
from examples.http_block_compositions import active_router_recipe
from tests.postgres_case import PostgresStoreTestCase


class InstanceReadServiceTests(PostgresStoreTestCase):
    def test_workspace_read_model_represents_empty_workspace(self):
        self.stores.workspace.create(
            WorkspaceRecord(
                workspace_id="workspace-a",
                name="Demo",
                metadata={"owner": "jacob"},
            )
        )

        read_model = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
        ).workspace("workspace-a")

        self.assertEqual(
            read_model.descriptor(),
            {
                "workspace_id": "workspace-a",
                "name": "Demo",
                "lifecycle": "created",
                "metadata": {"owner": "jacob"},
                "current_graph_id": None,
                "desired_graph_id": None,
            },
        )

    def test_workspace_read_model_includes_current_and_desired_graphs(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        current = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=compile_recipe(app_recipe()),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        desired = GraphVersionRecord.from_graph(
            graph_id="graph-desired",
            workspace_id="workspace-a",
            version=2,
            graph=compile_recipe(active_router_recipe()),
            created_by="jacob",
            created_at="2026-07-15T00:01:00Z",
        )
        self.stores.graph_topology.save(current)
        self.stores.graph_topology.save(desired)
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-desired")

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
        ).workspace("workspace-a").descriptor()

        self.assertEqual(descriptor["current_graph_id"], "graph-current")
        self.assertEqual(descriptor["desired_graph_id"], "graph-desired")
        self.assertEqual(descriptor["current_graph"]["name"], "app-with-postgres")
        self.assertEqual(descriptor["desired_graph"]["name"], "http-active-router-composition-app-v1")
        self.assertEqual(descriptor["current_graph"]["operator_graph"]["name"], "app-with-postgres")
        self.assertEqual(
            descriptor["desired_graph"]["operator_graph"]["name"],
            "http-active-router-composition-app-v1",
        )

    def test_workspace_read_model_redacts_graph_addresses_by_default(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        graph = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=compile_recipe(app_recipe()),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        self.stores.graph_topology.save(graph)
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
        ).current_graph("workspace-a").descriptor()

        operator_graph = descriptor["operator_graph"]
        postgres = {
            node["node_id"]: node
            for node in operator_graph["nodes"]
        }["postgres"]
        self.assertNotIn("url", postgres["endpoints"][0])
        self.assertNotIn("env_assignments", operator_graph["edges"][0])

    def test_workspace_read_model_can_include_addresses_explicitly(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        graph = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=compile_recipe(app_recipe()),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        self.stores.graph_topology.save(graph)
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
            include_addresses=True,
        ).current_graph("workspace-a").descriptor()

        operator_graph = descriptor["operator_graph"]
        postgres = {
            node["node_id"]: node
            for node in operator_graph["nodes"]
        }["postgres"]
        self.assertIn("url", postgres["endpoints"][0])
        self.assertIn("env_assignments", operator_graph["edges"][0])

    def test_control_surface_expands_capability_route_sets(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        graph = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=compile_recipe(active_router_recipe()),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        self.stores.graph_topology.save(graph)
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
        ).control_surface("workspace-a").descriptor()

        router = {
            node["node_id"]: node
            for node in descriptor["nodes"]
        }["router"]
        capabilities = {
            capability["name"]: capability
            for capability in router["capabilities"]
        }
        self.assertEqual(capabilities["switchable"]["route_set"], "targets")
        route_names = {
            route["name"]
            for route in capabilities["switchable"]["control_routes"]["routes"]
        }
        self.assertIn("active-target", route_names)
        self.assertIn("drain-target", route_names)

    def test_control_surface_summarizes_contracts_without_urls(self):
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        graph = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=compile_recipe(active_router_recipe()),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        self.stores.graph_topology.save(graph)
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
        ).control_surface("workspace-a").descriptor()

        router = {
            node["node_id"]: node
            for node in descriptor["nodes"]
        }["router"]
        self.assertEqual(
            router["providers"],
            [{"name": "internal", "protocol": "http", "endpoint_available": True}],
        )
        self.assertEqual(
            router["requirements"],
            [
                {
                    "name": "active",
                    "protocol": "http",
                    "required": True,
                    "env_bindings": ["ACTIVE_TARGET_URL"],
                    "fulfilled": True,
                    "provider": {"node_id": "app-v1", "socket": "internal"},
                }
            ],
        )
        self.assertNotIn("http://", str(descriptor))
        self.assertNotIn("postgresql://", str(descriptor))

    def test_activity_timeline_is_bounded_and_structured(self):
        history = self.stores.activity_history
        for index in range(3):
            history.add_session(
                OperationSessionRecord(
                    session_id=f"session-{index}",
                    workspace_id="workspace-a",
                    actor_id="jacob",
                    title=f"Session {index}",
                    status="open",
                    created_at=f"2026-07-15T00:0{index}:00Z",
                )
            )
        history.add_action(
            OperationActionRecord(
                action_id="action-1",
                session_id="session-2",
                ordinal=1,
                action_type="inspect",
                actor_id="jacob",
                payload={"node_id": "api"},
                created_at="2026-07-15T00:03:00Z",
            )
        )
        history.add_approval(
            ApprovalRecord(
                approval_id="approval-1",
                session_id="session-2",
                target_id="plan-1",
                actor_id="manager",
                decision="approved",
                scope="plan:approve",
                decided_at="2026-07-15T00:04:00Z",
            )
        )
        history.add_plan(
            ActivityPlanRecord(
                plan_id="plan-1",
                session_id="session-2",
                base_graph_id="graph-1",
                desired_graph_id="graph-2",
                status="planned",
                created_at="2026-07-15T00:05:00Z",
            )
        )
        history.add_run(
            ActivityRunRecord(
                run_id="run-1",
                plan_id="plan-1",
                status="open",
                started_at="2026-07-15T00:06:00Z",
            )
        )
        history.add_event(
            ActivityEventRecord(
                event_id="event-1",
                run_id="run-1",
                ordinal=1,
                event_type="step-started",
                occurred_at="2026-07-15T00:07:00Z",
                payload={"step": "start-api"},
            )
        )

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
        ).activity_timeline("workspace-a", limit=2).descriptor()

        self.assertEqual(descriptor["limit"], 2)
        self.assertEqual([session["session_id"] for session in descriptor["sessions"]], ["session-2", "session-1"])
        newest = descriptor["sessions"][0]
        self.assertEqual(newest["actions"][0]["action_type"], "inspect")
        self.assertEqual(newest["approvals"][0]["decision"], "approved")
        self.assertEqual(newest["plans"][0]["runs"][0]["events"][0]["event_type"], "step-started")

    def test_observed_state_reads_latest_subject_state(self):
        observed = self.stores.observed_state
        observed.put(
            ObservationRecord(
                observation_id="obs-api-old",
                workspace_id="workspace-a",
                subject_id="api",
                status="starting",
                observed_at="2026-07-15T00:00:00Z",
            )
        )
        observed.put(
            ObservationRecord(
                observation_id="obs-api-new",
                workspace_id="workspace-a",
                subject_id="api",
                status="healthy",
                observed_at="2026-07-15T00:01:00Z",
                payload={"health_path": "/health"},
            )
        )
        observed.put(
            ObservationRecord(
                observation_id="obs-router",
                workspace_id="workspace-a",
                subject_id="router",
                status="unknown",
                observed_at="2026-07-15T00:02:00Z",
                stale=True,
            )
        )

        descriptor = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
            observed_state_store=self.stores.observed_state,
        ).observed_state("workspace-a").descriptor()

        observations = {
            observation["subject_id"]: observation
            for observation in descriptor["observations"]
        }
        self.assertEqual(observations["api"]["status"], "healthy")
        self.assertEqual(observations["api"]["payload"], {"health_path": "/health"})
        self.assertTrue(observations["router"]["stale"])


if __name__ == "__main__":
    main()
