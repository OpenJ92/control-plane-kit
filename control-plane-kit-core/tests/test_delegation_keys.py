from __future__ import annotations

import unittest

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.policies import PolicyScope


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
-----END PUBLIC KEY-----
"""


class DelegationPublicKeyTests(unittest.TestCase):
    def test_public_key_identity_is_canonical_and_secret_free(self) -> None:
        key = DelegationPublicKey(
            key_id="gateway-a",
            algorithm=DelegationKeyAlgorithm.ED25519,
            public_key_pem=PUBLIC_KEY_A,
        )

        self.assertEqual(key.key_id, "gateway-a")
        self.assertEqual(key.algorithm, DelegationKeyAlgorithm.ED25519)
        self.assertRegex(key.fingerprint_sha256, r"^[0-9a-f]{64}$")
        self.assertNotIn("public_key_pem", key.descriptor())
        self.assertEqual(
            key.descriptor()["fingerprint_sha256"],
            key.fingerprint_sha256,
        )

    def test_private_material_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DelegationPublicKey(
                key_id="gateway-a",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=(
                    "-----BEGIN PRIVATE KEY-----\nsecret\n"
                    "-----END PRIVATE KEY-----\n"
                ),
            )

    def test_purpose_is_provider_neutral(self) -> None:
        self.assertEqual(
            tuple(purpose.value for purpose in DelegationKeyPurpose),
            (
                "gateway-probe",
                "workload-node-control",
                "workload-node-control-surface-read",
            ),
        )
        self.assertEqual(
            DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ.value,
            "workload-node-control-surface-read",
        )
        with self.assertRaises(ValueError):
            DelegationKeyPurpose("private-unknown-purpose")

    def test_generation_authority_is_distinct_from_registration_and_use(self) -> None:
        self.assertEqual(
            PolicyScope.DELEGATION_KEY_GENERATE.value,
            "delegation-key:generate",
        )
        self.assertNotEqual(
            PolicyScope.DELEGATION_KEY_GENERATE,
            PolicyScope.DELEGATION_KEY_REGISTER,
        )
        self.assertNotEqual(
            PolicyScope.DELEGATION_KEY_GENERATE,
            PolicyScope.DELEGATION_KEY_USE,
        )


if __name__ == "__main__":
    unittest.main()
