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
    ProductReference,
    ProductRuntimeContract,
    instantiate_product,
)
from control_plane_kit_core.topology import DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.graph_authoring import (
    GraphAuthoringError,
    GraphAuthoringService,
    SetDesiredGraphCommand,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.records import WorkspaceRecord


class GraphAuthoringTests(unittest.TestCase):
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

    def test_registered_product_graph_becomes_desired_graph_truth(self) -> None:
        document = ProductDescriptorCodec().encode_document(self.product("hello-server"))
        graph = self.graph_from_document(document.product)

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
            unit_of_work.commit()

        result = GraphAuthoringService(
            self.unit_of_work,
            graph_id_factory=lambda: "graph-desired",
            clock=lambda: "2026-07-22T10:05:00Z",
        ).set_desired_graph(
            SetDesiredGraphCommand(
                workspace_id="workspace-a",
                actor_id="operator-a",
                graph=graph,
                expected_desired_graph_id=None,
            )
        )

        self.assertEqual(result.graph_version.graph_id, "graph-desired")
        self.assertEqual(result.graph_version.version, 1)
        self.assertEqual(result.workspace.desired_graph_id, "graph-desired")
        self.assertEqual(result.product_references, (ProductReference.from_document(document),))

        with self.unit_of_work() as unit_of_work:
            stored = unit_of_work.stores.graphs.get("graph-desired")
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            self.assertEqual(stored.graph_descriptor, result.graph_version.graph_descriptor)
            self.assertEqual(workspace.desired_graph_id, "graph-desired")

    def test_unregistered_product_reference_rejects_without_graph_write(self) -> None:
        graph = self.graph_from_document(self.product("hello-server"))

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            unit_of_work.commit()

        with self.assertRaisesRegex(GraphAuthoringError, "unregistered product"):
            GraphAuthoringService(
                self.unit_of_work,
                graph_id_factory=lambda: "graph-desired",
                clock=lambda: "2026-07-22T10:05:00Z",
            ).set_desired_graph(
                SetDesiredGraphCommand(
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    graph=graph,
                    expected_desired_graph_id=None,
                )
            )

        self.assertEqual(self._row_count("cpk_graph_versions"), 0)
        with self.unit_of_work() as unit_of_work:
            self.assertIsNone(
                unit_of_work.stores.workspaces.get("workspace-a").desired_graph_id
            )

    def test_revoked_product_reference_is_not_authorable(self) -> None:
        document = ProductDescriptorCodec().encode_document(self.product("hello-server"))
        graph = self.graph_from_document(document.product)

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            registered = unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
            unit_of_work.stores.registered_products.revoke("workspace-a", registered.reference)
            unit_of_work.commit()

        with self.assertRaisesRegex(GraphAuthoringError, "unregistered product"):
            GraphAuthoringService(
                self.unit_of_work,
                graph_id_factory=lambda: "graph-desired",
                clock=lambda: "2026-07-22T10:05:00Z",
            ).set_desired_graph(
                SetDesiredGraphCommand(
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    graph=graph,
                    expected_desired_graph_id=None,
                )
            )

        self.assertEqual(self._row_count("cpk_graph_versions"), 0)

    def test_product_registration_is_workspace_scoped(self) -> None:
        document = ProductDescriptorCodec().encode_document(self.product("hello-server"))
        graph = self.graph_from_document(document.product)

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-b", name="Other")
            )
            unit_of_work.stores.registered_products.register(
                workspace_id="workspace-b",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-b",
                imported_at="2026-07-22T10:00:00Z",
            )
            unit_of_work.commit()

        with self.assertRaisesRegex(GraphAuthoringError, "unregistered product"):
            GraphAuthoringService(
                self.unit_of_work,
                graph_id_factory=lambda: "graph-desired",
                clock=lambda: "2026-07-22T10:05:00Z",
            ).set_desired_graph(
                SetDesiredGraphCommand(
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    graph=graph,
                    expected_desired_graph_id=None,
                )
            )

    def test_same_identity_different_descriptor_digest_is_not_silently_selected(self) -> None:
        registered_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server", digest="sha256:" + "b" * 64)
        )
        changed_document = ProductDescriptorCodec().encode_document(
            self.product("hello-server", digest="sha256:" + "c" * 64)
        )
        graph = self.graph_from_document(changed_document.product)

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=registered_document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
            unit_of_work.commit()

        with self.assertRaisesRegex(GraphAuthoringError, "unregistered product"):
            GraphAuthoringService(
                self.unit_of_work,
                graph_id_factory=lambda: "graph-desired",
                clock=lambda: "2026-07-22T10:05:00Z",
            ).set_desired_graph(
                SetDesiredGraphCommand(
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    graph=graph,
                    expected_desired_graph_id=None,
                )
            )

        self.assertEqual(self._row_count("cpk_graph_versions"), 0)

    def test_selectable_products_exposes_only_active_workspace_products(self) -> None:
        active = ProductDescriptorCodec().encode_document(self.product("hello-server"))
        revoked = ProductDescriptorCodec().encode_document(self.product("old-server"))

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-a", name="Demo")
            )
            active_registration = unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=active,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
            revoked_registration = unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=revoked,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:01:00Z",
            )
            unit_of_work.stores.registered_products.revoke(
                "workspace-a",
                revoked_registration.reference,
            )
            unit_of_work.commit()

        selectable = GraphAuthoringService(
            self.unit_of_work,
            graph_id_factory=lambda: "unused",
            clock=lambda: "2026-07-22T10:05:00Z",
        ).selectable_products("workspace-a")

        self.assertEqual(tuple(value.reference for value in selectable), (active_registration.reference,))
        self.assertEqual(selectable[0].display_name, "hello-server")
        self.assertEqual(
            selectable[0].description,
            "Server product used for graph authoring tests.",
        )

    def test_stale_expected_desired_graph_rolls_back_graph_insert(self) -> None:
        document = ProductDescriptorCodec().encode_document(self.product("hello-server"))
        graph = self.graph_from_document(document.product)

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(
                    workspace_id="workspace-a",
                    name="Demo",
                    desired_graph_id="graph-existing",
                )
            )
            unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
            unit_of_work.commit()

        with self.assertRaisesRegex(GraphAuthoringError, "stale desired graph"):
            GraphAuthoringService(
                self.unit_of_work,
                graph_id_factory=lambda: "graph-new",
                clock=lambda: "2026-07-22T10:05:00Z",
            ).set_desired_graph(
                SetDesiredGraphCommand(
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    graph=graph,
                    expected_desired_graph_id=None,
                )
            )

        with self.assertRaises(KeyError):
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.graphs.get("graph-new")

    def product(
        self,
        name: str,
        *,
        digest: str = "sha256:" + "b" * 64,
    ) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", name, 1),
            image=OciImageReference(
                "ghcr.io",
                f"openj92/control-plane-kit-servers/{name}",
                digest,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name=name,
            description="Server product used for graph authoring tests.",
        )

    def graph_from_document(self, product: ContainerServerProduct) -> DeploymentGraph:
        block = instantiate_product(product, "app", ProductInstanceConfiguration())
        return compile_topology(
            DeploymentTopology("desired", DockerRuntime(children=(block,)))
        )

    def _row_count(self, table: str) -> int:
        if table != "cpk_graph_versions":
            raise ValueError(f"unexpected table {table!r}")
        return self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
