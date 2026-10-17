"""Pure health signing correspondence; no issuer or provider authority."""

from dataclasses import replace
import unittest

from control_plane_kit_core import secrets
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretProviderContractError,
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionError,
    SecretResolutionGrant,
    SecretUseIntent,
    secret_delivery_from_descriptor,
)


class HealthSigningIntentTests(unittest.TestCase):
    def pairs(self):
        values = (
            (DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
             "WORKLOAD_NODE_HEALTH_READ_SIGNING_KEY",
             "workload.node-health-read-signing-key"),
            (DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
             "GATEWAY_NODE_HEALTH_READ_TRANSIT_SIGNING_KEY",
             "gateway.node-health-read-transit-signing-key"),
        )
        for _purpose, name, _wire in values:
            self.assertIn(name, SecretUseIntent.__members__, "health signing intent is missing")
        return tuple((purpose, SecretUseIntent[name], wire) for purpose, name, wire in values)

    def correspondence(self):
        function = getattr(secrets, "health_signing_intent_for", None)
        self.assertIsNotNone(function, "health signing correspondence is missing")
        return function

    def test_exact_health_purposes_have_distinct_literal_signing_intents(self):
        function = self.correspondence()
        pairs = self.pairs()
        self.assertIsNot(pairs[0][1], pairs[1][1])
        for purpose, intent, wire in pairs:
            with self.subTest(purpose=purpose):
                self.assertIs(function(purpose), intent)
                self.assertEqual(intent.value, wire)
                self.assertIs(SecretUseIntent(wire), intent)

    def test_only_nominal_health_purposes_select_an_intent_with_bounded_errors(self):
        function = self.correspondence()
        health = (
            DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
            DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
        )
        rejected = tuple(purpose for purpose in DelegationKeyPurpose if purpose not in health) + (
            health[0].value, health[1].value, None, 1,
            SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
            "UNTRUSTED-PURPOSE-CANARY",
        )
        for candidate in rejected:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SecretProviderContractError) as captured:
                    function(candidate)
                self.assertEqual(str(captured.exception), "health signing purpose is unsupported")
                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                self.assertNotIn("UNTRUSTED-PURPOSE-CANARY", repr(captured.exception))

    def test_reference_only_grants_preserve_exact_intent_and_descriptor(self):
        pairs = self.pairs()
        reference = SecretReference("secret://provider-a/health/key")
        base = SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id="workspace-a",
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference("secret://bootstrap/provider-token"),
            reference=reference,
            intent=SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
            actor_subject="worker-a", correlation_id="health-request-a",
            intent_fingerprint="d" * 64,
        )
        baseline = base.descriptor()
        for _purpose, intent, wire in pairs:
            with self.subTest(intent=wire):
                grant = replace(base, intent=intent)
                self.assertTrue(grant.permits(reference, intent))
                self.assertFalse(grant.permits(SecretReference("secret://provider-a/health/other"), intent))
                other = pairs[1][1] if intent is pairs[0][1] else pairs[0][1]
                for wrong_intent in (other, SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY,
                                     SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY):
                    self.assertFalse(grant.permits(reference, wrong_intent))
                self.assertFalse(grant.permits(reference, wire))
                self.assertEqual(grant.descriptor(), {**baseline, "intent": wire})
                self.assertNotIn("secret_value", grant.descriptor())
                with self.assertRaises(SecretProviderContractError):
                    replace(grant, intent=wire)

    def test_existing_delivery_descriptors_round_trip_with_strict_fields(self):
        reference = SecretReference("secret://provider-a/health/key")
        for _purpose, intent, wire in self.pairs():
            deliveries = (
                (SecretEnvironmentDelivery("SIGNING_KEY", reference, intent), {
                    "kind": "environment", "environment_name": "SIGNING_KEY",
                    "reference_id": reference.reference_id, "intent": wire,
                }),
                (SecretFileDelivery("/run/secrets/health-key", reference, intent), {
                    "kind": "file", "target_path": "/run/secrets/health-key",
                    "reference_id": reference.reference_id, "intent": wire,
                    "file_mode": "0400", "path_binding": None,
                }),
            )
            for delivery, descriptor in deliveries:
                with self.subTest(intent=wire, kind=descriptor["kind"]):
                    self.assertEqual(delivery.descriptor(), descriptor)
                    self.assertEqual(secret_delivery_from_descriptor(descriptor), delivery)
                    for malformed in ({**descriptor, "extra": True},
                                      {**descriptor, "intent": "unknown.health-signing-key"},
                                      {**descriptor, "intent": None}):
                        with self.assertRaises(SecretResolutionError):
                            secret_delivery_from_descriptor(malformed)
