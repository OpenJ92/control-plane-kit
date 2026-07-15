from unittest import main, skipUnless

from control_plane_kit import InstanceReadService, compile_recipe, create_instance_read_app
from control_plane_kit.stores import GraphVersionRecord, WorkspaceRecord
from examples.http_block_compositions import active_router_recipe
from tests.postgres_case import PostgresStoreTestCase

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


@skipUnless(TestClient is not None, "FastAPI optional dependency is not installed")
class InstanceReadFastAPITests(PostgresStoreTestCase):
    def _client(self, *, token: str = ""):
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
        read_service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            observed_state_store=self.stores.observed_state,
        )
        return TestClient(create_instance_read_app(read_service, token=token))

    def test_workspace_route_returns_workspace_summary(self):
        client = self._client()

        response = client.get("/instances/workspace-a/workspace")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workspace_id"], "workspace-a")
        self.assertEqual(response.json()["current_graph_id"], "graph-current")

    def test_current_graph_route_redacts_addresses_by_default(self):
        client = self._client()

        response = client.get("/instances/workspace-a/graphs/current")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("http://", str(response.json()))
        self.assertNotIn("env_assignments", str(response.json()))

    def test_activity_and_observed_state_routes_are_read_only_shapes(self):
        client = self._client()

        activity = client.get("/instances/workspace-a/activity?limit=3")
        observed = client.get("/instances/workspace-a/observed-state?limit=3")

        self.assertEqual(activity.status_code, 200)
        self.assertEqual(activity.json(), {"workspace_id": "workspace-a", "limit": 3, "sessions": []})
        self.assertEqual(observed.status_code, 200)
        self.assertEqual(observed.json(), {"workspace_id": "workspace-a", "limit": 3, "observations": []})

    def test_activity_and_observed_state_routes_validate_limits(self):
        client = self._client()

        activity = client.get("/instances/workspace-a/activity?limit=0")
        observed = client.get("/instances/workspace-a/observed-state?limit=-1")

        self.assertEqual(activity.status_code, 422)
        self.assertEqual(activity.json()["detail"], "limit must be a positive integer")
        self.assertEqual(observed.status_code, 422)
        self.assertEqual(observed.json()["detail"], "limit must be a positive integer")

    def test_activity_and_observed_state_routes_reject_unknown_workspace(self):
        client = self._client()

        activity = client.get("/instances/missing-workspace/activity")
        observed = client.get("/instances/missing-workspace/observed-state")

        self.assertEqual(activity.status_code, 404)
        self.assertEqual(observed.status_code, 404)

    def test_control_surface_route_returns_declared_routes(self):
        client = self._client()

        response = client.get("/instances/workspace-a/control-surface")

        self.assertEqual(response.status_code, 200)
        router = {
            node["node_id"]: node
            for node in response.json()["nodes"]
        }["router"]
        capabilities = {
            capability["name"]: capability
            for capability in router["capabilities"]
        }
        self.assertEqual(capabilities["switchable"]["control_routes"]["name"], "targets")

    def test_token_protects_instance_read_routes(self):
        client = self._client(token="secret")

        self.assertEqual(client.get("/instances/workspace-a/workspace").status_code, 401)
        self.assertEqual(
            client.get("/instances/workspace-a/workspace", headers={"Authorization": "Bearer secret"}).status_code,
            200,
        )

    def test_unassigned_desired_graph_returns_not_found(self):
        client = self._client()

        response = client.get("/instances/workspace-a/graphs/desired")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "desired graph not assigned")


if __name__ == "__main__":
    main()
