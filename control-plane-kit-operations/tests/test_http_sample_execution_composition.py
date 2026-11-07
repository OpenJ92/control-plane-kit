"""Pure composition/profile proofs; no admitted history or native effect proof."""
from dataclasses import replace
import importlib
import importlib.util
import unittest

from control_plane_kit_core.planning import NodeTarget, WaitForHealthy
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from control_plane_kit_operations.ordinary_verification_invocations import ordinary_verification_invocation_profile
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority, RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
from tests.effect_attempt_intent_fixture import EffectAttemptIntentFixture, product_material
from tests.test_http_sample_compatibility import case, ROUTE_SLOT

MODULE = "control_plane_kit_operations.http_sample_execution"


class _DeclaredAdapter:
    """Pure descriptor witness; never a qualified native interpreter."""
    def __init__(self, support):
        self.http_sample_support = support

    def execute_with_authority(self, *args, **kwargs):
        raise AssertionError("composition test must not execute an adapter")


class _UnusedDecoder:
    def decode(self, artifact):
        raise AssertionError("descriptor proof does not acquire a route")


class HttpSampleExecutionCompositionTests(unittest.TestCase):
    def support(self):
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "typed sample execution binding is missing")
        api = importlib.import_module(MODULE)
        from control_plane_kit_operations.http_sample_compatibility import GatewayHttpSampleBinding
        values, product, slot = case(self)
        support = api.HttpSampleExecutionSupport(GatewayHttpSampleBinding(
            product, slot, _UnusedDecoder(), route_slot=ROUTE_SLOT), "management")
        return api, support, values["plan"]

    def test_actual_registered_consumer_must_share_the_exact_support_object(self):
        api, support, _ = self.support()
        registration = RuntimeAdapterRegistration(_DeclaredAdapter(support),
            invocation_profile="cpk.semantic-invocations.v1", ordinary_verification_invocations=True,
            http_sample_support=support)
        api.require_http_sample_execution_support(support, registration)
        for candidate in (None, replace(support)):
            with self.assertRaises(ValueError):
                api.require_http_sample_execution_support(candidate, registration)
        registration.adapter.http_sample_support = replace(support)
        with self.assertRaises(ValueError):
            api.require_http_sample_execution_support(support, registration)

    def test_no_registered_sender_cannot_acquire_sample_execution_support(self):
        api, support, _ = self.support()
        for registration in (None, RuntimeAdapterRegistration(_DeclaredAdapter(None)),
                RuntimeAdapterRegistration(_DeclaredAdapter(support),
                    invocation_profile="cpk.semantic-invocations.v1", ordinary_verification_invocations=True)):
            with self.assertRaises(ValueError):
                api.require_http_sample_execution_support(support, registration)

    def test_only_original_sample_profile_omits_unused_private_tls_roles(self):
        _, _, plan = self.support()
        material = product_material()
        material = replace(material, product=replace(material.product,
            runtime_contract=replace(material.product.runtime_contract,
                verification=VerificationContract((HttpCheck(check_id="ready", provider_socket="http", path="/ready"),)))))
        intent = EffectAttemptIntentFixture.intent(self, products=(material,), process_delivery=False,
            operation=WaitForHealthy(NodeTarget("api")))
        authority = RegisteredRuntimeAuthority.from_authority(workspace_id="workspace-a",
            authority_ref=intent.authority_ref, runtime_kind=intent.runtime_kind,
            authority=RemoteDockerTlsAuthority("tcp://daemon.example.test:2376",
                *(SecretReference("secret://fixture/tls/" + role) for role in ("ca", "certificate", "key"))),
            admitted_by="operator-a", admitted_at="2026-11-07T12:00:00Z")
        options = dict(original_event_id="event-a", runtime_authority=authority)
        ordinary = ordinary_verification_invocation_profile(intent, **options)
        sample = ordinary_verification_invocation_profile(intent, **options,
            plan=replace(plan, plan_id=intent.source.plan_id))
        self.assertEqual(len(ordinary.connection_uses), 3)
        self.assertEqual(sample.connection_uses, ())
        self.assertTrue(sample.http_sample)
        self.assertEqual(sample.family_bound, 4)  # acquisition, two uses, one check
        self.assertNotEqual(sample.target_fingerprint, ordinary.target_fingerprint)
        self.assertEqual(ordinary_verification_invocation_profile(intent, **options), ordinary)
