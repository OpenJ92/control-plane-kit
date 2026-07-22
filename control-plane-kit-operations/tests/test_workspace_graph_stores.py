from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    GraphDescriptorCodec,
    compile_topology,
)
from control_plane_kit_core.types import Protocol, WorkspaceLifecycle
from control_plane_kit_operations.postgres import (
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    WorkspaceRecord,
)


class WorkspaceGraphStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def test_workspace_tracks_lifecycle_current_and_desired_graph_pointers(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            unit_of_work.stores.workspaces.set_lifecycle(
                "workspace-a",
                WorkspaceLifecycle.RUNNING,
            )
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                "graph-current",
            )
            record = unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                "graph-desired",
            )
            unit_of_work.commit()

        self.assertEqual(record.lifecycle, WorkspaceLifecycle.RUNNING)
        self.assertEqual(record.current_graph_id, "graph-current")
        self.assertEqual(record.desired_graph_id, "graph-desired")

        with self.unit_of_work() as unit_of_work:
            stored = unit_of_work.stores.workspaces.get("workspace-a")
            self.assertEqual(stored, record)

    def test_graph_store_preserves_typed_descriptor_and_latest_version(self) -> None:
        graph = self.product_graph("first")
        second = DeploymentGraph("second")

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            first_record = GraphVersionRecord.from_graph(
                graph_id="graph-1",
                workspace_id="workspace-a",
                version=1,
                graph=graph,
                created_by="operator-a",
                created_at="2026-07-22T10:00:00Z",
            )
            second_record = GraphVersionRecord.from_graph(
                graph_id="graph-2",
                workspace_id="workspace-a",
                version=2,
                graph=second,
                created_by="operator-a",
                created_at="2026-07-22T10:05:00Z",
            )
            unit_of_work.stores.graphs.save(first_record)
            unit_of_work.stores.graphs.save(second_record)
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            restored = unit_of_work.stores.graphs.get("graph-1")
            latest = unit_of_work.stores.graphs.latest_for_workspace("workspace-a")
            self.assertEqual(latest, second_record)
            self.assertEqual(
                GraphDescriptorCodec().decode(restored.graph_descriptor),
                graph,
            )

    def test_next_version_and_compare_and_set_are_workspace_scoped(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-1",
                    workspace_id="workspace-a",
                    version=1,
                    graph=DeploymentGraph("first"),
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:00Z",
                )
            )
            self.assertEqual(unit_of_work.stores.graphs.next_version_for_workspace("workspace-a"), 2)
            unit_of_work.stores.workspaces.set_current_graph("workspace-a", "graph-1")
            stale = unit_of_work.stores.workspaces.compare_and_set_current_graph(
                "workspace-a",
                expected_graph_id="graph-stale",
                replacement_graph_id="graph-2",
            )
            advanced = unit_of_work.stores.workspaces.compare_and_set_current_graph(
                "workspace-a",
                expected_graph_id="graph-1",
                replacement_graph_id="graph-2",
            )
            unit_of_work.commit()

        self.assertIsNone(stale)
        self.assertIsNotNone(advanced)
        self.assertEqual(advanced.current_graph_id, "graph-2")

    def test_workspace_and_graph_writes_roll_back_together(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            unit_of_work.stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-1",
                    workspace_id="workspace-a",
                    version=1,
                    graph=DeploymentGraph("first"),
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:00Z",
                )
            )

        self.assertEqual(self._row_count("cpk_workspaces"), 0)
        self.assertEqual(self._row_count("cpk_graph_versions"), 0)

    def product_graph(self, name: str) -> DeploymentGraph:
        product = ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "hello-server", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/hello-server",
                "sha256:" + "a" * 64,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name="Hello server",
            description="Server product used for graph store tests.",
        )
        document = ProductDescriptorCodec().encode_document(product)
        block = instantiate_product(
            document.product,
            "hello",
            ProductInstanceConfiguration(),
        )
        return compile_topology(DeploymentTopology(name, DockerRuntime(children=(block,))))

    def _row_count(self, table: str) -> int:
        if table not in {"cpk_workspaces", "cpk_graph_versions"}:
            raise ValueError(f"unexpected table {table!r}")
        return self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
