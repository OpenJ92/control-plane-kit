"""Prove one exact signed gateway grant survives the immutable image boundary."""

from __future__ import annotations

import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt

from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId
from control_plane_kit_servers_cpk_local_gateway import (
    Ed25519GatewayProbeVerifier,
    GatewayProbeReplayCache,
)


WORKSPACE_ID = "workspace-secret-cloudflare-1785466744-66810"
GATEWAY_NODE_ID = "gateway"
ISSUER = "urn:control-plane-kit:source-live"
KEY_ID = "source-live-gateway-key"
AUDIENCE = f"gateway:{WORKSPACE_ID}:{GATEWAY_NODE_ID}"
NOW = 1_785_466_744


def main() -> int:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    request = GatewayProbeRequest(
        GatewayProbeCommandKind.HTTP_STATUS,
        GatewayTargetId("hello.internal"),
        "/",
    )
    grant = DelegatedGatewayProbeGrant(
        issuer=ISSUER,
        key_id=KEY_ID,
        audience=AUDIENCE,
        workspace_id=WORKSPACE_ID,
        operation_id="probe-operation",
        request_id="probe-request",
        gateway_node_id=GATEWAY_NODE_ID,
        probe_kind=request.kind,
        target_id=request.target_id,
        request_digest=request.canonical_digest(),
        issued_at=NOW - 1,
        expires_at=NOW + 60,
        jti="source-live-structural-grant",
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(
        {
            "iss": grant.issuer,
            "aud": grant.audience,
            "iat": grant.issued_at,
            "exp": grant.expires_at,
            "jti": grant.jti,
            "gateway_probe": grant.descriptor(),
        },
        private_pem,
        algorithm="EdDSA",
        headers={
            "kid": grant.key_id,
            "typ": "CPK-GATEWAY-PROBE+JWT",
        },
    )
    verifier = Ed25519GatewayProbeVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        gateway_node_id=GATEWAY_NODE_ID,
        public_keys={KEY_ID: public_key},
        replay_cache=GatewayProbeReplayCache(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    body = json.dumps(
        request.descriptor(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    if verifier.verify(f"CPK-Gateway {token}", body) != request:
        raise RuntimeError("gateway structural grant did not round trip")
    print("cpk-local-gateway structural grant check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
