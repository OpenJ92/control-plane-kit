"""#1857 closed configuration-derived facts and deterministic trusted bindings."""
import base64
from dataclasses import FrozenInstanceError, replace
import unittest

from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.products import ProductReference
from tests.health_effect_preparation_fixture import forged_copy
from tests.health_receiver_trust_fixture import api, bindings, ByteDecoder, context, reference, selection


class HealthReceiverTrustTests(unittest.TestCase):
    def setUp(self):
        self.world, self.documents, self.artifacts = context(self)
        self.assertTrue(self.world[0].ready_for_execution)
        self.assertNotEqual(self.artifacts["transit"].content_digest,
            self.documents["transit"].product.runtime_contract.configuration_artifacts[0].content_digest)
        self.api = api(self)
        self.decoder = ByteDecoder(self.api)

    def decoded(self, family):
        return self.decoder.decode(selection(self.api, self.documents[family], self.artifacts[family], family))

    def refuse(self, call):
        with self.assertRaises(self.api.HealthReceiverTrustError) as caught:
            call()
        self.assertEqual(str(caught.exception), "health receiver trust is unavailable")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_distinct_family_values_are_immutable_and_hide_configured_material(self):
        gateway, workload = self.decoded("transit"), self.decoded("workload")
        self.assertEqual(gateway.gateway_node_id.value, "gateway")
        self.assertEqual(workload.target.node_id.value, "api")
        self.assertIs(gateway.purpose, DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT)
        self.assertIs(workload.purpose, DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
        for value in (gateway, workload):
            with self.assertRaises(FrozenInstanceError):
                value.issuer = "changed"
            self.assertFalse(hasattr(value, "__dict__"))
            self.assertFalse(hasattr(value, "descriptor"))
            for canary in ("BEGIN PUBLIC KEY", "workspace-a", "cpk-server", "health-transit", "health-workload"):
                self.assertNotIn(canary, repr(value))

    def test_family_role_issuer_and_public_identity_are_nominal_not_equality_only(self):
        for family in ("transit", "workload"):
            value = self.decoded(family)
            key = value.public_keys[0]
            for change in (
                dict(purpose=value.purpose.value),
                dict(purpose=DelegationKeyPurpose.GATEWAY_PROBE),
                dict(runtime_id=reference("node", "docker")),
                dict(audience="wrong-audience"),
                dict(issuer="https://example.invalid"), dict(issuer="sk-synthetic"),
                dict(public_keys=[key]),
                dict(public_keys=(forged_copy(key, algorithm="ed25519"),)),
                dict(public_keys=(forged_copy(key, fingerprint_sha256="0" * 64),)),
            ):
                with self.subTest(family=family, field=next(iter(change))):
                    self.refuse(lambda: replace(value, **change))
            self.assertEqual(replace(value, issuer="i" * 256).issuer, "i" * 256)
            self.refuse(lambda: replace(value, issuer="i" * 257))

    def test_distinct_key_bounds_and_duplicate_id_or_material_are_independent(self):
        # Valid Ed25519 SPKI public encodings, no private key or signing needed.
        keys = tuple(DelegationPublicKey("key-" + str(index), DelegationKeyAlgorithm.ED25519,
            "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(
                bytes.fromhex("302a300506032b6570032100") + bytes([index]) * 32).decode("ascii")
            + "\n-----END PUBLIC KEY-----\n") for index in range(1, 18))
        for family in ("transit", "workload"):
            value = self.decoded(family)
            self.assertEqual(len(replace(value, public_keys=keys[:16]).public_keys), 16)
            for candidate in ((), keys, (keys[0], replace(keys[1], key_id=keys[0].key_id)),
                              (keys[0], replace(keys[0], key_id="same-material"))):
                with self.subTest(family=family, count=len(candidate)):
                    self.refuse(lambda: replace(value, public_keys=candidate))

    def test_registry_refuses_duplicate_exact_product_purpose_and_wrong_product_selection(self):
        admitted = bindings(self.api, self.documents, self.decoder)
        self.api.HealthReceiverDecoders(admitted)
        self.api.HealthReceiverDecoders(())
        self.refuse(lambda: self.api.HealthReceiverDecoders((*admitted, admitted[0])))
        selected = selection(self.api, self.documents["transit"], self.artifacts["transit"])
        self.refuse(lambda: replace(selected,
            product_reference=ProductReference.from_document(self.documents["workload"])))
        with self.assertRaises(FrozenInstanceError):
            admitted[0].configuration_profile = "replacement-profile"
