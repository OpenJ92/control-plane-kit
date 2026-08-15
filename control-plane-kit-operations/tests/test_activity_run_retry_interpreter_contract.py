from __future__ import annotations

import ast
import importlib
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations.lifecycle import RecoveryScope
from control_plane_kit_operations.lifecycle import RunLifecycleDenied

from tests.activity_run_retry_interpreter_fixture import (
    ActivityRunRetryCommandService,
    TARGET_MODULE,
    retry_interpreter,
)
from tests.execution_lease_recovery_fixture import safe_error


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = Path(
    os.environ.get(
        "CPK_PACKAGE_MODULE_INVENTORY",
        REPOSITORY_ROOT
        / "docs"
        / "architecture"
        / "package-module-inventory.json",
    )
)


class ActivityRunRetryInterpreterContractTests(unittest.TestCase):
    def test_service_is_the_exact_root_export(self) -> None:
        self.assertIsNotNone(
            ActivityRunRetryCommandService,
            "activity-run retry interpreter is missing",
        )
        self.assertIs(
            getattr(operations_root, "ActivityRunRetryCommandService", None),
            ActivityRunRetryCommandService,
        )
        self.assertEqual(
            ActivityRunRetryCommandService.__module__,
            TARGET_MODULE,
        )

    def test_service_constructor_and_execute_surface_are_exact(self) -> None:
        self.assertIsNotNone(ActivityRunRetryCommandService)
        source = Path(
            ActivityRunRetryCommandService.__module__.replace(".", "/")
        )
        self.assertEqual(source.name, "activity_run_retry_interpreter")
        tree = ast.parse(
            (
                REPOSITORY_ROOT
                / "control-plane-kit-operations"
                / "src"
                / "control_plane_kit_operations"
                / "activity_run_retry_interpreter.py"
            ).read_text(encoding="utf-8")
        )
        service = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ActivityRunRetryCommandService"
        )
        methods = {
            node.name: node
            for node in service.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(set(methods), {"__init__", "execute", "_plan_result"})
        self.assertEqual(
            tuple(argument.arg for argument in methods["execute"].args.args),
            ("self", "command"),
        )

    def test_missing_operate_scope_rejects_before_unit_of_work(self) -> None:
        self.assertIsNotNone(ActivityRunRetryCommandService)
        from tests.activity_run_retry_interpreter_fixture import (
            PostgresActivityRunRetryFixture,
        )

        fixture = PostgresActivityRunRetryFixture()
        def fail_factory():
            raise AssertionError("scope rejection opened a unit of work")

        for scopes in ((), (RecoveryScope.RENEW_CLAIM,)):
            with self.subTest(scopes=scopes):
                command = fixture.retry_command(scopes=scopes)
                service = ActivityRunRetryCommandService(
                    fail_factory,
                    id_factory=lambda: "unused-id",
                )
                with self.assertRaises(RunLifecycleDenied) as raised:
                    service.execute(command)
                safe_error(self, raised.exception, "authority-reference-a")

    def test_inventory_owns_one_public_interpreter_module(self) -> None:
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        entries = [
            item
            for item in inventory["modules"]
            if item["module"] == TARGET_MODULE
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(
            entry["canonical_public_exports"],
            ["ActivityRunRetryCommandService"],
        )
        self.assertEqual(entry["semantic_roles"], ["interpreter", "transformations"])
        self.assertIn(
            "control_plane_kit_operations._execution_lease_recovery_support",
            entry["internal_dependencies"],
        )
        self.assertIn(
            "control_plane_kit_operations.activity_run_retry",
            entry["internal_dependencies"],
        )

    def test_interpreter_binds_exact_shared_recovery_support(self) -> None:
        self.assertIsNotNone(retry_interpreter)
        support_module = (
            "control_plane_kit_operations._execution_lease_recovery_support"
        )
        support = importlib.import_module(support_module)
        path = (
            REPOSITORY_ROOT
            / "control-plane-kit-operations"
            / "src"
            / "control_plane_kit_operations"
            / "activity_run_retry_interpreter.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == support_module
            for alias in node.names
        }
        expected = {
            "locked_recovery_approval",
            "require_recovery_eligible_journal",
        }
        self.assertEqual(imported, expected)
        self.assertIs(
            retry_interpreter.locked_recovery_approval,
            support.locked_recovery_approval,
        )
        self.assertIs(
            retry_interpreter.require_recovery_eligible_journal,
            support.require_recovery_eligible_journal,
        )
        owned = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "locked_recovery_approval",
                "require_recovery_eligible_journal",
                "_approval",
                "_require_journal",
                "_journal_without_recovery_pairs",
            }.isdisjoint(owned)
        )


if __name__ == "__main__":
    unittest.main()
