from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptTransition,
    EffectRecoveryDecision,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import BoundedEvidence, FailureEvidence
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.effect_attempt_fold_fixture import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldError,
    EffectAttemptFoldFixture,
    EffectAttemptFoldNotFound,
    EffectAttemptFoldResult,
    EffectAttemptFoldService,
    ExistingFold,
    FOLD_MODULE,
    FoldEffectAttempt,
    INTERPRETER_MODULE,
    NewlyFolded,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = Path(
    os.environ.get(
        "CPK_PACKAGE_MODULE_INVENTORY",
        REPOSITORY_ROOT / "docs" / "architecture" / "package-module-inventory.json",
    )
)


class FailIfUnitOfWork:
    def __init__(self, message: str = "unit of work opened") -> None:
        self.error = AssertionError(message)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise self.error


def bypass(command_type, template, **changes):
    values = {
        "request_id": template.request_id,
        "transition": template.transition,
        "authority": template.authority,
        "fence": template.fence,
        "failure": template.failure,
        "outcome": template.outcome,
    }
    values.update(changes)
    command = object.__new__(command_type)
    for name, value in values.items():
        object.__setattr__(command, name, value)
    return command


class EffectAttemptFoldInterpreterContractTests(
    EffectAttemptFoldFixture,
    unittest.TestCase,
):
    def service(self, unit_of_work_factory):
        self.require_fold_service()
        return EffectAttemptFoldService(
            unit_of_work_factory,
            id_factory=lambda: "unused-event-id",
        )

    def test_service_is_exact_root_export_with_one_command_surface(self) -> None:
        self.require_fold_service()
        public_exports = {
            "FoldEffectAttempt": FoldEffectAttempt,
            "NewlyFolded": NewlyFolded,
            "ExistingFold": ExistingFold,
            "EffectAttemptFoldResult": EffectAttemptFoldResult,
            "EffectAttemptFoldError": EffectAttemptFoldError,
            "EffectAttemptFoldNotFound": EffectAttemptFoldNotFound,
            "EffectAttemptFoldConflict": EffectAttemptFoldConflict,
            "EffectAttemptFoldDenied": EffectAttemptFoldDenied,
            "EffectAttemptFoldService": EffectAttemptFoldService,
        }
        for name, value in public_exports.items():
            with self.subTest(export=name):
                self.assertIn(name, operations_root.__all__)
                self.assertIs(getattr(operations_root, name, None), value)
        self.assertEqual(EffectAttemptFoldService.__module__, INTERPRETER_MODULE)
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptFoldService).parameters),
            ("unit_of_work_factory", "id_factory"),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptFoldService.execute).parameters),
            ("self", "command"),
        )

    def test_scope_rejection_is_categorical_and_precedes_unit_of_work(self) -> None:
        self.require_atomic_command_surface()
        fail = FailIfUnitOfWork("scope rejection opened a unit of work")
        command = self.command(authority=self.authority(scopes=()))
        with self.assertRaises(EffectAttemptFoldDenied) as caught:
            self.service(fail).execute(command)
        self.assertEqual(str(caught.exception), "scope execution:operate is missing")
        self.assert_safe_error(caught.exception)
        self.assertEqual(fail.calls, 0)

    def test_service_rejects_raw_bypassed_and_nested_hostile_commands(self) -> None:
        self.require_atomic_command_surface()
        self.require_fold_service()
        valid = self.command("failed")

        class HostileCommand(FoldEffectAttempt):
            pass

        class HostileText(str):
            pass

        class HostileIdentity(EffectAttemptIdentity):
            def __eq__(self, other) -> bool:
                return (
                    isinstance(other, EffectAttemptIdentity)
                    and self.descriptor() == other.descriptor()
                )

        class HostileAuthority(ExecutionWorkerAuthority):
            pass

        class HostileFence(ExecutionLeaseFence):
            pass

        class HostileDetails(BoundedEvidence):
            pass

        hostile_fingerprint = EffectAttemptTransition(
            valid.transition.kind,
            valid.transition.identity,
            outcome_fingerprint=HostileText(valid.transition.outcome_fingerprint),
        )
        hostile_worker = HostileText("hostile-worker-canary")
        recovery = self.command("recovered-failed")
        decision = recovery.transition.recovery_decision
        hostile_identity = HostileIdentity(**decision.attempt_identity.__dict__)
        hostile_identity_decision = EffectRecoveryDecision(
            decision.decision_id,
            hostile_identity,
            decision.resolution,
            decision.uncertain_fingerprint,
            decision.evidence_fingerprint,
        )
        hostile_identity_transition = EffectAttemptTransition(
            recovery.transition.kind,
            recovery.transition.identity,
            recovery_decision=hostile_identity_decision,
        )
        hostile_text_decision = EffectRecoveryDecision(
            HostileText("decision-id-canary"),
            decision.attempt_identity,
            decision.resolution,
            HostileText(decision.uncertain_fingerprint),
            HostileText(decision.evidence_fingerprint),
        )
        hostile_text_transition = EffectAttemptTransition(
            recovery.transition.kind,
            recovery.transition.identity,
            recovery_decision=hostile_text_decision,
        )
        failure = valid.failure
        hostile_failure_code = FailureEvidence(
            failure.category,
            HostileText("failure-code-canary"),
            failure.message,
            failure.details,
        )
        hostile_failure_message = FailureEvidence(
            failure.category,
            failure.code,
            HostileText("failure-message-canary"),
            failure.details,
        )
        hostile_failure_details = FailureEvidence(
            failure.category,
            failure.code,
            failure.message,
            HostileDetails(failure.details.canonical_json),
        )
        candidates = (
            ("raw-object", object(), ()),
            ("hostile-command", bypass(HostileCommand, valid), ()),
            (
                "hostile-authority",
                bypass(
                    FoldEffectAttempt,
                    valid,
                    authority=HostileAuthority(**valid.authority.__dict__),
                ),
                (),
            ),
            (
                "hostile-fence",
                bypass(
                    FoldEffectAttempt,
                    valid,
                    fence=HostileFence(**valid.fence.__dict__),
                ),
                (),
            ),
            (
                "hostile-fingerprint",
                bypass(FoldEffectAttempt, valid, transition=hostile_fingerprint),
                (valid.transition.outcome_fingerprint,),
            ),
            (
                "hostile-worker",
                bypass(
                    FoldEffectAttempt,
                    valid,
                    authority=self.authority(hostile_worker),
                    fence=self.execution_fence(hostile_worker),
                ),
                ("hostile-worker-canary",),
            ),
            (
                "hostile-recovery-identity",
                bypass(
                    FoldEffectAttempt,
                    recovery,
                    transition=hostile_identity_transition,
                ),
                (),
            ),
            (
                "hostile-recovery-text",
                bypass(
                    FoldEffectAttempt,
                    recovery,
                    transition=hostile_text_transition,
                ),
                (
                    "decision-id-canary",
                    decision.uncertain_fingerprint,
                    decision.evidence_fingerprint,
                ),
            ),
            (
                "hostile-failure-code",
                bypass(FoldEffectAttempt, valid, failure=hostile_failure_code),
                ("failure-code-canary",),
            ),
            (
                "hostile-failure-message",
                bypass(FoldEffectAttempt, valid, failure=hostile_failure_message),
                ("failure-message-canary",),
            ),
            (
                "hostile-failure-details",
                bypass(FoldEffectAttempt, valid, failure=hostile_failure_details),
                (),
            ),
            (
                "non-utf8-request",
                bypass(FoldEffectAttempt, valid, request_id="request-\ud800-canary"),
                ("canary",),
            ),
        )
        for label, candidate, canaries in candidates:
            with self.subTest(candidate=label):
                fail = FailIfUnitOfWork("invalid command opened a unit of work")
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.service(fail).execute(candidate)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt fold command is invalid",
                )
                self.assert_safe_error(caught.exception, *canaries)
                self.assertEqual(fail.calls, 0)

    def test_fence_translation_is_exact_bounded_and_precedes_unit_of_work(self) -> None:
        self.require_atomic_command_surface()
        self.require_fold_service()
        for worker_id in ("w" * 257, "worker-\ud800-canary"):
            with self.subTest(worker=repr(worker_id)):
                fail = FailIfUnitOfWork("invalid fence opened a unit of work")
                command = self.command(
                    authority=self.authority(worker_id),
                    fence=self.execution_fence(worker_id),
                )
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.service(fail).execute(command)
                self.assertEqual(
                    str(caught.exception),
                    "execution lease fence cannot identify an effect attempt",
                )
                self.assert_safe_error(caught.exception, "canary", worker_id)
                self.assertEqual(fail.calls, 0)

    def test_maximum_fence_coordinates_reach_unit_of_work_unchanged(self) -> None:
        self.require_atomic_command_surface()
        fail = FailIfUnitOfWork("valid fence reached unit of work")
        command = self.command(
            authority=self.authority("w" * 256),
            fence=self.execution_fence("w" * 256, 2**63 - 1),
        )
        with self.assertRaises(AssertionError) as caught:
            self.service(fail).execute(command)
        self.assertIs(caught.exception, fail.error)
        self.assertEqual(fail.calls, 1)

    def test_public_modules_are_provider_free_and_own_no_foreign_authority(self) -> None:
        self.require_fold_service()
        forbidden_import_fragments = {
            "coordinator",
            "cpk_server",
            "provider",
            "runtime_effect",
            "gateway",
            "http",
            "mcp",
            "network",
            "execution_lease_recovery",
        }
        forbidden_names = {
            "provider_request",
            "provider_result",
            "dispatch",
            "RecoveryAuthority",
        }
        for module_name in (FOLD_MODULE, INTERPRETER_MODULE):
            with self.subTest(module=module_name):
                path = (
                    PACKAGE_ROOT / "src" / Path(module_name.replace(".", "/"))
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
                    node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
                }
                self.assertTrue(forbidden_names.isdisjoint(names), names)

    def test_inventory_exactly_owns_public_language_and_interpreter(self) -> None:
        self.require_fold_service()
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        entries = {
            item["module"]: item
            for item in inventory["modules"]
            if item["module"] in {FOLD_MODULE, INTERPRETER_MODULE}
        }
        self.assertEqual(set(entries), {FOLD_MODULE, INTERPRETER_MODULE})
        language = entries[FOLD_MODULE]
        interpreter = entries[INTERPRETER_MODULE]
        self.assertEqual(language["owner"], "operation")
        self.assertEqual(interpreter["owner"], "operation")
        self.assertEqual(
            set(language["canonical_public_exports"]),
            {
                "EffectAttemptFoldConflict",
                "EffectAttemptFoldDenied",
                "EffectAttemptFoldError",
                "EffectAttemptFoldNotFound",
                "EffectAttemptFoldResult",
                "ExistingFold",
                "FoldEffectAttempt",
                "NewlyFolded",
            },
        )
        self.assertEqual(
            interpreter["canonical_public_exports"],
            ["EffectAttemptFoldService"],
        )
        self.assertEqual(
            set(interpreter["internal_dependencies"]),
            {
                "control_plane_kit_core.operations",
                "control_plane_kit_core.policies",
                "control_plane_kit_operations.effect_attempt_fold",
                "control_plane_kit_operations.effect_attempts",
                "control_plane_kit_operations.records",
                "control_plane_kit_operations.workflows",
            },
        )
        motivation = interpreter["motivation"].lower()
        for required in ("provider-free", "atomic", "durable", "fold"):
            self.assertIn(required, motivation)
        self.assertNotIn("held", motivation)
        self.assertIn(
            "control_plane_kit_operations.effect_outcome_evidence",
            language["internal_dependencies"],
        )
        self.assertEqual(language["optional_external_dependencies"], [])
        self.assertEqual(interpreter["optional_external_dependencies"], [])
        self.assertIn(
            "tests/test_effect_attempt_fold_contract.py",
            language["protecting_tests"],
        )
        self.assertIn(
            "tests/test_atomic_effect_attempt_fold_contract.py",
            language["protecting_tests"],
        )
        self.assertIn(
            "tests/test_effect_attempt_fold_interpreter_contract.py",
            interpreter["protecting_tests"],
        )


if __name__ == "__main__":
    unittest.main()
