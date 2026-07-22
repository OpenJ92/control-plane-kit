from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductRuntimeContract,
)
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.postgres import (
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.products import (
    CatalogueDescriptorSource,
    DescriptorSourceCodec,
    InlineDescriptorSource,
    ImportProductDescriptorCommand,
    ProductRegistrationConflict,
    ProductRegistrationService,
    RegisteredProductStatus,
    RemoteDescriptorSource,
)


class RegisteredProductSourceTests(unittest.TestCase):
    def test_descriptor_source_evidence_is_closed_and_secret_free(self) -> None:
        inline = InlineDescriptorSource()
        remote = RemoteDescriptorSource(
            "https://example.com/products/hello/product.cpk.json",
            expected_sha256="a" * 64,
        )
        catalogue = CatalogueDescriptorSource(
            url="https://example.com/catalogue/products.json",
            product_identity=ProductIdentity("cpk-servers", "hello-server", 1),
            expected_catalogue_sha256="b" * 64,
        )
        codec = DescriptorSourceCodec()

        self.assertEqual(codec.decode(codec.encode(inline)), inline)
        self.assertEqual(codec.decode(codec.encode(remote)), remote)
        self.assertEqual(codec.decode(codec.encode(catalogue)), catalogue)

        with self.assertRaisesRegex(ValueError, "unsupported"):
            codec.decode({"kind": "local_path", "path": "/tmp/product.cpk.json"})
        with self.assertRaisesRegex(ValueError, "credentials"):
            RemoteDescriptorSource("https://token@example.com/product.cpk.json")
        with self.assertRaisesRegex(ValueError, "query"):
            RemoteDescriptorSource("https://example.com/product.cpk.json?token=secret")


class RegisteredProductStoreTests(unittest.TestCase):
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
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def test_service_imports_descriptor_as_workspace_scoped_registered_product(self) -> None:
        service = ProductRegistrationService(self.unit_of_work)
        document = self.document("hello-server", image_digit="1")

        registered = service.import_descriptor(
            ImportProductDescriptorCommand(
                workspace_id="workspace-a",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
        )

        self.assertEqual(registered.workspace_id, "workspace-a")
        self.assertEqual(registered.reference.identity, document.product.identity)
        self.assertEqual(registered.reference.descriptor_sha256.value, document.content_digest)
        self.assertEqual(registered.status, RegisteredProductStatus.ACTIVE)
        self.assertEqual(self._row_count(), 1)

        with self.unit_of_work() as unit_of_work:
            listed = unit_of_work.stores.registered_products.list_active("workspace-a")
            self.assertEqual(listed, (registered,))

    def test_duplicate_descriptor_import_is_idempotent_and_preserves_first_admission(self) -> None:
        service = ProductRegistrationService(self.unit_of_work)
        document = self.document("hello-server", image_digit="2")
        command = ImportProductDescriptorCommand(
            workspace_id="workspace-a",
            descriptor_document=document,
            source=InlineDescriptorSource(),
            imported_by="operator-a",
            imported_at="2026-07-22T10:00:00Z",
        )

        first = service.import_descriptor(command)
        second = service.import_descriptor(
            ImportProductDescriptorCommand(
                workspace_id="workspace-a",
                descriptor_document=document,
                source=RemoteDescriptorSource("https://example.com/hello/product.cpk.json"),
                imported_by="operator-b",
                imported_at="2026-07-22T11:00:00Z",
            )
        )

        self.assertEqual(second, first)
        self.assertEqual(second.imported_by, "operator-a")
        self.assertEqual(second.imported_at, "2026-07-22T10:00:00Z")
        self.assertEqual(self._row_count(), 1)

    def test_same_identity_different_digest_requires_explicit_replacement_policy(self) -> None:
        service = ProductRegistrationService(self.unit_of_work)
        first = self.document("hello-server", image_digit="3")
        changed = self.document("hello-server", image_digit="4")

        service.import_descriptor(
            ImportProductDescriptorCommand(
                workspace_id="workspace-a",
                descriptor_document=first,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
        )

        with self.assertRaisesRegex(ProductRegistrationConflict, "replacement"):
            service.import_descriptor(
                ImportProductDescriptorCommand(
                    workspace_id="workspace-a",
                    descriptor_document=changed,
                    source=InlineDescriptorSource(),
                    imported_by="operator-a",
                    imported_at="2026-07-22T10:05:00Z",
                )
            )
        self.assertEqual(self._row_count(), 1)

    def test_store_writes_roll_back_when_service_does_not_commit(self) -> None:
        document = self.document("hello-server", image_digit="5")

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.registered_products.register(
                workspace_id="workspace-a",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )

        self.assertEqual(self._row_count(), 0)

    def test_revoked_product_is_not_selectable(self) -> None:
        service = ProductRegistrationService(self.unit_of_work)
        document = self.document("hello-server", image_digit="6")
        registered = service.import_descriptor(
            ImportProductDescriptorCommand(
                workspace_id="workspace-a",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-a",
                imported_at="2026-07-22T10:00:00Z",
            )
        )

        service.revoke(
            workspace_id="workspace-a",
            reference=registered.reference,
        )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(unit_of_work.stores.registered_products.list_active("workspace-a"), ())
            revoked = unit_of_work.stores.registered_products.get(
                "workspace-a",
                registered.reference,
            )
            self.assertEqual(revoked.status, RegisteredProductStatus.REVOKED)

    def document(self, name: str, *, image_digit: str):
        product = ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", name, 1),
            image=OciImageReference(
                "ghcr.io",
                f"openj92/control-plane-kit-servers/{name}",
                "sha256:" + image_digit * 64,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name=f"{name} product",
            description="Server product used for operations registration tests.",
        )
        return ProductDescriptorCodec().encode_document(product)

    def _row_count(self) -> int:
        return self.connection.execute(
            "SELECT count(*) FROM cpk_registered_products"
        ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
