import ast
from pathlib import Path
import tomllib
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PACKAGE_ROOT / "pyproject.toml"
INIT = PACKAGE_ROOT / "src" / "control_plane_kit_core" / "__init__.py"


class PackageBoundaryTests(unittest.TestCase):
    def test_base_package_declares_only_core_language_dependencies(self) -> None:
        metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

        project = metadata["project"]
        self.assertEqual(project["name"], "control-plane-kit-core")
        self.assertEqual(project["dependencies"], ["PyYAML>=6.0"])
        self.assertNotIn("optional-dependencies", project)
        self.assertNotIn("scripts", project)

    def test_root_import_stays_inside_pure_core(self) -> None:
        tree = ast.parse(INIT.read_text(encoding="utf-8"))
        forbidden = {
            "control_plane_kit",
            "docker",
            "fastapi",
            "httpx",
            "mcp",
            "psycopg",
            "uvicorn",
        }

        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])

        self.assertFalse(imports & forbidden)

    def test_extracted_core_does_not_name_package_owned_server_products(self) -> None:
        forbidden = (
            "PackageServerProduct",
            "PackageServerSpec",
            "ProductMaturity",
            "hello",
            "coredns",
            "webhook-delivery",
            "managed-http-router",
            "http-auth-gateway",
        )
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PACKAGE_ROOT / "src" / "control_plane_kit_core").rglob("*.py")
        )

        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, source)


if __name__ == "__main__":
    unittest.main()
