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
from control_plane_kit.read_services import ReadModelError
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
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
                status=OperationSessionStatus.OPEN,
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

    def test_focused_routes_delegate_with_explicit_bounds(self):
        service = FocusedReadService()
        client = TestClient(create_instance_read_app(service))

        sessions = client.get(
            "/workspaces/workspace-a/sessions?limit=10&offset=2"
        ).json()
        detail = client.get(
            "/workspaces/workspace-a/sessions/session-a?limit=11"
        ).json()
        plan = client.get(
            "/workspaces/workspace-a/plans/plan-a?limit=12"
        ).json()
        approvals = client.get(
            "/workspaces/workspace-a/approvals/pending?limit=13&offset=3"
        ).json()

        self.assertEqual(sessions["call"], ["open_sessions", "workspace-a", 10, 2])
        self.assertEqual(detail["call"], ["session_detail", "workspace-a", "session-a", 11])
        self.assertEqual(plan["call"], ["plan_detail", "workspace-a", "plan-a", 12])
        self.assertEqual(approvals["call"], ["pending_approvals", "workspace-a", 13, 3])

    def test_focused_routes_share_auth_and_workspace_safe_errors(self):
        client = TestClient(
            create_instance_read_app(FocusedReadService(fail=True), token="secret")
        )

        unauthorized = client.get("/workspaces/workspace-a/sessions")
        missing = client.get(
            "/workspaces/workspace-a/plans/foreign",
            headers={"Authorization": "Bearer secret"},
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["detail"],
            "missing plan 'foreign' in workspace 'workspace-a'",
        )

    def test_instance_read_app_exposes_no_workflow_mutation_routes(self):
        app = create_instance_read_app(FocusedReadService())
        workflow_paths = {
            route.path: route.methods
            for route in app.routes
            if route.path.startswith("/workspaces/")
        }

        self.assertTrue(workflow_paths)
        self.assertTrue(
            all(methods == {"GET"} for methods in workflow_paths.values())
        )

    def test_stale_recovery_graph_truth_maps_to_conflict(self):
        client = TestClient(
            create_instance_read_app(
                FocusedReadService(
                    plan_error="plan 'plan-a' references graph truth outside workspace"
                )
            )
        )

        response = client.get("/workspaces/workspace-a/plans/plan-a")

        self.assertEqual(response.status_code, 409)

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


class DescriptorResult:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def descriptor(self) -> dict[str, object]:
        return self._payload


class FocusedReadService:
    def __init__(
        self,
        *,
        fail: bool = False,
        plan_error: str | None = None,
    ) -> None:
        self._fail = fail
        self._plan_error = plan_error

    def open_sessions(self, workspace_id: str, *, limit: int, offset: int):
        return DescriptorResult({"call": ["open_sessions", workspace_id, limit, offset]})

    def session_detail(self, workspace_id: str, session_id: str, *, limit: int):
        return DescriptorResult(
            {"call": ["session_detail", workspace_id, session_id, limit]}
        )

    def plan_detail(self, workspace_id: str, plan_id: str, *, limit: int):
        if self._plan_error is not None:
            raise ReadModelError(self._plan_error)
        if self._fail:
            raise ReadModelError(
                f"missing plan {plan_id!r} in workspace {workspace_id!r}"
            )
        return DescriptorResult({"call": ["plan_detail", workspace_id, plan_id, limit]})

    def pending_approvals(self, workspace_id: str, *, limit: int, offset: int):
        return DescriptorResult(
            {"call": ["pending_approvals", workspace_id, limit, offset]}
        )


if __name__ == "__main__":
    main()
