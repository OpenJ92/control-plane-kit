from __future__ import annotations

import ast
from pathlib import Path
import tomllib
import unittest

from control_plane_kit_core import DeploymentProgramStage
from control_plane_kit_operations import OPERATIONS_PACKAGE_BOUNDARY


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PACKAGE_ROOT / "pyproject.toml"
SRC_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_operations"


class OperationsPackageBoundaryTests(unittest.TestCase):
    def test_package_declares_core_dependency_and_no_entrypoints(self) -> None:
        metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

        project = metadata["project"]
        self.assertEqual(project["name"], "control-plane-kit-operations")
        self.assertEqual(project["dependencies"], ["control-plane-kit-core>=0.1.0"])
        self.assertNotIn("optional-dependencies", project)
        self.assertNotIn("scripts", project)

    def test_boundary_descriptor_preserves_deployment_spine(self) -> None:
        descriptor = OPERATIONS_PACKAGE_BOUNDARY.descriptor()

        self.assertEqual(descriptor["distribution"], "control-plane-kit-operations")
        self.assertEqual(descriptor["depends_on"], ["control-plane-kit-core"])
        self.assertEqual(
            descriptor["deployment_spine"],
            [stage.value for stage in DeploymentProgramStage],
        )
        self.assertIn("DeploymentProgram", descriptor["future_owners"])
        self.assertIn("RegisteredProduct", descriptor["future_owners"])
        self.assertIn("cpk-server process", descriptor["excluded_owners"])

    def test_operations_source_does_not_import_servers_or_process_packages(self) -> None:
        forbidden = {
            "control_plane_kit",
            "control_plane_kit_servers",
            "docker",
            "fastapi",
            "httpx",
            "mcp",
            "psycopg",
            "uvicorn",
        }

        imports: set[str] = set()
        for path in SRC_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])

        self.assertFalse(imports & forbidden)
        self.assertIn("control_plane_kit_core", imports)


if __name__ == "__main__":
    unittest.main()
