import unittest

from control_plane_kit_core.operations import (
    canonical_operator_command_workflow_contract,
    canonical_operator_read_projection_set,
    operator_command_http_routes,
    operator_read_http_routes,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderContractError,
    SecretProviderEndpointReference,
    SecretProviderEndpointReferenceCodec,
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
