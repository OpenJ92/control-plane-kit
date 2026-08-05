from __future__ import annotations

import unittest

from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    IngressAuthorityReferenceCodec,
    NamedPublicIngress,
    NamedPublicIngressCodec,
    ObservedPublicEndpoint,
    PublicIngressContractError,
    PublicIngressExposure,
    PublicIngressLifecycle,
    PublicIngressObservation,
    PublicIngressObservationCodec,
    PublicIngressObservationStatus,
    PublicIngressRequest,
    PublicIngressTarget,
    PublicIngressTargetCodec,
)


class PublicIngressLanguageTests(unittest.TestCase):
    def test_named_public_ingress_is_provider_neutral_socket_exposure(self) -> None:
        ingress = NamedPublicIngress(
            ingress_id="gateway-001",
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            target=PublicIngressTarget("gateway", "control"),
            connector_node_id="cloudflared-gateway",
            hostname="cpk-gateway-001.openj92.dev",
            readiness_check_id="ready",
        )

        descriptor = NamedPublicIngressCodec().encode(ingress)

        self.assertEqual(
            descriptor,
            {
                "ingress_id": "gateway-001",
                "authority_ref": {"reference_id": "openj92-public-ingress"},
                "target": {
                    "node_id": "gateway",
                    "provider_socket": "control",
                },
                "connector_node_id": "cloudflared-gateway",
                "hostname": "cpk-gateway-001.openj92.dev",
                "readiness_check_id": "ready",
                "exposure": "https",
                "lifecycle": "ephemeral",
            },
        )
        self.assertEqual(NamedPublicIngressCodec().decode(descriptor), ingress)
        self.assertIs(PublicIngressRequest, NamedPublicIngress)
        self.assertNotIn("Cloudflare", type(ingress).__name__)
        self.assertNotIn("provider_kind", descriptor)
        self.assertNotIn("api_token", repr(descriptor).lower())
        self.assertNotIn("tunnel_token", repr(descriptor).lower())

    def test_ingress_authority_reference_is_secret_free(self) -> None:
        reference = IngressAuthorityReference("openj92-public-ingress")

        descriptor = IngressAuthorityReferenceCodec().encode(reference)

        self.assertEqual(descriptor, {"reference_id": "openj92-public-ingress"})
        self.assertEqual(IngressAuthorityReferenceCodec().decode(descriptor), reference)

        invalid = (
            "",
            "OpenJ92",
            "openj92/public",
            "token=do-not-store",
            "password=do-not-store",
            "secret=do-not-store",
            "begin-private-key",
            "cf_tunnel_do_not_store",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PublicIngressContractError):
                    IngressAuthorityReference(value)

    def test_public_ingress_target_preserves_socket_boundary(self) -> None:
        target = PublicIngressTarget("gateway", "control")

        descriptor = PublicIngressTargetCodec().encode(target)

        self.assertEqual(
            descriptor,
            {"node_id": "gateway", "provider_socket": "control"},
        )
        self.assertEqual(PublicIngressTargetCodec().decode(descriptor), target)

        with self.assertRaises(PublicIngressContractError):
            PublicIngressTarget("gateway", "tcp://postgres:5432")

    def test_named_public_ingress_codec_fails_closed_on_provider_fields(self) -> None:
        descriptor = NamedPublicIngress(
            ingress_id="gateway-001",
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            target=PublicIngressTarget("gateway", "control"),
            connector_node_id="cloudflared-gateway",
            hostname="cpk-gateway-001.openj92.dev",
            readiness_check_id="ready",
            exposure=PublicIngressExposure.HTTPS,
            lifecycle=PublicIngressLifecycle.RETAINED,
        ).descriptor()

        invalid_descriptors = (
            {**descriptor, "provider_kind": "cloudflare"},
            {**descriptor, "tunnel_token": "cf_tunnel_do_not_store"},
            {**descriptor, "api_token": "token=do-not-store"},
            {**descriptor, "exposure": "tcp"},
            {**descriptor, "hostname": "gateway-001.cpk.openj92.dev/token=bad"},
        )

        for value in invalid_descriptors:
            with self.subTest(value=value):
                with self.assertRaises(PublicIngressContractError):
                    NamedPublicIngressCodec().decode(value)

    def test_public_ingress_observation_is_bounded_endpoint_evidence(self) -> None:
        observation = PublicIngressObservation(
            ingress_id="gateway-001",
            hostname="cpk-gateway-001.openj92.dev",
            url="https://cpk-gateway-001.openj92.dev/health/ready",
            target=PublicIngressTarget("gateway", "control"),
            observed_at="2026-07-27T22:50:00Z",
            status=PublicIngressObservationStatus.READY,
            evidence={"http_status": 200, "body_size": 31},
        )

        descriptor = PublicIngressObservationCodec().encode(observation)

        self.assertEqual(descriptor["status"], "ready")
        self.assertEqual(
            descriptor["target"],
            {"node_id": "gateway", "provider_socket": "control"},
        )
        self.assertEqual(
            PublicIngressObservationCodec().decode(descriptor).descriptor(),
            descriptor,
        )
        self.assertIs(ObservedPublicEndpoint, PublicIngressObservation)
        self.assertNotIn("token", repr(descriptor).lower())

    def test_public_ingress_observation_rejects_secret_shaped_evidence(self) -> None:
        with self.assertRaises(PublicIngressContractError):
            PublicIngressObservation(
                ingress_id="gateway-001",
                hostname="cpk-gateway-001.openj92.dev",
                url="https://cpk-gateway-001.openj92.dev/health/ready",
                target=PublicIngressTarget("gateway", "control"),
                observed_at="2026-07-27T22:50:00Z",
                status=PublicIngressObservationStatus.READY,
                evidence={"tunnel_token": "cf_tunnel_do_not_store"},
            )


if __name__ == "__main__":
    unittest.main()
