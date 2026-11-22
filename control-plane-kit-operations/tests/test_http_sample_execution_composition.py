"""Pure composition/profile proofs; no admitted history or native effect proof."""
from dataclasses import replace
import importlib
import importlib.util
import unittest

from control_plane_kit_core.planning import NodeTarget, WaitForHealthy
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from control_plane_kit_operations.ordinary_verification_invocations import ordinary_verification_invocation_profile
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority, RemoteDockerTlsAuthority, LocalDockerSocketAuthority
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
    def require_support(self, support, registration):
        module = importlib.import_module("control_plane_kit_operations.runtime_adapter_registration")
        return module.require_http_sample_execution_support(support, registration)

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
        self.require_support(support, registration)
        for candidate in (None, replace(support)):
            with self.assertRaises(ValueError):
                self.require_support(candidate, registration)
        registration.adapter.http_sample_support = replace(support)
        with self.assertRaises(ValueError):
            self.require_support(support, registration)

    def test_no_registered_sender_cannot_acquire_sample_execution_support(self):
        api, support, _ = self.support()
        for registration in (None, RuntimeAdapterRegistration(_DeclaredAdapter(None)),
                RuntimeAdapterRegistration(_DeclaredAdapter(support),
                    invocation_profile="cpk.semantic-invocations.v1", ordinary_verification_invocations=True)):
            with self.assertRaises(ValueError):
                self.require_support(support, registration)

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


class ManagedVerificationInvocationCompositionTests(unittest.TestCase):
    def support(self):
        from tests.test_managed_verification_execution import ManagedVerificationRouteTests
        return ManagedVerificationRouteTests().inputs()[1]

    def test_managed_registration_requires_the_actual_shared_native_support_and_http_arm(self):
        from control_plane_kit_operations.runtime_adapter_registration import require_managed_verification_execution_support
        support = self.support()
        adapter = _DeclaredAdapter(support.http_samples)
        adapter.managed_verification_support = support
        registration = RuntimeAdapterRegistration(adapter, invocation_profile="cpk.semantic-invocations.v1",
            ordinary_verification_invocations=True, managed_verification_support=support)
        require_managed_verification_execution_support(support, registration)
        for candidate in (None, replace(support)):
            with self.assertRaises(ValueError):
                require_managed_verification_execution_support(candidate, registration)
        adapter.http_sample_support = replace(support.http_samples)
        with self.assertRaises(ValueError):
            require_managed_verification_execution_support(support, registration)

    def test_original_sql_profile_selects_three_roles_without_eager_password_reference(self):
        from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
        from tests.test_http_sample_compatibility import case
        material = product_material()
        intent = EffectAttemptIntentFixture.intent(self, products=(material,), process_delivery=False,
            operation=WaitForHealthy(NodeTarget("api")))
        authority = RegisteredRuntimeAuthority.from_authority(workspace_id="workspace-a",
            authority_ref=intent.authority_ref, runtime_kind=intent.runtime_kind,
            authority=LocalDockerSocketAuthority(),
            admitted_by="operator-a", admitted_at="2026-11-08T12:00:00Z")
        values, _, _ = case(self)
        plan = replace(values["plan"], plan_id=intent.source.plan_id,
            derivation_profile=PlanDerivationProfile.MANAGED_SQL_HTTP_BOOTSTRAP_V1)
        options = dict(original_event_id="event-a", runtime_authority=authority)
        old = ordinary_verification_invocation_profile(intent, **options)
        selected = ordinary_verification_invocation_profile(intent, **options, plan=plan)
        self.assertTrue(selected.managed_verification)
        self.assertFalse(selected.http_sample)
        self.assertEqual(selected.connection_uses, ())
        self.assertEqual(len(selected.checks), 1)
        self.assertTrue(selected.checks[0].postgres_readiness)
        self.assertIsNone(selected.checks[0].credential_use)
        self.assertEqual(selected.family_bound, 5)
        self.assertEqual(selected.secret_roles, ())  # Reached SQL roles are not eager metadata.
        self.assertNotEqual(selected.target_fingerprint, old.target_fingerprint)
        remote = RegisteredRuntimeAuthority.from_authority(workspace_id=authority.workspace_id,
            authority_ref=authority.authority_ref, runtime_kind=authority.runtime_kind,
            authority=RemoteDockerTlsAuthority("tcp://daemon.example.test:2376",
                *(SecretReference("secret://fixture/tls/" + role) for role in ("ca", "certificate", "key"))),
            admitted_by=authority.admitted_by, admitted_at=authority.admitted_at)
        with self.assertRaises(ValueError):
            ordinary_verification_invocation_profile(intent, original_event_id="event-a", runtime_authority=remote, plan=plan)
        check, = material.product.runtime_contract.verification.checks
        for checks in ((), (check, HttpCheck(check_id="http", provider_socket="http", path="/ready"))):
            changed = replace(material, product=replace(material.product,
                runtime_contract=replace(material.product.runtime_contract, verification=VerificationContract(checks))))
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                ordinary_verification_invocation_profile(replace(intent, products=(changed,)), **options, plan=plan)
