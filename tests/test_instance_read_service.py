from unittest import main

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.read_services import InstanceReadService, ReadModelError
from control_plane_kit.stores import GraphVersionRecord, WorkspaceRecord
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
