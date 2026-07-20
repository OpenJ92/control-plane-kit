from unittest import main

from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    CapabilityName,
    DeploymentRecipe,
    DockerRuntime,
    PlanOnlyImplementation,
    Protocol,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.mcp_read import (
    McpReadError,
    ReadOnlyMcpAdapter,
)
from control_plane_kit.read_services import (
    InstanceReadService,
)
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from tests.postgres_case import PostgresStoreTestCase


class McpReadAdapterTests(PostgresStoreTestCase):
    def test_list_tools_is_read_only_and_deterministic(self):
        adapter = ReadOnlyMcpAdapter(self._service_with_graph())

        tools = adapter.list_tools()

        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "get_activity_timeline",
                "get_control_surface",
                "get_current_graph",
                "get_desired_graph",
                "get_observed_state",
                "get_operator_graph",
                "get_plan_detail",
                "get_session_detail",
                "get_workspace",
                "list_open_sessions",
                "list_pending_approvals",
            ],
        )
        self.assertNotIn("mutate", str(tools))
        self.assertNotIn("execute", str(tools))
        self.assertEqual(tools[0]["input_schema"]["additionalProperties"], False)

    def test_workspace_tool_returns_mcp_shaped_json_content(self):
        adapter = ReadOnlyMcpAdapter(self._service_with_graph())

        result = adapter.call_tool("get_workspace", {"workspace_id": "workspace-a"})

        self.assertFalse(result["is_error"])
        self.assertEqual(result["tool"], "get_workspace")
        self.assertEqual(result["content"][0]["type"], "json")
        self.assertEqual(result["content"][0]["json"]["workspace"]["workspace_id"], "workspace-a")

    def test_graph_and_control_surface_tools_delegate_to_read_service(self):
        adapter = ReadOnlyMcpAdapter(self._service_with_graph())

        graph = adapter.call_tool("get_current_graph", {"workspace_id": "workspace-a"})["content"][0]["json"]
        surface = adapter.call_tool(
            "get_control_surface",
            {"workspace_id": "workspace-a", "pointer": "current"},
        )["content"][0]["json"]

        self.assertEqual(graph["graph_name"], "mcp-demo")
        bindings = graph["graph_descriptor"]["nodes"]["api-router"]["environment_bindings"]
        self.assertTrue(bindings)
        self.assertTrue(
            all(
                binding["value"] == "<redacted>"
                for binding in bindings
            )
        )
        self.assertNotIn("http://api-v1", str(graph))
        router = _node(surface, "api-router")
        self.assertEqual([capability["name"] for capability in router["capabilities"]], ["health-checkable"])

    def test_activity_timeline_tool_uses_bounded_limit(self):
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
        adapter = ReadOnlyMcpAdapter(service)

        result = adapter.call_tool("get_activity_timeline", {"workspace_id": "workspace-a", "limit": 1})

        self.assertEqual(result["content"][0]["json"]["limit"], 1)
        self.assertEqual([session["session_id"] for session in result["content"][0]["json"]["sessions"]], ["session-a"])

    def test_unknown_or_mutation_like_tools_fail_closed(self):
        adapter = ReadOnlyMcpAdapter(self._service_with_graph())

        with self.assertRaisesRegex(McpReadError, "unknown read-only tool 'mutate_graph'"):
            adapter.call_tool("mutate_graph", {"workspace_id": "workspace-a"})

        with self.assertRaisesRegex(McpReadError, "unknown arguments"):
            adapter.call_tool(
                "get_workspace",
                {"workspace_id": "workspace-a", "execute": True},
            )

    def test_focused_tools_delegate_identifiers_and_bounds(self):
        adapter = ReadOnlyMcpAdapter(FocusedMcpService())

        calls = (
            adapter.call_tool(
                "list_open_sessions",
                {"workspace_id": "workspace-a", "limit": 2, "offset": 3},
            ),
            adapter.call_tool(
                "get_session_detail",
                {"workspace_id": "workspace-a", "session_id": "session-a", "limit": 4},
            ),
            adapter.call_tool(
                "get_plan_detail",
                {"workspace_id": "workspace-a", "plan_id": "plan-a", "limit": 5},
            ),
            adapter.call_tool(
                "list_pending_approvals",
                {"workspace_id": "workspace-a", "limit": 6, "offset": 7},
            ),
        )

        self.assertEqual(
            [result["content"][0]["json"]["call"] for result in calls],
            [
                ["open_sessions", "workspace-a", 2, 3],
                ["session_detail", "workspace-a", "session-a", 4],
                ["plan_detail", "workspace-a", "plan-a", 5],
                ["pending_approvals", "workspace-a", 6, 7],
            ],
        )

    def test_focused_tool_arguments_enforce_declared_bounds(self):
        adapter = ReadOnlyMcpAdapter(FocusedMcpService())

        invalid_calls = (
            ("list_open_sessions", {"workspace_id": "workspace-a", "limit": True}),
            ("list_open_sessions", {"workspace_id": "workspace-a", "limit": 0}),
            ("list_open_sessions", {"workspace_id": "workspace-a", "limit": 101}),
            ("list_open_sessions", {"workspace_id": "workspace-a", "offset": -1}),
            ("get_session_detail", {"workspace_id": "workspace-a", "session_id": "  "}),
            ("get_plan_detail", {"workspace_id": "  ", "plan_id": "plan-a"}),
        )
        for tool_name, arguments in invalid_calls:
            with self.subTest(tool_name=tool_name, arguments=arguments):
                with self.assertRaises(McpReadError):
                    adapter.call_tool(tool_name, arguments)

    def test_argument_and_service_errors_are_readable(self):
        adapter = ReadOnlyMcpAdapter(self._service_with_graph())

        with self.assertRaisesRegex(McpReadError, "workspace_id is required"):
            adapter.call_tool("get_workspace", {})
        with self.assertRaisesRegex(McpReadError, "limit must be an integer"):
            adapter.call_tool("get_activity_timeline", {"workspace_id": "workspace-a", "limit": "many"})
        with self.assertRaisesRegex(McpReadError, "missing workspace 'missing'"):
            adapter.call_tool("get_workspace", {"workspace_id": "missing"})

    def _service_with_graph(self) -> InstanceReadService:
        self.stores.workspace.create(WorkspaceRecord(workspace_id="workspace-a", name="Demo"))
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=_mcp_graph(),
                created_by="jacob",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            observed_state_store=self.stores.observed_state,
        )


def _mcp_graph():
    target = ProxyBlock(
        spec=BlockSpec("api-v1", display_name="API v1"),
        implementation=PlanOnlyImplementation(kind="plan-api", output_urls={"internal": "http://api-v1:8080"}),
        sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    router = ProxyBlock(
        spec=BlockSpec(
            "api-router",
            display_name="API Router",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
        ),
        implementation=PlanOnlyImplementation(kind="plan-router", output_urls={"internal": "http://router:8080"}),
        sockets=BlockSockets(
            requirements=(RequirementSocket("active", Protocol.HTTP, ("ACTIVE_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    return compile_recipe(
        DeploymentRecipe(
            "mcp-demo",
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


class FocusedMcpService:
    def open_sessions(self, workspace_id: str, *, limit: int, offset: int):
        return DescriptorResult({"call": ["open_sessions", workspace_id, limit, offset]})

    def session_detail(self, workspace_id: str, session_id: str, *, limit: int):
        return DescriptorResult(
            {"call": ["session_detail", workspace_id, session_id, limit]}
        )

    def plan_detail(self, workspace_id: str, plan_id: str, *, limit: int):
        return DescriptorResult({"call": ["plan_detail", workspace_id, plan_id, limit]})

    def pending_approvals(self, workspace_id: str, *, limit: int, offset: int):
        return DescriptorResult(
            {"call": ["pending_approvals", workspace_id, limit, offset]}
        )


if __name__ == "__main__":
    main()
