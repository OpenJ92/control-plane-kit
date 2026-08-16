from __future__ import annotations

from dataclasses import fields, replace
import ast
import inspect
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core import (
    EffectResultKind,
    RuntimeEffectObservedSucceeded,
    RuntimeEffectResult,
)
from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptTransition,
)
from control_plane_kit_operations.records import OperationsRecordError

from effect_outcome_evidence_fixture import (
    EffectOutcomeEvidenceFixture,
    EffectOutcomeProfile,
    ExecutionEffectOutcome,
    MODULE_NAME,
    ObservedEffectOutcome,
    REQUEST_FINGERPRINT,
    effect_outcome_failure,
    effect_outcome_transition,
)


ROOT_EXPORTS = {
    "EffectOutcomeProfile",
    "ExecutionEffectOutcome",
    "ObservedEffectOutcome",
    "EffectAttemptOutcome",
    "EffectAttemptOutcomeRecord",
    "effect_outcome_transition",
    "effect_outcome_failure",
    "effect_outcome_observation_records",
}


def inventory_path() -> Path:
    return Path(
        os.environ.get(
            "CPK_PACKAGE_MODULE_INVENTORY",
            Path(__file__).parents[2]
            / "docs"
            / "architecture"
            / "package-module-inventory.json",
        )
    )


class HostileExecutionEffectOutcome(ExecutionEffectOutcome or object):
    pass


class HostileObservedEffectOutcome(ObservedEffectOutcome or object):
    pass


class HostileRuntimeEffectResult(RuntimeEffectResult):
    pass


class HostileObservedSucceeded(RuntimeEffectObservedSucceeded):
    pass


class EffectOutcomeEvidencePredecessorTest(
    EffectOutcomeEvidenceFixture,
    unittest.TestCase,
):
    def test_all_ten_outcomes_build_valid_predecessor_worlds_in_both_phases(self) -> None:
        stories = self.stories()

        self.assertEqual(len(stories), 20)
        self.assertEqual(
            {(story.name, story.compensation) for story in stories},
            {
                (name, compensation)
                for name, *_ in self.raw_rows()
                for compensation in (False, True)
            },
        )
        for story in stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                self.assertEqual(story.attempt.state.status, story.status)
                self.assertEqual(
                    story.attempt.original_start_event.event_id,
                    story.value.effect_id,
                )
                self.assertEqual(
                    story.attempt.state.request_fingerprint,
                    REQUEST_FINGERPRINT,
                )
                self.assertIsNone(story.attempt.state.recovery_decision)
                self.assertEqual(
                    story.attempt.state.outcome_fingerprint,
                    story.fingerprint,
                )


class EffectOutcomeEvidenceContractTest(
    EffectOutcomeEvidenceFixture,
    unittest.TestCase,
):
    def test_package_root_declares_the_exact_public_surface(self) -> None:
        missing = sorted(ROOT_EXPORTS.difference(operations_root.__all__))
        self.assertEqual(missing, [], "effect-outcome root exports are missing")

    def test_package_inventory_declares_the_exact_owned_module(self) -> None:
        inventory = json.loads(inventory_path().read_text())
        rows = [
            value
            for value in inventory["modules"]
            if value.get("module") == MODULE_NAME
        ]
        self.assertEqual(len(rows), 1, "effect-outcome inventory row is missing")

    def test_public_language_is_closed_and_root_exported_by_identity(self) -> None:
        self.require_outcome_language()

        self.assertEqual(
            tuple(member.value for member in EffectOutcomeProfile),
            ("execution-result", "provider-observation"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ExecutionEffectOutcome)),
            ("identity", "request_fingerprint", "result"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ObservedEffectOutcome)),
            ("identity", "observation"),
        )
        for name in ROOT_EXPORTS:
            with self.subTest(name=name):
                self.assertIn(name, operations_root.__all__)
                self.assertIs(
                    getattr(operations_root, name),
                    getattr(__import__(MODULE_NAME, fromlist=(name,)), name),
                )

    def test_transition_and_failure_projection_cover_all_ten_rows(self) -> None:
        self.require_outcome_language()

        for story in self.stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                outcome = self.outcome_for(story)
                transition = effect_outcome_transition(outcome)
                failure = effect_outcome_failure(outcome)

                self.assertEqual(
                    transition,
                    EffectAttemptTransition(
                        kind=story.transition,
                        identity=story.attempt.state.identity,
                        outcome_fingerprint=story.fingerprint,
                    ),
                )
                self.assertEqual(
                    failure,
                    story.attempt.latest_transition_event.failure,
                )
                if failure is not None:
                    rendered = f"{failure!s} {failure!r}"
                    for canary in (
                        "provider-canary",
                        "provider message",
                        "provider-detail-canary",
                        "observer-canary",
                        "observer message",
                        "observer-detail-canary",
                        "bounded-evidence-canary",
                    ):
                        self.assertNotIn(canary, rendered)

    def test_outcome_descriptors_commit_only_to_bounded_coordinates(self) -> None:
        self.require_outcome_language()

        for story in self.stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                outcome = self.outcome_for(story)
                descriptor = outcome.descriptor()
                self.assertEqual(
                    descriptor,
                    {
                        "profile": story.profile,
                        "identity": story.attempt.state.identity.descriptor(),
                        "effect_id": story.value.effect_id,
                        "request_fingerprint": REQUEST_FINGERPRINT,
                        "outcome_fingerprint": story.fingerprint,
                        "transition_kind": story.transition.value,
                        "observation_count": len(story.endpoint_observations),
                    },
                )
                rendered = f"{outcome!s} {outcome!r} {descriptor!s}"
                for canary in (
                    "success-canary",
                    "provider-canary",
                    "provider message",
                    "provider-detail-canary",
                    "observer-canary",
                    "observer message",
                    "observer-detail-canary",
                    "bounded-evidence-canary",
                    "http://service-a:8080",
                    "http://service-b:8080",
                ):
                    self.assertNotIn(canary, rendered)

    def test_outer_and_nested_nominal_types_are_revalidated(self) -> None:
        self.require_outcome_language()
        execution = next(
            story for story in self.stories() if story.name == "execution-succeeded"
        )
        observed = next(
            story for story in self.stories() if story.name == "observed-succeeded"
        )

        hostile_result = HostileRuntimeEffectResult(
            execution.value.effect_id,
            EffectResultKind.SUCCEEDED,
            execution.value.evidence,
            observations=execution.value.observations,
        )
        hostile_observation = HostileObservedSucceeded(
            observed.value.effect_id,
            observed.value.request_fingerprint,
            observed.value.evidence,
            observations=observed.value.observations,
        )
        hostile_identity = type(
            "HostileIdentity",
            (EffectAttemptIdentity,),
            {},
        )(*execution.attempt.state.identity.__dict__.values())

        candidates = (
            lambda: ExecutionEffectOutcome(
                hostile_identity,
                REQUEST_FINGERPRINT,
                execution.value,
            ),
            lambda: ExecutionEffectOutcome(
                execution.attempt.state.identity,
                REQUEST_FINGERPRINT,
                hostile_result,
            ),
            lambda: ObservedEffectOutcome(
                observed.attempt.state.identity,
                hostile_observation,
            ),
            lambda: effect_outcome_transition(
                HostileExecutionEffectOutcome(
                    execution.attempt.state.identity,
                    REQUEST_FINGERPRINT,
                    execution.value,
                )
            ),
            lambda: effect_outcome_failure(
                HostileObservedEffectOutcome(
                    observed.attempt.state.identity,
                    observed.value,
                )
            ),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assert_fixed_error(
                    candidate,
                    "effect outcome evidence is invalid",
                )

    def test_effect_and_request_coordinates_are_exact_and_congruent(self) -> None:
        self.require_outcome_language()
        execution = next(
            story for story in self.stories() if story.name == "execution-succeeded"
        )
        observed = next(
            story for story in self.stories() if story.name == "observed-succeeded"
        )
        hostile_request = type("HostileRequestFingerprint", (str,), {})(
            REQUEST_FINGERPRINT
        )

        candidates = (
            lambda: ExecutionEffectOutcome(
                execution.attempt.state.identity,
                hostile_request,
                execution.value,
            ),
            lambda: ExecutionEffectOutcome(
                execution.attempt.state.identity,
                "not-a-fingerprint",
                execution.value,
            ),
            lambda: ObservedEffectOutcome(
                observed.attempt.state.identity,
                replace(observed.value, request_fingerprint="not-a-fingerprint"),
            ),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assert_fixed_error(
                    candidate,
                    "effect outcome evidence is invalid",
                    "not-a-fingerprint",
                )

    def test_effect_identity_uses_the_exact_postgres_text_domain(self) -> None:
        self.require_outcome_language()
        story = next(
            item for item in self.stories() if item.name == "execution-succeeded"
        )
        exact_max = replace(story.value, effect_id="e" * 512)
        self.assertEqual(
            ExecutionEffectOutcome(
                story.attempt.state.identity,
                REQUEST_FINGERPRINT,
                exact_max,
            ).result.effect_id,
            "e" * 512,
        )

        for candidate in (
            type("HostileEffectId", (str,), {})(story.value.effect_id),
            "e" * 513,
            "event\x00canary",
            "event-surrogate-\ud800",
        ):
            forged = object.__new__(RuntimeEffectResult)
            object.__setattr__(forged, "effect_id", candidate)
            object.__setattr__(forged, "kind", story.value.kind)
            object.__setattr__(forged, "evidence", story.value.evidence)
            object.__setattr__(forged, "failure", story.value.failure)
            object.__setattr__(forged, "observations", story.value.observations)
            with self.subTest(candidate=repr(candidate)):
                self.assert_fixed_error(
                    lambda forged=forged: ExecutionEffectOutcome(
                        story.attempt.state.identity,
                        REQUEST_FINGERPRINT,
                        forged,
                    ),
                    "effect outcome evidence is invalid",
                    "canary",
                    "surrogate",
                )

    def test_invalid_candidates_are_cause_free_and_candidate_free(self) -> None:
        self.require_outcome_language()
        story = self.stories()[0]
        canary = "private-outcome-candidate"
        forged = object.__new__(RuntimeEffectResult)
        object.__setattr__(forged, "effect_id", canary)
        object.__setattr__(forged, "kind", EffectResultKind.SUCCEEDED)
        object.__setattr__(forged, "evidence", {})
        object.__setattr__(forged, "failure", None)
        object.__setattr__(forged, "observations", ())

        self.assert_fixed_error(
            lambda: ExecutionEffectOutcome(
                story.attempt.state.identity,
                REQUEST_FINGERPRINT,
                forged,
            ),
            "effect outcome evidence is invalid",
            canary,
        )

    def test_module_is_effect_free_and_inventory_owned(self) -> None:
        self.require_outcome_language()
        module = __import__(MODULE_NAME, fromlist=("__file__",))
        source_path = Path(inspect.getsourcefile(module))
        tree = ast.parse(source_path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        forbidden_fragments = (
            "postgres",
            "store",
            "unit_of_work",
            "coordinator",
            "interpreter",
            "effect_attempt_fold",
        )
        self.assertFalse(
            any(
                fragment in module_name
                for fragment in forbidden_fragments
                for module_name in imported
            )
        )

        inventory = json.loads(inventory_path().read_text())
        row = next(
            value
            for value in inventory["modules"]
            if value.get("module") == MODULE_NAME
        )
        self.assertEqual(row["owner"], "operation")
        self.assertEqual(row["destination"], MODULE_NAME)
        self.assertEqual(set(row["canonical_public_exports"]), ROOT_EXPORTS)
        self.assertEqual(
            set(row["internal_dependencies"]),
            {
                "control_plane_kit_core.operations",
                "control_plane_kit_core.runtime_effect_observation",
                "control_plane_kit_operations.effect_attempts",
                "control_plane_kit_operations.records",
            },
        )
        self.assertEqual(row["optional_external_dependencies"], [])


if __name__ == "__main__":
    unittest.main()
