from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane_kit_core.products import (
    DuplicateProductIdentity,
    ProductIdentity,
    ProductIdentityCodec,
    ProductIdentityError,
    require_unique_product_identities,
)


class ProductIdentityTests(unittest.TestCase):
    def test_constructs_structural_namespaced_identity(self) -> None:
        identity = ProductIdentity("cpk-servers", "coredns", 1)

        self.assertEqual(identity.namespace, "cpk-servers")
        self.assertEqual(identity.name, "coredns")
        self.assertEqual(identity.contract_revision, 1)
        self.assertEqual(identity.key, "cpk-servers/coredns/1")
        self.assertEqual(
            identity.descriptor(),
            {
                "namespace": "cpk-servers",
                "name": "coredns",
                "contract_revision": 1,
            },
        )

    def test_unknown_namespaces_are_data_not_imports(self) -> None:
        identity = ProductIdentity("pottery-factory", "api", 1)

        self.assertEqual(identity.key, "pottery-factory/api/1")

    def test_rejects_case_unicode_paths_shells_and_invalid_revisions(self) -> None:
        invalid_values = (
            ("CPK", "hello", 1),
            ("cpk", "Hello", 1),
            ("cpk", "héllo", 1),
            ("cpk", "../hello", 1),
            ("cpk", "hello;rm", 1),
            ("cpk", "hello world", 1),
            ("", "hello", 1),
            ("cpk", "", 1),
            ("cpk", "hello", 0),
            ("cpk", "hello", -1),
            ("cpk", "hello", True),
        )

        for namespace, name, revision in invalid_values:
            with self.subTest(namespace=namespace, name=name, revision=revision):
                with self.assertRaises(ProductIdentityError):
                    ProductIdentity(namespace, name, revision)

    def test_codec_round_trips_strict_descriptor(self) -> None:
        identity = ProductIdentity("control-plane-kit", "instance", 1)
        codec = ProductIdentityCodec()

        descriptor = codec.encode(identity)
        restored = codec.decode(descriptor)

        self.assertEqual(restored, identity)
        self.assertEqual(codec.encode(restored), descriptor)

    def test_codec_rejects_unknown_missing_and_wrong_typed_fields(self) -> None:
        codec = ProductIdentityCodec()
        valid = {
            "namespace": "cpk-servers",
            "name": "hello",
            "contract_revision": 1,
        }

        bad_descriptors = (
            {**valid, "future": "unknown"},
            {"namespace": "cpk-servers", "contract_revision": 1},
            {"namespace": "cpk-servers", "name": "hello", "contract_revision": "1"},
            {"namespace": "cpk-servers", "name": "hello", "contract_revision": True},
        )

        for descriptor in bad_descriptors:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(ProductIdentityError):
                    codec.decode(descriptor)

    def test_ordering_is_stable_and_structural(self) -> None:
        identities = (
            ProductIdentity("pottery-factory", "api", 1),
            ProductIdentity("cpk-servers", "hello", 2),
            ProductIdentity("cpk-servers", "hello", 1),
        )

        self.assertEqual(
            [identity.key for identity in sorted(identities)],
            [
                "cpk-servers/hello/1",
                "cpk-servers/hello/2",
                "pottery-factory/api/1",
            ],
        )

    def test_duplicate_identities_fail_before_catalogue_mutation(self) -> None:
        identities = (
            ProductIdentity("cpk-servers", "hello", 1),
            ProductIdentity("cpk-servers", "coredns", 1),
            ProductIdentity("cpk-servers", "hello", 1),
        )

        with self.assertRaisesRegex(DuplicateProductIdentity, "cpk-servers/hello/1"):
            require_unique_product_identities(identities)

    def test_non_identity_catalogue_member_fails_before_sorting(self) -> None:
        with self.assertRaisesRegex(ProductIdentityError, "ProductIdentity"):
            require_unique_product_identities(
                (ProductIdentity("cpk-servers", "hello", 1), object())
            )

    def test_product_identity_module_has_no_dynamic_import_or_effect_boundary(self) -> None:
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
            "uvicorn",
        }
        imported_roots: set[str] = set()
        dynamic_import_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "__import__":
                    dynamic_import_calls.append("__import__")

        self.assertEqual(imported_roots & forbidden_import_roots, set())
        self.assertEqual(dynamic_import_calls, [])


if __name__ == "__main__":
    unittest.main()
