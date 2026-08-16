from __future__ import annotations

import ast
from dataclasses import fields, replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import unittest

import rfc8785

import control_plane_kit_core as root
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.run_identity import RunId
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
    SecretEndpointMaterial,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectResult,
    RuntimeEffectSource,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.runtime_authority import RuntimeEffectContractError
from control_plane_kit_core.types import Protocol, RuntimeKind


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parent
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_core"
MODULE = "control_plane_kit_core.runtime_effect_observation"
RESULT_DOMAIN = b"control-plane-kit.runtime-effect-result.v1\x00"
OUTCOME_FINGERPRINT_MAX_BYTES = 8_192
ENDPOINT_TEXT_MAX = 512
BRIDGE_EVIDENCE_MAX_BYTES = 4_096
PUBLIC_NAMES = {
    "RuntimeEffectIntent",
    "RuntimeEffectIntentSource",
    "RuntimeEffectObservationEvidence",
    "RuntimeEffectObservationFailure",
    "RuntimeEffectObservationRequest",
    "RuntimeEffectObservedSucceeded",
    "RuntimeEffectObservedFailed",
    "RuntimeEffectObservedAbsent",
    "RuntimeEffectObservedConflict",
    "RuntimeEffectObservedIndeterminate",
    "RuntimeEffectObserverUnsupported",
    "RuntimeEffectObservationResult",
    "runtime_effect_intent_for_request",
    "runtime_effect_request_for_intent",
    "runtime_effect_intent_fingerprint",
    "runtime_effect_result_fingerprint",
    "runtime_effect_observation_fingerprint",
}


def _language():
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        if error.name != MODULE:
            raise
        raise AssertionError("missing #1693 runtime effect observation module") from error
    missing = sorted(PUBLIC_NAMES - set(vars(module)))
    if missing:
        raise AssertionError(f"missing #1693 public language: {', '.join(missing)}")
    return module


def _source(*, intent_event_id: str = "event-started-a") -> RuntimeEffectSource:
    return RuntimeEffectSource(
        workspace_id="workspace-a",
        request_id="request-a",
        run_id=RunId("run-a"),
        plan_id="plan-a",
        base_graph_id="graph-base",
        desired_graph_id="graph-desired",
        intent_event_id=intent_event_id,
    )


def _grant() -> SecretResolutionGrant:
    return SecretResolutionGrant(
        authorization_id="suse_" + "a" * 64,
        workspace_id="workspace-a",
        reference_registration_id="sref_" + "b" * 64,
        provider_registration_id="sprov_" + "c" * 64,
        endpoint_reference=SecretProviderEndpointReference("provider-a"),
        credential_reference=SecretReference("secret://bootstrap/provider-a-token"),
        reference=SecretReference("secret://local/workspace-a/docker/client-key"),
        intent=SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
        actor_subject="worker-a",
        correlation_id="secret-use-" + "d" * 64,
        intent_fingerprint="e" * 64,
        run_id="run-a",
        activity_id="activity-a",
        effect_id="event-started-a",
    )


def _subclass_copy(value):
    hostile_type = type(f"Hostile{type(value).__name__}", (type(value),), {})
    arguments = {
        item.name: getattr(value, item.name)
        for item in fields(value)
        if item.init
    }
    return hostile_type(**arguments)


def _endpoint(
    *,
    subject_id: str = "api",
    socket_name: str = "http",
    graph_id: str = "graph-desired",
    protocol: Protocol = Protocol.HTTP,
    context: EndpointContext = EndpointContext.RUNTIME_PRIVATE,
    address: str = "http://api:8000",
) -> RuntimeEndpointObservation:
    return RuntimeEndpointObservation(
        subject_id=subject_id,
        socket_name=socket_name,
        graph_id=graph_id,
        protocol=protocol,
        context=context,
        address=LiteralEndpointMaterial(address),
    )


def _literal_address(length: int) -> str:
    prefix = "http://"
    suffix = ":8000"
    return prefix + "a" * (length - len(prefix) - len(suffix)) + suffix


def _raw_endpoint_descriptor(*, subject_id: str) -> dict[str, object]:
    descriptor = _endpoint().descriptor()
    descriptor["subject_id"] = subject_id
    return descriptor


def _bridge_evidence_size(*, subject_id: str) -> int:
    document = {
        "runtime_endpoint": _raw_endpoint_descriptor(subject_id=subject_id),
    }
    return len(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _subject_for_bridge_evidence_size(target: int) -> str:
    marker = "\U0001f4a1"
    for marker_count in range(ENDPOINT_TEXT_MAX + 1):
        base = marker * marker_count
        remaining = target - _bridge_evidence_size(subject_id=base)
        if 0 <= remaining <= ENDPOINT_TEXT_MAX - marker_count:
            candidate = base + "s" * remaining
            if _bridge_evidence_size(subject_id=candidate) == target:
                return candidate
    raise AssertionError(f"cannot construct {target}-byte endpoint evidence")


class RuntimeEffectExistingBoundaryTests(unittest.TestCase):
    def test_request_requires_effect_and_intent_event_congruence(self) -> None:
        class HostileText(str):
            pass

        candidates = (
            ("event-started-a", "different-event"),
            (HostileText("event-started-a"), "event-started-a"),
            ("event-started-a", HostileText("event-started-a")),
            ("event-started-\ud800", "event-started-\ud800"),
        )
        for effect_id, intent_event_id in candidates:
            with self.subTest(case=type(effect_id).__name__):
                with self.assertRaisesRegex(
                    RuntimeEffectContractError,
                    "effect.*event|event.*effect",
                ):
                    RuntimeEffectRequest(
                        effect_id=effect_id,
                        kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                        runtime_kind=RuntimeKind.DOCKER,
                        source=_source(intent_event_id=intent_event_id),
                        activity_id=ActivityId("activity-a"),
                        operation=StartNode(NodeTarget("api")),
                    )

    def test_transient_secret_grants_are_repr_hidden_and_not_described(self) -> None:
        grant = _grant()
        request = RuntimeEffectRequest(
            effect_id="event-started-a",
            kind=RuntimeEffectKind.REALIZE_ACTIVITY,
            runtime_kind=RuntimeKind.DOCKER,
            source=_source(),
            activity_id=ActivityId("activity-a"),
            operation=StartNode(NodeTarget("api")),
            secret_resolution_grants=(grant,),
        )

        self.assertIs(request.secret_resolution_grants[0], grant)
        if "secret_resolution_grants" in request.descriptor():
            self.fail("runtime effect request descriptor exposes transient grants")
        for candidate in (
            grant.authorization_id,
            grant.endpoint_reference.reference_id,
            grant.credential_reference.reference_id,
            grant.actor_subject,
            grant.correlation_id,
            grant.intent_fingerprint,
        ):
            self.assertNotIn(candidate, repr(request))
            self.assertNotIn(candidate, repr(request.descriptor()))
        grant_field = next(
            item
            for item in fields(RuntimeEffectRequest)
            if item.name == "secret_resolution_grants"
        )
        self.assertTrue(grant_field.init)
        self.assertFalse(grant_field.repr)

    def test_live_result_fingerprint_has_exact_golden_and_distinct_domain(self) -> None:
        language = _language()
        result = RuntimeEffectResult.succeeded(
            "event-started-a",
            evidence={"container_state": "running", "exit_code": 0},
        )
        canonical = rfc8785.dumps(result.descriptor())

        self.assertEqual(
            canonical,
            b'{"effect_id":"event-started-a","evidence":{"container_state":"running","exit_code":0},"failure":null,"kind":"succeeded","observations":[]}',
        )
        self.assertEqual(
            language.runtime_effect_result_fingerprint(result),
            "c034bbfb31a97465a275bde6adc433b7060fe7f29ac6055e8ac6a58c520826c2",
        )
        self.assertEqual(
            hashlib.sha256(RESULT_DOMAIN + canonical).hexdigest(),
            "c034bbfb31a97465a275bde6adc433b7060fe7f29ac6055e8ac6a58c520826c2",
        )
        observation = language.RuntimeEffectObservedSucceeded(
            effect_id="event-started-a",
            request_fingerprint="a" * 64,
            evidence=language.RuntimeEffectObservationEvidence(
                {"container_state": "running", "exit_code": 0}
            ),
            observations=(_endpoint(),),
        )
        live = RuntimeEffectResult.succeeded(
            "event-started-a",
            evidence={"container_state": "running", "exit_code": 0},
            observations=(_endpoint(),),
        )
        self.assertEqual(observation.effect_id, live.effect_id)
        self.assertEqual(observation.descriptor()["kind"], live.descriptor()["kind"])
        self.assertEqual(
            observation.descriptor()["evidence"], live.descriptor()["evidence"]
        )
        self.assertEqual(
            observation.descriptor()["observations"],
            live.descriptor()["observations"],
        )
        self.assertNotEqual(
            language.runtime_effect_result_fingerprint(live),
            language.runtime_effect_observation_fingerprint(observation),
        )

    def test_result_fingerprint_rejects_hostile_nominal_and_json_values(self) -> None:
        language = _language()

        result = RuntimeEffectResult.failed(
            "event-started-a",
            RuntimeEffectFailure("runtime.failed", "runtime failed"),
        )
        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_result_fingerprint(
                _subclass_copy(result)
            )

        forged = object.__new__(RuntimeEffectResult)
        for item in fields(result):
            object.__setattr__(
                forged,
                item.name,
                {"unsafe": float("nan")}
                if item.name == "evidence"
                else getattr(result, item.name),
            )
        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_result_fingerprint(forged)

    def test_result_fingerprint_revalidates_semantics_and_preserves_internal_errors(self) -> None:
        language = _language()
        failure = RuntimeEffectFailure("runtime.failed", "runtime failed")

        def forged(kind, nested_failure):
            value = object.__new__(RuntimeEffectResult)
            object.__setattr__(value, "effect_id", "event-started-a")
            object.__setattr__(value, "kind", kind)
            object.__setattr__(value, "evidence", {})
            object.__setattr__(value, "failure", nested_failure)
            object.__setattr__(value, "observations", ())
            return value

        for kind, nested_failure in (
            (EffectResultKind.SUCCEEDED, failure),
            (EffectResultKind.FAILED, None),
            (EffectResultKind.UNSUPPORTED, None),
            (EffectResultKind.UNCERTAIN, None),
        ):
            with self.subTest(kind=kind.value):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    language.runtime_effect_result_fingerprint(
                        forged(kind, nested_failure)
                    )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

        valid = RuntimeEffectResult.succeeded("event-started-a")
        original = RuntimeEffectResult.descriptor
        for injected in (
            RuntimeError("internal result descriptor canary"),
            TypeError("internal result descriptor type canary"),
        ):
            with self.subTest(error_type=type(injected).__name__):
                def fail_if_called(_result, error=injected):
                    raise error

                RuntimeEffectResult.descriptor = fail_if_called
                try:
                    with self.assertRaises(type(injected)) as raised:
                        language.runtime_effect_result_fingerprint(valid)
                    self.assertIs(raised.exception, injected)
                finally:
                    RuntimeEffectResult.descriptor = original

    def test_live_result_fingerprint_commits_complete_nested_result(self) -> None:
        language = _language()
        succeeded = RuntimeEffectResult.succeeded(
            "event-started-a",
            evidence={"container_state": "running", "exit_code": 0},
            observations=(_endpoint(),),
        )
        baseline = language.runtime_effect_result_fingerprint(succeeded)
        endpoint = succeeded.observations[0]
        mutations = {
            "effect_id": replace(succeeded, effect_id="event-started-b"),
            "kind": RuntimeEffectResult.failed(
                "event-started-a",
                RuntimeEffectFailure("runtime.failed", "runtime failed"),
            ),
            "evidence_key": replace(
                succeeded,
                evidence={"provider_state": "running", "exit_code": 0},
            ),
            "evidence_text": replace(
                succeeded,
                evidence={"container_state": "stopped", "exit_code": 0},
            ),
            "evidence_integer": replace(
                succeeded,
                evidence={"container_state": "running", "exit_code": 1},
            ),
            "endpoint_subject": replace(
                succeeded,
                observations=(replace(endpoint, subject_id="worker"),),
            ),
            "endpoint_socket": replace(
                succeeded,
                observations=(replace(endpoint, socket_name="admin"),),
            ),
            "endpoint_graph": replace(
                succeeded,
                observations=(replace(endpoint, graph_id="graph-next"),),
            ),
            "endpoint_protocol": replace(
                succeeded,
                observations=(
                    _endpoint(
                        protocol=Protocol.POSTGRES,
                        address="postgres://api:5432",
                    ),
                ),
            ),
            "endpoint_context": replace(
                succeeded,
                observations=(replace(endpoint, context=EndpointContext.PUBLIC),),
            ),
            "endpoint_address": replace(
                succeeded,
                observations=(
                    replace(
                        endpoint,
                        address=LiteralEndpointMaterial("http://api:8001"),
                    ),
                ),
            ),
        }
        for name, candidate in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    language.runtime_effect_result_fingerprint(candidate), baseline
                )

        failure = RuntimeEffectFailure(
            "runtime.failed",
            "runtime failed",
            {"reason": "provider-error"},
        )
        failed = RuntimeEffectResult.failed("event-started-a", failure)
        unsupported = RuntimeEffectResult.unsupported("event-started-a", failure)
        uncertain = RuntimeEffectResult.uncertain("event-started-a", failure)
        self.assertEqual(
            len(
                {
                    language.runtime_effect_result_fingerprint(value)
                    for value in (failed, unsupported, uncertain)
                }
            ),
            3,
        )
        self.assertEqual(failed.failure, unsupported.failure)
        self.assertEqual(failed.failure, uncertain.failure)
        without_kind = lambda value: {
            key: nested
            for key, nested in value.descriptor().items()
            if key != "kind"
        }
        self.assertEqual(without_kind(failed), without_kind(unsupported))
        self.assertEqual(without_kind(failed), without_kind(uncertain))
        failed_fingerprint = language.runtime_effect_result_fingerprint(failed)
        for name, changed in {
            "failure_code": replace(failure, code="runtime.changed"),
            "failure_message": replace(failure, message="runtime changed"),
            "failure_details_key": replace(failure, details={"cause": "provider-error"}),
            "failure_details_value": replace(failure, details={"reason": "timeout"}),
        }.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    language.runtime_effect_result_fingerprint(
                        replace(failed, failure=changed)
                    ),
                    failed_fingerprint,
                )

    def test_live_result_descriptor_preserves_endpoint_order(self) -> None:
        first = _endpoint(subject_id="z-endpoint")
        second = _endpoint(subject_id="a-endpoint")
        forward = RuntimeEffectResult.succeeded(
            "event-started-a",
            observations=(first, second),
        )
        reverse = RuntimeEffectResult.succeeded(
            "event-started-a",
            observations=(second, first),
        )

        self.assertEqual(
            forward.descriptor()["observations"],
            [item.descriptor() for item in forward.observations],
        )
        self.assertEqual(
            reverse.descriptor()["observations"],
            [item.descriptor() for item in reverse.observations],
        )
        self.assertNotEqual(forward.descriptor(), reverse.descriptor())

    def test_live_result_fingerprint_preserves_endpoint_order(self) -> None:
        language = _language()
        first = _endpoint(subject_id="z-endpoint")
        second = _endpoint(subject_id="a-endpoint")
        forward = RuntimeEffectResult.succeeded(
            "event-started-a",
            observations=(first, second),
        )
        reverse = RuntimeEffectResult.succeeded(
            "event-started-a",
            observations=(second, first),
        )

        self.assertNotEqual(
            language.runtime_effect_result_fingerprint(forward),
            language.runtime_effect_result_fingerprint(reverse),
        )

    def test_runtime_endpoint_observation_fits_operations_text_bounds(self) -> None:
        maximum_literal = _literal_address(ENDPOINT_TEXT_MAX)
        maximum_secret = "secret://" + "s" * (ENDPOINT_TEXT_MAX - len("secret://"))
        maximum = {
            "subject": lambda: _endpoint(subject_id="s" * ENDPOINT_TEXT_MAX),
            "socket": lambda: _endpoint(socket_name="s" * ENDPOINT_TEXT_MAX),
            "graph": lambda: _endpoint(graph_id="g" * ENDPOINT_TEXT_MAX),
            "literal": lambda: _endpoint(address=maximum_literal),
            "secret-reference": lambda: RuntimeEndpointObservation(
                "api",
                "http",
                "graph-desired",
                Protocol.HTTP,
                EndpointContext.RUNTIME_PRIVATE,
                SecretEndpointMaterial(maximum_secret),
            ),
        }
        for name, constructor in maximum.items():
            with self.subTest(name=name, boundary="maximum"):
                self.assertIsInstance(constructor(), RuntimeEndpointObservation)

        candidates = {
            "subject": (
                lambda: _endpoint(subject_id="subject-canary-" + "s" * 498),
                "endpoint subject identity must be bounded text",
                "subject-canary-",
            ),
            "socket": (
                lambda: _endpoint(socket_name="socket-canary-" + "s" * 499),
                "endpoint socket identity must be bounded text",
                "socket-canary-",
            ),
            "graph": (
                lambda: _endpoint(graph_id="graph-canary-" + "g" * 500),
                "endpoint graph identity must be bounded text",
                "graph-canary-",
            ),
            "literal": (
                lambda: _endpoint(address=_literal_address(ENDPOINT_TEXT_MAX + 1)),
                "literal endpoint material must be bounded text",
                "http://",
            ),
            "secret-reference": (
                lambda: RuntimeEndpointObservation(
                    "api",
                    "http",
                    "graph-desired",
                    Protocol.HTTP,
                    EndpointContext.RUNTIME_PRIVATE,
                    SecretEndpointMaterial(maximum_secret + "s"),
                ),
                "secret endpoint material must be bounded text",
                maximum_secret[:64],
            ),
        }
        for name, (constructor, message, canary) in candidates.items():
            with self.subTest(name=name, boundary="plus-one"):
                with self.assertRaises(ValueError) as caught:
                    constructor()
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(str(caught.exception), message)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                rendered = f"{caught.exception!s} {caught.exception!r}"
                self.assertNotIn(canary, rendered)

    def test_runtime_endpoint_observation_fits_operations_evidence_bound(self) -> None:
        maximum_subject = _subject_for_bridge_evidence_size(
            BRIDGE_EVIDENCE_MAX_BYTES
        )
        plus_one_subject = _subject_for_bridge_evidence_size(
            BRIDGE_EVIDENCE_MAX_BYTES + 1
        )
        self.assertLessEqual(len(maximum_subject), ENDPOINT_TEXT_MAX)
        self.assertLessEqual(len(plus_one_subject), ENDPOINT_TEXT_MAX)

        maximum = _endpoint(subject_id=maximum_subject)
        self.assertEqual(
            _bridge_evidence_size(subject_id=maximum.subject_id),
            BRIDGE_EVIDENCE_MAX_BYTES,
        )
        with self.assertRaises(ValueError) as caught:
            _endpoint(subject_id=plus_one_subject)
        self.assertIs(type(caught.exception), ValueError)
        self.assertEqual(
            str(caught.exception),
            "runtime endpoint evidence must not exceed 4096 encoded bytes",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        rendered = f"{caught.exception!s} {caught.exception!r}"
        self.assertNotIn(plus_one_subject[:64], rendered)

    def test_runtime_endpoint_text_owners_reject_hostile_subclasses_first(self) -> None:
        injected = RuntimeError("hostile text dispatch canary")
        dispatches: list[str] = []

        class HostileText(str):
            def __len__(self) -> int:
                dispatches.append("__len__")
                raise injected

            def startswith(self, *args, **kwargs) -> bool:
                dispatches.append("startswith")
                raise injected

            def strip(self, *args, **kwargs) -> str:
                dispatches.append("strip")
                raise injected

        candidates = {
            "subject": (
                lambda: _endpoint(subject_id=HostileText("subject-canary")),
                "endpoint subject identity must be bounded text",
                "subject-canary",
            ),
            "socket": (
                lambda: _endpoint(socket_name=HostileText("socket-canary")),
                "endpoint socket identity must be bounded text",
                "socket-canary",
            ),
            "graph": (
                lambda: _endpoint(graph_id=HostileText("graph-canary")),
                "endpoint graph identity must be bounded text",
                "graph-canary",
            ),
            "literal": (
                lambda: LiteralEndpointMaterial(
                    HostileText("http://literal-canary:8000")
                ),
                "literal endpoint material must be bounded text",
                "literal-canary",
            ),
            "secret-reference": (
                lambda: SecretEndpointMaterial(
                    HostileText("secret://reference-canary")
                ),
                "secret endpoint material must be bounded text",
                "reference-canary",
            ),
        }
        for name, (constructor, message, canary) in candidates.items():
            with self.subTest(name=name):
                dispatches.clear()
                try:
                    constructor()
                except (ValueError, RuntimeError) as caught:
                    pass
                else:
                    self.fail(f"{name} admitted a hostile text subclass")
                self.assertEqual(
                    dispatches,
                    [],
                    f"{name} dispatched a hostile text method",
                )
                self.assertIs(type(caught), ValueError)
                self.assertEqual(str(caught), message)
                self.assertIsNone(caught.__cause__)
                self.assertIsNone(caught.__context__)
                rendered = f"{caught!s} {caught!r}"
                self.assertNotIn(canary, rendered)

    def test_runtime_endpoint_rejects_subclassed_nested_values(self) -> None:
        endpoint = _endpoint(subject_id="endpoint-subclass-canary")
        hostile_endpoint = _subclass_copy(endpoint)
        hostile_literal = type(
            "HostileLiteralEndpointMaterial",
            (LiteralEndpointMaterial,),
            {},
        )("http://literal-subclass-canary:8000")
        hostile_secret = type(
            "HostileSecretEndpointMaterial",
            (SecretEndpointMaterial,),
            {},
        )("secret://material-subclass-canary")

        for name, address in (
            ("literal", hostile_literal),
            ("secret-reference", hostile_secret),
        ):
            with self.subTest(name=name):
                with self.assertRaises(TypeError) as caught:
                    RuntimeEndpointObservation(
                        "api",
                        "http",
                        "graph-desired",
                        Protocol.HTTP,
                        EndpointContext.RUNTIME_PRIVATE,
                        address,
                    )
                self.assertIs(type(caught.exception), TypeError)
                self.assertEqual(
                    str(caught.exception),
                    "runtime endpoint address must be typed material",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(
                    "subclass-canary",
                    f"{caught.exception!s} {caught.exception!r}",
                )

        with self.assertRaises(RuntimeEffectContractError) as caught:
            RuntimeEffectResult.succeeded(
                "event-started-a",
                observations=(hostile_endpoint,),
            )
        self.assertIs(type(caught.exception), RuntimeEffectContractError)
        self.assertEqual(
            str(caught.exception),
            "runtime effect observations must be RuntimeEndpointObservation",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(
            "endpoint-subclass-canary",
            f"{caught.exception!s} {caught.exception!r}",
        )

    def test_runtime_endpoint_construction_does_not_call_virtual_descriptor(self) -> None:
        injected = RuntimeError("runtime endpoint descriptor dispatch canary")
        original = RuntimeEndpointObservation.descriptor

        def fail_if_called(_endpoint):
            raise injected

        RuntimeEndpointObservation.descriptor = fail_if_called
        try:
            try:
                endpoint = _endpoint(subject_id="nonvirtual-sizing")
            except RuntimeError as caught:
                self.assertIs(caught, injected)
                self.fail("endpoint construction called the virtual descriptor")
            self.assertEqual(endpoint.subject_id, "nonvirtual-sizing")
        finally:
            RuntimeEndpointObservation.descriptor = original

    def test_runtime_endpoint_identities_reject_all_c0_controls(self) -> None:
        constructors = {
            "subject": lambda candidate: _endpoint(subject_id=candidate),
            "socket": lambda candidate: _endpoint(socket_name=candidate),
            "graph": lambda candidate: _endpoint(graph_id=candidate),
        }
        for name, constructor in constructors.items():
            admitted: list[int] = []
            message = f"endpoint {name} identity must not contain control characters"
            for codepoint in range(32):
                candidate = f"{name}-control-canary-{chr(codepoint)}-value"
                try:
                    constructor(candidate)
                except ValueError as caught:
                    self.assertIs(type(caught), ValueError)
                    self.assertEqual(str(caught), message)
                    self.assertIsNone(caught.__cause__)
                    self.assertIsNone(caught.__context__)
                    rendered = f"{caught!s} {caught!r}"
                    self.assertNotIn(f"{name}-control-canary", rendered)
                else:
                    admitted.append(codepoint)
            with self.subTest(name=name, boundary="c0"):
                self.assertEqual(admitted, [])

            printable_boundary = f"{name}-" + " " * (ENDPOINT_TEXT_MAX - len(name) - 1)
            with self.subTest(name=name, boundary="space"):
                self.assertEqual(len(printable_boundary), ENDPOINT_TEXT_MAX)
                self.assertIsInstance(
                    constructor(printable_boundary),
                    RuntimeEndpointObservation,
                )

    def test_live_result_requires_exact_observation_tuple_and_values(self) -> None:
        endpoint = _endpoint(subject_id="tuple-canary")
        hostile_endpoint = _subclass_copy(endpoint)

        class HostileTuple(tuple):
            pass

        consumed: list[RuntimeEndpointObservation] = []

        def generated():
            consumed.append(endpoint)
            yield endpoint

        candidates = {
            "list": [endpoint],
            "generator": generated(),
            "tuple-subclass": HostileTuple((endpoint,)),
            "endpoint-subclass": (hostile_endpoint,),
        }
        for name, observations in candidates.items():
            with self.subTest(name=name):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    RuntimeEffectResult.succeeded(
                        "event-started-a",
                        observations=observations,
                    )
                self.assertIs(type(caught.exception), RuntimeEffectContractError)
                self.assertEqual(
                    str(caught.exception),
                    "runtime effect observations must be RuntimeEndpointObservation",
                )
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(
                    "tuple-canary",
                    f"{caught.exception!s} {caught.exception!r}",
                )
        self.assertEqual(consumed, [])

    def test_complete_live_result_fingerprint_input_has_exact_byte_ceiling(self) -> None:
        language = _language()

        def result_for_size(target: int) -> RuntimeEffectResult:
            observations = (
                _endpoint(subject_id="endpoint-z"),
                _endpoint(subject_id="endpoint-a", socket_name="admin"),
            )
            for full_fields in range(32):
                evidence = {
                    f"padding-{index:02d}": "x" * ENDPOINT_TEXT_MAX
                    for index in range(full_fields)
                }
                evidence["tail"] = ""
                seed = RuntimeEffectResult.succeeded(
                    "event-started-a",
                    evidence=evidence,
                    observations=observations,
                )
                remaining = target - len(rfc8785.dumps(seed.descriptor()))
                if 0 <= remaining <= ENDPOINT_TEXT_MAX:
                    evidence["tail"] = "x" * remaining
                    candidate = RuntimeEffectResult.succeeded(
                        "event-started-a",
                        evidence=evidence,
                        observations=observations,
                    )
                    self.assertEqual(
                        len(rfc8785.dumps(candidate.descriptor())),
                        target,
                    )
                    return candidate
            raise AssertionError(f"cannot construct {target}-byte live result")

        maximum = result_for_size(OUTCOME_FINGERPRINT_MAX_BYTES)
        plus_one = result_for_size(OUTCOME_FINGERPRINT_MAX_BYTES + 1)
        self.assertGreaterEqual(len(maximum.observations), 2)
        self.assertTrue(
            all(
                len(value) <= ENDPOINT_TEXT_MAX
                for value in maximum.evidence.values()
            )
        )
        self.assertEqual(len(language.runtime_effect_result_fingerprint(maximum)), 64)
        with self.assertRaisesRegex(RuntimeEffectContractError, "too large") as caught:
            language.runtime_effect_result_fingerprint(plus_one)
        self.assertNotIn("s" * 64, str(caught.exception) + repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


class RuntimeEffectObservationPackageBoundaryTests(unittest.TestCase):
    def test_root_exports_are_exact_module_identities(self) -> None:
        module = _language()
        missing = sorted(PUBLIC_NAMES - set(root.__all__))
        self.assertEqual(missing, [])
        for name in sorted(PUBLIC_NAMES):
            with self.subTest(name=name):
                self.assertIs(getattr(root, name), getattr(module, name))

    def test_inventory_owns_one_source_true_pure_module(self) -> None:
        inventory_path = os.environ.get("CPK_PACKAGE_MODULE_INVENTORY")
        if inventory_path is None:
            inventory_path = str(
                REPOSITORY_ROOT
                / "docs"
                / "architecture"
                / "package-module-inventory.json"
            )
        inventory = json.loads(
            Path(inventory_path).read_text(encoding="utf-8")
        )
        rows = [row for row in inventory["modules"] if row["module"] == MODULE]

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "core")
        self.assertEqual(row["role"], "pure-contract")
        self.assertEqual(
            row["source"],
            "control_plane_kit_core/runtime_effect_observation.py",
        )
        self.assertEqual(set(row["canonical_public_exports"]), PUBLIC_NAMES)
        self.assertEqual(
            set(row["internal_dependencies"]),
            {
                "control_plane_kit_core.runtime_effects",
                "control_plane_kit_core.secrets",
            },
        )
        self.assertEqual(row["optional_external_dependencies"], [])
        self.assertEqual(
            set(row["protecting_tests"]),
            {
                "tests/test_runtime_effect_intent.py",
                "tests/test_runtime_effect_observation.py",
                "tests/test_runtime_effect_observation_boundary.py",
            },
        )
        self.assertIn("pure", row["motivation"].lower())
        self.assertIn("observation", row["motivation"].lower())

    def test_module_import_dag_and_calls_are_effect_free(self) -> None:
        path = SOURCE_ROOT / "runtime_effect_observation.py"
        self.assertTrue(path.is_file(), "missing #1693 observation source module")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)

        forbidden_import_roots = {
            "control_plane_kit_operations",
            "docker",
            "httpx",
            "pathlib",
            "psycopg",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        forbidden_calls = {
            "connect",
            "execute",
            "open",
            "read_text",
            "resolve",
            "run",
            "system",
            "urlopen",
            "write_text",
        }
        self.assertEqual(
            {name.split(".", 1)[0] for name in imports} & forbidden_import_roots,
            set(),
        )
        self.assertEqual(calls & forbidden_calls, set())

        runtime_effects_tree = ast.parse(
            (SOURCE_ROOT / "runtime_effects.py").read_text(encoding="utf-8")
        )
        reverse_imports = {
            node.module
            for node in ast.walk(runtime_effects_tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn(MODULE, reverse_imports)


if __name__ == "__main__":
    unittest.main()
