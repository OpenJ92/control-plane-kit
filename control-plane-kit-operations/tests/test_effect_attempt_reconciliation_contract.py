from __future__ import annotations

import dataclasses
import inspect
import json
import os
from pathlib import Path
import typing
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptIdentity,
    EffectAttemptTransition,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationRequest,
    RuntimeEffectObservationResult,
)
from control_plane_kit_operations.effect_attempt_fold import EffectAttemptFoldResult
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority
from control_plane_kit_operations.records import ActivityEventRecord
from control_plane_kit_operations.runtime_effects import (
    runtime_effect_request_for_context,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.effect_outcome_evidence_fixture import (
    EffectOutcomeEvidenceFixture,
    ObservedEffectOutcome,
    REQUEST_FINGERPRINT,
    effect_outcome_failure,
    effect_outcome_transition,
)
from tests.runtime_effect_reconciliation_fixture import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationError,
    EffectAttemptReconciliationNotFound,
    EffectAttemptReconciliationService,
    INTERPRETER_MODULE,
    LANGUAGE_MODULE,
    ReconcileEffectAttempt,
    RuntimeEffectObserver,
    RuntimeEffectReconciliationFixture,
    _load_optional,
)
from tests.test_runtime_effect_translation import _context


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
ROOT_EXPORTS = {
    "EffectAttemptReconciliationConflict",
    "EffectAttemptReconciliationDenied",
    "EffectAttemptReconciliationError",
    "EffectAttemptReconciliationNotFound",
    "EffectAttemptReconciliationService",
    "ReconcileEffectAttempt",
    "RuntimeEffectObserver",
}


class EffectAttemptReconciliationContractTests(
    RuntimeEffectReconciliationFixture,
    EffectOutcomeEvidenceFixture,
    unittest.TestCase,
):
    def test_missing_module_guard_preserves_nested_import_failures(self) -> None:
        nested = ModuleNotFoundError("nested dependency missing")
        nested.name = "nested_dependency"

        def missing_nested(_name):
            raise nested

        with self.assertRaises(ModuleNotFoundError) as caught:
            _load_optional(LANGUAGE_MODULE, missing_nested)
        self.assertIs(caught.exception, nested)

        def partial_import(_name):
            raise ImportError("partial public module")

        with self.assertRaises(ImportError):
            _load_optional(LANGUAGE_MODULE, partial_import)

    def test_command_is_exact_frozen_nominal_and_root_identical(self) -> None:
        command = self.command()
        self.assertIs(
            getattr(operations_root, "ReconcileEffectAttempt", None),
            ReconcileEffectAttempt,
        )
        self.assertEqual(ReconcileEffectAttempt.__module__, LANGUAGE_MODULE)
        self.assertTrue(dataclasses.is_dataclass(ReconcileEffectAttempt))
        self.assertTrue(ReconcileEffectAttempt.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(ReconcileEffectAttempt)),
            ("request_id", "identity", "authority", "fence"),
        )
        self.assertEqual(
            command,
            ReconcileEffectAttempt(
                command.request_id,
                command.identity,
                command.authority,
                command.fence,
            ),
        )
        self.assertNotIn("from_descriptor", ReconcileEffectAttempt.__dict__)

        class HostileCommand(ReconcileEffectAttempt):
            pass

        with self.assertRaises(InvalidOperationCommand) as caught:
            HostileCommand(
                command.request_id,
                command.identity,
                command.authority,
                command.fence,
            )
        self.assertEqual(
            str(caught.exception),
            "effect attempt reconciliation command is invalid",
        )
        self.assert_safe_error(caught.exception)

    def test_command_rejects_hostile_forged_and_unbounded_coordinates(self) -> None:
        self.require_language()

        class HostileText(str):
            pass

        class HostileIdentity(EffectAttemptIdentity):
            pass

        class HostileAuthority(ExecutionWorkerAuthority):
            pass

        class HostileFence(ExecutionLeaseFence):
            pass

        identity = self.identity()
        hostile_identity = HostileIdentity(
            identity.run_id,
            identity.activity_id,
            identity.attempt,
        )
        cases = (
            ({"request_id": ""}, ""),
            ({"request_id": None}, ""),
            ({"request_id": True}, ""),
            ({"request_id": "x" * 513}, "x" * 513),
            ({"request_id": "request\x00canary"}, "canary"),
            ({"request_id": "request\ncanary"}, "canary"),
            ({"request_id": "request-\ud800-canary"}, "canary"),
            ({"request_id": HostileText("request-canary")}, "request-canary"),
            ({"identity": hostile_identity}, ""),
            ({"authority": HostileAuthority("worker-a", ())}, ""),
            ({"fence": HostileFence("worker-a", 7)}, ""),
            ({"authority": self.authority("worker-b")}, "worker-b"),
        )
        for changes, canary in cases:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.command(**changes)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt reconciliation command is invalid",
                )
                self.assert_safe_error(caught.exception, canary)

    def test_public_error_sum_is_closed_root_identical_and_candidate_free(self) -> None:
        self.require_language()
        self.assertEqual(EffectAttemptReconciliationError.__bases__, (RuntimeError,))
        for name, error_type in (
            (
                "EffectAttemptReconciliationNotFound",
                EffectAttemptReconciliationNotFound,
            ),
            (
                "EffectAttemptReconciliationConflict",
                EffectAttemptReconciliationConflict,
            ),
            (
                "EffectAttemptReconciliationDenied",
                EffectAttemptReconciliationDenied,
            ),
        ):
            with self.subTest(error=name):
                self.assertEqual(
                    error_type.__bases__,
                    (EffectAttemptReconciliationError,),
                )
                self.assertIs(getattr(operations_root, name, None), error_type)
                error = error_type("fixed categorical error")
                self.assert_safe_error(
                    error,
                    "secret://private-canary",
                    "https://address-canary",
                    "provider-payload-canary",
                )
        self.assertIs(
            getattr(operations_root, "EffectAttemptReconciliationError", None),
            EffectAttemptReconciliationError,
        )

    def test_observer_protocol_and_existing_fold_result_are_exact(self) -> None:
        self.require_language()
        self.require_service()
        self.assertIs(
            getattr(operations_root, "RuntimeEffectObserver", None),
            RuntimeEffectObserver,
        )
        self.assertEqual(RuntimeEffectObserver.__module__, LANGUAGE_MODULE)
        self.assertEqual(
            tuple(inspect.signature(RuntimeEffectObserver.observe).parameters),
            ("self", "request", "authority"),
        )
        hints = typing.get_type_hints(RuntimeEffectObserver.observe)
        self.assertIs(hints["request"], RuntimeEffectObservationRequest)
        self.assertEqual(
            hints["authority"],
            RegisteredRuntimeAuthority | None,
        )
        self.assertEqual(hints["return"], RuntimeEffectObservationResult)

        self.assertIs(
            getattr(operations_root, "EffectAttemptReconciliationService", None),
            EffectAttemptReconciliationService,
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    EffectAttemptReconciliationService.execute
                ).parameters
            ),
            ("self", "command"),
        )
        service_hints = typing.get_type_hints(
            EffectAttemptReconciliationService.execute
        )
        self.assertIs(service_hints["command"], ReconcileEffectAttempt)
        self.assertEqual(service_hints["return"], EffectAttemptFoldResult)
        self.assertIs(
            getattr(operations_root, "EffectAttemptFoldResult"),
            EffectAttemptFoldResult,
        )

    def test_all_observation_variants_and_phases_are_lawful_projection_worlds(
        self,
    ) -> None:
        observed_stories = tuple(
            story
            for story in self.stories()
            if story.profile == "provider-observation"
        )
        self.assertEqual(len(observed_stories), 12)
        self.assertEqual(
            {story.compensation for story in observed_stories},
            {False, True},
        )
        self.assertEqual(
            {type(story.value).__name__ for story in observed_stories},
            {
                "RuntimeEffectObservedAbsent",
                "RuntimeEffectObservedConflict",
                "RuntimeEffectObservedFailed",
                "RuntimeEffectObservedIndeterminate",
                "RuntimeEffectObservedSucceeded",
                "RuntimeEffectObserverUnsupported",
            },
        )

        for story in observed_stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                outcome = self.outcome_for(story)
                self.assertIs(type(outcome), ObservedEffectOutcome)
                self.assertEqual(
                    story.attempt.original_start_event.event_id,
                    story.value.effect_id,
                )
                self.assertEqual(
                    story.attempt.state.request_fingerprint,
                    story.value.request_fingerprint,
                )
                self.assertIs(
                    story.attempt.original_start_event.kind,
                    (
                        ActivityEventKind.STEP_COMPENSATION_STARTED
                        if story.compensation
                        else ActivityEventKind.STEP_STARTED
                    ),
                )
                self.assertEqual(
                    effect_outcome_transition(outcome),
                    EffectAttemptTransition(
                        story.transition,
                        story.attempt.state.identity,
                        outcome_fingerprint=story.fingerprint,
                    ),
                )
                self.assertEqual(
                    effect_outcome_failure(outcome),
                    self.failure_for(story.failure_row, story.fingerprint),
                )

    def test_foreign_effect_and_fingerprint_are_individually_lawful_inputs(self) -> None:
        for compensation in (False, True):
            story = next(
                value
                for value in self.stories()
                if value.profile == "provider-observation"
                and value.name == "observed-succeeded"
                and value.compensation is compensation
            )
            foreign_effect = dataclasses.replace(
                story.value,
                effect_id="event-foreign",
            )
            foreign_fingerprint = dataclasses.replace(
                story.value,
                request_fingerprint="b" * 64,
            )
            for label, value in (
                ("effect", foreign_effect),
                ("fingerprint", foreign_fingerprint),
            ):
                with self.subTest(compensation=compensation, mismatch=label):
                    outcome = ObservedEffectOutcome(
                        story.attempt.state.identity,
                        value,
                    )
                    effect_outcome_transition(outcome)
                    effect_outcome_failure(outcome)
                    self.assertTrue(
                        value.effect_id
                        != story.attempt.original_start_event.event_id
                        or value.request_fingerprint != REQUEST_FINGERPRINT
                    )

    def test_context_accepts_exact_forward_and_compensation_start_events(self) -> None:
        base = _context()
        rejected = []
        for kind in (
            ActivityEventKind.STEP_STARTED,
            ActivityEventKind.STEP_COMPENSATION_STARTED,
        ):
            event = self._event_with_kind(base.intent_event, kind)
            try:
                context = dataclasses.replace(base, intent_event=event)
            except InvalidOperationCommand as error:
                rejected.append((kind, error))
                continue
            self.assertIs(context.intent_event.kind, kind)
            self.assertEqual(
                runtime_effect_request_for_context(context).effect_id,
                context.intent_event.event_id,
            )
        self.assertEqual(rejected, [])

    def test_context_rejects_every_other_event_kind_with_one_fixed_error(self) -> None:
        base = _context()
        admitted = {
            ActivityEventKind.STEP_STARTED,
            ActivityEventKind.STEP_COMPENSATION_STARTED,
        }
        missing = []
        errors = []
        for kind in ActivityEventKind:
            if kind in admitted:
                continue
            event = self._event_with_kind(base.intent_event, kind)
            try:
                dataclasses.replace(base, intent_event=event)
            except InvalidOperationCommand as error:
                errors.append(error)
            else:
                missing.append(kind)
        self.assertEqual(missing, [])
        self.assertEqual(
            {str(error) for error in errors},
            {
                "realization intent must be step_started or "
                "step_compensation_started"
            },
        )
        for error in errors:
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)

    @staticmethod
    def _event_with_kind(
        event: ActivityEventRecord,
        kind: ActivityEventKind,
    ) -> ActivityEventRecord:
        candidate = object.__new__(ActivityEventRecord)
        for field in dataclasses.fields(ActivityEventRecord):
            object.__setattr__(
                candidate,
                field.name,
                kind if field.name == "kind" else getattr(event, field.name),
            )
        return candidate

    def test_root_and_inventory_publish_only_the_stage_one_surface(self) -> None:
        missing = sorted(ROOT_EXPORTS.difference(operations_root.__all__))
        self.assertEqual(missing, [], "reconciliation root exports are missing")

        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        entries = {
            row["module"]: row
            for row in inventory["modules"]
            if row["module"] in {LANGUAGE_MODULE, INTERPRETER_MODULE}
        }
        self.assertEqual(set(entries), {LANGUAGE_MODULE, INTERPRETER_MODULE})
        language = entries[LANGUAGE_MODULE]
        interpreter = entries[INTERPRETER_MODULE]
        self.assertEqual(language["owner"], "operation")
        self.assertEqual(interpreter["owner"], "operation")
        self.assertEqual(
            set(language["canonical_public_exports"]),
            ROOT_EXPORTS - {"EffectAttemptReconciliationService"},
        )
        self.assertEqual(
            interpreter["canonical_public_exports"],
            ["EffectAttemptReconciliationService"],
        )
        self.assertEqual(
            set(language["internal_dependencies"]),
            {
                "control_plane_kit_core.operations",
                "control_plane_kit_core.policies",
                "control_plane_kit_core.runtime_effect_observation",
                "control_plane_kit_operations.execution_leases",
                "control_plane_kit_operations.lifecycle",
                "control_plane_kit_operations.runtime_authorities",
                "control_plane_kit_operations.workflows",
            },
        )
        self.assertEqual(
            set(interpreter["internal_dependencies"]),
            {
                "control_plane_kit_core.policies",
                "control_plane_kit_operations.effect_attempt_fold",
                LANGUAGE_MODULE,
                "control_plane_kit_operations.workflows",
            },
        )
        self.assertEqual(
            set(language["protecting_tests"]),
            {"tests/test_effect_attempt_reconciliation_contract.py"},
        )
        self.assertEqual(
            set(interpreter["protecting_tests"]),
            {"tests/test_effect_attempt_reconciliation_interpreter_contract.py"},
        )
        self.assertIn("DB-free", interpreter["motivation"])
        for held in (
            "PostgreSQL",
            "expiry",
            "grant",
            "race",
            "replay",
        ):
            self.assertNotIn(held, interpreter["motivation"])


if __name__ == "__main__":
    unittest.main()
