from __future__ import annotations

import dataclasses
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureEvidence,
    OperationsRecordError,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.effect_attempt_fold_fixture import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldError,
    EffectAttemptFoldFixture,
    EffectAttemptFoldNotFound,
    EffectAttemptFoldResult,
    ExistingFold,
    FAILURE_STORIES,
    FOLD_MODULE,
    FOLD_STORIES,
    FoldEffectAttempt,
    NewlyFolded,
    _load_optional,
)
from tests.effect_attempt_record_fixture import EffectAttemptRecord


class EffectAttemptFoldLanguageTests(
    EffectAttemptFoldFixture,
    unittest.TestCase,
):
    def test_missing_module_guard_preserves_nested_import_failures(self) -> None:
        nested = ModuleNotFoundError("nested dependency missing")
        nested.name = "nested_dependency"

        def missing_nested(_name):
            raise nested

        with self.assertRaises(ModuleNotFoundError) as caught:
            _load_optional(FOLD_MODULE, missing_nested)
        self.assertIs(caught.exception, nested)

        def partial_import(_name):
            raise ImportError("partial public module")

        with self.assertRaises(ImportError):
            _load_optional(FOLD_MODULE, partial_import)

    def test_command_is_exact_frozen_nominal_and_root_identical(self) -> None:
        command = self.command()
        self.assertIs(getattr(operations_root, "FoldEffectAttempt", None), FoldEffectAttempt)
        self.assertEqual(FoldEffectAttempt.__module__, FOLD_MODULE)
        self.assertTrue(dataclasses.is_dataclass(FoldEffectAttempt))
        self.assertTrue(FoldEffectAttempt.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(FoldEffectAttempt)),
            ("request_id", "transition", "authority", "fence", "failure"),
        )
        self.assertEqual(
            command,
            FoldEffectAttempt(
                command.request_id,
                command.transition,
                command.authority,
                command.fence,
                command.failure,
            ),
        )

        class HostileCommand(FoldEffectAttempt):
            pass

        with self.assertRaises(InvalidOperationCommand) as caught:
            HostileCommand(**command.__dict__)
        self.assertEqual(str(caught.exception), "effect attempt fold command is invalid")
        self.assert_safe_error(caught.exception)

    def test_command_closes_transition_and_failure_evidence_sum(self) -> None:
        for story in FOLD_STORIES:
            with self.subTest(story=story, valid=True):
                self.assertEqual(self.command(story).transition, self.transition(story))

            wrong_failure = None if story in FAILURE_STORIES else self.failure(story)
            with self.subTest(story=story, valid=False):
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.command(story, failure=wrong_failure)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt fold command is invalid",
                )
                self.assert_safe_error(caught.exception)

        started = EffectAttemptTransition(
            EffectAttemptTransitionKind.STARTED,
            self.identity(),
            request_fingerprint="a" * 64,
        )
        with self.assertRaises(InvalidOperationCommand) as caught:
            self.command(transition=started)
        self.assertEqual(str(caught.exception), "effect attempt fold command is invalid")
        self.assert_safe_error(caught.exception)

    def test_command_rejects_non_nominal_nested_values_and_hostile_text(self) -> None:
        self.require_fold_language()

        class HostileTransition(EffectAttemptTransition):
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

        class HostileFailure(FailureEvidence):
            pass

        class HostileDetails(BoundedEvidence):
            pass

        class HostileRecoveryDecision(EffectRecoveryDecision):
            pass

        class HostileText(str):
            pass

        transition = self.transition("failed")
        hostile_transition = HostileTransition(**transition.__dict__)
        hostile_identity = HostileIdentity(**transition.identity.__dict__)
        nested_identity = EffectAttemptTransition(
            transition.kind,
            hostile_identity,
            outcome_fingerprint=transition.outcome_fingerprint,
        )
        hostile_fingerprint = EffectAttemptTransition(
            transition.kind,
            transition.identity,
            outcome_fingerprint=HostileText(transition.outcome_fingerprint),
        )
        failure = self.failure("nested-canary")
        hostile_failure = HostileFailure(**failure.__dict__)
        hostile_worker = HostileText("hostile-worker-canary")
        recovery = self.transition("recovered-failed")
        decision = recovery.recovery_decision
        hostile_decision = HostileRecoveryDecision(**decision.__dict__)
        nested_decision = EffectAttemptTransition(
            recovery.kind,
            recovery.identity,
            recovery_decision=hostile_decision,
        )
        hostile_details = HostileDetails(failure.details.canonical_json)
        nested_failure = FailureEvidence(
            failure.category,
            failure.code,
            failure.message,
            hostile_details,
        )
        hostile_decision_identity = HostileIdentity(**decision.attempt_identity.__dict__)
        decision_with_hostile_identity = EffectRecoveryDecision(
            decision.decision_id,
            hostile_decision_identity,
            decision.resolution,
            decision.uncertain_fingerprint,
            decision.evidence_fingerprint,
        )
        transition_with_hostile_decision_identity = EffectAttemptTransition(
            recovery.kind,
            recovery.identity,
            recovery_decision=decision_with_hostile_identity,
        )
        hostile_decision_texts = (
            (
                "decision-id-canary",
                EffectRecoveryDecision(
                    HostileText("decision-id-canary"),
                    decision.attempt_identity,
                    decision.resolution,
                    decision.uncertain_fingerprint,
                    decision.evidence_fingerprint,
                ),
            ),
            (
                decision.uncertain_fingerprint,
                EffectRecoveryDecision(
                    decision.decision_id,
                    decision.attempt_identity,
                    decision.resolution,
                    HostileText(decision.uncertain_fingerprint),
                    decision.evidence_fingerprint,
                ),
            ),
            (
                decision.evidence_fingerprint,
                EffectRecoveryDecision(
                    decision.decision_id,
                    decision.attempt_identity,
                    decision.resolution,
                    decision.uncertain_fingerprint,
                    HostileText(decision.evidence_fingerprint),
                ),
            ),
        )
        hostile_failure_leaves = (
            (
                "failure-code-canary",
                FailureEvidence(
                    failure.category,
                    HostileText("failure-code-canary"),
                    failure.message,
                    failure.details,
                ),
            ),
            (
                "failure-message-canary",
                FailureEvidence(
                    failure.category,
                    failure.code,
                    HostileText("failure-message-canary"),
                    failure.details,
                ),
            ),
        )
        cases = (
            ({"transition": hostile_transition}, ()),
            ({"transition": nested_identity}, ()),
            ({"transition": hostile_fingerprint}, (transition.outcome_fingerprint,)),
            ({"authority": HostileAuthority("worker-a", ())}, ()),
            ({"fence": HostileFence("worker-a", 7)}, ()),
            ({"failure": hostile_failure}, ("nested-canary",)),
            ({"failure": nested_failure}, ("nested-canary",)),
            ({"transition": nested_decision}, ()),
            ({"transition": transition_with_hostile_decision_identity}, ()),
            *tuple(
                (
                    {
                        "transition": EffectAttemptTransition(
                            recovery.kind,
                            recovery.identity,
                            recovery_decision=hostile_text_decision,
                        )
                    },
                    (canary,),
                )
                for canary, hostile_text_decision in hostile_decision_texts
            ),
            *tuple(
                ({"failure": hostile_leaf}, (canary,))
                for canary, hostile_leaf in hostile_failure_leaves
            ),
            (
                {
                    "authority": self.authority(hostile_worker),
                    "fence": self.execution_fence(hostile_worker),
                },
                ("hostile-worker-canary",),
            ),
        )
        for changes, canaries in cases:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.command("failed", **changes)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt fold command is invalid",
                )
                self.assert_safe_error(caught.exception, *canaries)

    def test_command_rejects_unbounded_or_incongruent_coordinates(self) -> None:
        class HostileText(str):
            pass

        cases = (
            ({"request_id": ""}, ()),
            ({"request_id": None}, ()),
            ({"request_id": True}, ()),
            ({"request_id": "x" * 513}, ("x" * 513,)),
            ({"request_id": "request\ncanary"}, ("canary",)),
            ({"request_id": "request-\ud800-canary"}, ("canary",)),
            ({"request_id": HostileText("request-canary")}, ("request-canary",)),
            ({"authority": self.authority("worker-b")}, ("worker-b",)),
        )
        for changes, canaries in cases:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.command(**changes)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt fold command is invalid",
                )
                self.assert_safe_error(caught.exception, *canaries)

    def test_result_sum_is_exact_frozen_root_identical_and_settled_only(self) -> None:
        self.require_fold_language()
        self.assertEqual(EffectAttemptFoldResult, NewlyFolded | ExistingFold)
        for name, variant in (
            ("NewlyFolded", NewlyFolded),
            ("ExistingFold", ExistingFold),
        ):
            with self.subTest(variant=name):
                self.assertIs(getattr(operations_root, name, None), variant)
                self.assertTrue(dataclasses.is_dataclass(variant))
                self.assertTrue(variant.__dataclass_params__.frozen)
                self.assertEqual(
                    tuple(field.name for field in dataclasses.fields(variant)),
                    ("attempt",),
                )
                self.assertNotIn("from_descriptor", variant.__dict__)
        self.assertIs(
            getattr(operations_root, "EffectAttemptFoldResult", None),
            EffectAttemptFoldResult,
        )

        for compensation in (False, True):
            for story in FOLD_STORIES:
                record = self.record(story, compensation=compensation)
                with self.subTest(compensation=compensation, story=story):
                    self.assertEqual(NewlyFolded(record).attempt, record)
                    self.assertEqual(ExistingFold(record).attempt, record)

            started = self.record("started", compensation=compensation)
            for variant in (NewlyFolded, ExistingFold):
                with self.subTest(compensation=compensation, variant=variant):
                    with self.assertRaises(OperationsRecordError) as caught:
                        variant(started)
                    self.assertEqual(
                        str(caught.exception),
                        "effect attempt fold result is invalid",
                    )
                    self.assert_safe_error(caught.exception)

    def test_result_variants_reject_hostile_outer_and_nested_records(self) -> None:
        self.require_fold_language()
        record = self.record("succeeded")

        class HostileRecord(EffectAttemptRecord):
            pass

        class HostileState(EffectAttemptState):
            pass

        class HostileEvent(ActivityEventRecord):
            pass

        hostile_record = object.__new__(HostileRecord)
        for field in dataclasses.fields(EffectAttemptRecord):
            object.__setattr__(hostile_record, field.name, getattr(record, field.name))
        nested_record = object.__new__(EffectAttemptRecord)
        hostile_state = HostileState(**record.state.__dict__)
        for field in dataclasses.fields(EffectAttemptRecord):
            object.__setattr__(
                nested_record,
                field.name,
                hostile_state if field.name == "state" else getattr(record, field.name),
            )

        recovered = self.record("recovered-failed")
        hostile_original = HostileEvent(**recovered.original_start_event.__dict__)
        hostile_latest = HostileEvent(**recovered.latest_transition_event.__dict__)

        def forged_record(*, original, latest):
            forged = object.__new__(EffectAttemptRecord)
            object.__setattr__(forged, "state", recovered.state)
            object.__setattr__(forged, "original_start_event", original)
            object.__setattr__(forged, "latest_transition_event", latest)
            return forged

        hostile_original_record = forged_record(
            original=hostile_original,
            latest=recovered.latest_transition_event,
        )
        hostile_latest_record = forged_record(
            original=recovered.original_start_event,
            latest=hostile_latest,
        )

        class HostileNewlyFolded(NewlyFolded):
            pass

        class HostileExistingFold(ExistingFold):
            pass

        constructors = (
            lambda: NewlyFolded(hostile_record),
            lambda: ExistingFold(hostile_record),
            lambda: NewlyFolded(nested_record),
            lambda: ExistingFold(nested_record),
            lambda: NewlyFolded(hostile_original_record),
            lambda: ExistingFold(hostile_original_record),
            lambda: NewlyFolded(hostile_latest_record),
            lambda: ExistingFold(hostile_latest_record),
            lambda: HostileNewlyFolded(record),
            lambda: HostileExistingFold(record),
        )
        for construct in constructors:
            with self.subTest(construct=construct):
                with self.assertRaises(OperationsRecordError) as caught:
                    construct()
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt fold result is invalid",
                )
                self.assert_safe_error(caught.exception)

    def test_public_error_sum_is_closed_root_identical_and_candidate_free(self) -> None:
        self.require_fold_language()
        self.assertEqual(EffectAttemptFoldError.__bases__, (RuntimeError,))
        for name, error_type in (
            ("EffectAttemptFoldNotFound", EffectAttemptFoldNotFound),
            ("EffectAttemptFoldConflict", EffectAttemptFoldConflict),
            ("EffectAttemptFoldDenied", EffectAttemptFoldDenied),
        ):
            with self.subTest(error=name):
                self.assertEqual(error_type.__bases__, (EffectAttemptFoldError,))
                self.assertIs(getattr(operations_root, name, None), error_type)
                error = error_type("fixed categorical error")
                self.assert_safe_error(error, "secret-canary", "address-canary")
        self.assertIs(
            getattr(operations_root, "EffectAttemptFoldError", None),
            EffectAttemptFoldError,
        )


if __name__ == "__main__":
    unittest.main()
