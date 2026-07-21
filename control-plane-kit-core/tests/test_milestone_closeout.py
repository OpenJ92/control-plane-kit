import ast
from pathlib import Path
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_core"

EXPECTED_MODULES = {
    "__init__",
    "algebra",
    "capabilities",
    "configuration",
    "configuration_rendering",
    "control_contracts",
    "control_routes",
    "environment",
    "lifecycle",
    "operations.__init__",
    "operations.handoff",
    "operations.http",
    "operations.mcp",
    "operations.parity",
    "operations.process",
    "operations.services",
    "operations.transactions",
    "planning.__init__",
    "planning.activity_plan",
    "planning.codec",
    "planning.compiler",
    "planning.scenarios",
    "policies",
    "probe_intents",
    "products",
    "secrets",
    "topology.__init__",
    "topology.changes",
    "topology.codec",
    "topology.compiler",
    "topology.diff",
    "topology.graph",
    "topology.validation",
    "types",
    "verification",
}

FORBIDDEN_IMPORT_ROOTS = {
    "control_plane_kit",
    "docker",
    "fastapi",
    "httpx",
    "mcp",
    "psycopg",
    "pytest",
    "uvicorn",
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC_ROOT).with_suffix("")
    return ".".join(relative.parts)


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".", 1)[0])
            elif node.level:
                roots.add("control_plane_kit_core")
    return roots


class MilestoneCloseoutTests(unittest.TestCase):
    def test_core_module_inventory_is_exact(self) -> None:
        modules = {
            _module_name(path)
            for path in SRC_ROOT.rglob("*.py")
        }

        self.assertEqual(modules, EXPECTED_MODULES)

    def test_core_modules_do_not_import_runtime_or_product_dependencies(self) -> None:
        findings: list[str] = []
        for path in sorted(SRC_ROOT.rglob("*.py")):
            forbidden = _import_roots(path) & FORBIDDEN_IMPORT_ROOTS
            if forbidden:
                findings.append(
                    f"{_module_name(path)} imports {', '.join(sorted(forbidden))}"
                )

        self.assertEqual(findings, [])

    def test_unittest_is_the_only_test_framework_named_by_successor_tests(self) -> None:
        findings: list[str] = []
        for path in sorted((PACKAGE_ROOT / "tests").rglob("test_*.py")):
            roots = _import_roots(path)
            if "pytest" in roots:
                findings.append(str(path.relative_to(PACKAGE_ROOT)))

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
