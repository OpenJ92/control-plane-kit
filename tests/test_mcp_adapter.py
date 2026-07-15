from unittest import main

from control_plane_kit import InstanceReadService, ReadOnlyMcpAdapter, compile_recipe
from control_plane_kit.stores import GraphVersionRecord, WorkspaceRecord
from examples.http_block_compositions import active_router_recipe
from tests.postgres_case import PostgresStoreTestCase


class ReadOnlyMcpAdapterTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
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
        self.adapter = ReadOnlyMcpAdapter(
            InstanceReadService(
                workspace_store=self.stores.workspace,
                graph_store=self.stores.graph_topology,
                activity_history_store=self.stores.activity_history,
                observed_state_store=self.stores.observed_state,
            )
        )

    def test_tool_descriptors_are_read_only(self):
        descriptors = [tool.descriptor() for tool in self.adapter.list_tools()]
        names = {descriptor["name"] for descriptor in descriptors}

        self.assertEqual(
            names,
            {
                "get_workspace",
                "get_current_graph",
                "get_desired_graph",
                "get_activity_timeline",
                "get_observed_state",
                "get_control_surface",
            },
        )
        self.assertFalse(any("set_" in name or "mutate" in name or "switch" in name for name in names))

    def test_get_workspace_returns_descriptor(self):
        descriptor = self.adapter.call_tool("get_workspace", {"workspace_id": "workspace-a"})

        self.assertEqual(descriptor["workspace_id"], "workspace-a")
        self.assertEqual(descriptor["current_graph_id"], "graph-current")

    def test_get_activity_timeline_is_bounded(self):
        descriptor = self.adapter.call_tool("get_activity_timeline", {"workspace_id": "workspace-a", "limit": 3})

        self.assertEqual(descriptor, {"workspace_id": "workspace-a", "limit": 3, "sessions": []})

    def test_get_control_surface_returns_declared_capabilities(self):
        descriptor = self.adapter.call_tool("get_control_surface", {"workspace_id": "workspace-a"})
        router = {
            node["node_id"]: node
            for node in descriptor["nodes"]
        }["router"]

        self.assertIn("capabilities", router)
        self.assertNotIn("http://", str(descriptor))

    def test_unknown_tool_fails_loudly(self):
        with self.assertRaises(KeyError):
            self.adapter.call_tool("switch_router", {"workspace_id": "workspace-a"})

    def test_missing_workspace_argument_fails_structurally(self):
        with self.assertRaises(ValueError):
            self.adapter.call_tool("get_workspace", {})


if __name__ == "__main__":
    main()
