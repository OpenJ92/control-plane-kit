from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = ROOT / "control_plane_kit" / "__init__.py"
OPERATIONAL_ROOTS = (
    "control_plane_kit.adapters",
    "control_plane_kit.discovery_registry",
    "control_plane_kit.discovery_server",
    "control_plane_kit.docker_runtime",
    "control_plane_kit.idempotency_gateway",
    "control_plane_kit.mcp_read",
    "control_plane_kit.read_services",
    "control_plane_kit.runtimes",
    "control_plane_kit.servers",
    "control_plane_kit.stores",
    "control_plane_kit.webhook",
    "control_plane_kit.webhook_server",
    "control_plane_kit.workflows",
)
OPTIONAL_DEPENDENCIES = ("fastapi", "httpx", "psycopg", "uvicorn")


class RootApiTests(unittest.TestCase):
    def test_root_api_imports_only_pure_package_surfaces(self) -> None:
        tree = ast.parse(PACKAGE_ROOT.read_text())
        imported = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }

        forbidden = {
            module
            for module in imported
            if module.startswith(OPERATIONAL_ROOTS)
        }

        self.assertEqual(forbidden, set())

    def test_root_api_does_not_import_optional_dependencies_directly(self) -> None:
        tree = ast.parse(PACKAGE_ROOT.read_text())
        imported = {
            node.module.split(".", 1)[0]
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported.update(
            alias.name.split(".", 1)[0]
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        )

        self.assertEqual(imported.intersection(OPTIONAL_DEPENDENCIES), set())

    def test_root_all_names_are_bound(self) -> None:
        namespace: dict[str, object] = {}
        exec(PACKAGE_ROOT.read_text(), namespace)

        missing = {name for name in namespace["__all__"] if name not in namespace}

        self.assertEqual(missing, set())


if __name__ == "__main__":
    unittest.main()
