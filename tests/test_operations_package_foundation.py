from __future__ import annotations

import ast
from pathlib import Path
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
OPERATIONS_ROOT = REPO_ROOT / "control-plane-kit-operations"
PYPROJECT = OPERATIONS_ROOT / "pyproject.toml"
INIT = (
    OPERATIONS_ROOT
    / "src"
    / "control_plane_kit_operations"
    / "__init__.py"
)


class OperationsPackageFoundationTests(unittest.TestCase):
    def test_operations_distribution_exists_as_core_sibling(self) -> None:
        metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["name"], "control-plane-kit-operations")
        self.assertIn("control-plane-kit-core>=0.1.0", metadata["project"]["dependencies"])
        self.assertNotIn("scripts", metadata["project"])

    def test_operations_import_boundary_points_inward_to_core_only(self) -> None:
        tree = ast.parse(INIT.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])

        self.assertIn("control_plane_kit_core", imports)
        self.assertFalse(
            imports
            & {
                "control_plane_kit",
                "control_plane_kit_servers",
                "docker",
                "fastapi",
                "httpx",
                "mcp",
                "psycopg",
                "uvicorn",
            }
        )

    def test_root_docker_suite_runs_operations_harness(self) -> None:
        root_test = (REPO_ROOT / "test.sh").read_text(encoding="utf-8")

        self.assertIn("./control-plane-kit-core/test.sh", root_test)
        self.assertIn("./control-plane-kit-operations/test.sh", root_test)


if __name__ == "__main__":
    unittest.main()
