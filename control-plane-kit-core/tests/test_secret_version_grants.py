"""#1975 Gate 2: consumer versions and operational authority are disjoint roles."""
from dataclasses import fields, replace
from hashlib import sha256
import importlib
import unittest

import rfc8785

from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationRequest, runtime_effect_intent_for_request,
    runtime_effect_intent_fingerprint, runtime_effect_request_for_intent,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectContractError
from control_plane_kit_core.secret_installation import secret_material_selection_fingerprint
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery, SecretFileDelivery, SecretProviderEndpointReference,
    SecretReference, SecretResolutionGrant, SecretUseIntent, secret_delivery_sort_key,
)
from tests.test_runtime_effect_observation import _grant_product, _request


def grant_language():
    name = "control_plane_kit_core.secret_version_grants"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        raise AssertionError("missing Gate 2 version-bound grant language") from error
    for symbol in ("SecretVersionResolutionGrant", "SecretResolutionGrantCodec"):
        if not hasattr(module, symbol):
            raise AssertionError(f"missing Gate 2 capability: {symbol}")
    return module


def selected_request(*, shared_probe=False, tls_delivery=False):
    material = _grant_product()
    contract = material.product.runtime_contract
    delivery = contract.secret_deliveries[0]
    if shared_probe:
        delivery = replace(delivery,
            reference=contract.verification.checks[0].authentication.password_reference,
            intent=SecretUseIntent.POSTGRES_PASSWORD)
    if tls_delivery:
        delivery = replace(delivery, intent=SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY)
    material = replace(material, pull_authority=None,
        product=replace(material.product, runtime_contract=replace(contract,
            secret_deliveries=(delivery,))),
        secret_selections=(SecretVersionSelection(delivery.reference, delivery.intent, "version-1"),))
    return replace(_request(products=(material,)), authority_ref=None)


def operational_grant(request):
    selection = request.products[0].secret_selections[0]
    return SecretResolutionGrant(
        authorization_id="suse_" + "a" * 64, workspace_id=request.source.workspace_id,
        reference_registration_id="sref_" + "b" * 64,
        provider_registration_id="sprov_" + "c" * 64,
        endpoint_reference=SecretProviderEndpointReference("provider-a"),
        credential_reference=SecretReference("secret://bootstrap/provider-a-token"),
        reference=selection.reference, intent=selection.intent,
        actor_subject="worker-a", correlation_id="operational-use-a",
        intent_fingerprint="d" * 64, operation_id=request.source.request_id,
        run_id=request.source.run_id.value, activity_id=request.activity_id.value,
        effect_id=request.effect_id)


def version_grant(request):
    operational = operational_grant(request)
    values = {item.name: getattr(operational, item.name) for item in fields(operational)
        if item.name not in ("reference", "intent", "probe_id")}
    return grant_language().SecretVersionResolutionGrant(**{
        **values, "authorization_id": "suse_" + "e" * 64,
        "correlation_id": "consumer-version-use-a",
        "selection": request.products[0].secret_selections[0],
        "request_fingerprint": runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)),
        "selection_fingerprint": secret_material_selection_fingerprint(request),
    })


class SecretVersionGrantTests(unittest.TestCase):
    def test_operational_grant_subclass_cannot_cross_exact_runtime_boundary(self):
        request = selected_request(shared_probe=True)
        grant = operational_grant(request)
        derived_type = type("DerivedOperationalGrant", (SecretResolutionGrant,), {})
        derived = derived_type(**{item.name: getattr(grant, item.name) for item in fields(grant)})
        with self.assertRaises(RuntimeEffectContractError):
            replace(request, secret_resolution_grants=(derived,))

    def test_operational_grant_cannot_authorize_only_a_consumer_delivery(self):
        request = selected_request()
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeEffectObservationRequest(replace(request,
                secret_resolution_grants=(operational_grant(request),)))

    def test_closed_codec_roundtrips_both_roles_and_rejects_extra_or_missing_fields(self):
        request = selected_request()
        codec = grant_language().SecretResolutionGrantCodec()
        operational, consumer = operational_grant(request), version_grant(request)
        self.assertIsNot(type(consumer), type(operational))
        self.assertFalse(isinstance(consumer, SecretResolutionGrant))
        profiles = set()
        for grant in (operational, consumer):
            document = codec.encode(grant)
            profiles.add(document["profile"])
            self.assertEqual(codec.decode(document), grant)
            encoded = codec.encode_canonical_bytes(grant)
            self.assertEqual(codec.decode_canonical_bytes(encoded), grant)
            malformed = [{**document, "value": "private-canary"},
                {**document, "profile": "unknown"}]
            malformed.extend({key: value for key, value in document.items() if key != missing}
                for missing in document)
            for bad in malformed:
                with self.subTest(role=type(grant).__name__, fields=tuple(bad)), self.assertRaises(ValueError) as caught:
                    codec.decode(bad)
                self.assertNotIn("private-canary", str(caught.exception))
            with self.assertRaises(ValueError):
                codec.decode_canonical_bytes(b" " + encoded)
        self.assertEqual(len(profiles), 2)
        with self.assertRaises(ValueError):
            codec.decode_canonical_bytes(b" " * 1_048_577)

    def test_required_consumer_identities_and_exact_nominal_selection_are_closed(self):
        grant = version_grant(selected_request())
        self.assertNotIn("probe_id", {item.name for item in fields(grant)})
        self.assertIsNone(grant.session_id)
        for key in ("operation_id", "run_id", "activity_id", "effect_id",
                    "request_fingerprint", "selection_fingerprint", "selection"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(grant, **{key: None})
        forged_type = type("DerivedSelection", (SecretVersionSelection,), {})
        with self.assertRaises(ValueError):
            replace(grant, selection=forged_type(grant.selection.reference,
                grant.selection.intent, grant.selection.version_id))

    def test_same_pair_can_have_distinct_roles_but_never_duplicate_a_role(self):
        request = selected_request(shared_probe=True)
        operational, consumer = operational_grant(request), version_grant(request)
        bound = replace(request, secret_resolution_grants=(consumer, operational))
        self.assertEqual(set(bound.secret_resolution_grants), {consumer, operational})
        self.assertEqual(RuntimeEffectObservationRequest(bound).intent,
            runtime_effect_intent_for_request(request))
        for grants in ((consumer, consumer), (operational, operational),
                       (consumer, replace(consumer, authorization_id="another"))):
            with self.subTest(count=len(grants)), self.assertRaises(RuntimeEffectContractError):
                replace(request, secret_resolution_grants=grants)

    def test_consumer_grant_binds_exact_effect_request_and_complete_material(self):
        request = selected_request()
        grant = version_grant(request)
        for changes in ({"workspace_id": "workspace-b"}, {"operation_id": "request-b"},
                        {"run_id": "run-b"}, {"activity_id": "activity-b"},
                        {"effect_id": "event-b"}, {"request_fingerprint": "1" * 64},
                        {"selection_fingerprint": "2" * 64},
                        {"selection": replace(grant.selection, version_id="version-2")},
                        {"selection": replace(grant.selection, reference=SecretReference("secret://other/key"))}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(RuntimeEffectContractError):
                replace(request, secret_resolution_grants=(replace(grant, **changes),))
        material = request.products[0]
        delivery = material.product.runtime_contract.secret_deliveries[0]
        changed = replace(material, product=replace(material.product,
            runtime_contract=replace(material.product.runtime_contract,
                secret_deliveries=(delivery, SecretFileDelivery("/run/secrets/second",
                    delivery.reference, delivery.intent)))))
        with self.assertRaises(RuntimeEffectContractError):
            replace(request, products=(changed,), secret_resolution_grants=(grant,))

    def test_existing_fingerprint_domains_and_original_intent_survive_transient_grants(self):
        request = selected_request()
        intent = runtime_effect_intent_for_request(request)
        material = request.products[0]
        grant = version_grant(request)
        self.assertEqual(grant.request_fingerprint, sha256(
            b"control-plane-kit.runtime-effect-intent.v1\0" + rfc8785.dumps(intent.descriptor())).hexdigest())
        descriptor = dict(workspace_id=request.source.workspace_id, runtime_id=material.runtime_id,
            node_id=material.node_id,
            deliveries=[value.descriptor() for value in sorted(material.product.runtime_contract.secret_deliveries,
                key=secret_delivery_sort_key) if type(value) in (SecretEnvironmentDelivery, SecretFileDelivery)],
            selections=[value.descriptor() for value in material.secret_selections])
        self.assertEqual(grant.selection_fingerprint, sha256(
            b"control-plane-kit.secret-material-selection.v1\0" + rfc8785.dumps(descriptor)).hexdigest())
        bound = runtime_effect_request_for_intent(intent, effect_id=request.effect_id,
            secret_resolution_grants=(grant,))
        self.assertEqual(runtime_effect_intent_for_request(bound), intent)
        self.assertEqual(secret_material_selection_fingerprint(bound), grant.selection_fingerprint)
        refreshed = replace(grant, authorization_id="refreshed", correlation_id="refreshed")
        self.assertEqual(runtime_effect_intent_for_request(replace(bound,
            secret_resolution_grants=(refreshed,))), intent)

    def test_workload_tls_intent_does_not_become_transport_connection_authority(self):
        request = selected_request(tls_delivery=True)
        consumer = version_grant(request)
        observation = RuntimeEffectObservationRequest(replace(request,
            secret_resolution_grants=(consumer,)))
        self.assertIsNone(observation.connection_admission)
        self.assertEqual(observation.intent, runtime_effect_intent_for_request(request))
