from __future__ import annotations

import dataclasses
import inspect
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_architecture_testing as architecture_testing
import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.atomic_effect_attempt_fold_fixture import (
    AtomicEffectAttemptFoldFixture,
    ExistingFold,
    NewlyFolded,
)
from tests.effect_attempt_fold_fixture import (
    EffectAttemptFoldDenied,
    EffectAttemptFoldResult,
    EffectAttemptFoldService,
    FOLD_MODULE,
    FoldEffectAttempt,
    INTERPRETER_MODULE,
)
from tests.effect_outcome_evidence_fixture import (
    EffectAttemptOutcomeRecord,
    ExecutionEffectOutcome,
    ObservedEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
    forge_exact,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = Path(
    os.environ.get(
        "CPK_PACKAGE_MODULE_INVENTORY",
        REPOSITORY_ROOT / "docs" / "architecture" / "package-module-inventory.json",
    )
)
INTERPRETER_SOURCE_PATH = (
    "control_plane_kit_operations/effect_attempt_fold_interpreter.py"
)

EXACT_INTERPRETER_IMPORTS = tuple(
    architecture_testing.ImportSurfaceEntry(*value)
    for value in (
        ("__future__", "annotations", None),
        ("control_plane_kit_core.operations", "EffectAttemptFence", None),
        ("control_plane_kit_core.operations", "EffectAttemptState", None),
        ("control_plane_kit_core.operations", "EffectAttemptStatus", None),
        ("control_plane_kit_core.operations", "EffectAttemptTransitionKind", None),
        ("control_plane_kit_core.operations", "InvalidEffectRecoveryContract", None),
        ("control_plane_kit_core.operations", "fold_effect_attempt", None),
        ("control_plane_kit_core.operations.lifecycle", "ActivityEventKind", None),
        (
            "control_plane_kit_core.operations.lifecycle",
            "ExecutionRequestStatus",
            None,
        ),
        ("control_plane_kit_core.policies", "PolicyScope", None),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldConflict",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldDenied",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldNotFound",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "EffectAttemptFoldResult",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "ExistingFold",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "FoldEffectAttempt",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "GuardedObservedEffectFold",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "NewlyFolded",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "_valid_fold_command",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempt_fold",
            "_valid_guarded_observed_fold",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempts",
            "EffectAttemptEventEvidence",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempts",
            "EffectAttemptRecord",
            None,
        ),
        (
            "control_plane_kit_operations.effect_attempts",
            "effect_attempt_state_fingerprint",
            None,
        ),
        (
            "control_plane_kit_operations.effect_outcome_evidence",
            "EffectAttemptOutcomeRecord",
            None,
        ),
        (
            "control_plane_kit_operations.effect_outcome_evidence",
            "effect_outcome_observation_records",
            None,
        ),
        (
            "control_plane_kit_operations.records",
            "ActivityEventRecord",
            None,
        ),
        ("control_plane_kit_operations.records", "BoundedEvidence", None),
        ("control_plane_kit_operations.records", "ObservationRecord", None),
        (
            "control_plane_kit_operations.records",
            "OperationsRecordError",
            None,
        ),
        (
            "control_plane_kit_operations.workflows",
            "InvalidOperationCommand",
            None,
        ),
        ("typing", "Any", None),
        ("typing", "Callable", None),
    )
)

EXACT_INTERPRETER_CALLS = (
    architecture_testing.UnresolvedCallTarget(),
    *tuple(
        architecture_testing.ResolvedCallTarget(value)
        for value in (
            "_EVENT_KIND_BY_STATE.get",
            "_attempt_for_update",
            "_event_kind",
            "_fold",
            "_observation",
            "_representable_effect_fence",
            "_request_for_update",
            "_require_current_authority",
            "_require_exact_replay",
            "_require_request_run",
            "_require_transition_authority",
            "_run_for_request_for_update",
            "_translate_fence",
            "control_plane_kit_core.operations.EffectAttemptFence",
            "control_plane_kit_core.operations.fold_effect_attempt",
            *("control_plane_kit_operations.effect_attempt_fold.EffectAttemptFoldConflict",) * 13,
            *("control_plane_kit_operations.effect_attempt_fold.EffectAttemptFoldDenied",) * 3,
            *("control_plane_kit_operations.effect_attempt_fold.EffectAttemptFoldNotFound",) * 3,
            "control_plane_kit_operations.effect_attempt_fold.ExistingFold",
            "control_plane_kit_operations.effect_attempt_fold.NewlyFolded",
            "control_plane_kit_operations.effect_attempt_fold._valid_fold_command",
            "control_plane_kit_operations.effect_attempt_fold._valid_guarded_observed_fold",
            "control_plane_kit_operations.effect_attempts.EffectAttemptEventEvidence",
            "control_plane_kit_operations.effect_attempts.EffectAttemptRecord",
            "control_plane_kit_operations.effect_attempts.effect_attempt_state_fingerprint",
            "control_plane_kit_operations.effect_outcome_evidence.EffectAttemptOutcomeRecord",
            "control_plane_kit_operations.effect_outcome_evidence.effect_outcome_observation_records",
            "control_plane_kit_operations.records.ActivityEventRecord",
            "control_plane_kit_operations.records.BoundedEvidence.from_mapping",
            *("control_plane_kit_operations.workflows.InvalidOperationCommand",) * 3,
            "len",
            "self._id_factory",
            "self._plan_result",
            "self._unit_of_work_factory",
            "self._unit_of_work_factory",
            "stores.effect_attempts.compare_and_set",
            "stores.effect_attempts.get_for_update",
            "stores.effect_outcomes.get",
            "stores.effect_outcomes.insert",
            "stores.execution.add_event",
            "stores.execution.get_request_for_update",
            "stores.execution.get_run_for_request_for_update",
            "stores.execution.next_event_ordinal",
            "stores.execution.observe_request_lease_for_update",
            "stores.observed_state.put",
            *("type",) * 8,
            "unit_of_work.commit",
            "worker_id.encode",
            "worker_id.strip",
        )
    ),
)


class FailIfUnitOfWork:
    def __init__(self, message: str = "unit of work opened") -> None:
        self.error = AssertionError(message)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise self.error


def forge_command(template, **changes):
    values = {
        field.name: getattr(template, field.name)
        for field in dataclasses.fields(FoldEffectAttempt)
    }
    values.update(changes)
    return forge_exact(FoldEffectAttempt, **values)


class AtomicEffectAttemptFoldContractTests(
    AtomicEffectAttemptFoldFixture,
    unittest.TestCase,
):
    def require_atomic_command_surface(self) -> None:
        self.require_fold_language()
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(FoldEffectAttempt)),
            ("request_id", "transition", "authority", "fence", "failure", "outcome"),
            "effect-attempt fold command lacks the direct outcome arm",
        )

    def require_atomic_result_surface(self) -> None:
        self.require_fold_language()
        for variant in (NewlyFolded, ExistingFold):
            self.assertEqual(
                tuple(field.name for field in dataclasses.fields(variant)),
                ("attempt", "outcome_record"),
                "effect-attempt fold result lacks the durable outcome arm",
            )

    def assert_invalid_command(self, construct, *canaries: str) -> None:
        with self.assertRaises(InvalidOperationCommand) as caught:
            construct()
        self.assertEqual(
            str(caught.exception),
            "effect attempt fold command is invalid",
        )
        self.assert_safe_error(caught.exception, *canaries)

    def assert_invalid_result(self, construct, *canaries: str) -> None:
        with self.assertRaises(OperationsRecordError) as caught:
            construct()
        self.assertEqual(
            str(caught.exception),
            "effect attempt fold result is invalid",
        )
        self.assert_safe_error(caught.exception, *canaries)

    def service(self, factory):
        self.require_fold_service()
        return EffectAttemptFoldService(factory, id_factory=lambda: "unused-event-id")

    def test_all_direct_and_recovery_predecessor_worlds_are_lawful(self) -> None:
        stories = self.stories()
        self.assertEqual(len(stories), 20)
        for story in stories:
            with self.subTest(
                arm="direct",
                story=story.name,
                compensation=story.compensation,
            ):
                outcome = self.outcome_for(story)
                record = self.direct_outcome_record(story)
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
                    story.attempt.latest_transition_event.failure,
                )
                self.assertEqual(record.attempt, story.attempt)
                self.assertEqual(
                    record.endpoint_observations,
                    self.expected_observation_records(story),
                )

        for compensation in (False, True):
            for story in ("recovered-succeeded", "recovered-failed", "abandoned"):
                with self.subTest(
                    arm="recovery",
                    story=story,
                    compensation=compensation,
                ):
                    record = self.recovery_record(
                        story,
                        compensation=compensation,
                    )
                    self.assertEqual(
                        EffectAttemptRecord(
                            record.state,
                            record.original_start_event,
                            record.latest_transition_event,
                        ),
                        record,
                    )

    def test_command_is_exact_frozen_dependent_sum_and_hides_outcome(self) -> None:
        self.require_atomic_command_surface()
        story = self.stories()[0]
        command = self.direct_command(story)

        self.assertTrue(FoldEffectAttempt.__dataclass_params__.frozen)
        self.assertIs(operations_root.FoldEffectAttempt, FoldEffectAttempt)
        outcome_field = dataclasses.fields(FoldEffectAttempt)[-1]
        self.assertFalse(outcome_field.repr)
        self.assertEqual(command.outcome, self.outcome_for(story))
        rendered = f"{command!s} {command!r}"
        for canary in ("provider-canary", "observer-canary", "http://service-a:8080"):
            self.assertNotIn(canary, rendered)

    def test_all_twenty_direct_rows_require_canonical_transition_and_failure(self) -> None:
        self.require_atomic_command_surface()
        stories = self.stories()
        self.assertEqual(len(stories), 20)
        for story in stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                command = self.direct_command(story)
                self.assertEqual(command.outcome, self.outcome_for(story))
                self.assertEqual(
                    command.transition,
                    effect_outcome_transition(command.outcome),
                )
                self.assertEqual(
                    command.failure,
                    effect_outcome_failure(command.outcome),
                )
                self.assertIsNone(command.transition.recovery_decision)

    def test_direct_command_arm_is_exactly_congruent_iff(self) -> None:
        self.require_atomic_command_surface()
        story = self.stories()[0]
        outcome = self.outcome_for(story)
        other = self.outcome_for(self.stories()[1])
        transition = effect_outcome_transition(outcome)
        wrong_transition = EffectAttemptTransition(
            EffectAttemptTransitionKind.UNCERTAIN,
            transition.identity,
            outcome_fingerprint=transition.outcome_fingerprint,
        )
        for construct in (
            lambda: self.direct_command(story, outcome=None),
            lambda: self.direct_command(story, outcome=other),
            lambda: self.direct_command(story, transition=wrong_transition),
            lambda: self.direct_command(story, failure=self.failure("private-canary")),
        ):
            self.assert_invalid_command(construct, "private-canary")

    def test_recovery_command_arm_requires_absent_outcome_exactly_iff(self) -> None:
        self.require_atomic_command_surface()
        for story in ("recovered-succeeded", "recovered-failed", "abandoned"):
            with self.subTest(story=story):
                command = self.recovery_command(story)
                self.assertIsNone(command.outcome)
                self.assertIsNotNone(command.transition.recovery_decision)
        direct = self.stories()[0]
        self.assert_invalid_command(
            lambda: self.recovery_command(
                "recovered-succeeded",
                outcome=self.outcome_for(direct),
            )
        )

    def test_command_revalidates_hostile_and_forged_outcomes_without_dispatch(self) -> None:
        self.require_atomic_command_surface()
        story = self.stories()[0]
        outcome = self.outcome_for(story)
        dispatches = []

        class HostileOutcome(ExecutionEffectOutcome):
            @property
            def outcome_fingerprint(self):
                dispatches.append("outcome_fingerprint")
                raise RuntimeError("private-outcome-canary")

        class HostileObservedOutcome(ObservedEffectOutcome):
            @property
            def outcome_fingerprint(self):
                dispatches.append("observed_outcome_fingerprint")
                raise RuntimeError("private-observed-canary")

        hostile = object.__new__(HostileOutcome)
        for field in dataclasses.fields(ExecutionEffectOutcome):
            object.__setattr__(hostile, field.name, getattr(outcome, field.name))
        forged = forge_exact(
            ExecutionEffectOutcome,
            identity=outcome.identity,
            request_fingerprint=outcome.request_fingerprint,
            result=forge_exact(
                type(outcome.result),
                effect_id=outcome.result.effect_id,
                kind="private-kind-canary",
                evidence=outcome.result.evidence,
                failure=outcome.result.failure,
                observations=outcome.result.observations,
            ),
        )
        observed_story = next(
            value
            for value in self.stories()
            if value.name == "observed-succeeded" and not value.compensation
        )
        observed = self.outcome_for(observed_story)
        hostile_observed = object.__new__(HostileObservedOutcome)
        for field in dataclasses.fields(ObservedEffectOutcome):
            object.__setattr__(
                hostile_observed,
                field.name,
                getattr(observed, field.name),
            )
        forged_observed = forge_exact(
            ObservedEffectOutcome,
            identity=observed.identity,
            observation=object(),
        )
        for candidate in (hostile, forged, hostile_observed, forged_observed):
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_invalid_command(
                    lambda candidate=candidate: self.direct_command(
                        observed_story
                        if type(candidate)
                        in (HostileObservedOutcome, ObservedEffectOutcome)
                        else story,
                        outcome=candidate,
                    ),
                    "private-outcome-canary",
                    "private-kind-canary",
                    "private-observed-canary",
                )
                self.assertEqual(dispatches, [])

    def test_result_is_exact_frozen_dependent_sum_and_hides_record(self) -> None:
        self.require_atomic_result_surface()
        story = self.stories()[0]
        record = self.direct_outcome_record(story)
        self.assertEqual(EffectAttemptFoldResult, NewlyFolded | ExistingFold)
        for variant in (NewlyFolded, ExistingFold):
            with self.subTest(variant=variant.__name__):
                value = variant(story.attempt, record)
                self.assertTrue(variant.__dataclass_params__.frozen)
                self.assertFalse(dataclasses.fields(variant)[1].repr)
                self.assertIs(value.outcome_record, record)
                rendered = f"{value!s} {value!r}"
                self.assertNotIn("provider-canary", rendered)
                self.assertNotIn("http://service-a:8080", rendered)

    def test_new_and_existing_results_are_total_for_direct_and_recovery_replay(self) -> None:
        self.require_atomic_result_surface()
        for story in self.stories():
            for variant in (NewlyFolded, ExistingFold):
                with self.subTest(
                    arm="direct",
                    story=story.name,
                    compensation=story.compensation,
                    variant=variant.__name__,
                ):
                    result = self.direct_result(variant, story)
                    self.assertEqual(result.attempt, story.attempt)
                    self.assertEqual(
                        result.outcome_record,
                        self.direct_outcome_record(story),
                    )
        for compensation in (False, True):
            for story in ("recovered-succeeded", "recovered-failed", "abandoned"):
                for variant in (NewlyFolded, ExistingFold):
                    with self.subTest(
                        arm="recovery",
                        story=story,
                        compensation=compensation,
                        variant=variant.__name__,
                    ):
                        result = self.recovery_result(
                            variant,
                            story,
                            compensation=compensation,
                        )
                        self.assertIsNone(result.outcome_record)

    def test_result_revalidates_attempt_and_outcome_record_deeply(self) -> None:
        self.require_atomic_result_surface()
        story = self.stories()[0]
        outcome_record = self.direct_outcome_record(story)
        valid = NewlyFolded(story.attempt, outcome_record)
        hostile_attempt = type("HostileAttempt", (EffectAttemptRecord,), {})
        hostile = object.__new__(hostile_attempt)
        for field in dataclasses.fields(EffectAttemptRecord):
            object.__setattr__(hostile, field.name, getattr(story.attempt, field.name))
        forged_record = forge_exact(
            EffectAttemptOutcomeRecord,
            workspace_id=outcome_record.workspace_id,
            outcome=self.outcome_for(self.stories()[1]),
            attempt=outcome_record.attempt,
            endpoint_observations=outcome_record.endpoint_observations,
        )
        recovery = self.recovery_record(
            "recovered-succeeded",
            compensation=False,
        )

        class HostileNewlyFolded(NewlyFolded):
            pass

        class HostileExistingFold(ExistingFold):
            pass

        for construct in (
            lambda: NewlyFolded(story.attempt, None),
            lambda: ExistingFold(story.attempt, None),
            lambda: NewlyFolded(hostile, outcome_record),
            lambda: ExistingFold(hostile, outcome_record),
            lambda: NewlyFolded(story.attempt, forged_record),
            lambda: ExistingFold(story.attempt, forged_record),
            lambda: NewlyFolded(recovery, outcome_record),
            lambda: ExistingFold(recovery, outcome_record),
            lambda: HostileNewlyFolded(valid.attempt, valid.outcome_record),
            lambda: HostileExistingFold(valid.attempt, valid.outcome_record),
        ):
            self.assert_invalid_result(construct)

    def test_service_preflight_enforces_sum_before_unit_of_work(self) -> None:
        self.require_atomic_command_surface()
        story = self.stories()[0]
        valid_direct = self.direct_command(story)
        valid_recovery = self.recovery_command()
        invalid = (
            forge_command(valid_direct, outcome=None),
            forge_command(valid_recovery, outcome=self.outcome_for(story)),
        )
        for command in invalid:
            fail = FailIfUnitOfWork("invalid sum reached unit of work")
            with self.assertRaises(InvalidOperationCommand) as caught:
                self.service(fail).execute(command)
            self.assertEqual(
                str(caught.exception),
                "effect attempt fold command is invalid",
            )
            self.assert_safe_error(caught.exception)
            self.assertEqual(fail.calls, 0)

        for command in (valid_direct, valid_recovery):
            fail = FailIfUnitOfWork("valid sum reached unit of work")
            with self.assertRaises(AssertionError) as caught:
                self.service(fail).execute(command)
            self.assertIs(caught.exception, fail.error)
            self.assertEqual(fail.calls, 1)

        denied = dataclasses.replace(
            valid_direct,
            authority=self.authority(scopes=()),
        )
        fail = FailIfUnitOfWork("scope denial reached unit of work")
        with self.assertRaises(EffectAttemptFoldDenied):
            self.service(fail).execute(denied)
        self.assertEqual(fail.calls, 0)

    def test_interpreter_shared_import_and_call_policy_is_exact(self) -> None:
        module = __import__(INTERPRETER_MODULE, fromlist=("__file__",))
        source_path = Path(inspect.getsourcefile(module))
        facts = architecture_testing.analyze_source(
            source_path.read_text(encoding="utf-8"),
            path=INTERPRETER_SOURCE_PATH,
            module=INTERPRETER_MODULE,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId("cpk.operations.atomic-fold.imports"),
                    architecture_testing.RuleId("exact"),
                    INTERPRETER_SOURCE_PATH,
                    INTERPRETER_MODULE,
                    EXACT_INTERPRETER_IMPORTS,
                    "atomic fold interpreter import surface differs",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId("cpk.operations.atomic-fold.calls"),
                    architecture_testing.RuleId("exact"),
                    INTERPRETER_SOURCE_PATH,
                    INTERPRETER_MODULE,
                    EXACT_INTERPRETER_CALLS,
                    "atomic fold interpreter lexical call surface differs",
                ),
            ),
        )
        self.assertEqual(findings, ())

    def test_root_and_inventory_publish_the_atomic_dependent_sum(self) -> None:
        expected_exports = {
            "FoldEffectAttempt",
            "GuardedObservedEffectFold",
            "NewlyFolded",
            "ExistingFold",
            "EffectAttemptFoldResult",
        }
        with self.subTest(surface="root"):
            self.assertTrue(expected_exports.issubset(operations_root.__all__))
            self.assertNotIn("_valid_guarded_observed_fold", operations_root.__all__)
            self.assertFalse(
                hasattr(operations_root, "_valid_guarded_observed_fold")
            )

        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        entries = {
            row["module"]: row
            for row in inventory["modules"]
            if row["module"] in {FOLD_MODULE, INTERPRETER_MODULE}
        }
        with self.subTest(surface="inventory-modules"):
            self.assertEqual(set(entries), {FOLD_MODULE, INTERPRETER_MODULE})
        with self.subTest(surface="fold-dependencies"):
            self.assertEqual(
                set(entries[FOLD_MODULE]["internal_dependencies"]),
                {
                    "control_plane_kit_core.operations",
                    "control_plane_kit_core.policies",
                    "control_plane_kit_core.runtime_authority",
                    "control_plane_kit_core.secrets",
                    "control_plane_kit_core.types",
                    "control_plane_kit_operations.effect_attempt_intent_evidence",
                    "control_plane_kit_operations.effect_attempts",
                    "control_plane_kit_operations.effect_outcome_evidence",
                    "control_plane_kit_operations.execution_leases",
                    "control_plane_kit_operations.lifecycle",
                    "control_plane_kit_operations.records",
                    "control_plane_kit_operations.runtime_authorities",
                    "control_plane_kit_operations.workflows",
                },
            )
        with self.subTest(surface="fold-exports"):
            self.assertEqual(
                set(entries[FOLD_MODULE]["canonical_public_exports"]),
                {
                    "EffectAttemptFoldConflict",
                    "EffectAttemptFoldDenied",
                    "EffectAttemptFoldError",
                    "EffectAttemptFoldNotFound",
                    "EffectAttemptFoldResult",
                    "ExistingFold",
                    "FoldEffectAttempt",
                    "GuardedObservedEffectFold",
                    "NewlyFolded",
                },
            )
        with self.subTest(surface="fold-existing-protection"):
            self.assertIn(
                "tests/test_atomic_effect_attempt_fold_contract.py",
                entries[FOLD_MODULE]["protecting_tests"],
            )
        with self.subTest(surface="fold-guarded-protection"):
            self.assertIn(
                "tests/test_guarded_observed_effect_fold_contract.py",
                entries[FOLD_MODULE]["protecting_tests"],
            )
        with self.subTest(surface="interpreter-dependencies"):
            self.assertEqual(
                set(entries[INTERPRETER_MODULE]["internal_dependencies"]),
                {
                    "control_plane_kit_core.operations",
                    "control_plane_kit_core.policies",
                    "control_plane_kit_operations.effect_attempt_fold",
                    "control_plane_kit_operations.effect_attempts",
                    "control_plane_kit_operations.effect_outcome_evidence",
                    "control_plane_kit_operations.records",
                    "control_plane_kit_operations.workflows",
                },
            )
        with self.subTest(surface="interpreter-exports"):
            self.assertEqual(
                entries[INTERPRETER_MODULE]["canonical_public_exports"],
                ["EffectAttemptFoldService"],
            )
        with self.subTest(surface="interpreter-existing-protection"):
            self.assertIn(
                "tests/test_atomic_effect_attempt_fold_contract.py",
                entries[INTERPRETER_MODULE]["protecting_tests"],
            )
        with self.subTest(surface="interpreter-guarded-protection"):
            self.assertIn(
                "tests/test_guarded_observed_effect_fold_contract.py",
                entries[INTERPRETER_MODULE]["protecting_tests"],
            )


if __name__ == "__main__":
    unittest.main()
