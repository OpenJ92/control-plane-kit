from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import EffectAttemptTransition
from control_plane_kit_operations.execution_leases import (
    ExecutionLeaseFence,
    InvalidExecutionLeaseFence,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.effect_attempt_start_fixture import (
    EffectAttemptStartDenied,
    EffectAttemptStartFixture,
    EffectAttemptStartService,
    INTERPRETER_MODULE,
    START_MODULE,
    StartEffectAttempt,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = Path(
    os.environ.get(
        "CPK_PACKAGE_MODULE_INVENTORY",
        REPOSITORY_ROOT
        / "docs"
        / "architecture"
        / "package-module-inventory.json",
    )
)


class FailIfUnitOfWork:
    def __init__(self, message: str = "unit of work opened") -> None:
        self.error = AssertionError(message)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise self.error


def malformed_fence(worker_id: str, generation: int) -> ExecutionLeaseFence:
    fence = object.__new__(ExecutionLeaseFence)
    object.__setattr__(fence, "worker_id", worker_id)
    object.__setattr__(fence, "generation", generation)
    return fence


class EffectAttemptStartInterpreterContractTests(
    EffectAttemptStartFixture,
    unittest.TestCase,
):
    def service(self, unit_of_work_factory):
        self.require_service()
        return EffectAttemptStartService(
            unit_of_work_factory,
            id_factory=lambda: "unused-event-id",
        )

    def test_service_is_exact_root_export_with_one_command_surface(self) -> None:
        self.require_service()
        self.assertIs(
            getattr(operations_root, "EffectAttemptStartService", None),
            EffectAttemptStartService,
        )
        self.assertEqual(EffectAttemptStartService.__module__, INTERPRETER_MODULE)
        signature = inspect.signature(EffectAttemptStartService)
        self.assertEqual(
            tuple(signature.parameters),
            ("unit_of_work_factory", "id_factory"),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptStartService.execute).parameters),
            ("self", "command"),
        )

    def test_scope_rejection_is_categorical_and_precedes_unit_of_work(self) -> None:
        fail = FailIfUnitOfWork("scope rejection opened a unit of work")
        command = self.command(authority=self.authority(scopes=()))
        with self.assertRaises(EffectAttemptStartDenied) as caught:
            self.service(fail).execute(command)
        self.assertEqual(
            str(caught.exception),
            "scope execution:operate is missing",
        )
        self.assert_safe_error(caught.exception)
        self.assertEqual(fail.calls, 0)

    def test_service_rejects_raw_hostile_and_nested_non_nominal_commands(
        self,
    ) -> None:
        self.require_service()
        valid = self.command()

        class HostileCommand(StartEffectAttempt):
            pass

        class HostileText(str):
            pass

        def bypass(command_type, **changes):
            values = {
                "request_id": valid.request_id,
                "transition": valid.transition,
                "authority": valid.authority,
                "fence": valid.fence,
            }
            values.update(changes)
            command = object.__new__(command_type)
            for name, value in values.items():
                object.__setattr__(command, name, value)
            return command

        hostile_fingerprint = EffectAttemptTransition(
            valid.transition.kind,
            valid.transition.identity,
            request_fingerprint=HostileText(
                valid.transition.request_fingerprint
            ),
        )
        hostile_worker = HostileText("hostile-worker-canary")
        candidates = (
            object(),
            bypass(HostileCommand),
            bypass(StartEffectAttempt, transition=hostile_fingerprint),
            bypass(
                StartEffectAttempt,
                authority=self.authority(hostile_worker),
                fence=self.fence(hostile_worker),
            ),
        )
        for candidate in candidates:
            with self.subTest(candidate=type(candidate).__name__):
                fail = FailIfUnitOfWork(
                    "invalid command opened a unit of work"
                )
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.service(fail).execute(candidate)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt start command is invalid",
                )
                self.assert_safe_error(
                    caught.exception,
                    valid.transition.request_fingerprint,
                    hostile_worker,
                )
                self.assertEqual(fail.calls, 0)

    def test_fence_translation_is_exact_bounded_and_precedes_unit_of_work(self) -> None:
        self.require_service()

        class HostileText(str):
            pass

        rejected = (
            "w" * 257,
            "worker-\ud800-canary",
            HostileText("h" * 257),
        )
        for worker_id in rejected:
            with self.subTest(worker_type=type(worker_id).__name__):
                fail = FailIfUnitOfWork("invalid fence opened a unit of work")
                command = self.command(
                    authority=self.authority(worker_id),
                    fence=self.fence(worker_id),
                )
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.service(fail).execute(command)
                self.assertEqual(
                    str(caught.exception),
                    "execution lease fence cannot identify an effect attempt",
                )
                self.assert_safe_error(caught.exception, str(worker_id))
                self.assertEqual(fail.calls, 0)

        for worker_id, generation in (("worker\x00canary", 7), ("worker-a", 0)):
            with self.subTest(worker_id=repr(worker_id), generation=generation):
                fail = FailIfUnitOfWork("malformed fence opened a unit of work")
                command = self.command(
                    authority=self.authority(worker_id),
                    fence=malformed_fence(worker_id, generation),
                )
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.service(fail).execute(command)
                self.assertEqual(
                    str(caught.exception),
                    "execution lease fence cannot identify an effect attempt",
                )
                self.assert_safe_error(caught.exception, "canary")
                self.assertEqual(fail.calls, 0)

    def test_maximum_fence_coordinates_reach_the_unit_of_work_unchanged(self) -> None:
        fail = FailIfUnitOfWork("valid fence reached unit of work")
        command = self.command(
            authority=self.authority("w" * 256),
            fence=self.fence("w" * 256, 2**63 - 1),
        )
        with self.assertRaises(AssertionError) as caught:
            self.service(fail).execute(command)
        self.assertIs(caught.exception, fail.error)
        self.assertEqual(fail.calls, 1)

        with self.assertRaises(InvalidExecutionLeaseFence):
            self.fence("worker-a", 2**63)

    def test_public_modules_are_provider_free_and_own_no_dispatch_surface(self) -> None:
        self.require_service()
        forbidden_import_fragments = {
            "coordinator",
            "cpk_server",
            "provider",
            "runtime_effect",
            "gateway",
            "http",
            "mcp",
            "network",
        }
        for module_name in (START_MODULE, INTERPRETER_MODULE):
            with self.subTest(module=module_name):
                path = (
                    PACKAGE_ROOT
                    / "src"
                    / Path(module_name.replace(".", "/")).relative_to(
                        "control_plane_kit_operations"
                    )
                ).with_suffix(".py")
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imports = {
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                imports.update(
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )
                self.assertTrue(
                    all(
                        fragment not in imported
                        for imported in imports
                        for fragment in forbidden_import_fragments
                    ),
                    imports,
                )
                names = {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name)
                }
                self.assertTrue(
                    {"provider_request", "provider_result", "dispatch"}.isdisjoint(
                        names
                    )
                )

    def test_inventory_exactly_owns_public_language_and_interpreter(self) -> None:
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        entries = {
            item["module"]: item
            for item in inventory["modules"]
            if item["module"] in {START_MODULE, INTERPRETER_MODULE}
        }
        self.assertEqual(set(entries), {START_MODULE, INTERPRETER_MODULE})
        language = entries[START_MODULE]
        interpreter = entries[INTERPRETER_MODULE]
        self.assertEqual(language["owner"], "operation")
        self.assertEqual(interpreter["owner"], "operation")
        self.assertEqual(
            set(language["canonical_public_exports"]),
            {
                "EffectAttemptStartConflict",
                "EffectAttemptStartDenied",
                "EffectAttemptStartError",
                "EffectAttemptStartNotFound",
                "EffectAttemptStartResult",
                "ExistingAttempt",
                "NewlyStarted",
                "StartEffectAttempt",
            },
        )
        self.assertEqual(
            interpreter["canonical_public_exports"],
            ["EffectAttemptStartService"],
        )
        self.assertEqual(language["optional_external_dependencies"], [])
        self.assertEqual(interpreter["optional_external_dependencies"], [])
        self.assertIn(
            "tests/test_effect_attempt_start_contract.py",
            language["protecting_tests"],
        )
        self.assertIn(
            "tests/test_effect_attempt_start_interpreter_contract.py",
            interpreter["protecting_tests"],
        )


if __name__ == "__main__":
    unittest.main()
