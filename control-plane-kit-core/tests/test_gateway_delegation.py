from __future__ import annotations

import unittest

from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    DelegatedGatewayProbeGrantCodec,
    DelegatedGatewayProbeVerificationCode,
    DelegatedGatewayProbeVerificationResult,
    GatewayHealthAccess,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
    GatewayProbeRequestCodec,
    GatewayProbeRequestDigest,
    GatewayDelegationContractError,
    canonical_gateway_health_disclosure_policy,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId


class GatewayDelegationLanguageTests(unittest.TestCase):
    def test_canonical_request_digest_binds_kind_target_and_http_path(self) -> None:
        request = GatewayProbeRequest(
            kind=GatewayProbeCommandKind.HTTP_STATUS,
            target_id=GatewayTargetId("hello.internal"),
            path="/health/ready",
        )

        descriptor = GatewayProbeRequestCodec().encode(request)
        digest = request.canonical_digest()

        self.assertEqual(
            descriptor,
            {
                "kind": "http-status",
                "target_id": "hello.internal",
                "path": "/health/ready",
            },
        )
        self.assertEqual(GatewayProbeRequestCodec().decode(descriptor), request)
        self.assertIsInstance(digest, GatewayProbeRequestDigest)
        self.assertEqual(len(digest.value), 64)
        self.assertNotEqual(
            digest,
            GatewayProbeRequest(
                kind=GatewayProbeCommandKind.HTTP_STATUS,
                target_id=GatewayTargetId("hello.internal"),
                path="/health/live",
            ).canonical_digest(),
        )
        self.assertNotEqual(
            digest,
            GatewayProbeRequest(
                kind=GatewayProbeCommandKind.HTTP_STATUS,
                target_id=GatewayTargetId("router.internal"),
                path="/health/ready",
            ).canonical_digest(),
        )
        self.assertNotEqual(
            digest,
            GatewayProbeRequest(
                kind=GatewayProbeCommandKind.POSTGRES_SELECT_ONE,
                target_id=GatewayTargetId("hello.internal"),
            ).canonical_digest(),
        )

    def test_probe_request_is_closed_and_rejects_path_substitution(self) -> None:
        with self.assertRaisesRegex(
            GatewayDelegationContractError,
            "does not accept path",
        ):
            GatewayProbeRequest(
                kind=GatewayProbeCommandKind.POSTGRES_SELECT_ONE,
                target_id=GatewayTargetId("postgres.postgres"),
                path="/health/ready",
            )

        with self.assertRaisesRegex(GatewayDelegationContractError, "path"):
            GatewayProbeRequest(
                kind=GatewayProbeCommandKind.HTTP_STATUS,
                target_id=GatewayTargetId("hello.internal"),
                path="https://attacker.invalid",
            )

        descriptor = {
            "kind": "http-status",
            "target_id": "hello.internal",
            "path": "/health/ready",
            "url": "https://attacker.invalid",
        }
        with self.assertRaisesRegex(GatewayDelegationContractError, "unknown"):
            GatewayProbeRequestCodec().decode(descriptor)

    def test_unsigned_grant_is_exact_bounded_and_secret_free(self) -> None:
        request = GatewayProbeRequest(
            kind=GatewayProbeCommandKind.POSTGRES_SELECT_ONE,
            target_id=GatewayTargetId("postgres.postgres"),
        )
        grant = _grant(request)

        descriptor = DelegatedGatewayProbeGrantCodec().encode(grant)

        self.assertEqual(
            descriptor,
            {
                "issuer": "cpk-server",
                "key_id": "gateway-signing-key-1",
                "audience": "runtime-island-a",
                "workspace_id": "workspace-a",
                "operation_id": "operation-a",
                "request_id": "request-a",
                "gateway_node_id": "gateway-a",
                "probe_kind": "postgres-select-one",
                "target_id": "postgres.postgres",
                "request_digest": request.canonical_digest().value,
                "issued_at": 1_784_000_000,
                "expires_at": 1_784_000_120,
                "jti": "probe-grant-a",
            },
        )
        self.assertEqual(DelegatedGatewayProbeGrantCodec().decode(descriptor), grant)
        self.assertNotIn("token", repr(descriptor).lower())
        self.assertNotIn("credential", repr(descriptor).lower())
        self.assertNotIn("signature", repr(descriptor).lower())

    def test_grant_rejects_invalid_lifetime_and_malformed_digest(self) -> None:
        request = GatewayProbeRequest(
            kind=GatewayProbeCommandKind.HTTP_STATUS,
            target_id=GatewayTargetId("hello.internal"),
            path="/health/ready",
        )

        with self.assertRaisesRegex(GatewayDelegationContractError, "expires"):
            _grant(request, issued_at=100, expires_at=100)
        with self.assertRaisesRegex(GatewayDelegationContractError, "lifetime"):
            _grant(request, issued_at=100, expires_at=401)
        with self.assertRaisesRegex(GatewayDelegationContractError, "digest"):
            GatewayProbeRequestDigest("not-a-canonical-digest")

    def test_grant_rejects_missing_or_secret_shaped_identity(self) -> None:
        request = GatewayProbeRequest(
            kind=GatewayProbeCommandKind.POSTGRES_SELECT_ONE,
            target_id=GatewayTargetId("postgres.postgres"),
        )
        base = _grant_kwargs(request)

        for field in (
            "issuer",
            "audience",
            "workspace_id",
            "operation_id",
            "request_id",
            "gateway_node_id",
            "jti",
        ):
            with self.subTest(field=field):
                with self.assertRaises(GatewayDelegationContractError):
                    DelegatedGatewayProbeGrant(**{**base, field: ""})

        with self.assertRaises(GatewayDelegationContractError):
            DelegatedGatewayProbeGrant(
                **{**base, "audience": "token=do-not-store"}
            )

    def test_grant_codec_rejects_unknown_and_secret_shaped_material(self) -> None:
        request = GatewayProbeRequest(
            kind=GatewayProbeCommandKind.POSTGRES_SELECT_ONE,
            target_id=GatewayTargetId("postgres.postgres"),
        )
        descriptor = DelegatedGatewayProbeGrantCodec().encode(_grant(request))

        with self.assertRaisesRegex(GatewayDelegationContractError, "unknown"):
            DelegatedGatewayProbeGrantCodec().decode(
                {**descriptor, "compact_token": "do-not-store"}
            )
        with self.assertRaises(GatewayDelegationContractError):
            DelegatedGatewayProbeGrantCodec().decode(
                {**descriptor, "issuer": "bearer do-not-store"}
            )

    def test_health_disclosure_is_explicit_and_minimal(self) -> None:
        policy = canonical_gateway_health_disclosure_policy()

        self.assertIs(policy.liveness, GatewayHealthAccess.PUBLIC_MINIMAL)
        self.assertIs(policy.readiness, GatewayHealthAccess.DELEGATED_CAPABILITY)
        self.assertFalse(policy.public_target_count)
        self.assertEqual(
            policy.descriptor(),
            {
                "liveness": "public-minimal",
                "readiness": "delegated-capability",
                "public_target_count": False,
            },
        )

    def test_verification_result_is_bounded_and_carries_no_token_material(self) -> None:
        accepted = DelegatedGatewayProbeVerificationResult.allow()
        rejected = DelegatedGatewayProbeVerificationResult.reject(
            DelegatedGatewayProbeVerificationCode.REQUEST_MISMATCH
        )

        self.assertEqual(accepted.descriptor(), {"accepted": True, "code": None})
        self.assertEqual(
            rejected.descriptor(),
            {"accepted": False, "code": "request-mismatch"},
        )
        with self.assertRaises(GatewayDelegationContractError):
            DelegatedGatewayProbeVerificationResult(True, "forged")  # type: ignore[arg-type]

    def test_core_contract_remains_provider_and_transport_neutral(self) -> None:
        names = " ".join(
            (
                DelegatedGatewayProbeGrant.__name__,
                GatewayProbeRequest.__name__,
                GatewayProbeRequestDigest.__name__,
                DelegatedGatewayProbeVerificationResult.__name__,
            )
        ).lower()

        for forbidden in (
            "cloudflare",
            "docker",
            "jwt",
            "authorization-header",
            "bearer",
            "signature-algorithm",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)


def _grant(
    request: GatewayProbeRequest,
    *,
    issued_at: int = 1_784_000_000,
    expires_at: int = 1_784_000_120,
) -> DelegatedGatewayProbeGrant:
    return DelegatedGatewayProbeGrant(
        **_grant_kwargs(
            request,
            issued_at=issued_at,
            expires_at=expires_at,
        )
    )


def _grant_kwargs(
    request: GatewayProbeRequest,
    *,
    issued_at: int = 1_784_000_000,
    expires_at: int = 1_784_000_120,
) -> dict[str, object]:
    return {
        "issuer": "cpk-server",
        "key_id": "gateway-signing-key-1",
        "audience": "runtime-island-a",
        "workspace_id": "workspace-a",
        "operation_id": "operation-a",
        "request_id": "request-a",
        "gateway_node_id": "gateway-a",
        "probe_kind": request.kind,
        "target_id": request.target_id,
        "request_digest": request.canonical_digest(),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "jti": "probe-grant-a",
    }


if __name__ == "__main__":
    unittest.main()
