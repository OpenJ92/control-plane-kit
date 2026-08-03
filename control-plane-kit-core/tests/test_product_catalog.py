from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductCatalog,
    ProductCatalogConflict,
    ProductCatalogError,
    ProductDescriptorCodec,
    ProductDescriptorDocument,
    ProductIdentity,
    ProductRuntimeContract,
    UnknownProductIdentity,
)
from control_plane_kit_core.types import Protocol


VALID_DIGEST = "sha256:" + "d" * 64


class ProductCatalogTests(unittest.TestCase):
    def document(
        self,
        name: str,
        *,
        revision: int = 1,
        image_digest: str = VALID_DIGEST,
        description: str | None = None,
    ) -> ProductDescriptorDocument:
        product = ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", name, revision),
            image=OciImageReference(
                "ghcr.io",
                f"openj92/control-plane-kit-servers/{name}",
                image_digest,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name=f"{name} server",
            description=description or f"{name} server product.",
        )
        return ProductDescriptorCodec().encode_document(product)

    def test_empty_catalogue_is_identity_for_add_and_merge(self) -> None:
        hello = self.document("hello")
        empty = ProductCatalog.empty()

        catalog = empty.add(hello)

        self.assertEqual(empty.products, ())
        self.assertEqual(empty.merge(catalog), catalog)
        self.assertEqual(catalog.merge(empty), catalog)
        self.assertIs(catalog.add(hello), catalog)

    def test_lookup_is_explicit_and_deterministic(self) -> None:
        hello = self.document("hello")
        router = self.document("router")
        catalog = ProductCatalog.from_documents((router, hello))

        self.assertEqual(
            tuple(document.product.identity.key for document in catalog.products),
            ("cpk-servers/hello/1", "cpk-servers/router/1"),
        )
        self.assertEqual(catalog.lookup(ProductIdentity("cpk-servers", "hello", 1)), hello)
        with self.assertRaisesRegex(UnknownProductIdentity, "cpk-servers/missing/1"):
            catalog.lookup(ProductIdentity("cpk-servers", "missing", 1))

    def test_conflicting_same_identity_different_descriptor_digest_fails(self) -> None:
        first = self.document("hello", description="first descriptor")
        second = self.document("hello", description="second descriptor")

        with self.assertRaisesRegex(ProductCatalogConflict, "cpk-servers/hello/1"):
            ProductCatalog.from_documents((first, second))

        with self.assertRaisesRegex(ProductCatalogConflict, "cpk-servers/hello/1"):
            ProductCatalog.empty().add(first).add(second)

    def test_merge_is_associative_when_identities_do_not_conflict(self) -> None:
        hello = ProductCatalog.from_documents((self.document("hello"),))
        router = ProductCatalog.from_documents((self.document("router"),))
        proxy = ProductCatalog.from_documents((self.document("proxy"),))

        left = hello.merge(router).merge(proxy)
        right = hello.merge(router.merge(proxy))

        self.assertEqual(left, right)
        self.assertEqual(left.content_digest, right.content_digest)

    def test_descriptor_and_digest_are_canonical(self) -> None:
        catalog = ProductCatalog.from_documents(
            (self.document("router"), self.document("hello"))
        )

        descriptor = catalog.descriptor()

        self.assertEqual(list(descriptor), ["products"])
        self.assertEqual(
            [item["product"]["identity"]["name"] for item in descriptor["products"]],
            ["hello", "router"],
        )
        self.assertEqual(catalog.content, ProductCatalog.from_documents(catalog.products).content)
        self.assertEqual(catalog.content_digest, ProductCatalog.from_documents(catalog.products).content_digest)

    def test_constructor_rejects_non_documents_without_sorting(self) -> None:
        with self.assertRaisesRegex(ProductCatalogError, "ProductDescriptorDocument"):
            ProductCatalog((object(),))  # type: ignore[arg-type]

    def test_catalogue_module_has_no_import_or_filesystem_product_loading(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden_calls = {"__import__", "open"}
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)

        self.assertEqual(calls & forbidden_calls, set())


if __name__ == "__main__":
    unittest.main()
