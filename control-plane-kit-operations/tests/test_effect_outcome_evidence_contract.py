from __future__ import annotations

from dataclasses import fields, replace
import inspect
import json
import os
from pathlib import Path
import unittest

import rfc8785

import control_plane_kit_architecture_testing as architecture_testing
import control_plane_kit_operations as operations_root
from control_plane_kit_core import (
    EffectResultKind,
    RuntimeEffectObservedSucceeded,
    RuntimeEffectResult,
    runtime_effect_observation_fingerprint,
    runtime_effect_result_fingerprint,
)
from control_plane_kit_core.probe_intents import (
    RuntimeEndpointObservation,
    SecretEndpointMaterial,
)
from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptTransition,
    RunId,
)
from control_plane_kit_operations.records import FailureEvidence, OperationsRecordError

from effect_outcome_evidence_fixture import (
    EffectOutcomeEvidenceFixture,
    EffectOutcomeProfile,
    ExecutionEffectOutcome,
    MODULE_NAME,
    ObservedEffectOutcome,
    OUTCOME_MAX_BYTES,
    REQUEST_FINGERPRINT,
    effect_outcome_failure,
    effect_outcome_transition,
    forge_exact,
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

OUTCOME_SOURCE_PATH = "control_plane_kit_operations/effect_outcome_evidence.py"
EXACT_IMPORT_SURFACE = (
    architecture_testing.ImportSurfaceEntry("__future__", "annotations", None),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "EffectAttemptIdentity",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "EffectAttemptStatus",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "EffectAttemptTransition",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "EffectAttemptTransitionKind",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "EffectResultKind",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "FailureCategory",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "RunId",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.probe_intents",
        "EndpointContext",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.probe_intents",
        "LiteralEndpointMaterial",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.probe_intents",
        "RuntimeEndpointObservation",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.probe_intents",
        "SecretEndpointMaterial",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservationEvidence",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservationFailure",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservationResult",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservedAbsent",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservedConflict",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservedFailed",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservedIndeterminate",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObservedSucceeded",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectObserverUnsupported",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_observation_fingerprint",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_result_fingerprint",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effects",
        "RuntimeEffectContractError",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effects",
        "RuntimeEffectResult",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.types",
        "Protocol",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.effect_attempts",
        "EffectAttemptRecord",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "BoundedEvidence",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "FailureEvidence",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "ObservationFreshness",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "ObservationRecord",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "ObservationStatus",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "OperationsRecordError",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "ProbeKind",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "ProbeOutcome",
        None,
    ),
    architecture_testing.ImportSurfaceEntry("dataclasses", "dataclass", None),
    architecture_testing.ImportSurfaceEntry("dataclasses", "field", None),
    architecture_testing.ImportSurfaceEntry("enum", "StrEnum", None),
)
EXACT_CALL_SURFACE = (
    architecture_testing.ResolvedCallTarget("_validated_attempt"),
    architecture_testing.ResolvedCallTarget("_validated_attempt"),
    architecture_testing.ResolvedCallTarget("any"),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.operations.EffectAttemptTransition"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.probe_intents.RuntimeEndpointObservation"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.probe_intents.SecretEndpointMaterial"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_observation_fingerprint"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation."
        "runtime_effect_result_fingerprint"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.types.Protocol"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.effect_attempts.EffectAttemptRecord"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.BoundedEvidence.from_mapping"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.FailureEvidence"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.ObservationRecord"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.OperationsRecordError"
    ),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("dataclasses.field"),
    architecture_testing.ResolvedCallTarget("dataclasses.field"),
    architecture_testing.ResolvedCallTarget("dataclasses.field"),
    architecture_testing.ResolvedCallTarget("dataclasses.field"),
    architecture_testing.ResolvedCallTarget("dataclasses.field"),
    architecture_testing.ResolvedCallTarget("endpoint.descriptor"),
    architecture_testing.ResolvedCallTarget("enumerate"),
    architecture_testing.ResolvedCallTarget("len"),
    architecture_testing.ResolvedCallTarget("ord"),
    architecture_testing.ResolvedCallTarget("set"),
    architecture_testing.ResolvedCallTarget("tuple"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("value.encode"),
    architecture_testing.ResolvedCallTarget("zip"),
)


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

    def test_boundary_vectors_are_exact_valid_predecessor_values(self) -> None:
        maximum_live = self.live_result_for_size(OUTCOME_MAX_BYTES)
        oversized_live = self.live_result_for_size(OUTCOME_MAX_BYTES + 1)
        maximum_observed = self.observed_result_for_size(OUTCOME_MAX_BYTES)
        oversized_observed = self.observed_result_for_size(OUTCOME_MAX_BYTES + 1)
        maximum_endpoint = self.endpoint_for_bridge_size(4_096)
        oversized_endpoint = self.endpoint_for_bridge_size(4_097)

        self.assertEqual(len(rfc8785.dumps(maximum_live.descriptor())), 8_192)
        self.assertEqual(len(rfc8785.dumps(oversized_live.descriptor())), 8_193)
        self.assertEqual(len(rfc8785.dumps(maximum_observed.descriptor())), 8_192)
        self.assertEqual(len(rfc8785.dumps(oversized_observed.descriptor())), 8_193)
        self.assertIs(type(maximum_endpoint), RuntimeEndpointObservation)
        self.assertIs(type(oversized_endpoint), RuntimeEndpointObservation)
        self.assertEqual(
            len(
                json.dumps(
                    {"runtime_endpoint": maximum_endpoint.descriptor()},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            4_096,
        )
        self.assertEqual(
            len(
                json.dumps(
                    {"runtime_endpoint": oversized_endpoint.descriptor()},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            4_097,
        )
        policy_path = "tests/shared_architecture_policy_canary.py"
        policy_module = "shared_architecture_policy_canary"
        facts = architecture_testing.analyze_source(
            "from sample.tools import inspect as inspect_value\n"
            "inspect_value()\n",
            path=policy_path,
            module=policy_module,
        )
        self.assertEqual(
            architecture_testing.evaluate_policies(
                (facts,),
                (
                    architecture_testing.ExactImportSurfacePolicy(
                        architecture_testing.PolicyId("cpk.canary.imports"),
                        architecture_testing.RuleId("exact"),
                        policy_path,
                        policy_module,
                        (
                            architecture_testing.ImportSurfaceEntry(
                                "sample.tools",
                                "inspect",
                                "inspect_value",
                            ),
                        ),
                        "shared import surface differs",
                    ),
                    architecture_testing.ExactCallSurfacePolicy(
                        architecture_testing.PolicyId("cpk.canary.calls"),
                        architecture_testing.RuleId("exact"),
                        policy_path,
                        policy_module,
                        (
                            architecture_testing.ResolvedCallTarget(
                                "sample.tools.inspect"
                            ),
                        ),
                        "shared call surface differs",
                    ),
                ),
            ),
            (),
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

                self.assertIs(type(transition), EffectAttemptTransition)
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
                    self.assertIs(type(failure), FailureEvidence)
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
        malformed_observation = object.__new__(type(observed.value))
        for field in fields(observed.value):
            object.__setattr__(
                malformed_observation,
                field.name,
                getattr(observed.value, field.name),
            )
        object.__setattr__(
            malformed_observation,
            "request_fingerprint",
            "not-a-fingerprint",
        )
        self.assertIs(type(malformed_observation), type(observed.value))
        self.assertEqual(
            malformed_observation.request_fingerprint,
            "not-a-fingerprint",
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
                malformed_observation,
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

    def test_outcomes_revalidate_complete_8192_byte_core_inputs(self) -> None:
        self.require_outcome_language()
        story = self.stories()[0]
        maximum_live = self.live_result_for_size(OUTCOME_MAX_BYTES)
        oversized_live = self.live_result_for_size(OUTCOME_MAX_BYTES + 1)
        maximum_observed = self.observed_result_for_size(OUTCOME_MAX_BYTES)
        oversized_observed = self.observed_result_for_size(OUTCOME_MAX_BYTES + 1)

        execution = ExecutionEffectOutcome(
            story.attempt.state.identity,
            REQUEST_FINGERPRINT,
            maximum_live,
        )
        observed = ObservedEffectOutcome(
            story.attempt.state.identity,
            maximum_observed,
        )
        self.assertEqual(
            execution.outcome_fingerprint,
            runtime_effect_result_fingerprint(maximum_live),
        )
        self.assertEqual(
            observed.outcome_fingerprint,
            runtime_effect_observation_fingerprint(maximum_observed),
        )
        for constructor in (
            lambda: ExecutionEffectOutcome(
                story.attempt.state.identity,
                REQUEST_FINGERPRINT,
                oversized_live,
            ),
            lambda: ObservedEffectOutcome(
                story.attempt.state.identity,
                oversized_observed,
            ),
        ):
            self.assert_fixed_error(
                constructor,
                "effect outcome evidence is invalid",
                "x" * 64,
                "y" * 64,
            )

    def test_endpoint_bridge_boundary_is_total_and_nested_nominal(self) -> None:
        self.require_outcome_language()
        execution_story = next(
            item for item in self.stories() if item.name == "execution-succeeded"
        )
        observed_story = next(
            item for item in self.stories() if item.name == "observed-succeeded"
        )
        maximum = self.endpoint_for_bridge_size(4_096)
        oversized = self.endpoint_for_bridge_size(4_097)
        maximum_live = RuntimeEffectResult.succeeded(
            "event-start",
            observations=(maximum,),
        )
        maximum_observed = replace(
            observed_story.value,
            observations=(maximum,),
        )
        self.assertIs(
            ExecutionEffectOutcome(
                execution_story.attempt.state.identity,
                REQUEST_FINGERPRINT,
                maximum_live,
            ).result.observations[0],
            maximum,
        )
        self.assertIs(
            ObservedEffectOutcome(
                observed_story.attempt.state.identity,
                maximum_observed,
            ).observation.observations[0],
            maximum,
        )

        class HostileEndpoint(RuntimeEndpointObservation):
            pass

        hostile = HostileEndpoint(**maximum.__dict__)
        for endpoint in (oversized, hostile):
            live = forge_exact(
                RuntimeEffectResult,
                effect_id="event-start",
                kind=EffectResultKind.SUCCEEDED,
                evidence={},
                failure=None,
                observations=(endpoint,),
            )
            observed = forge_exact(
                type(observed_story.value),
                effect_id="event-start",
                request_fingerprint=REQUEST_FINGERPRINT,
                evidence=observed_story.value.evidence,
                failure=None,
                observations=(endpoint,),
            )
            for constructor in (
                lambda live=live: ExecutionEffectOutcome(
                    execution_story.attempt.state.identity,
                    REQUEST_FINGERPRINT,
                    live,
                ),
                lambda observed=observed: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    observed,
                ),
            ):
                self.assert_fixed_error(
                    constructor,
                    "effect outcome evidence is invalid",
                    "s" * 64,
                )

    def test_direct_outcome_identity_is_exact_and_bounded(self) -> None:
        self.require_outcome_language()
        story = self.stories()[0]
        maximum = EffectAttemptIdentity(
            RunId("r" * 200),
            "a" * 200,
            2_147_483_647,
        )
        self.assertIs(
            ExecutionEffectOutcome(
                maximum,
                REQUEST_FINGERPRINT,
                story.value,
            ).identity,
            maximum,
        )

        hostile_run = forge_exact(
            RunId,
            value=type("HostileRun", (str,), {})("run-a"),
        )
        hostile_activity = type("HostileActivity", (str,), {})("activity-a")
        hostile_attempt = type("HostileAttempt", (int,), {})(1)
        candidates = (
            forge_exact(
                EffectAttemptIdentity,
                run_id=hostile_run,
                activity_id="activity-a",
                attempt=1,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=RunId("run-a"),
                activity_id=hostile_activity,
                attempt=1,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=RunId("run-a"),
                activity_id="activity-a",
                attempt=hostile_attempt,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=RunId("run-a"),
                activity_id="activity-a",
                attempt=0,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=RunId("run-a"),
                activity_id="activity-a",
                attempt=2_147_483_648,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=forge_exact(RunId, value="run\x00canary"),
                activity_id="activity-a",
                attempt=1,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=forge_exact(RunId, value="run-surrogate-\ud800"),
                activity_id="activity-a",
                attempt=1,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=forge_exact(RunId, value="r" * 201),
                activity_id="activity-a",
                attempt=1,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=RunId("run-a"),
                activity_id="a" * 201,
                attempt=1,
            ),
            forge_exact(
                EffectAttemptIdentity,
                run_id=RunId("run-a"),
                activity_id="activity/canary",
                attempt=1,
            ),
        )
        for identity in candidates:
            with self.subTest(identity=repr(identity)):
                self.assert_fixed_error(
                    lambda identity=identity: ExecutionEffectOutcome(
                        identity,
                        REQUEST_FINGERPRINT,
                        story.value,
                    ),
                    "effect outcome evidence is invalid",
                    "canary",
                    "surrogate",
                    "r" * 201,
                    "a" * 201,
                )

    def test_invalid_candidates_are_cause_free_and_candidate_free(self) -> None:
        self.require_outcome_language()
        story = self.stories()[0]
        canary = "private-outcome-candidate"
        forged = object.__new__(RuntimeEffectResult)
        object.__setattr__(forged, "effect_id", story.value.effect_id)
        object.__setattr__(forged, "kind", canary)
        object.__setattr__(forged, "evidence", story.value.evidence)
        object.__setattr__(forged, "failure", story.value.failure)
        object.__setattr__(forged, "observations", story.value.observations)

        self.assert_fixed_error(
            lambda: ExecutionEffectOutcome(
                story.attempt.state.identity,
                REQUEST_FINGERPRINT,
                forged,
            ),
            "effect outcome evidence is invalid",
            canary,
        )

    def test_metadata_spoofs_and_unhashable_kind_reject_before_dispatch(self) -> None:
        self.require_outcome_language()
        execution_story = next(
            item for item in self.stories() if item.name == "execution-succeeded"
        )
        observed_story = next(
            item for item in self.stories() if item.name == "observed-succeeded"
        )
        failed_story = next(
            item for item in self.stories() if item.name == "observed-failed"
        )

        run_dispatches = []

        class UnrelatedRunId:
            @property
            def value(self):
                run_dispatches.append("run-id.value")
                raise RuntimeError("private-run-id-canary")

            def descriptor(self):
                run_dispatches.append("run-id.descriptor")
                raise RuntimeError("private-run-id-canary")

        UnrelatedRunId.__module__ = "control_plane_kit_core.operations.run_identity"
        UnrelatedRunId.__qualname__ = "RunId"
        spoofed_identity = forge_exact(
            EffectAttemptIdentity,
            run_id=UnrelatedRunId(),
            activity_id="activity-a",
            attempt=1,
        )

        evidence_dispatches = []

        class UnrelatedObservationEvidence:
            def descriptor(self):
                evidence_dispatches.append("observation-evidence.descriptor")
                raise RuntimeError("private-observation-evidence-canary")

        UnrelatedObservationEvidence.__module__ = (
            "control_plane_kit_core.runtime_effect_observation"
        )
        UnrelatedObservationEvidence.__qualname__ = (
            "RuntimeEffectObservationEvidence"
        )
        spoofed_evidence_observation = forge_exact(
            type(observed_story.value),
            effect_id=observed_story.value.effect_id,
            request_fingerprint=observed_story.value.request_fingerprint,
            evidence=UnrelatedObservationEvidence(),
            failure=observed_story.value.failure,
            observations=observed_story.value.observations,
        )

        failure_dispatches = []

        class UnrelatedObservationFailure:
            code = failed_story.value.failure.code
            message = failed_story.value.failure.message
            details = failed_story.value.failure.details

            def descriptor(self):
                failure_dispatches.append("observation-failure.descriptor")
                raise RuntimeError("private-observation-failure-canary")

        UnrelatedObservationFailure.__module__ = (
            "control_plane_kit_core.runtime_effect_observation"
        )
        UnrelatedObservationFailure.__qualname__ = (
            "RuntimeEffectObservationFailure"
        )
        spoofed_failure_observation = forge_exact(
            type(failed_story.value),
            effect_id=failed_story.value.effect_id,
            request_fingerprint=failed_story.value.request_fingerprint,
            evidence=failed_story.value.evidence,
            failure=UnrelatedObservationFailure(),
            observations=failed_story.value.observations,
        )

        endpoint_dispatches = []
        endpoint = observed_story.value.observations[0]
        secret_material = SecretEndpointMaterial("secret://runtime/endpoint-a")
        secret_endpoint = RuntimeEndpointObservation(
            subject_id=endpoint.subject_id,
            socket_name=endpoint.socket_name,
            graph_id=endpoint.graph_id,
            protocol=endpoint.protocol,
            context=endpoint.context,
            address=secret_material,
        )
        secret_outcome = ObservedEffectOutcome(
            observed_story.attempt.state.identity,
            replace(observed_story.value, observations=(secret_endpoint,)),
        )
        self.assertIs(
            secret_outcome.endpoint_observations[0].address,
            secret_material,
        )

        class UnrelatedEndpoint:
            subject_id = endpoint.subject_id
            socket_name = endpoint.socket_name
            graph_id = endpoint.graph_id
            protocol = endpoint.protocol
            context = endpoint.context
            address = endpoint.address

            def descriptor(self):
                endpoint_dispatches.append("runtime-endpoint.descriptor")
                raise RuntimeError("private-endpoint-canary")

        UnrelatedEndpoint.__module__ = "control_plane_kit_core.probe_intents"
        UnrelatedEndpoint.__qualname__ = "RuntimeEndpointObservation"
        spoofed_endpoint_observation = forge_exact(
            type(observed_story.value),
            effect_id=observed_story.value.effect_id,
            request_fingerprint=observed_story.value.request_fingerprint,
            evidence=observed_story.value.evidence,
            failure=observed_story.value.failure,
            observations=(UnrelatedEndpoint(),),
        )

        material_dispatches = []

        class UnrelatedLiteralMaterial:
            @property
            def value(self):
                material_dispatches.append("literal-material.value")
                raise RuntimeError("private-material-canary")

            def descriptor(self):
                material_dispatches.append("literal-material.descriptor")
                raise RuntimeError("private-material-canary")

        UnrelatedLiteralMaterial.__module__ = "control_plane_kit_core.probe_intents"
        UnrelatedLiteralMaterial.__qualname__ = "LiteralEndpointMaterial"
        material_endpoint = forge_exact(
            RuntimeEndpointObservation,
            subject_id=endpoint.subject_id,
            socket_name=endpoint.socket_name,
            graph_id=endpoint.graph_id,
            protocol=endpoint.protocol,
            context=endpoint.context,
            address=UnrelatedLiteralMaterial(),
        )
        spoofed_material_observation = forge_exact(
            type(observed_story.value),
            effect_id=observed_story.value.effect_id,
            request_fingerprint=observed_story.value.request_fingerprint,
            evidence=observed_story.value.evidence,
            failure=observed_story.value.failure,
            observations=(material_endpoint,),
        )

        context_dispatches = []

        class UnrelatedEndpointContext:
            @property
            def value(self):
                context_dispatches.append("endpoint-context.value")
                raise RuntimeError("private-context-canary")

            def descriptor(self):
                context_dispatches.append("endpoint-context.descriptor")
                raise RuntimeError("private-context-canary")

        UnrelatedEndpointContext.__module__ = "control_plane_kit_core.probe_intents"
        UnrelatedEndpointContext.__qualname__ = "EndpointContext"
        context_endpoint = forge_exact(
            RuntimeEndpointObservation,
            subject_id=endpoint.subject_id,
            socket_name=endpoint.socket_name,
            graph_id=endpoint.graph_id,
            protocol=endpoint.protocol,
            context=UnrelatedEndpointContext(),
            address=endpoint.address,
        )
        spoofed_context_observation = forge_exact(
            type(observed_story.value),
            effect_id=observed_story.value.effect_id,
            request_fingerprint=observed_story.value.request_fingerprint,
            evidence=observed_story.value.evidence,
            failure=observed_story.value.failure,
            observations=(context_endpoint,),
        )

        protocol_dispatches = []

        class UnrelatedProtocol:
            @property
            def transport(self):
                protocol_dispatches.append("protocol.transport")
                raise RuntimeError("private-protocol-canary")

            @property
            def application(self):
                protocol_dispatches.append("protocol.application")
                raise RuntimeError("private-protocol-canary")

            def descriptor(self):
                protocol_dispatches.append("protocol.descriptor")
                raise RuntimeError("private-protocol-canary")

        UnrelatedProtocol.__module__ = "control_plane_kit_core.types"
        UnrelatedProtocol.__qualname__ = "Protocol"
        protocol_endpoint = forge_exact(
            RuntimeEndpointObservation,
            subject_id=endpoint.subject_id,
            socket_name=endpoint.socket_name,
            graph_id=endpoint.graph_id,
            protocol=UnrelatedProtocol(),
            context=endpoint.context,
            address=endpoint.address,
        )
        spoofed_protocol_observation = forge_exact(
            type(observed_story.value),
            effect_id=observed_story.value.effect_id,
            request_fingerprint=observed_story.value.request_fingerprint,
            evidence=observed_story.value.evidence,
            failure=observed_story.value.failure,
            observations=(protocol_endpoint,),
        )

        transport_dispatches = []

        class HostileTransport:
            @property
            def value(self):
                transport_dispatches.append("protocol.transport.value")
                raise RuntimeError("private-transport-canary")

        forged_transport_protocol = forge_exact(
            type(endpoint.protocol),
            transport=HostileTransport(),
            application=endpoint.protocol.application,
        )
        transport_endpoint = forge_exact(
            RuntimeEndpointObservation,
            subject_id=endpoint.subject_id,
            socket_name=endpoint.socket_name,
            graph_id=endpoint.graph_id,
            protocol=forged_transport_protocol,
            context=endpoint.context,
            address=endpoint.address,
        )
        spoofed_transport_observation = forge_exact(
            type(observed_story.value),
            effect_id=observed_story.value.effect_id,
            request_fingerprint=observed_story.value.request_fingerprint,
            evidence=observed_story.value.evidence,
            failure=observed_story.value.failure,
            observations=(transport_endpoint,),
        )

        application_dispatches = []

        class HostileApplication:
            @property
            def value(self):
                application_dispatches.append("protocol.application.value")
                raise RuntimeError("private-application-canary")

        forged_application_protocol = forge_exact(
            type(endpoint.protocol),
            transport=endpoint.protocol.transport,
            application=HostileApplication(),
        )
        application_endpoint = forge_exact(
            RuntimeEndpointObservation,
            subject_id=endpoint.subject_id,
            socket_name=endpoint.socket_name,
            graph_id=endpoint.graph_id,
            protocol=forged_application_protocol,
            context=endpoint.context,
            address=endpoint.address,
        )
        spoofed_application_observation = forge_exact(
            type(observed_story.value),
            effect_id=observed_story.value.effect_id,
            request_fingerprint=observed_story.value.request_fingerprint,
            evidence=observed_story.value.evidence,
            failure=observed_story.value.failure,
            observations=(application_endpoint,),
        )

        cases = (
            (
                "run-id",
                run_dispatches,
                lambda: ExecutionEffectOutcome(
                    spoofed_identity,
                    REQUEST_FINGERPRINT,
                    execution_story.value,
                ),
                "private-run-id-canary",
            ),
            (
                "observation-evidence",
                evidence_dispatches,
                lambda: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    spoofed_evidence_observation,
                ),
                "private-observation-evidence-canary",
            ),
            (
                "observation-failure",
                failure_dispatches,
                lambda: ObservedEffectOutcome(
                    failed_story.attempt.state.identity,
                    spoofed_failure_observation,
                ),
                "private-observation-failure-canary",
            ),
            (
                "endpoint",
                endpoint_dispatches,
                lambda: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    spoofed_endpoint_observation,
                ),
                "private-endpoint-canary",
            ),
            (
                "material",
                material_dispatches,
                lambda: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    spoofed_material_observation,
                ),
                "private-material-canary",
            ),
            (
                "context",
                context_dispatches,
                lambda: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    spoofed_context_observation,
                ),
                "private-context-canary",
            ),
            (
                "protocol",
                protocol_dispatches,
                lambda: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    spoofed_protocol_observation,
                ),
                "private-protocol-canary",
            ),
            (
                "protocol-transport",
                transport_dispatches,
                lambda: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    spoofed_transport_observation,
                ),
                "private-transport-canary",
            ),
            (
                "protocol-application",
                application_dispatches,
                lambda: ObservedEffectOutcome(
                    observed_story.attempt.state.identity,
                    spoofed_application_observation,
                ),
                "private-application-canary",
            ),
        )
        for name, dispatches, constructor, canary in cases:
            with self.subTest(candidate=name):
                captured = None
                try:
                    constructor()
                except BaseException as error:
                    captured = error
                self.assertEqual(dispatches, [], "candidate dispatched before rejection")
                self.assertIs(type(captured), OperationsRecordError)
                self.assertEqual(str(captured), "effect outcome evidence is invalid")
                self.assert_safe_error(captured, canary)

        kind_canary = "private-unhashable-kind-canary"
        forged_result = forge_exact(
            RuntimeEffectResult,
            effect_id=execution_story.value.effect_id,
            kind=[kind_canary],
            evidence=execution_story.value.evidence,
            failure=execution_story.value.failure,
            observations=execution_story.value.observations,
        )
        captured = None
        try:
            ExecutionEffectOutcome(
                execution_story.attempt.state.identity,
                REQUEST_FINGERPRINT,
                forged_result,
            )
        except BaseException as error:
            captured = error
        self.assertIs(type(captured), OperationsRecordError)
        self.assertEqual(str(captured), "effect outcome evidence is invalid")
        self.assert_safe_error(captured, kind_canary)

    def test_live_result_rejects_non_tuple_observations_before_iteration(self) -> None:
        self.require_outcome_language()
        story = next(
            item for item in self.stories() if item.name == "execution-succeeded"
        )
        dispatches = []
        canary = "private-observations-iterator-canary"

        class HostileObservations:
            def __iter__(self):
                dispatches.append("observations.__iter__")
                raise RuntimeError(canary)

        forged_result = forge_exact(
            RuntimeEffectResult,
            effect_id=story.value.effect_id,
            kind=story.value.kind,
            evidence=story.value.evidence,
            failure=story.value.failure,
            observations=HostileObservations(),
        )
        captured = None
        try:
            ExecutionEffectOutcome(
                story.attempt.state.identity,
                REQUEST_FINGERPRINT,
                forged_result,
            )
        except BaseException as error:
            captured = error
        self.assertEqual(dispatches, [])
        self.assertIs(type(captured), OperationsRecordError)
        self.assertEqual(str(captured), "effect outcome evidence is invalid")
        self.assert_safe_error(captured, canary)

    def test_exact_secret_material_revalidates_reference_grammar(self) -> None:
        self.require_outcome_language()
        story = next(
            item for item in self.stories() if item.name == "observed-succeeded"
        )
        endpoint = story.value.observations[0]
        canary = "private-secret-reference-canary"
        forged_material = forge_exact(
            SecretEndpointMaterial,
            reference_id=canary,
        )
        forged_endpoint = forge_exact(
            RuntimeEndpointObservation,
            subject_id=endpoint.subject_id,
            socket_name=endpoint.socket_name,
            graph_id=endpoint.graph_id,
            protocol=endpoint.protocol,
            context=endpoint.context,
            address=forged_material,
        )
        forged_observation = forge_exact(
            type(story.value),
            effect_id=story.value.effect_id,
            request_fingerprint=story.value.request_fingerprint,
            evidence=story.value.evidence,
            failure=story.value.failure,
            observations=(forged_endpoint,),
        )
        self.assert_fixed_error(
            lambda: ObservedEffectOutcome(
                story.attempt.state.identity,
                forged_observation,
            ),
            "effect outcome evidence is invalid",
            canary,
        )

    def test_module_has_closed_import_and_lexical_call_surface(self) -> None:
        self.require_outcome_language()
        module = __import__(MODULE_NAME, fromlist=("__file__",))
        source_path = Path(inspect.getsourcefile(module))
        facts = architecture_testing.analyze_source(
            source_path.read_text(),
            path=OUTCOME_SOURCE_PATH,
            module=MODULE_NAME,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.operations.effect-outcome.imports"
                    ),
                    architecture_testing.RuleId("exact"),
                    OUTCOME_SOURCE_PATH,
                    MODULE_NAME,
                    EXACT_IMPORT_SURFACE,
                    "effect outcome import surface differs",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId(
                        "cpk.operations.effect-outcome.calls"
                    ),
                    architecture_testing.RuleId("exact"),
                    OUTCOME_SOURCE_PATH,
                    MODULE_NAME,
                    EXACT_CALL_SURFACE,
                    "effect outcome lexical call surface differs",
                ),
            ),
        )
        self.assertEqual(findings, ())

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
                "control_plane_kit_core.probe_intents",
                "control_plane_kit_core.runtime_effect_observation",
                "control_plane_kit_core.runtime_effects",
                "control_plane_kit_core.types",
                "control_plane_kit_operations.effect_attempts",
                "control_plane_kit_operations.records",
            },
        )
        self.assertEqual(row["optional_external_dependencies"], [])


if __name__ == "__main__":
    unittest.main()
