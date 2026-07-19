"""FastAPI boundary and deployable block for durable HTTP idempotency."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response

from control_plane_kit.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
)
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.idempotency import IdempotencyGatewayPolicy
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers.http_messages import HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


IdempotencyRequestExecutor = Callable[[HttpRequest, str, str, str], HttpResponse]


def http_idempotency_gateway_block(
    block_id: str = "http-idempotency-gateway",
    *,
    display_name: str = "HTTP Idempotency Gateway",
    image: str = "control-plane-kit:local",
    policy: IdempotencyGatewayPolicy,
    identity_secret_reference: str = "secret://http-idempotency-gateway/identity-attestation",
) -> ProxyBlock:
    policy_json = json.dumps(policy.descriptor(), sort_keys=True, separators=(",", ":"))
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_IDEMPOTENCY_GATEWAY,
            maturity=ProductMaturity.TEST_ONLY,
            display_name=display_name,
            health_path="/health",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
        ),
        DockerImageImplementation(
            image=image,
            command=("python", "-m", "control_plane_kit.idempotency_gateway.main", policy_json),
            ports={"internal": 8080},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_IDEMPOTENCY_IDENTITY_TOKEN",
                    SecretReference(identity_secret_reference),
                ),
            ),
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("target", Protocol.HTTP, ("IDEMPOTENCY_TARGET_URL",)),
                RequirementSocket("database", Protocol.POSTGRES, ("IDEMPOTENCY_DATABASE_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def create_idempotency_gateway_app(
    execute: IdempotencyRequestExecutor,
    policy: IdempotencyGatewayPolicy,
    *,
    identity_attestation_token: str,
) -> FastAPI:
    if not isinstance(policy, IdempotencyGatewayPolicy):
        raise TypeError("idempotency gateway app requires a typed policy")
    if not identity_attestation_token:
        raise ValueError("idempotency identity attestation token is required")
    app = FastAPI(title="control-plane-kit idempotency gateway")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.api_route("/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE"])
    async def forward(path: str, request: Request) -> Response:
        supplied_attestation = request.headers.get("x-cpk-identity-attestation", "")
        if not hmac.compare_digest(supplied_attestation, identity_attestation_token):
            return Response(status_code=401, content=b"Unauthorized")
        tenant = request.headers.get("x-cpk-authenticated-tenant", "")
        actor = request.headers.get("x-cpk-authenticated-subject", "")
        key = request.headers.get("idempotency-key", "")
        if not tenant or not actor or not key:
            return Response(status_code=400, content=b"Missing idempotency identity")
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return Response(status_code=400, content=b"Invalid content length")
            if declared_length < 0 or declared_length > policy.max_request_bytes:
                return Response(status_code=413, content=b"Request Entity Too Large")
        chunks: list[bytes] = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > policy.max_request_bytes:
                return Response(status_code=413, content=b"Request Entity Too Large")
            chunks.append(chunk)
        body = b"".join(chunks)
        response = execute(
            HttpRequest(
                request.method,
                "/" + path,
                request.url.query,
                dict(request.headers),
                body,
            ),
            key,
            tenant,
            actor,
        )
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower()
            not in {"connection", "content-length", "transfer-encoding"}
        }
        return Response(
            status_code=response.status_code,
            content=response.body,
            headers=headers,
        )

    return app
