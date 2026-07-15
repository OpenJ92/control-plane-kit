from unittest import main

from control_plane_kit import (
    InstanceReadService,
    compile_recipe,
)
from control_plane_kit.stores import GraphVersionRecord, WorkspaceRecord
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


if __name__ == "__main__":
    main()
