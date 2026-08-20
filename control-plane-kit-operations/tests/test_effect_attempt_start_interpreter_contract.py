from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_architecture_testing as architecture_testing
import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import EffectAttemptTransition
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
    RuntimeEffectIntentSource,
)
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
from tests.effect_attempt_intent_fixture import (
    class_access_hostile_copy,
    deep_coordinate_intent_candidates,
    forge_exact,
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
START_SOURCE_PATH = (
    "control-plane-kit-operations/src/control_plane_kit_operations/"
    "effect_attempt_start.py"
)

EXACT_START_IMPORT_SURFACE = (
    architecture_testing.ImportSurfaceEntry("__future__", "annotations", None),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations", "EffectAttemptIdentity", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations", "EffectAttemptStatus", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations", "EffectAttemptTransition", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations", "EffectAttemptTransitionKind", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations", "RunId", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.planning", "ActivityId", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.policies", "PolicyScope", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectIntent",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectIntentSource",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_intent_fingerprint",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_intent_for_request",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_request_for_intent",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.effect_attempts", "EffectAttemptRecord", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.execution_leases",
        "ExecutionLeaseFence",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.lifecycle",
        "ExecutionWorkerAuthority",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records", "OperationsRecordError", None
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.workflows", "InvalidOperationCommand", None
    ),
    architecture_testing.ImportSurfaceEntry("dataclasses", "dataclass", None),
)

EXACT_START_CALL_SURFACE = (
    architecture_testing.ResolvedCallTarget("_bounded_command_text"),
    architecture_testing.ResolvedCallTarget("_valid_start_command"),
    architecture_testing.ResolvedCallTarget("_valid_start_transition"),
    architecture_testing.ResolvedCallTarget("any"),
    architecture_testing.ResolvedCallTarget("any"),
    architecture_testing.ResolvedCallTarget("any"),
    architecture_testing.ResolvedCallTarget("any"),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.operations.EffectAttemptIdentity"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.operations.EffectAttemptTransition"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_intent_fingerprint"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_intent_for_request"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_request_for_intent"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.OperationsRecordError"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.OperationsRecordError"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.workflows.InvalidOperationCommand"
    ),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("len"),
    architecture_testing.ResolvedCallTarget("ord"),
    architecture_testing.ResolvedCallTarget("ord"),
    architecture_testing.ResolvedCallTarget("ord"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("value.encode"),
)

EXACT_START_DEPENDENCIES = {
    "control_plane_kit_core.operations",
    "control_plane_kit_core.planning",
    "control_plane_kit_core.policies",
    "control_plane_kit_core.runtime_effect_observation",
    "control_plane_kit_operations.effect_attempts",
    "control_plane_kit_operations.execution_leases",
    "control_plane_kit_operations.lifecycle",
    "control_plane_kit_operations.records",
    "control_plane_kit_operations.workflows",
}


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
                "intent": valid.intent,
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
        forged_intent = forge_exact(
            RuntimeEffectIntent,
            kind=valid.intent.kind,
            runtime_kind=valid.intent.runtime_kind,
            source=forge_exact(
                RuntimeEffectIntentSource,
                workspace_id=valid.intent.source.workspace_id,
                request_id="request-forged-canary",
                run_id=valid.intent.source.run_id,
                plan_id=valid.intent.source.plan_id,
                base_graph_id=valid.intent.source.base_graph_id,
                desired_graph_id=valid.intent.source.desired_graph_id,
            ),
            activity_id=valid.intent.activity_id,
            operation=valid.intent.operation,
            authority_ref=valid.intent.authority_ref,
            authority_deliveries=valid.intent.authority_deliveries,
            products=valid.intent.products,
        )
        intent_dispatches: list[str] = []
        hostile_intent = class_access_hostile_copy(
            valid.intent,
            intent_dispatches,
        )
        foreign_fingerprint = EffectAttemptTransition(
            valid.transition.kind,
            valid.transition.identity,
            request_fingerprint="f" * 64,
        )
        control_worker = "worker\ncontrol-canary"
        candidates = (
            ("raw-object", object(), ()),
            ("hostile-command", bypass(HostileCommand), ()),
            (
                "hostile-fingerprint",
                bypass(StartEffectAttempt, transition=hostile_fingerprint),
                (valid.transition.request_fingerprint,),
            ),
            (
                "forged-intent",
                bypass(StartEffectAttempt, intent=forged_intent),
                ("request-forged-canary",),
            ),
            (
                "hostile-intent-class",
                bypass(StartEffectAttempt, intent=hostile_intent),
                (),
            ),
            (
                "foreign-intent-fingerprint",
                bypass(StartEffectAttempt, transition=foreign_fingerprint),
                ("f" * 64,),
            ),
            (
                "hostile-worker",
                bypass(
                    StartEffectAttempt,
                    authority=self.authority(hostile_worker),
                    fence=self.fence(hostile_worker),
                ),
                (hostile_worker,),
            ),
            (
                "non-utf8-request",
                bypass(StartEffectAttempt, request_id="request-\ud800-canary"),
                ("canary",),
            ),
            (
                "control-worker",
                bypass(
                    StartEffectAttempt,
                    authority=self.authority(control_worker),
                    fence=malformed_fence(control_worker, 7),
                ),
                ("canary",),
            ),
        )
        for label, candidate, canaries in candidates:
            with self.subTest(candidate=label):
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
                    *canaries,
                )
                self.assertEqual(fail.calls, 0)
                self.assertEqual(intent_dispatches, [])

        self.require_service()
        lawful = self.command()
        control = FailIfUnitOfWork("lawful command reached unit of work")
        with self.assertRaises(AssertionError) as caught:
            self.service(control).execute(lawful)
        self.assertIs(caught.exception, control.error)
        self.assertEqual(control.calls, 1)

        def bypass(intent):
            command = object.__new__(StartEffectAttempt)
            for name, value in (
                ("request_id", lawful.request_id),
                ("transition", lawful.transition),
                ("intent", intent),
                ("authority", lawful.authority),
                ("fence", lawful.fence),
            ):
                object.__setattr__(command, name, value)
            return command

        module = __import__(START_MODULE, fromlist=("__file__",))
        original_projection = module.runtime_effect_request_for_intent
        for label, candidate, coordinate_dispatches in (
            deep_coordinate_intent_candidates(lawful.intent)
        ):
            with self.subTest(candidate=label):
                coordinate_dispatches.clear()
                projections: list[str] = []
                fail = FailIfUnitOfWork(
                    "invalid deep intent opened a unit of work"
                )

                def forbidden_projection(*_args, **_kwargs):
                    projections.append("projection")
                    raise AssertionError("public request projection dispatched")

                captured = None
                module.runtime_effect_request_for_intent = forbidden_projection
                try:
                    try:
                        self.service(fail).execute(bypass(candidate))
                    except BaseException as error:
                        captured = error
                finally:
                    module.runtime_effect_request_for_intent = original_projection
                self.assertEqual(coordinate_dispatches, [])
                self.assertEqual(projections, [])
                self.assertEqual(fail.calls, 0)
                self.assertIs(type(captured), InvalidOperationCommand)
                self.assertEqual(
                    str(captured),
                    "effect attempt start command is invalid",
                )
                self.assert_safe_error(captured, label)

    def test_start_language_has_closed_import_and_lexical_call_surface(self) -> None:
        path = PACKAGE_ROOT / "src" / Path(START_MODULE.replace(".", "/"))
        facts = architecture_testing.analyze_source(
            path.with_suffix(".py").read_text(encoding="utf-8"),
            path=START_SOURCE_PATH,
            module=START_MODULE,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId("cpk.operations.start.imports"),
                    architecture_testing.RuleId("exact"),
                    START_SOURCE_PATH,
                    START_MODULE,
                    EXACT_START_IMPORT_SURFACE,
                    "effect attempt start import surface differs",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId("cpk.operations.start.calls"),
                    architecture_testing.RuleId("exact"),
                    START_SOURCE_PATH,
                    START_MODULE,
                    EXACT_START_CALL_SURFACE,
                    "effect attempt start lexical call surface differs",
                ),
            ),
        )
        self.assertEqual(findings, ())

    def test_fence_translation_is_exact_bounded_and_precedes_unit_of_work(self) -> None:
        self.require_service()

        rejected = (
            "w" * 257,
            "worker-\ud800-canary",
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
                    / Path(module_name.replace(".", "/"))
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
        self.assertEqual(
            set(language["internal_dependencies"]),
            EXACT_START_DEPENDENCIES,
        )
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
