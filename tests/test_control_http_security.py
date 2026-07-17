from __future__ import annotations

from dataclasses import dataclass
import unittest

from control_plane_kit.adapters.control_http import (
    ControlAddressPolicy,
    ControlAddressSource,
    ControlEndpointObservation,
    ControlSecurityCode,
    ControlSecurityError,
    CredentialReference,
    RuntimeEndpointProvenance,
    SecretValue,
    authorize_control_endpoint,
)
from control_plane_kit.effects import EndpointMaterial, LiteralEndpointMaterial, SecretEndpointMaterial
from control_plane_kit.types import EndpointScope, Protocol


@dataclass
class Resolver:
    value: str = "synthetic-control-token"

    def resolve(self, reference: CredentialReference) -> SecretValue:
        self.reference = reference
        return SecretValue(self.value)


class ControlHttpSecurityTests(unittest.TestCase):
    def test_docker_private_endpoint_requires_observed_network_and_credential(self) -> None:
        resolver = Resolver()
        authorized = authorize_control_endpoint(
            self._observation("http://runtime-api:8000", network="deployment-a"),
            ControlAddressPolicy(docker_networks=frozenset({"deployment-a"})),
            CredentialReference("control-token-a"),
            resolver,
        )

        self.assertEqual(
            authorized.request_url("/__deploy/status"),
            "http://runtime-api:8000/__deploy/status",
        )
        headers = authorized.request_headers(request_id="request-a", idempotency_key="attempt-a")
        self.assertEqual(headers["Authorization"], "Bearer synthetic-control-token")
        self.assertEqual(resolver.reference, CredentialReference("control-token-a"))
        self.assertNotIn("runtime-api", repr(authorized))
        self.assertNotIn("synthetic-control-token", repr(authorized))
        self.assertNotIn("synthetic-control-token", str(authorized.descriptor()))

    def test_host_local_and_public_authority_are_separate_explicit_policies(self) -> None:
        local = ControlEndpointObservation(
            "api",
            EndpointMaterial("internal", Protocol.HTTP, EndpointScope.LOCAL, LiteralEndpointMaterial("http://127.0.0.1:8000")),
            RuntimeEndpointProvenance(ControlAddressSource.HOST_LOCAL, "local"),
        )
        public = ControlEndpointObservation(
            "api",
            EndpointMaterial("public", Protocol.HTTP, EndpointScope.PUBLIC, LiteralEndpointMaterial("https://control.example.test")),
            RuntimeEndpointProvenance(ControlAddressSource.EXPLICIT_PUBLIC, "cloud"),
        )

        authorize_control_endpoint(local, ControlAddressPolicy(allow_host_local=True), CredentialReference("token"), Resolver())
        authorize_control_endpoint(public, ControlAddressPolicy(public_hosts=frozenset({"control.example.test"})), CredentialReference("token"), Resolver())

        with self.assertRaises(ControlSecurityError):
            authorize_control_endpoint(local, ControlAddressPolicy(), CredentialReference("token"), Resolver())
        with self.assertRaises(ControlSecurityError):
            authorize_control_endpoint(public, ControlAddressPolicy(public_hosts=frozenset({"other.example.test"})), CredentialReference("token"), Resolver())

    def test_unsafe_authorities_fail_without_echoing_address_or_secret(self) -> None:
        unsafe = (
            "ftp://runtime-api:8000",
            "http://user:password@runtime-api:8000",
            "http://runtime-api:8000/path",
            "http://runtime-api:8000?next=http://evil",
            "http://runtime-api:8000#fragment",
            "http://runtime-api:not-a-port",
        )
        for value in unsafe:
            with self.subTest(value=value):
                with self.assertRaises(ControlSecurityError) as raised:
                    authorize_control_endpoint(
                        self._observation(value, network="deployment-a"),
                        ControlAddressPolicy(docker_networks=frozenset({"deployment-a"})),
                        CredentialReference("token"),
                        Resolver("never-disclose"),
                    )
                self.assertNotIn(value, str(raised.exception))
                self.assertNotIn("never-disclose", str(raised.exception))

    def test_arbitrary_secret_endpoint_and_missing_token_fail_closed(self) -> None:
        observation = ControlEndpointObservation(
            "api",
            EndpointMaterial("internal", Protocol.HTTP, EndpointScope.PRIVATE, SecretEndpointMaterial("secret://authority")),
            RuntimeEndpointProvenance(ControlAddressSource.DOCKER_PRIVATE, "docker", "deployment-a"),
        )
        with self.assertRaises(ControlSecurityError) as raised:
            authorize_control_endpoint(
                observation,
                ControlAddressPolicy(docker_networks=frozenset({"deployment-a"})),
                CredentialReference("token"),
                Resolver(),
            )
        self.assertIs(raised.exception.code, ControlSecurityCode.INVALID_OBSERVATION)

        with self.assertRaises(ControlSecurityError) as missing:
            CredentialReference("")
        self.assertIs(missing.exception.code, ControlSecurityCode.MISSING_CREDENTIAL)

    def test_redirect_like_route_and_invalid_resolver_result_fail_closed(self) -> None:
        authorized = authorize_control_endpoint(
            self._observation("http://runtime-api:8000", network="deployment-a"),
            ControlAddressPolicy(docker_networks=frozenset({"deployment-a"})),
            CredentialReference("token"),
            Resolver(),
        )
        with self.assertRaises(ControlSecurityError):
            authorized.request_url("//other.example.test/path")
        with self.assertRaises(ControlSecurityError):
            authorized.request_url("/__deploy/status?next=http://other.example.test")
        with self.assertRaises(ControlSecurityError):
            authorized.request_headers(
                request_id="request-a\r\nForwarded: injected",
                idempotency_key="attempt-a",
            )

        class InvalidResolver:
            def resolve(self, reference):
                return "plaintext"

        with self.assertRaises(ControlSecurityError) as raised:
            authorize_control_endpoint(
                self._observation("http://runtime-api:8000", network="deployment-a"),
                ControlAddressPolicy(docker_networks=frozenset({"deployment-a"})),
                CredentialReference("token"),
                InvalidResolver(),
            )
        self.assertIs(raised.exception.code, ControlSecurityCode.UNRESOLVED_CREDENTIAL)

    @staticmethod
    def _observation(address: str, *, network: str) -> ControlEndpointObservation:
        return ControlEndpointObservation(
            "api",
            EndpointMaterial("internal", Protocol.HTTP, EndpointScope.PRIVATE, LiteralEndpointMaterial(address)),
            RuntimeEndpointProvenance(ControlAddressSource.DOCKER_PRIVATE, "docker", network),
        )


if __name__ == "__main__":
    unittest.main()
