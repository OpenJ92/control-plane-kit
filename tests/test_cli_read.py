import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from unittest import main

from control_plane_kit import compile_recipe
from control_plane_kit.cli.read import main as read_main
from control_plane_kit.stores import GraphVersionRecord, WorkspaceRecord
from examples.http_block_compositions import active_router_recipe
from tests.postgres_case import PostgresStoreTestCase


class ReadCliTests(PostgresStoreTestCase):
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
        self.database_url = os.environ["CPK_TEST_DATABASE_URL"]

    def test_workspace_command_prints_json_descriptor(self):
        code, stdout, stderr = self._run("workspace", "workspace-a")

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        descriptor = json.loads(stdout)
        self.assertEqual(descriptor["workspace_id"], "workspace-a")
        self.assertEqual(descriptor["current_graph_id"], "graph-current")

    def test_current_graph_command_redacts_addresses_by_default(self):
        code, stdout, _stderr = self._run("current-graph", "workspace-a")

        self.assertEqual(code, 0)
        self.assertNotIn("http://", stdout)
        self.assertNotIn("env_assignments", stdout)

    def test_current_graph_command_includes_addresses_only_when_requested(self):
        code, stdout, _stderr = self._run("--include-addresses", "current-graph", "workspace-a")

        self.assertEqual(code, 0)
        self.assertIn("http://", stdout)
        self.assertIn("env_assignments", stdout)

    def test_control_surface_command_prints_declared_routes(self):
        code, stdout, _stderr = self._run("control-surface", "workspace-a")

        self.assertEqual(code, 0)
        descriptor = json.loads(stdout)
        router = {
            node["node_id"]: node
            for node in descriptor["nodes"]
        }["router"]
        capability_names = {
            capability["name"]
            for capability in router["capabilities"]
        }
        self.assertIn("switchable", capability_names)

    def test_unassigned_desired_graph_returns_nonzero(self):
        code, _stdout, stderr = self._run("desired-graph", "workspace-a")

        self.assertEqual(code, 1)
        self.assertIn("desired-graph is not assigned", stderr)

    def test_invalid_limit_returns_user_error(self):
        code, _stdout, stderr = self._run("activity", "workspace-a", "--limit", "0")

        self.assertEqual(code, 2)
        self.assertIn("limit must be a positive integer", stderr)

    def _run(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = read_main(("--database-url", self.database_url, *args))
        return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    main()
