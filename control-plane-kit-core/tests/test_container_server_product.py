from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    ContainerServerProductCodec,
    ContainerServerProductError,
    OciImageReference,
    ProductIdentity,
    ProductRuntimeContract,
)
from control_plane_kit_core.types import Protocol


VALID_DIGEST = "sha256:" + "b" * 64


class ContainerServerProductTests(unittest.TestCase):
    def product(self) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "http-proxy", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/http-proxy",
                VALID_DIGEST,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            ),
            display_name="HTTP proxy",
            description="Generic HTTP proxy server product.",
        )

    def test_constructs_pure_container_server_product(self) -> None:
        product = self.product()

        self.assertEqual(product.identity.key, "cpk-servers/http-proxy/1")
        self.assertEqual(
            product.image.execution_reference,
            f"ghcr.io/openj92/control-plane-kit-servers/http-proxy@{VALID_DIGEST}",
        )
        self.assertEqual(product.runtime_contract.sockets.provider_names(), ("http",))

    def test_product_is_immutable_and_hashable(self) -> None:
        product = self.product()

        self.assertEqual({product}, {product})
        with self.assertRaises(FrozenInstanceError):
            product.display_name = "changed"  # type: ignore[misc]

    def test_codec_round_trips_container_variant(self) -> None:
        product = self.product()
        codec = ContainerServerProductCodec()

        descriptor = codec.encode(product)
        restored = codec.decode(descriptor)

        self.assertEqual(descriptor["kind"], "container-server")
        self.assertEqual(restored, product)
        self.assertEqual(codec.encode(restored), descriptor)

    def test_codec_rejects_unknown_variant_and_unknown_fields(self) -> None:
        codec = ContainerServerProductCodec()
        descriptor = codec.encode(self.product())

        with self.assertRaisesRegex(ContainerServerProductError, "unsupported"):
            codec.decode({**descriptor, "kind": "lambda-container"})
        with self.assertRaises(ContainerServerProductError):
            codec.decode({**descriptor, "python_class": "example.Product"})

    def test_product_metadata_is_bounded_and_not_a_host_or_command_escape_hatch(self) -> None:
        with self.assertRaises(ContainerServerProductError):
            ContainerServerProduct(
                ProductIdentity("cpk-servers", "bad", 1),
                OciImageReference("ghcr.io", "openj92/bad", VALID_DIGEST),
                ProductRuntimeContract(),
                display_name="/var/run/docker.sock",
            )
        with self.assertRaises(ContainerServerProductError):
            ContainerServerProduct(
                ProductIdentity("cpk-servers", "bad", 1),
                OciImageReference("ghcr.io", "openj92/bad", VALID_DIGEST),
                ProductRuntimeContract(),
                description="run `rm -rf /` after startup",
            )

    def test_container_server_product_module_has_no_callback_or_effect_field_policy(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden_field_names = {
            "callback",
            "callable",
            "class_path",
            "command",
            "dockerfile",
            "entrypoint",
            "host_path",
            "module",
            "python_class",
            "shell",
        }
        forbidden_import_roots = {
            "control_plane_kit",
            "docker",
            "fastapi",
            "httpx",
            "importlib",
            "mcp",
            "psycopg",
            "uvicorn",
        }
        imports: set[str] = set()
        field_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                field_names.add(node.target.id)

        self.assertEqual(imports & forbidden_import_roots, set())
        self.assertEqual(field_names & forbidden_field_names, set())


if __name__ == "__main__":
    unittest.main()
