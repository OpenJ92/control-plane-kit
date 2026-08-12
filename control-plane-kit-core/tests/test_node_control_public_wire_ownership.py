from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from pathlib import Path
import unittest

import control_plane_kit_core as core


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
PUBLIC_MATERIAL_FIXTURE = FIXTURE_ROOT / "node_control_public_material_v1.json"
CANONICAL_FIXTURE = FIXTURE_ROOT / "node_control_canonical_wire_v1.json"
SOURCE_ROOT = Path(__file__).parents[1] / "src" / "control_plane_kit_core"
MODULE_NAME = "control_plane_kit_core._node_control_public_wire"
MAX_SAFE_INTEGER = 2**53 - 1


class NodeControlPublicWireOwnershipTests(unittest.TestCase):
    def contract_module(self):
        self.assertIsNotNone(
            importlib.util.find_spec(MODULE_NAME),
            "shared node-control public-wire law is not implemented",
        )
        return importlib.import_module(MODULE_NAME)

    def contract(self, name: str):
        value = getattr(self.contract_module(), name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def fixture(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_public_material_classifier_matches_language_neutral_vectors(self) -> None:
        classify = self.contract("public_material_violation")
        code = self.contract("NodeControlPublicWireViolation")
        fixture = self.fixture(PUBLIC_MATERIAL_FIXTURE)

        self.assertEqual(
            tuple(member.value for member in code),
            (
                "shape-invalid",
                "credential-envelope",
                "endpoint-envelope",
            ),
        )
        for value in fixture["accepted"]:
            with self.subTest(admitted=value):
                self.assertIsNone(classify(value))
        for vector in fixture["rejected"]:
            with self.subTest(rejected=vector["value"]):
                expected = (
                    code.CREDENTIAL_ENVELOPE
                    if vector["law"] == "credential-envelope"
                    else code.ENDPOINT_ENVELOPE
                )
                self.assertIs(classify(vector["value"]), expected)
        self.assertIs(
            classify("token=credential-canary http://endpoint-canary"),
            code.CREDENTIAL_ENVELOPE,
        )
        self.assertIs(
            classify("%74oken%3Dcredential-canary"),
            code.CREDENTIAL_ENVELOPE,
        )
        self.assertIsNone(classify("%2574oken%253Dcredential-canary"))

    def test_shape_classifiers_preserve_exact_bounds_and_precedence(self) -> None:
        code = self.contract("NodeControlPublicWireViolation")
        identifier = self.contract("identifier_violation")
        reference = self.contract("reference_violation")
        digest = self.contract("digest_violation")
        epoch = self.contract("epoch_violation")

        for value in ("a", "a" * 128, "router.internal", "secret-agent"):
            with self.subTest(identifier=value):
                self.assertIsNone(identifier(value))
        for value in (None, "", "a" * 129, "contains/slash", "http://router"):
            with self.subTest(identifier_shape=value):
                self.assertIs(identifier(value), code.SHAPE_INVALID)
        self.assertIs(identifier("localhost"), code.ENDPOINT_ENVELOPE)
        self.assertIs(identifier("sk-abc12345"), code.CREDENTIAL_ENVELOPE)

        for value in ("a", "a" * 256, "gateway:workspace:node"):
            with self.subTest(reference=value):
                self.assertIsNone(reference(value))
        for value in (None, "", "a" * 257, "contains space"):
            with self.subTest(reference_shape=value):
                self.assertIs(reference(value), code.SHAPE_INVALID)
        self.assertIs(reference("http://router"), code.ENDPOINT_ENVELOPE)
        self.assertIs(reference("sk-abc12345"), code.CREDENTIAL_ENVELOPE)

        self.assertIsNone(digest("a" * 64))
        for value in (None, "a" * 63, "A" * 64, "g" * 64):
            with self.subTest(digest_shape=value):
                self.assertIs(digest(value), code.SHAPE_INVALID)

        for value in (0, MAX_SAFE_INTEGER):
            with self.subTest(epoch=value):
                self.assertIsNone(epoch(value))
        for value in (True, -1, MAX_SAFE_INTEGER + 1):
            with self.subTest(epoch_bounds=value):
                self.assertIs(epoch(value), code.SHAPE_INVALID)

    def test_canonical_json_domain_preserves_exact_fixed_vectors(self) -> None:
        canonical = self.contract("canonical_json_bytes")
        error_type = self.contract("NodeControlCanonicalDomainError")
        fixture = self.fixture(CANONICAL_FIXTURE)

        for vector in fixture["requests"]:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(
                    canonical(vector["descriptor"]),
                    vector["canonical_utf8"].encode("utf-8"),
                )

        with self.assertRaises(error_type) as caught:
            canonical({"attacker-canary": float("nan")})
        rendered = f"{caught.exception!s} {caught.exception!r}"
        self.assertEqual(str(caught.exception), "outside the canonical JSON domain")
        self.assertNotIn("attacker-canary", rendered)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertFalse(vars(caught.exception))

    def test_shared_module_is_private_pure_and_owns_only_proven_laws(self) -> None:
        module = self.contract_module()
        module_path = Path(module.__file__)
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        from_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertIn("rfc8785", imports)
        self.assertNotIn("_node_control_public_wire", core.__all__)
        forbidden_roots = {
            "control_plane_kit_core",
            "control_plane_kit_operations",
            "control_plane_kit_interpreters",
            "docker",
            "fastapi",
            "httpx",
            "mcp",
            "psycopg",
            "uvicorn",
        }
        self.assertTrue(
            forbidden_roots.isdisjoint(
                value.split(".", 1)[0] for value in imports | from_imports
            )
        )

    def test_consumers_relinquish_shared_implementation_without_moving_codecs(self) -> None:
        consumers = (
            "node_control.py",
            "node_control_surface_reads.py",
            "node_control_surface_read_results.py",
        )
        forbidden_imports = {"ipaddress", "rfc8785"}
        forbidden_definitions = {
            "_ascii_percent_projection",
            "_contains_credential_envelope",
            "_contains_endpoint_envelope",
            "_is_localhost_endpoint",
            "_reject_prohibited_public_material",
        }

        for filename in consumers:
            tree = ast.parse((SOURCE_ROOT / filename).read_text(encoding="utf-8"))
            imported = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            definitions = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            with self.subTest(filename=filename):
                self.assertTrue(forbidden_imports.isdisjoint(imported))
                self.assertTrue(forbidden_definitions.isdisjoint(definitions))

if __name__ == "__main__":
    unittest.main()
