from __future__ import annotations

from dataclasses import fields, replace
import ast
import inspect
import json
import os
from pathlib import Path
import unittest

import rfc8785

import control_plane_kit_operations as operations_root
from control_plane_kit_core import (
    EffectResultKind,
    RuntimeEffectObservedSucceeded,
    RuntimeEffectResult,
    runtime_effect_observation_fingerprint,
    runtime_effect_result_fingerprint,
)
from control_plane_kit_core.probe_intents import RuntimeEndpointObservation
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

EXACT_IMPORT_SYMBOLS = {
    "__future__": {("annotations", "annotations")},
    "dataclasses": {("dataclass", "dataclass"), ("field", "field")},
    "enum": {("StrEnum", "StrEnum")},
    "control_plane_kit_core.operations": {
        ("EffectAttemptIdentity", "EffectAttemptIdentity"),
        ("EffectAttemptStatus", "EffectAttemptStatus"),
        ("EffectAttemptTransition", "EffectAttemptTransition"),
        ("EffectAttemptTransitionKind", "EffectAttemptTransitionKind"),
        ("EffectResultKind", "EffectResultKind"),
        ("FailureCategory", "FailureCategory"),
    },
    "control_plane_kit_core.runtime_effect_observation": {
        ("RuntimeEffectObservationResult", "RuntimeEffectObservationResult"),
        ("RuntimeEffectObservedAbsent", "RuntimeEffectObservedAbsent"),
        ("RuntimeEffectObservedConflict", "RuntimeEffectObservedConflict"),
        ("RuntimeEffectObservedFailed", "RuntimeEffectObservedFailed"),
        ("RuntimeEffectObservedIndeterminate", "RuntimeEffectObservedIndeterminate"),
        ("RuntimeEffectObservedSucceeded", "RuntimeEffectObservedSucceeded"),
        ("RuntimeEffectObserverUnsupported", "RuntimeEffectObserverUnsupported"),
        ("runtime_effect_observation_fingerprint", "runtime_effect_observation_fingerprint"),
        ("runtime_effect_result_fingerprint", "runtime_effect_result_fingerprint"),
    },
    "control_plane_kit_core.runtime_effects": {
        ("RuntimeEffectContractError", "RuntimeEffectContractError"),
        ("RuntimeEffectResult", "RuntimeEffectResult"),
    },
    "control_plane_kit_operations.effect_attempts": {
        ("EffectAttemptRecord", "EffectAttemptRecord"),
    },
    "control_plane_kit_operations.records": {
        ("BoundedEvidence", "BoundedEvidence"),
        ("FailureEvidence", "FailureEvidence"),
        ("ObservationFreshness", "ObservationFreshness"),
        ("ObservationRecord", "ObservationRecord"),
        ("ObservationStatus", "ObservationStatus"),
        ("OperationsRecordError", "OperationsRecordError"),
        ("ProbeKind", "ProbeKind"),
        ("ProbeOutcome", "ProbeOutcome"),
    },
}
_PURE_BUILTIN_CALLS = {
    "all",
    "any",
    "enumerate",
    "len",
    "ord",
    "set",
    "type",
    "tuple",
    "zip",
}
_ALLOWED_INSTANCE_CALLS = {"descriptor", "encode"}


def _normalized_import_module(node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    if node.level == 1:
        suffix = f".{node.module}" if node.module else ""
        return f"control_plane_kit_operations{suffix}"
    return f"relative-level-{node.level}:{node.module or ''}"


def _import_contract(
    tree: ast.AST,
) -> tuple[
    dict[str, set[tuple[str, str]]],
    dict[str, str],
    tuple[str, ...],
]:
    symbols: dict[str, set[tuple[str, str]]] = {}
    bindings: dict[str, str] = {}
    wildcard_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                binding = alias.asname or alias.name.split(".", 1)[0]
                symbols.setdefault(alias.name, set()).add(("<module>", binding))
                bindings[binding] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = _normalized_import_module(node)
            for alias in node.names:
                if alias.name == "*":
                    wildcard_imports.append(module)
                    continue
                binding = alias.asname or alias.name
                symbols.setdefault(module, set()).add((alias.name, binding))
                bindings[binding] = f"{module}.{alias.name}"
    return symbols, bindings, tuple(wildcard_imports)


def _bound_target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _bound_target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return {
            name
            for element in target.elts
            for name in _bound_target_names(element)
        }
    return set()


def _forbidden_rebindings(tree: ast.AST) -> tuple[str, ...]:
    _, imported_bindings, _ = _import_contract(tree)
    local_callables = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    protected = set(imported_bindings) | _PURE_BUILTIN_CALLS | local_callables
    rebound: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                rebound.update(_bound_target_names(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            rebound.update(_bound_target_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            rebound.update(_bound_target_names(node.target))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            rebound.update(_bound_target_names(node.optional_vars))
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            rebound.add(node.name)
        elif (
            isinstance(node, (ast.MatchAs, ast.MatchStar))
            and node.name is not None
        ):
            rebound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            rebound.add(node.rest)
        elif isinstance(node, ast.arguments):
            rebound.update(
                argument.arg
                for argument in (
                    *node.posonlyargs,
                    *node.args,
                    *node.kwonlyargs,
                )
            )
            if node.vararg is not None:
                rebound.add(node.vararg.arg)
            if node.kwarg is not None:
                rebound.add(node.kwarg.arg)

    declared_collisions = local_callables.intersection(
        set(imported_bindings) | _PURE_BUILTIN_CALLS
    )
    return tuple(sorted((rebound & protected) | declared_collisions))


def _nonlocal_mutation_targets(tree: ast.AST) -> tuple[str, ...]:
    return tuple(
        sorted(
            type(node).__name__
            for node in ast.walk(tree)
            if isinstance(node, (ast.Attribute, ast.Subscript))
            and isinstance(node.ctx, (ast.Store, ast.Del))
        )
    )


def _builtins_namespace_accesses(tree: ast.AST) -> tuple[str, ...]:
    return tuple(
        sorted(
            type(node.ctx).__name__
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "__builtins__"
        )
    )


def _resolved_path(
    node: ast.AST,
    bindings: dict[str, str],
    local_callables: set[str],
) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id]
        if node.id in _PURE_BUILTIN_CALLS:
            return f"builtins.{node.id}"
        if node.id in local_callables:
            return f"local.{node.id}"
        return None
    if isinstance(node, ast.Attribute):
        owner = _resolved_path(node.value, bindings, local_callables)
        if owner is not None:
            return f"{owner}.{node.attr}"
        if node.attr in local_callables:
            return f"local.{node.attr}"
        if node.attr in _ALLOWED_INSTANCE_CALLS:
            return f"instance.{node.attr}"
    return None


def _call_contract(tree: ast.AST) -> tuple[set[str], tuple[str, ...]]:
    _, bindings, _ = _import_contract(tree)
    local_callables = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for node in assignments:
            value = node.value
            path = (
                _resolved_path(value, bindings, local_callables)
                if value is not None
                else None
            )
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if path is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and bindings.get(target.id) != path:
                    bindings[target.id] = path
                    changed = True
        if not changed:
            break

    targets: set[str] = set()
    unresolved: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        path = _resolved_path(node.func, bindings, local_callables)
        if path is None:
            unresolved.append(ast.dump(node.func, include_attributes=False))
        else:
            targets.add(path)
    return targets, tuple(sorted(unresolved))


def _allowed_call_targets(tree: ast.AST) -> set[str]:
    imported = {
        f"{module}.{symbol}"
        for module, values in EXACT_IMPORT_SYMBOLS.items()
        for symbol, _ in values
        if module != "__future__"
    }
    local = {
        f"local.{node.name}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    return (
        imported
        | local
        | {f"builtins.{name}" for name in _PURE_BUILTIN_CALLS}
        | {f"instance.{name}" for name in _ALLOWED_INSTANCE_CALLS}
        | {"control_plane_kit_operations.records.BoundedEvidence.from_mapping"}
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
        direct_output = ast.parse('print("candidate")')
        dynamic_open = ast.parse('__builtins__["open"]("candidate")')
        smuggled_output = ast.parse(
            "from dataclasses import sys\nsys.stdout.write('candidate')"
        )
        laundered_open = ast.parse(
            'tuple = __builtins__["open"]\n'
            'tuple("candidate")'
        )
        pattern_laundered_open = ast.parse(
            'match __builtins__["open"]:\n'
            '    case tuple:\n'
            '        tuple("candidate")'
        )
        namespace_laundered_open = ast.parse(
            '__builtins__["tuple"] = __builtins__["open"]\n'
            'tuple("candidate")'
        )
        direct_targets, direct_unresolved = _call_contract(direct_output)
        dynamic_targets, dynamic_unresolved = _call_contract(dynamic_open)
        smuggled_targets, smuggled_unresolved = _call_contract(smuggled_output)
        laundered_targets, laundered_unresolved = _call_contract(laundered_open)
        pattern_targets, pattern_unresolved = _call_contract(
            pattern_laundered_open
        )
        namespace_targets, namespace_unresolved = _call_contract(
            namespace_laundered_open
        )
        smuggled_symbols, _, _ = _import_contract(smuggled_output)

        self.assertEqual(direct_targets, set())
        self.assertEqual(len(direct_unresolved), 1)
        self.assertIn("print", direct_unresolved[0])
        self.assertEqual(dynamic_targets, set())
        self.assertEqual(len(dynamic_unresolved), 1)
        self.assertIn("Subscript", dynamic_unresolved[0])
        self.assertEqual(
            smuggled_symbols,
            {"dataclasses": {("sys", "sys")}},
        )
        self.assertEqual(smuggled_unresolved, ())
        self.assertEqual(smuggled_targets, {"dataclasses.sys.stdout.write"})
        self.assertNotEqual(smuggled_symbols, EXACT_IMPORT_SYMBOLS)
        self.assertTrue(
            smuggled_targets.difference(_allowed_call_targets(smuggled_output))
        )
        self.assertEqual(laundered_targets, {"builtins.tuple"})
        self.assertEqual(laundered_unresolved, ())
        self.assertEqual(_forbidden_rebindings(laundered_open), ("tuple",))
        self.assertEqual(pattern_targets, {"builtins.tuple"})
        self.assertEqual(pattern_unresolved, ())
        self.assertEqual(
            _forbidden_rebindings(pattern_laundered_open),
            ("tuple",),
        )
        self.assertEqual(namespace_targets, {"builtins.tuple"})
        self.assertEqual(namespace_unresolved, ())
        self.assertEqual(_forbidden_rebindings(namespace_laundered_open), ())
        self.assertEqual(
            _nonlocal_mutation_targets(namespace_laundered_open),
            ("Subscript",),
        )
        self.assertEqual(
            _builtins_namespace_accesses(namespace_laundered_open),
            ("Load", "Load"),
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
        imported_symbols, _, wildcard_imports = _import_contract(tree)
        self.assertEqual(imported_symbols, EXACT_IMPORT_SYMBOLS)
        self.assertEqual(wildcard_imports, ())
        self.assertEqual(_forbidden_rebindings(tree), ())
        self.assertEqual(_nonlocal_mutation_targets(tree), ())
        self.assertEqual(_builtins_namespace_accesses(tree), ())
        call_targets, unresolved_calls = _call_contract(tree)
        self.assertEqual(unresolved_calls, ())
        self.assertEqual(
            call_targets.difference(_allowed_call_targets(tree)),
            set(),
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
                "control_plane_kit_core.runtime_effects",
                "control_plane_kit_operations.effect_attempts",
                "control_plane_kit_operations.records",
            },
        )
        self.assertEqual(row["optional_external_dependencies"], [])


if __name__ == "__main__":
    unittest.main()
