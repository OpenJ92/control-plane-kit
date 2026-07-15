from unittest import main, skipUnless

from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    CapabilityName,
    DeploymentRecipe,
    DockerRuntime,
    InstanceReadService,
    PlanOnlyImplementation,
    Protocol,
    ProxyBlock,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
    create_instance_read_app,
)
from control_plane_kit.stores import GraphVersionRecord, OperationSessionRecord, WorkspaceRecord
from tests.postgres_case import PostgresStoreTestCase

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


@skipUnless(TestClient is not None, "FastAPI optional dependency is not installed")
class InstanceReadFastAPITests(PostgresStoreTestCase):
    def test_configured_token_protects_instance_reads(self):
        client = TestClient(create_instance_read_app(self._service_with_graph(), token="secret"))

        self.assertEqual(client.get("/workspaces/workspace-a").status_code, 401)
        self.assertEqual(
            client.get("/workspaces/workspace-a", headers={"Authorization": "Bearer secret"}).status_code,
            200,
        )
        self.assertEqual(
            client.get("/workspaces/workspace-a", headers={"X-Control-Plane-Token": "secret"}).status_code,
            200,
        )

    def test_workspace_and_graph_routes_return_redacted_descriptors(self):
        client = TestClient(create_instance_read_app(self._service_with_graph()))

        workspace = client.get("/workspaces/workspace-a").json()
        current = client.get("/workspaces/workspace-a/graphs/current").json()

        self.assertEqual(workspace["workspace"]["workspace_id"], "workspace-a")
        self.assertEqual(current["graph_name"], "control-surface")
        self.assertEqual(current["graph_descriptor"]["nodes"]["api-router"]["environment"], "<redacted>")
        self.assertNotIn("http://api-v1", str(current))

    def test_projection_routes_delegate_to_read_service(self):
        client = TestClient(create_instance_read_app(self._service_with_graph()))

        operator_graph = client.get("/workspaces/workspace-a/operator-graph").json()
        control_surface = client.get("/workspaces/workspace-a/control-surface").json()

        self.assertTrue(operator_graph["assigned"])
        self.assertEqual(operator_graph["operator_graph"]["name"], "control-surface")
        router = _node(control_surface, "api-router")
        self.assertEqual(
            [capability["name"] for capability in router["capabilities"]],
            ["health-checkable", "switchable"],
        )
        self.assertEqual([route_set["name"] for route_set in router["control_route_sets"]], ["common-status", "targets"])

    def test_activity_and_observed_state_routes_are_read_only(self):
        service = self._service_with_graph()
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Inspect",
                status="open",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        client = TestClient(create_instance_read_app(service))

        activity = client.get("/workspaces/workspace-a/activity?limit=1").json()
        observed = client.get("/workspaces/workspace-a/observed-state").json()

        self.assertEqual([session["session_id"] for session in activity["sessions"]], ["session-a"])
        self.assertEqual(observed["observations"], [])

    def test_read_model_errors_map_to_http_statuses(self):
        client = TestClient(create_instance_read_app(self._service_with_graph()))

        missing = client.get("/workspaces/missing")
        bad_pointer = client.get("/workspaces/workspace-a/operator-graph?pointer=future")
        bad_limit = client.get("/workspaces/workspace-a/activity?limit=0")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"], "missing workspace 'missing'")
        self.assertEqual(bad_pointer.status_code, 400)
        self.assertEqual(bad_pointer.json()["detail"], "unknown graph pointer 'future'")
        self.assertEqual(bad_limit.status_code, 400)
        self.assertEqual(bad_limit.json()["detail"], "limit must be positive, got 0")

    def test_unconfigured_activity_store_returns_service_unavailable(self):
        self._save_graph_workspace()
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
        )
        client = TestClient(create_instance_read_app(service))

        response = client.get("/workspaces/workspace-a/activity")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "activity history store is not configured")

    def _service_with_graph(self) -> InstanceReadService:
        self._save_graph_workspace()
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            observed_state_store=self.stores.observed_state,
        )

    def _save_graph_workspace(self) -> None:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=_control_surface_graph(),
                created_by="jacob",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")


def _control_surface_graph():
    target = ProxyBlock(
        spec=BlockSpec("api-v1", display_name="API v1"),
        implementation=PlanOnlyImplementation(kind="plan-api", output_urls={"internal": "http://api-v1:8080"}),
        sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    router = ProxyBlock(
        spec=BlockSpec(
            "api-router",
            display_name="API Router",
            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.SWITCHABLE),
        ),
        implementation=PlanOnlyImplementation(kind="plan-router", output_urls={"internal": "http://router:8080"}),
        sockets=BlockSockets(
            requirements=(RequirementSocket("active", Protocol.HTTP, ("ACTIVE_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    return compile_recipe(
        DeploymentRecipe(
            "control-surface",
            DockerRuntime(children=(target, router, SocketConnection("api-v1", "internal", "api-router", "active"))),
        )
    )


def _node(payload: dict[str, object], node_id: str) -> dict[str, object]:
    for node in payload["nodes"]:
        if node["node_id"] == node_id:
            return node
    raise AssertionError(f"missing node {node_id!r}")


if __name__ == "__main__":
    main()
