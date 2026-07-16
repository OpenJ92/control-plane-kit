from unittest import main

from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    CapabilityName,
    DeploymentRecipe,
    DockerRuntime,
    InstanceReadService,
    McpReadError,
    PlanOnlyImplementation,
    Protocol,
    ProxyBlock,
    ProviderSocket,
    ReadOnlyMcpAdapter,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
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
                "get_workspace",
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
        self.assertEqual(graph["graph_descriptor"]["nodes"]["api-router"]["environment"], "<redacted>")
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


if __name__ == "__main__":
    main()
