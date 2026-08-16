from __future__ import annotations

from collections import UserDict
from dataclasses import replace
import hashlib
import importlib
import typing
import unittest

import rfc8785

from control_plane_kit_core.operations.run_identity import RunId
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityDeliverySecretReference,
    RuntimeAuthorityReference,
    RuntimeEffectContractError,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectSource,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.types import RuntimeKind


MODULE = "control_plane_kit_core.runtime_effect_observation"
OBSERVATION_DOMAIN = b"control-plane-kit.runtime-effect-observation.v1\x00"
VARIANT_NAMES = (
    "RuntimeEffectObservedSucceeded",
    "RuntimeEffectObservedFailed",
    "RuntimeEffectObservedAbsent",
    "RuntimeEffectObservedConflict",
    "RuntimeEffectObservedIndeterminate",
    "RuntimeEffectObserverUnsupported",
)


def _language():
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        if error.name != MODULE:
            raise
        raise AssertionError("missing #1693 runtime effect observation language") from error
    required = {
        "RuntimeEffectObservationEvidence",
        "RuntimeEffectObservationFailure",
        "RuntimeEffectObservationRequest",
        "RuntimeEffectObservationResult",
        "runtime_effect_observation_fingerprint",
        "runtime_effect_intent_fingerprint",
        "runtime_effect_intent_for_request",
        "runtime_effect_request_for_intent",
        *VARIANT_NAMES,
    }
    missing = sorted(required - set(vars(module)))
    if missing:
        raise AssertionError(
            f"missing #1693 observation capability: {', '.join(missing)}"
        )
    return module


def _delivery() -> RuntimeAuthorityAccessDelivery:
    return RuntimeAuthorityAccessDelivery(
        authority_ref=RuntimeAuthorityReference("remote-docker"),
        delivery_kind=RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES,
        secret_references=(
            RuntimeAuthorityDeliverySecretReference(
                "ca-cert", "secret://local/workspace-a/docker/ca-cert"
            ),
            RuntimeAuthorityDeliverySecretReference(
                "client-cert", "secret://local/workspace-a/docker/client-cert"
            ),
            RuntimeAuthorityDeliverySecretReference(
                "client-key", "secret://local/workspace-a/docker/client-key"
            ),
        ),
    )


def _grant(
    *,
    fresh: str,
    label: str,
    reference: str,
    intent: SecretUseIntent,
    effect_id: str = "event-started-a",
) -> SecretResolutionGrant:
    return SecretResolutionGrant(
        authorization_id="suse_" + fresh * 64,
        workspace_id="workspace-a",
        reference_registration_id="sref_" + fresh * 64,
        provider_registration_id="sprov_" + fresh * 64,
        endpoint_reference=SecretProviderEndpointReference(f"provider-{fresh}"),
        credential_reference=SecretReference(
            f"secret://bootstrap/provider-{fresh}-token"
        ),
        reference=SecretReference(reference),
        intent=intent,
        actor_subject=f"worker-{fresh}",
        correlation_id="secret-use-" + fresh * 64,
        intent_fingerprint=fresh * 64,
        run_id="run-a",
        activity_id="activity-a",
        effect_id=effect_id,
        operation_id=f"operation-{fresh}",
        session_id=f"session-{fresh}",
    )


def _grant_set(fresh: str) -> tuple[SecretResolutionGrant, ...]:
    return (
        _grant(
            fresh=fresh,
            label="ca-cert",
            reference="secret://local/workspace-a/docker/ca-cert",
            intent=SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE,
        ),
        _grant(
            fresh=chr(ord(fresh) + 1),
            label="client-cert",
            reference="secret://local/workspace-a/docker/client-cert",
            intent=SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE,
        ),
        _grant(
            fresh=chr(ord(fresh) + 2),
            label="client-key",
            reference="secret://local/workspace-a/docker/client-key",
            intent=SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
        ),
    )


def _request(
    *,
    grants: tuple[SecretResolutionGrant, ...] = (),
    effect_id: str = "event-started-a",
) -> RuntimeEffectRequest:
    delivery = _delivery()
    return RuntimeEffectRequest(
        effect_id=effect_id,
        kind=RuntimeEffectKind.REALIZE_ACTIVITY,
        runtime_kind=RuntimeKind.DOCKER,
        source=RuntimeEffectSource(
            workspace_id="workspace-a",
            request_id="request-a",
            run_id=RunId("run-a"),
            plan_id="plan-a",
            base_graph_id="graph-base",
            desired_graph_id="graph-desired",
            intent_event_id=effect_id,
        ),
        activity_id=ActivityId("activity-a"),
        operation=StartNode(NodeTarget("api")),
        authority_ref=delivery.authority_ref,
        authority_deliveries=(delivery,),
        secret_resolution_grants=grants,
    )


class RuntimeEffectObservationRequestTests(unittest.TestCase):
    def test_two_fresh_complete_tls_grant_sets_preserve_public_intent(self) -> None:
        language = _language()
        first_grants = _grant_set("a")
        second_grants = _grant_set("d")
        ungranted_request = _request()
        intent = language.runtime_effect_intent_for_request(ungranted_request)
        first_request = language.runtime_effect_request_for_intent(
            intent,
            effect_id="event-started-a",
            secret_resolution_grants=first_grants,
        )
        second_request = language.runtime_effect_request_for_intent(
            intent,
            effect_id="event-started-a",
            secret_resolution_grants=second_grants,
        )
        ungranted = language.RuntimeEffectObservationRequest(ungranted_request)
        first = language.RuntimeEffectObservationRequest(first_request)
        second = language.RuntimeEffectObservationRequest(second_request)

        self.assertEqual(first.intent, ungranted.intent)
        self.assertEqual(second.intent, ungranted.intent)
        self.assertEqual(first.request_fingerprint, ungranted.request_fingerprint)
        self.assertEqual(second.request_fingerprint, ungranted.request_fingerprint)
        self.assertEqual(first.descriptor(), second.descriptor())
        self.assertEqual(
            set(first.descriptor()),
            {"effect_id", "request_fingerprint", "intent"},
        )
        self.assertIs(first.runtime_request, first_request)
        self.assertIs(second.runtime_request, second_request)
        for observation, grants in (
            (first, first_grants),
            (second, second_grants),
        ):
            for candidate in grants:
                self.assertNotIn(candidate.authorization_id, repr(observation))
                self.assertNotIn(
                    candidate.authorization_id,
                    repr(observation.descriptor()),
                )
                self.assertNotIn(
                    candidate.credential_reference.reference_id,
                    repr(observation),
                )

    def test_effect_id_is_post_start_correlation_not_intent_identity(self) -> None:
        language = _language()
        first = language.RuntimeEffectObservationRequest(_request())
        second = language.RuntimeEffectObservationRequest(
            _request(effect_id="event-started-b")
        )

        self.assertEqual(first.intent, second.intent)
        self.assertEqual(first.request_fingerprint, second.request_fingerprint)
        self.assertNotEqual(first.effect_id, second.effect_id)
        self.assertNotEqual(first.descriptor(), second.descriptor())

    def test_grant_admission_is_exact_and_closed(self) -> None:
        language = _language()
        complete = _grant_set("a")
        invalid = {
            "unrelated reference": replace(
                complete[0],
                reference=SecretReference(
                    "secret://local/workspace-a/docker/unrelated"
                ),
            ),
            "wrong intent": replace(
                complete[0],
                intent=SecretUseIntent.OCI_PULL_CREDENTIAL,
            ),
            "wrong workspace": replace(complete[0], workspace_id="workspace-b"),
            "wrong effect": replace(complete[0], effect_id="event-started-b"),
        }

        for name, grant in invalid.items():
            with self.subTest(name=name):
                try:
                    request = _request(grants=(grant,))
                except RuntimeEffectContractError as error:
                    if name not in {"wrong workspace", "wrong effect"}:
                        raise
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                    continue
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    language.RuntimeEffectObservationRequest(request)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(grant.reference.reference_id, str(caught.exception))
                self.assertNotIn(grant.authorization_id, repr(caught.exception))

    def test_observation_request_requires_exact_request_and_event_congruence(self) -> None:
        language = _language()

        class HostileRequest(RuntimeEffectRequest):
            pass

        request = _request()
        with self.assertRaises(RuntimeEffectContractError):
            language.RuntimeEffectObservationRequest(HostileRequest(**request.__dict__))

        forged = object.__new__(RuntimeEffectRequest)
        forged.__dict__.update(request.__dict__)
        forged.__dict__["effect_id"] = "different-event"
        with self.assertRaises(RuntimeEffectContractError):
            language.RuntimeEffectObservationRequest(forged)


class RuntimeEffectObservationResultTests(unittest.TestCase):
    def _values(self, language):
        evidence = language.RuntimeEffectObservationEvidence(
            {"container_state": "running", "exit_code": 0}
        )
        failure = language.RuntimeEffectObservationFailure(
            "observer.conflict",
            "provider state conflicts with the exact request",
            language.RuntimeEffectObservationEvidence({"reason": "identity-drift"}),
        )
        common = {
            "effect_id": "event-started-a",
            "request_fingerprint": "a" * 64,
            "evidence": evidence,
        }
        return (
            language.RuntimeEffectObservedSucceeded(**common),
            language.RuntimeEffectObservedFailed(**common, failure=failure),
            language.RuntimeEffectObservedAbsent(**common),
            language.RuntimeEffectObservedConflict(**common, failure=failure),
            language.RuntimeEffectObservedIndeterminate(**common, failure=failure),
            language.RuntimeEffectObserverUnsupported(**common, failure=failure),
        )

    def test_six_way_sum_and_exact_descriptors_are_closed(self) -> None:
        language = _language()
        values = self._values(language)

        self.assertEqual(
            set(typing.get_args(language.RuntimeEffectObservationResult)),
            {getattr(language, name) for name in VARIANT_NAMES},
        )
        self.assertEqual(
            tuple(value.descriptor()["kind"] for value in values),
            (
                "succeeded",
                "failed",
                "absent",
                "conflict",
                "indeterminate",
                "observer-unsupported",
            ),
        )
        for value in values:
            self.assertIs(type(value), getattr(language, type(value).__name__))
            self.assertEqual(
                set(value.descriptor()),
                {"kind", "effect_id", "request_fingerprint", "evidence", "failure"},
            )
            self.assertTrue(value.descriptor()["evidence"])
        self.assertIsNone(values[0].descriptor()["failure"])
        self.assertIsNone(values[2].descriptor()["failure"])
        for value in (values[1], *values[3:]):
            self.assertIsNotNone(value.descriptor()["failure"])

    def test_failure_congruence_and_exact_nested_nominality_are_total(self) -> None:
        language = _language()
        evidence = language.RuntimeEffectObservationEvidence({"state": "running"})
        failure = language.RuntimeEffectObservationFailure(
            "observer.failed", "inspection failed"
        )
        common = {
            "effect_id": "event-started-a",
            "request_fingerprint": "a" * 64,
            "evidence": evidence,
        }
        invalid = (
            lambda: language.RuntimeEffectObservedSucceeded(
                **common, failure=failure
            ),
            lambda: language.RuntimeEffectObservedAbsent(**common, failure=failure),
            lambda: language.RuntimeEffectObservedFailed(**common),
            lambda: language.RuntimeEffectObservedConflict(**common),
            lambda: language.RuntimeEffectObservedIndeterminate(**common),
            lambda: language.RuntimeEffectObserverUnsupported(**common),
        )
        for construct in invalid:
            with self.subTest(construct=construct):
                with self.assertRaises(RuntimeEffectContractError):
                    construct()

        class HostileEvidence(language.RuntimeEffectObservationEvidence):
            pass

        class HostileFailure(language.RuntimeEffectObservationFailure):
            pass

        with self.assertRaises(RuntimeEffectContractError):
            language.RuntimeEffectObservedSucceeded(
                **{**common, "evidence": HostileEvidence({"state": "running"})}
            )
        with self.assertRaises(RuntimeEffectContractError):
            language.RuntimeEffectObservedFailed(
                **common,
                failure=HostileFailure("observer.failed", "inspection failed"),
            )

    def test_evidence_is_nonempty_bounded_exact_json_and_redacted(self) -> None:
        language = _language()

        class HostileDict(dict):
            pass

        class HostileList(list):
            pass

        class HostileText(str):
            pass

        class HostileInt(int):
            pass

        invalid = (
            {},
            HostileDict({"state": "running"}),
            UserDict({"state": "running"}),
            {"state": HostileList(["running"])},
            {"state": HostileText("running")},
            {"count": HostileInt(1)},
            {"float": 1.5},
            {"tuple": ("running",)},
            {1: "non-text-key"},
            {"count": 9_007_199_254_740_992},
            {"text": "x" * 513},
            {"text": "unpaired-\ud800"},
            {"text": "nul\x00text"},
            {"nested": [[[[["too-deep"]]]]]},
            {f"field-{index}": index for index in range(33)},
            {"items": list(range(33))},
            {"credential": "token=do-not-store"},
            {"endpoint": "tcp://docker.example:2376"},
            {"address": "10.0.0.8"},
            {"raw": "BEGIN PRIVATE KEY"},
        )
        for index, candidate in enumerate(invalid):
            with self.subTest(case=index):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    language.RuntimeEffectObservationEvidence(candidate)
                self.assertNotIn("do-not-store", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

        accepted = language.RuntimeEffectObservationEvidence(
            {
                "minimum": -9_007_199_254_740_991,
                "maximum": 9_007_199_254_740_991,
            }
        )
        self.assertEqual(
            accepted.descriptor(),
            {
                "maximum": 9_007_199_254_740_991,
                "minimum": -9_007_199_254_740_991,
            },
        )

        oversized = {f"field-{index}": "x" * 500 for index in range(9)}
        with self.assertRaisesRegex(RuntimeEffectContractError, "too large"):
            language.RuntimeEffectObservationEvidence(oversized)

    def test_failure_is_bounded_categorical_and_candidate_free(self) -> None:
        language = _language()
        valid = language.RuntimeEffectObservationFailure(
            "observer.indeterminate",
            "provider observation is indeterminate",
            language.RuntimeEffectObservationEvidence({"reason": "timeout"}),
        )
        self.assertEqual(
            valid.descriptor(),
            {
                "code": "observer.indeterminate",
                "message": "provider observation is indeterminate",
                "details": {"reason": "timeout"},
            },
        )
        invalid = (
            ("", "message"),
            ("x" * 513, "message"),
            ("observer.failed", "x" * 513),
            ("observer.failed", "token=do-not-store"),
        )
        for index, (code, message) in enumerate(invalid):
            with self.subTest(case=index):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    language.RuntimeEffectObservationFailure(code, message)
                self.assertNotIn("do-not-store", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_observation_fingerprint_has_golden_domain_and_commits_variant(self) -> None:
        language = _language()
        succeeded = self._values(language)[0]
        canonical = rfc8785.dumps(succeeded.descriptor())

        self.assertEqual(
            canonical,
            b'{"effect_id":"event-started-a","evidence":{"container_state":"running","exit_code":0},"failure":null,"kind":"succeeded","request_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
        )
        self.assertEqual(
            language.runtime_effect_observation_fingerprint(succeeded),
            "f24dfe5d2953ea82bd2832cf7777f98b21dae8f5790350fd080a6e60b98ff18c",
        )
        self.assertEqual(
            hashlib.sha256(OBSERVATION_DOMAIN + canonical).hexdigest(),
            "f24dfe5d2953ea82bd2832cf7777f98b21dae8f5790350fd080a6e60b98ff18c",
        )
        fingerprints = {
            language.runtime_effect_observation_fingerprint(value)
            for value in self._values(language)
        }
        self.assertEqual(len(fingerprints), 6)


if __name__ == "__main__":
    unittest.main()
