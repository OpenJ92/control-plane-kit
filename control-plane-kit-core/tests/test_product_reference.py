from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductCatalog,
    ProductDescriptorCodec,
    ProductDescriptorDigest,
    ProductReference,
    ProductReferenceCodec,
    ProductReferenceError,
    ProductIdentity,
    ProductRuntimeContract,
)
from control_plane_kit_core.types import Protocol


VALID_IMAGE_DIGEST = "sha256:" + "7" * 64


class ProductReferenceTests(unittest.TestCase):
    def document(self) -> object:
        product = ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "hello-server", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/hello-server",
                VALID_IMAGE_DIGEST,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name="Hello server",
            description="Small HTTP server product used for acceptance tests.",
        )
        return ProductDescriptorCodec().encode_document(product)

    def test_reference_pins_identity_to_exact_descriptor_digest(self) -> None:
        document = self.document()

        reference = ProductReference.from_document(document)

        self.assertEqual(reference.identity, document.product.identity)
        self.assertEqual(reference.descriptor_sha256.value, document.content_digest)
        self.assertEqual(
            reference.descriptor(),
            {
                "identity": document.product.identity.descriptor(),
                "descriptor_sha256": document.content_digest,
            },
        )

    def test_catalogue_can_project_reference_without_acquisition_source(self) -> None:
        document = self.document()
        catalog = ProductCatalog.from_documents((document,))

        reference = catalog.reference_for(document.product.identity)

        self.assertEqual(reference, ProductReference.from_document(document))
        self.assertNotIn("source", reference.descriptor())
        self.assertNotIn("url", reference.descriptor())
        self.assertNotIn("registered_at", reference.descriptor())

    def test_reference_codec_is_closed_and_rejects_acquisition_or_workspace_truth(self) -> None:
        document = self.document()
        reference = ProductReference.from_document(document)
        descriptor = ProductReferenceCodec().encode(reference)

        self.assertEqual(ProductReferenceCodec().decode(descriptor), reference)

        for extra in ("source_url", "workspace_id", "imported_by", "registered_at"):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ProductReferenceError, "unknown keys"):
                    ProductReferenceCodec().decode({**descriptor, extra: "not-core-truth"})

    def test_descriptor_digest_is_exact_current_product_document_sha256(self) -> None:
        digest = ProductDescriptorDigest("a" * 64)
        self.assertEqual(digest.value, "a" * 64)

        invalid_values = (
            "sha256:" + "a" * 64,
            "a" * 63,
            "A" * 64,
            "not-a-digest",
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ProductReferenceError, "descriptor_sha256"):
                    ProductDescriptorDigest(value)

    def test_reference_language_has_no_server_or_persistence_imports(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)

        forbidden_fragments = (
            "control_plane_kit_servers",
            "psycopg",
            "stores",
            "workflows",
            "requests",
            "httpx",
        )
        self.assertEqual(
            [
                module
                for module in sorted(imports)
                if any(fragment in module for fragment in forbidden_fragments)
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
