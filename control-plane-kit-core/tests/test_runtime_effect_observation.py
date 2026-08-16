from __future__ import annotations

from collections import UserDict
from dataclasses import fields, replace
import hashlib
import importlib
import typing
import unittest

import rfc8785

from control_plane_kit_core.operations.run_identity import RunId
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityDeliverySecretReference,
    RuntimeAuthorityReference,
    RuntimeEffectContractError,
)
from control_plane_kit_core.runtime_effects import (
    ImagePullAuthority,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectSource,
    RuntimeProductMaterial,
)
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.types import Protocol, RuntimeKind
from control_plane_kit_core.verification import (
    PostgresPasswordAuthentication,
    PostgresQueryCheck,
    VerificationContract,
)


MODULE = "control_plane_kit_core.runtime_effect_observation"
OBSERVATION_DOMAIN = b"control-plane-kit.runtime-effect-observation.v1\x00"
OUTCOME_FINGERPRINT_MAX_BYTES = 8_192
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
        intent_fingerprint=hashlib.sha256(fresh.encode("utf-8")).hexdigest(),
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


def _grant_product() -> RuntimeProductMaterial:
    identity = ProductIdentity("openj92", "secret-using-server", 1)
    return RuntimeProductMaterial(
        node_id="api",
        runtime_id="docker",
        reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
        product=ContainerServerProduct(
            identity=identity,
            image=OciImageReference(
                registry="ghcr.io",
                repository="openj92/secret-using-server",
                digest="sha256:" + "a" * 64,
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(
                    providers=(ProviderSocket("database", Protocol.POSTGRES),)
                ),
                provider_ports=(ProviderRuntimePort("database", 5432),),
                secret_deliveries=(
                    SecretEnvironmentDelivery(
                        "APP_CONTROL_TOKEN",
                        SecretReference("secret://local/workspace-a/app/token"),
                        SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                    ),
                ),
                verification=VerificationContract(
                    (
                        PostgresQueryCheck(
                            check_id="database-ready",
                            provider_socket="database",
                            authentication=PostgresPasswordAuthentication(
                                database="app",
                                username="app",
                                password_reference=SecretReference(
                                    "secret://local/workspace-a/postgres/password"
                                ),
                            ),
                        ),
                    )
                ),
            ),
        ),
        pull_authority=ImagePullAuthority(
            registry="ghcr.io",
            repository="openj92",
            credential_reference=SecretReference(
                "secret://local/workspace-a/oci/pull"
            ),
        ),
    )


def _subclass_copy(value):
    hostile_type = type(f"Hostile{type(value).__name__}", (type(value),), {})
    arguments = {
        item.name: getattr(value, item.name)
        for item in fields(value)
        if item.init
    }
    return hostile_type(**arguments)


def _forged_dataclass(value, **changes):
    forged = object.__new__(type(value))
    for item in fields(value):
        object.__setattr__(
            forged,
            item.name,
            changes.get(item.name, getattr(value, item.name)),
        )
    return forged


def _request(
    *,
    grants: tuple[SecretResolutionGrant, ...] = (),
    effect_id: str = "event-started-a",
    products: tuple[RuntimeProductMaterial, ...] = (),
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
        products=products,
    )


def _observations() -> tuple[RuntimeEndpointObservation, ...]:
    return (
        RuntimeEndpointObservation(
            subject_id="api",
            socket_name="http",
            graph_id="graph-desired",
            protocol=Protocol.HTTP,
            context=EndpointContext.RUNTIME_PRIVATE,
            address=LiteralEndpointMaterial("http://api:8000"),
        ),
        RuntimeEndpointObservation(
            subject_id="database",
            socket_name="postgres",
            graph_id="graph-desired",
            protocol=Protocol.POSTGRES,
            context=EndpointContext.RUNTIME_PRIVATE,
            address=LiteralEndpointMaterial("postgres://database:5432"),
        ),
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

    def test_each_closed_grant_use_family_is_admitted_exactly_once(self) -> None:
        language = _language()
        families = {
            "product secret delivery": (
                "secret://local/workspace-a/app/token",
                SecretUseIntent.APPLICATION_CONTROL_TOKEN,
            ),
            "image pull authority": (
                "secret://local/workspace-a/oci/pull",
                SecretUseIntent.OCI_PULL_CREDENTIAL,
            ),
            "verification material": (
                "secret://local/workspace-a/postgres/password",
                SecretUseIntent.POSTGRES_PASSWORD,
            ),
            "runtime authority delivery": (
                "secret://local/workspace-a/docker/client-key",
                SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
            ),
        }
        for index, (name, (reference, intent)) in enumerate(families.items()):
            fresh = chr(ord("h") + index)
            admitted = _grant(
                fresh=fresh,
                label=name,
                reference=reference,
                intent=intent,
            )
            wrong_intent = replace(
                admitted,
                intent=(
                    SecretUseIntent.POSTGRES_PASSWORD
                    if intent is not SecretUseIntent.POSTGRES_PASSWORD
                    else SecretUseIntent.APPLICATION_CONTROL_TOKEN
                ),
            )
            unrelated = replace(
                admitted,
                reference=SecretReference(
                    f"secret://local/workspace-a/unrelated/{index}"
                ),
            )

            with self.subTest(family=name, case="positive"):
                request = _request(grants=(admitted,), products=(_grant_product(),))
                observation = language.RuntimeEffectObservationRequest(request)
                self.assertIs(observation.runtime_request.secret_resolution_grants[0], admitted)

            for case, rejected in (
                ("wrong intent", wrong_intent),
                ("unrelated reference", unrelated),
            ):
                with self.subTest(family=name, case=case):
                    request = _request(
                        grants=(rejected,),
                        products=(_grant_product(),),
                    )
                    with self.assertRaises(RuntimeEffectContractError) as caught:
                        language.RuntimeEffectObservationRequest(request)
                    message = str(caught.exception) + repr(caught.exception)
                    self.assertNotIn(rejected.reference.reference_id, message)
                    self.assertNotIn(rejected.authorization_id, message)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertIsNone(caught.exception.__context__)

        admitted = _grant(
            fresh="n",
            label="product secret delivery",
            reference="secret://local/workspace-a/app/token",
            intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
        )
        request = _request(grants=(admitted,), products=(_grant_product(),))
        duplicate = _forged_dataclass(
            request,
            secret_resolution_grants=(admitted, admitted),
        )
        with self.assertRaises(RuntimeEffectContractError) as caught:
            language.RuntimeEffectObservationRequest(duplicate)
        self.assertNotIn(admitted.authorization_id, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_grant_workspace_and_effect_coordinates_remain_closed(self) -> None:
        language = _language()
        admitted = _grant_set("a")[0]
        for name, grant in (
            ("wrong workspace", replace(admitted, workspace_id="workspace-b")),
            ("wrong effect", replace(admitted, effect_id="event-started-b")),
        ):
            with self.subTest(name=name):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    _request(grants=(grant,))
                self.assertNotIn(grant.authorization_id, repr(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_observation_request_requires_exact_request_and_event_congruence(self) -> None:
        language = _language()

        class HostileRequest(RuntimeEffectRequest):
            pass

        request = _request()
        with self.assertRaises(RuntimeEffectContractError):
            language.RuntimeEffectObservationRequest(_subclass_copy(request))

        forged = _forged_dataclass(request, effect_id="different-event")
        with self.assertRaises(RuntimeEffectContractError):
            language.RuntimeEffectObservationRequest(forged)

        intent = language.runtime_effect_intent_for_request(request)
        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_request_for_intent(
                _subclass_copy(intent),
                effect_id="event-started-a",
            )

    def test_observation_request_fields_are_exact_and_derived(self) -> None:
        language = _language()

        self.assertEqual(
            tuple(
                (item.name, item.init, item.repr)
                for item in fields(language.RuntimeEffectObservationRequest)
            ),
            (
                ("runtime_request", True, False),
                ("intent", False, True),
                ("request_fingerprint", False, True),
            ),
        )
        self.assertTrue(
            language.RuntimeEffectObservationRequest.__dataclass_params__.frozen
        )


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
            "observations": _observations(),
        }
        return (
            language.RuntimeEffectObservedSucceeded(**common),
            language.RuntimeEffectObservedFailed(**common, failure=failure),
            language.RuntimeEffectObservedAbsent(
                **{**common, "observations": ()}
            ),
            language.RuntimeEffectObservedConflict(**common, failure=failure),
            language.RuntimeEffectObservedIndeterminate(**common, failure=failure),
            language.RuntimeEffectObserverUnsupported(
                **{**common, "observations": ()},
                failure=failure,
            ),
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
        for index, value in enumerate(values):
            self.assertIs(type(value), getattr(language, type(value).__name__))
            self.assertEqual(
                set(value.descriptor()),
                {
                    "kind",
                    "effect_id",
                    "request_fingerprint",
                    "evidence",
                    "failure",
                    "observations",
                },
            )
            self.assertTrue(value.descriptor()["evidence"])
            self.assertEqual(
                value.descriptor()["observations"],
                (
                    []
                    if index in {2, 5}
                    else [item.descriptor() for item in _observations()]
                ),
            )
            self.assertEqual(
                value.observations,
                () if index in {2, 5} else _observations(),
            )
            self.assertNotIn("container_state", repr(value))
            self.assertNotIn("http://api:8000", repr(value))
            if value.failure is not None:
                self.assertNotIn(value.failure.message, repr(value))
        self.assertIsNone(values[0].descriptor()["failure"])
        self.assertIsNone(values[2].descriptor()["failure"])
        for value in (values[1], *values[3:]):
            self.assertIsNotNone(value.descriptor()["failure"])

    def test_public_evidence_failure_and_variant_fields_are_exact(self) -> None:
        language = _language()

        self.assertEqual(
            tuple((item.name, item.init) for item in fields(language.RuntimeEffectObservationEvidence)),
            (("values", True),),
        )
        self.assertEqual(
            tuple((item.name, item.init) for item in fields(language.RuntimeEffectObservationFailure)),
            (("code", True), ("message", True), ("details", True)),
        )
        for name in VARIANT_NAMES:
            with self.subTest(name=name):
                value_type = getattr(language, name)
                self.assertEqual(
                    tuple(
                        (item.name, item.init, item.repr)
                        for item in fields(value_type)
                    ),
                    (
                        ("effect_id", True, True),
                        ("request_fingerprint", True, True),
                        ("evidence", True, False),
                        ("failure", True, False),
                        ("observations", True, False),
                    ),
                )
                self.assertTrue(value_type.__dataclass_params__.frozen)

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

        class HostileObservations(tuple):
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
        for name, construct in (
            (
                "absent",
                lambda: language.RuntimeEffectObservedAbsent(
                    **common,
                    observations=_observations(),
                ),
            ),
            (
                "observer unsupported",
                lambda: language.RuntimeEffectObserverUnsupported(
                    **common,
                    failure=failure,
                    observations=_observations(),
                ),
            ),
        ):
            with self.subTest(name=name, case="nonempty observations"):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    construct()
                error = str(caught.exception) + repr(caught.exception)
                for candidate in (
                    "api",
                    "http",
                    "graph-desired",
                    "http://api:8000",
                ):
                    self.assertNotIn(candidate, error)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
        endpoint = _observations()[0]
        for observations in (
            list(_observations()),
            HostileObservations(_observations()),
            (_subclass_copy(endpoint),),
        ):
            with self.subTest(observations_type=type(observations).__name__):
                with self.assertRaises(RuntimeEffectContractError):
                    language.RuntimeEffectObservedSucceeded(
                        **{**common, "observations": observations},
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
            ({}, None),
            (HostileDict({"state": "running"}), None),
            (UserDict({"state": "running"}), None),
            ({"state": HostileList(["running"])}, None),
            ({"state": HostileText("running")}, None),
            ({"count": HostileInt(1)}, None),
            ({"float": 1.5}, None),
            ({"tuple": ("running",)}, None),
            ({1: "non-text-key"}, None),
            ({"count": 9_007_199_254_740_992}, None),
            ({"text": "x" * 513}, None),
            ({"text": "unpaired-\ud800"}, None),
            ({"text": "nul\x00text"}, None),
            ({"nested": [[[[["too-deep"]]]]]}, None),
            ({f"field-{index}": index for index in range(33)}, None),
            ({"items": list(range(33))}, None),
            ({"credential": "token=do-not-store"}, "do-not-store"),
            ({"endpoint": "tcp://docker.example:2376"}, "tcp://docker.example:2376"),
            ({"address": "10.0.0.8"}, "10.0.0.8"),
            ({"raw": "BEGIN PRIVATE KEY"}, "BEGIN PRIVATE KEY"),
        )
        for index, (candidate, sensitive) in enumerate(invalid):
            with self.subTest(case=index):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    language.RuntimeEffectObservationEvidence(candidate)
                message = str(caught.exception) + repr(caught.exception)
                if sensitive is not None:
                    self.assertNotIn(sensitive, message)
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
        golden = language.RuntimeEffectObservedSucceeded(
            effect_id="event-started-a",
            request_fingerprint="a" * 64,
            evidence=language.RuntimeEffectObservationEvidence(
                {"container_state": "running", "exit_code": 0}
            ),
        )
        canonical = rfc8785.dumps(golden.descriptor())

        self.assertEqual(
            canonical,
            b'{"effect_id":"event-started-a","evidence":{"container_state":"running","exit_code":0},"failure":null,"kind":"succeeded","observations":[],"request_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
        )
        self.assertEqual(
            language.runtime_effect_observation_fingerprint(golden),
            "8dae30a2619a074a210645c438c5372e0825d1c4d92419a3980deef2cfd4be0d",
        )
        self.assertEqual(
            hashlib.sha256(OBSERVATION_DOMAIN + canonical).hexdigest(),
            "8dae30a2619a074a210645c438c5372e0825d1c4d92419a3980deef2cfd4be0d",
        )
        fingerprints = {
            language.runtime_effect_observation_fingerprint(value)
            for value in self._values(language)
        }
        self.assertEqual(len(fingerprints), 6)

        evidence = language.RuntimeEffectObservationEvidence({"state": "observed"})
        failure = language.RuntimeEffectObservationFailure(
            "observer.failed",
            "provider observation failed",
            language.RuntimeEffectObservationEvidence({"reason": "provider-state"}),
        )
        common = {
            "effect_id": "event-started-a",
            "request_fingerprint": "a" * 64,
            "evidence": evidence,
        }
        pairs = (
            (
                language.RuntimeEffectObservedSucceeded(**common),
                language.RuntimeEffectObservedAbsent(**common),
            ),
            (
                language.RuntimeEffectObservedFailed(**common, failure=failure),
                language.RuntimeEffectObserverUnsupported(
                    **common,
                    failure=failure,
                ),
            ),
            (
                language.RuntimeEffectObservedConflict(
                    **common,
                    failure=failure,
                    observations=_observations(),
                ),
                language.RuntimeEffectObservedIndeterminate(
                    **common,
                    failure=failure,
                    observations=_observations(),
                ),
            ),
        )
        for left, right in pairs:
            with self.subTest(left=type(left).__name__, right=type(right).__name__):
                self.assertEqual(
                    {
                        key: value
                        for key, value in left.descriptor().items()
                        if key != "kind"
                    },
                    {
                        key: value
                        for key, value in right.descriptor().items()
                        if key != "kind"
                    },
                )
                self.assertNotEqual(
                    language.runtime_effect_observation_fingerprint(left),
                    language.runtime_effect_observation_fingerprint(right),
                )

    def test_observation_fingerprint_commits_every_nested_coordinate(self) -> None:
        language = _language()
        values = self._values(language)
        succeeded = values[0]
        succeeded_fingerprint = language.runtime_effect_observation_fingerprint(
            succeeded
        )
        succeeded_mutations = {
            "effect_id": replace(succeeded, effect_id="event-started-b"),
            "request_fingerprint": replace(
                succeeded, request_fingerprint="b" * 64
            ),
            "evidence_key": replace(
                succeeded,
                evidence=language.RuntimeEffectObservationEvidence(
                    {"provider_state": "running", "exit_code": 0}
                ),
            ),
            "evidence_text": replace(
                succeeded,
                evidence=language.RuntimeEffectObservationEvidence(
                    {"container_state": "stopped", "exit_code": 0}
                ),
            ),
            "evidence_integer": replace(
                succeeded,
                evidence=language.RuntimeEffectObservationEvidence(
                    {"container_state": "running", "exit_code": 1}
                ),
            ),
            "observation_order": replace(
                succeeded,
                observations=tuple(reversed(succeeded.observations)),
            ),
            "observation_subject": replace(
                succeeded,
                observations=(
                    replace(succeeded.observations[0], subject_id="worker"),
                    *succeeded.observations[1:],
                ),
            ),
            "observation_socket": replace(
                succeeded,
                observations=(
                    replace(succeeded.observations[0], socket_name="admin"),
                    *succeeded.observations[1:],
                ),
            ),
            "observation_graph": replace(
                succeeded,
                observations=(
                    replace(succeeded.observations[0], graph_id="graph-next"),
                    *succeeded.observations[1:],
                ),
            ),
            "observation_protocol": replace(
                succeeded,
                observations=(
                    replace(
                        succeeded.observations[0],
                        protocol=Protocol.TCP,
                        address=LiteralEndpointMaterial("tcp://api:8000"),
                    ),
                    *succeeded.observations[1:],
                ),
            ),
            "observation_context": replace(
                succeeded,
                observations=(
                    replace(
                        succeeded.observations[0],
                        context=EndpointContext.HOST_LOCAL,
                    ),
                    *succeeded.observations[1:],
                ),
            ),
            "observation_address": replace(
                succeeded,
                observations=(
                    replace(
                        succeeded.observations[0],
                        address=LiteralEndpointMaterial("http://api:8001"),
                    ),
                    *succeeded.observations[1:],
                ),
            ),
        }
        for name, candidate in succeeded_mutations.items():
            with self.subTest(group="common", name=name):
                self.assertNotEqual(
                    language.runtime_effect_observation_fingerprint(candidate),
                    succeeded_fingerprint,
                )

        failed = values[1]
        failed_fingerprint = language.runtime_effect_observation_fingerprint(failed)
        failure = failed.failure
        assert failure is not None
        failure_mutations = {
            "failure_code": replace(
                failed, failure=replace(failure, code="observer.changed")
            ),
            "failure_message": replace(
                failed,
                failure=replace(failure, message="provider state changed"),
            ),
            "failure_details_key": replace(
                failed,
                failure=replace(
                    failure,
                    details=language.RuntimeEffectObservationEvidence(
                        {"cause": "identity-drift"}
                    ),
                ),
            ),
            "failure_details_value": replace(
                failed,
                failure=replace(
                    failure,
                    details=language.RuntimeEffectObservationEvidence(
                        {"reason": "state-drift"}
                    ),
                ),
            ),
        }
        for name, candidate in failure_mutations.items():
            with self.subTest(group="failure", name=name):
                self.assertNotEqual(
                    language.runtime_effect_observation_fingerprint(candidate),
                    failed_fingerprint,
                )

    def test_observation_fingerprint_rejects_every_hostile_outer_variant(self) -> None:
        language = _language()

        for value in self._values(language):
            with self.subTest(kind=value.descriptor()["kind"]):
                with self.assertRaises(RuntimeEffectContractError):
                    language.runtime_effect_observation_fingerprint(
                        _subclass_copy(value)
                    )

    def test_complete_observation_fingerprint_input_has_exact_byte_ceiling(self) -> None:
        language = _language()

        def padding(marker: str, size: int):
            values = {}
            remaining = size
            index = 0
            while remaining:
                chunk = min(remaining, 500)
                values[f"padding-{index}"] = marker * chunk
                remaining -= chunk
                index += 1
            return language.RuntimeEffectObservationEvidence(values)

        def value(message: str, padding_size: int):
            return language.RuntimeEffectObservedFailed(
                effect_id="event-started-a",
                request_fingerprint="a" * 64,
                evidence=padding("x", padding_size),
                failure=language.RuntimeEffectObservationFailure(
                    "observer.failed",
                    message,
                    padding("y", 3_500),
                ),
                observations=_observations(),
            )

        padding_size = 1
        for _ in range(4):
            candidate_size = len(
                rfc8785.dumps(value("msgmax7", padding_size).descriptor())
            )
            padding_size += OUTCOME_FINGERPRINT_MAX_BYTES - candidate_size
        maximum = value("msgmax7", padding_size)
        plus_one = value("msgmax88", padding_size)
        self.assertEqual(
            len(rfc8785.dumps(maximum.descriptor())),
            OUTCOME_FINGERPRINT_MAX_BYTES,
        )
        self.assertEqual(
            len(rfc8785.dumps(plus_one.descriptor())),
            OUTCOME_FINGERPRINT_MAX_BYTES + 1,
        )
        self.assertEqual(
            len(language.runtime_effect_observation_fingerprint(maximum)),
            64,
        )
        with self.assertRaisesRegex(RuntimeEffectContractError, "too large") as caught:
            language.runtime_effect_observation_fingerprint(plus_one)
        error = str(caught.exception) + repr(caught.exception)
        for candidate in ("x" * 64, "y" * 64, "msgmax88"):
            self.assertNotIn(candidate, error)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


if __name__ == "__main__":
    unittest.main()
