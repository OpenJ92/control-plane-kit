from __future__ import annotations

import dataclasses
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptTransition,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.effect_attempt_record_fixture import (
    EffectAttemptRecord,
    EffectAttemptRecordFixture,
    STORIES,
)
from tests.effect_attempt_start_fixture import (
    EffectAttemptStartConflict,
    EffectAttemptStartDenied,
    EffectAttemptStartError,
    EffectAttemptStartFixture,
    EffectAttemptStartNotFound,
    EffectAttemptStartResult,
    ExistingAttempt,
    NewlyStarted,
    START_MODULE,
    StartEffectAttempt,
    _load_optional,
)


class EffectAttemptStartLanguageTests(
    EffectAttemptStartFixture,
    EffectAttemptRecordFixture,
    unittest.TestCase,
):
    def test_missing_module_guard_preserves_nested_import_failures(self) -> None:
        nested = ModuleNotFoundError("nested dependency missing")
        nested.name = "nested_dependency"

        def missing_nested(_name):
            raise nested

        with self.assertRaises(ModuleNotFoundError) as caught:
            _load_optional(START_MODULE, missing_nested)
        self.assertIs(caught.exception, nested)

        def partial_import(_name):
            raise ImportError("partial public module")

        with self.assertRaises(ImportError):
            _load_optional(START_MODULE, partial_import)

    def test_command_is_exact_frozen_nominal_and_root_identical(self) -> None:
        command = self.command()
        self.assertIs(
            getattr(operations_root, "StartEffectAttempt", None),
            StartEffectAttempt,
        )
        self.assertEqual(StartEffectAttempt.__module__, START_MODULE)
        self.assertTrue(dataclasses.is_dataclass(StartEffectAttempt))
        self.assertTrue(StartEffectAttempt.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(StartEffectAttempt)),
            ("request_id", "transition", "authority", "fence"),
        )
        self.assertEqual(
            command,
            StartEffectAttempt(
                "request-a",
                self.transition(),
                self.authority(),
                self.fence(),
            ),
        )

        class HostileCommand(StartEffectAttempt):
            pass

        with self.assertRaises(InvalidOperationCommand) as caught:
            HostileCommand(
                command.request_id,
                command.transition,
                command.authority,
                command.fence,
            )
        self.assertEqual(
            str(caught.exception),
            "effect attempt start command is invalid",
        )
        self.assert_safe_error(caught.exception)

    def test_command_rejects_every_non_nominal_or_unbounded_coordinate(self) -> None:
        self.require_language()

        class HostileTransition(EffectAttemptTransition):
            pass

        class HostileIdentity(EffectAttemptIdentity):
            pass

        class HostileAuthority(ExecutionWorkerAuthority):
            pass

        class HostileFence(ExecutionLeaseFence):
            pass

        class HostileText(str):
            pass

        transition = self.transition()
        hostile_transition = HostileTransition(**transition.__dict__)
        hostile_identity = HostileIdentity(**transition.identity.__dict__)
        nested_hostile = EffectAttemptTransition(
            transition.kind,
            hostile_identity,
            request_fingerprint=transition.request_fingerprint,
        )
        hostile_fingerprint = EffectAttemptTransition(
            transition.kind,
            transition.identity,
            request_fingerprint=HostileText(transition.request_fingerprint),
        )
        hostile_worker = HostileText("hostile-worker-canary")
        cases = (
            ({"request_id": ""}, ""),
            ({"request_id": None}, ""),
            ({"request_id": True}, ""),
            ({"request_id": "x" * 513}, "x" * 513),
            ({"request_id": "request\ncanary"}, "request\ncanary"),
            ({"request_id": HostileText("request-canary")}, "request-canary"),
            ({"transition": hostile_transition}, ""),
            ({"transition": nested_hostile}, ""),
            ({"transition": hostile_fingerprint}, transition.request_fingerprint),
            ({"authority": HostileAuthority("worker-a", ())}, ""),
            ({"fence": HostileFence("worker-a", 7)}, ""),
            ({"authority": self.authority("worker-b")}, "worker-b"),
            (
                {
                    "authority": self.authority(hostile_worker),
                    "fence": self.fence(hostile_worker),
                },
                hostile_worker,
            ),
        )
        for changes, canary in cases:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.command(**changes)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt start command is invalid",
                )
                self.assert_safe_error(caught.exception, canary)

    def test_command_accepts_only_started_attempt_one_without_a_prior(self) -> None:
        self.command()
        prior = self.identity(attempt=1)
        attempt_two = self.transition(
            attempt=2,
            prior_attempt=prior,
        )
        for transition in (self.settled_transition(), attempt_two):
            with self.subTest(
                kind=transition.kind,
                attempt=transition.identity.attempt,
            ):
                with self.assertRaises(InvalidOperationCommand) as caught:
                    self.command(transition=transition)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt start command is invalid",
                )
                self.assert_safe_error(caught.exception)

    def test_result_sum_is_exact_frozen_and_root_identical(self) -> None:
        self.require_language()
        self.assertEqual(EffectAttemptStartResult, NewlyStarted | ExistingAttempt)
        for name, variant in (
            ("NewlyStarted", NewlyStarted),
            ("ExistingAttempt", ExistingAttempt),
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
            getattr(operations_root, "EffectAttemptStartResult", None),
            EffectAttemptStartResult,
        )

    def test_newly_started_is_dispatch_distinct_and_existing_is_observation(
        self,
    ) -> None:
        self.require_language()
        for compensation in (False, True):
            for story in STORIES:
                record = self.record(story, compensation=compensation)
                with self.subTest(compensation=compensation, story=story):
                    self.assertEqual(ExistingAttempt(record).attempt, record)
                    if story == "started":
                        self.assertEqual(NewlyStarted(record).attempt, record)
                    else:
                        with self.assertRaises(OperationsRecordError) as caught:
                            NewlyStarted(record)
                        self.assertEqual(
                            str(caught.exception),
                            "effect attempt start result is invalid",
                        )
                        self.assert_safe_error(caught.exception)

    def test_result_variants_reject_hostile_outer_and_nested_records(self) -> None:
        self.require_language()
        record = self.record()

        class HostileRecord(EffectAttemptRecord):
            pass

        hostile = object.__new__(HostileRecord)
        for field in dataclasses.fields(EffectAttemptRecord):
            object.__setattr__(hostile, field.name, getattr(record, field.name))

        class HostileNewlyStarted(NewlyStarted):
            pass

        class HostileExistingAttempt(ExistingAttempt):
            pass

        constructors = (
            lambda: NewlyStarted(hostile),
            lambda: ExistingAttempt(hostile),
            lambda: HostileNewlyStarted(record),
            lambda: HostileExistingAttempt(record),
        )
        for construct in constructors:
            with self.subTest(construct=construct):
                with self.assertRaises(OperationsRecordError) as caught:
                    construct()
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt start result is invalid",
                )
                self.assert_safe_error(caught.exception)

    def test_public_error_sum_is_closed_root_identical_and_candidate_free(self) -> None:
        self.require_language()
        self.assertEqual(
            EffectAttemptStartError.__bases__,
            (RuntimeError,),
        )
        for name, error_type in (
            ("EffectAttemptStartNotFound", EffectAttemptStartNotFound),
            ("EffectAttemptStartConflict", EffectAttemptStartConflict),
            ("EffectAttemptStartDenied", EffectAttemptStartDenied),
        ):
            with self.subTest(error=name):
                self.assertEqual(error_type.__bases__, (EffectAttemptStartError,))
                self.assertIs(getattr(operations_root, name, None), error_type)
                error = error_type("fixed categorical error")
                self.assert_safe_error(error, "secret-canary", "address-canary")
        self.assertIs(
            getattr(operations_root, "EffectAttemptStartError", None),
            EffectAttemptStartError,
        )


if __name__ == "__main__":
    unittest.main()
