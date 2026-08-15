from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root


SUPPORT_MODULE = (
    "control_plane_kit_operations._execution_lease_recovery_support"
)


try:
    support = importlib.import_module(SUPPORT_MODULE)
except ModuleNotFoundError as error:
    if error.name != SUPPORT_MODULE:
        raise
    support = None


class ExecutionLeaseRecoverySupportContractTests(unittest.TestCase):
    def require_support(self) -> None:
        self.assertIsNotNone(
            support,
            "shared execution-lease recovery support is missing",
        )

    def test_private_support_has_exact_total_interfaces(self) -> None:
        self.require_support()
        expected = {
            "locked_recovery_approval": (
                "stores",
                "request",
            ),
            "require_recovery_eligible_journal": (
                "decision_kind",
                "expected_fence",
                "run",
                "plan",
                "events",
            ),
            "require_replay_run_evolution": (
                "stores",
                "request",
                "retained_run",
            ),
        }
        self.assertEqual(
            {
                name: tuple(inspect.signature(getattr(support, name)).parameters)
                for name in expected
            },
            expected,
        )
        self.assertEqual(getattr(support, "__all__", ()), ())
        for name in expected:
            self.assertFalse(hasattr(operations_root, name))

    def test_support_is_inventoried_once_and_predecessor_interpreter_depends_on_it(self) -> None:
        inventory_path = Path(
            os.environ.get(
                "CPK_PACKAGE_MODULE_INVENTORY",
                Path(__file__).parents[2]
                / "docs"
                / "architecture"
                / "package-module-inventory.json",
            )
        )
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        rows = [
            row for row in inventory["modules"] if row["module"] == SUPPORT_MODULE
        ]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "operation")
        self.assertEqual(row["destination"], SUPPORT_MODULE)
        self.assertEqual(row["canonical_public_exports"], [])
        self.assertEqual(row["optional_external_dependencies"], [])
        self.assertIn(
            "tests/test_execution_lease_recovery_support_contract.py",
            row["protecting_tests"],
        )

        source_root = Path(__file__).parents[1] / "src" / "control_plane_kit_operations"
        path = source_root / "execution_lease_recovery_interpreter.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertIn(SUPPORT_MODULE, imports)
        interpreter_module = (
            "control_plane_kit_operations.execution_lease_recovery_interpreter"
        )
        interpreter_rows = [
            candidate
            for candidate in inventory["modules"]
            if candidate["module"] == interpreter_module
        ]
        self.assertEqual(len(interpreter_rows), 1)
        self.assertEqual(
            set(interpreter_rows[0]["internal_dependencies"]),
            {
                module
                for module in imports
                if module is not None and module.startswith("control_plane_kit")
            },
        )
        owned_functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "_approval",
                "_require_journal",
                "_journal_without_recovery_pairs",
            }.isdisjoint(owned_functions)
        )


if __name__ == "__main__":
    unittest.main()
