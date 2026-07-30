import unittest

from control_plane_kit_core.operations import (
    canonical_operator_command_workflow_contract,
    canonical_operator_read_projection_set,
    operator_command_http_routes,
    operator_read_http_routes,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretCustodyGrant,
    SecretCustodyReceipt,
    SecretCustodyStatus,
    SecretProviderContractError,
    SecretProviderEndpointReference,
    SecretProviderEndpointReferenceCodec,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)


class SecretProviderContractTests(unittest.TestCase):
    def test_secret_use_intents_are_closed_and_secret_free(self) -> None:
        self.assertEqual(
            tuple(intent.value for intent in SecretUseIntent),
            (
                "application.control-token",
                "cloudflare.api-token",
                "cloudflare.tunnel-token",
                "docker.local-socket-access-marker",
                "docker.remote-tls.ca-certificate",
                "docker.remote-tls.client-certificate",
                "docker.remote-tls.client-key",
                "gateway.probe-signing-key",
                "oci.pull-credential",
                "postgres.password",
            ),
        )

    def test_provider_endpoint_reference_is_opaque_identity_not_url(self) -> None:
        reference = SecretProviderEndpointReference("workspace-secrets")
        codec = SecretProviderEndpointReferenceCodec()

        self.assertEqual(reference.reference_id, "workspace-secrets")
        self.assertEqual(codec.encode(reference), {"reference_id": "workspace-secrets"})
        self.assertEqual(codec.decode(codec.encode(reference)), reference)
        with self.assertRaises(SecretProviderContractError):
            SecretProviderEndpointReference("https://secrets.internal")
        with self.assertRaises(SecretProviderContractError):
            SecretProviderEndpointReference("token@secrets")
        with self.assertRaises(SecretProviderContractError):
            codec.decode({"reference_id": "workspace-secrets", "url": "https://invalid"})

    def test_resolution_grant_is_reference_only_and_exact(self) -> None:
        reference = SecretReference("secret://provider-a/postgres/password")
        grant = SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id="workspace-a",
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=reference,
            intent=SecretUseIntent.POSTGRES_PASSWORD,
            actor_subject="worker-a",
            correlation_id="secret-use-" + "d" * 64,
            intent_fingerprint="e" * 64,
            run_id="run-a",
            activity_id="activity-a",
            effect_id="effect-a",
        )

        self.assertTrue(
            grant.permits(reference, SecretUseIntent.POSTGRES_PASSWORD)
        )
        self.assertFalse(
            grant.permits(reference, SecretUseIntent.APPLICATION_CONTROL_TOKEN)
        )
        self.assertFalse(
            grant.permits(
                SecretReference("secret://provider-a/postgres/other"),
                SecretUseIntent.POSTGRES_PASSWORD,
            )
        )
        descriptor = grant.descriptor()
        self.assertEqual(descriptor["endpoint_reference"], "provider-a")
        self.assertEqual(
            descriptor["credential_reference"],
            "secret://bootstrap/provider-a-token",
        )
        self.assertNotIn("secret_value", descriptor)
        self.assertNotIn("plaintext", repr(descriptor).lower())

    def test_generated_secret_custody_is_reference_only_and_exact(self) -> None:
        reference = SecretReference(
            "secret://provider-a/generated/cloudflare/tunnel-a"
        )
        grant = SecretCustodyGrant(
            custody_id="scust_" + "a" * 64,
            workspace_id="workspace-a",
            provider_registration_id="sprov_" + "b" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=reference,
            intent=SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
            actor_subject="worker-a",
            correlation_id="secret-custody-" + "c" * 64,
            custody_fingerprint="d" * 64,
            run_id="run-a",
            activity_id="activity-a",
            effect_id="effect-a",
        )
        receipt = SecretCustodyReceipt(
            custody_id=grant.custody_id,
            provider_registration_id=grant.provider_registration_id,
            reference=reference,
            version_id="version-a",
            version_number=1,
        )

        self.assertTrue(
            grant.permits(
                reference,
                SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
            )
        )
        self.assertTrue(receipt.matches(grant))
        self.assertEqual(receipt.status, SecretCustodyStatus.ACTIVE)
        self.assertNotIn("secret_value", grant.descriptor())
        self.assertNotIn("plaintext", repr(grant.descriptor()).lower())
        self.assertNotIn("plaintext", repr(receipt.descriptor()).lower())

    def test_provider_permissions_are_independent(self) -> None:
        self.assertEqual(
            (
                PolicyScope.SECRET_PROVIDER_REGISTER.value,
                PolicyScope.SECRET_PROVIDER_READ.value,
                PolicyScope.SECRET_PROVIDER_USE.value,
                PolicyScope.SECRET_PROVIDER_REVOKE.value,
            ),
            (
                "secret-provider:register",
                "secret-provider:read",
                "secret-provider:use",
                "secret-provider:revoke",
            ),
        )

    def test_provider_and_reference_commands_and_reads_are_canonical(self) -> None:
        command_ids = {
            command.operation_id
            for command in canonical_operator_command_workflow_contract().commands
        }
        command_route_ids = {
            route.route_id for route in operator_command_http_routes()
        }
        read_ids = {
            projection.operation_id
            for projection in canonical_operator_read_projection_set().projections
        }
        read_route_ids = {route.route_id for route in operator_read_http_routes()}

        self.assertTrue(
            {
                "secret-provider.register",
                "secret-provider.revoke",
                "secret-reference.register",
                "secret-reference.revoke",
            }.issubset(command_ids)
        )
        self.assertTrue(
            {
                "command.secret-provider.register",
                "command.secret-provider.revoke",
                "command.secret-reference.register",
                "command.secret-reference.revoke",
            }.issubset(command_route_ids)
        )
        self.assertTrue(
            {
                "read.secret-providers",
                "read.secret-provider-detail",
                "read.secret-references",
                "read.secret-reference-detail",
            }.issubset(read_ids)
        )
        self.assertTrue(
            {
                "read.secret-providers",
                "read.secret-provider-detail",
                "read.secret-references",
                "read.secret-reference-detail",
            }.issubset(read_route_ids)
        )


if __name__ == "__main__":
    unittest.main()
