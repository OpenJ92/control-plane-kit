"""Process composition root for one durable idempotency gateway server."""

from __future__ import annotations

import json
import sys
import uuid

import psycopg
import uvicorn

from control_plane_kit.adapters.http_forwarding import forward_http_request_sync
from control_plane_kit.contracts import (
    EnvironmentContract,
    HttpVariable,
    PostgresVariable,
    SecretVariable,
    TextVariable,
)
from control_plane_kit.domains.idempotency import idempotency_policy_from_descriptor
from control_plane_kit.idempotency_gateway.service import (
    ExecuteIdempotentHttp,
    IdempotencyGatewayAuthority,
    IdempotencyGatewayScope,
    IdempotencyGatewayService,
)
from control_plane_kit.idempotency_gateway.unit_of_work import (
    IdempotencyGatewayUnitOfWork,
    install_idempotency_gateway_schema,
)
from control_plane_kit.servers.http_idempotency_gateway import create_idempotency_gateway_app
from control_plane_kit.servers.http_messages import HttpRequest, HttpResponse


class IdempotencyGatewayEnvironment(EnvironmentContract):
    database_url = PostgresVariable(
        "database_url", metadata={"env": "IDEMPOTENCY_DATABASE_URL"}
    )
    target_url = HttpVariable(
        "target_url", metadata={"env": "IDEMPOTENCY_TARGET_URL"}
    )
    identity_token = SecretVariable(
        "identity_token", metadata={"env": "CPK_IDEMPOTENCY_IDENTITY_TOKEN"}
    )
    gateway_id = TextVariable(
        "gateway_id",
        required=False,
        metadata={"env": "CPK_IDEMPOTENCY_GATEWAY_ID"},
    )


def main() -> None:
    policy = idempotency_policy_from_descriptor(json.loads(sys.argv[1]))
    environment = IdempotencyGatewayEnvironment.from_process()
    database_url = environment.get("database_url")
    target_url = environment.get("target_url").rstrip("/")
    identity_token = environment.get("identity_token")
    gateway_id = environment.get("gateway_id") or "idempotency-gateway"
    connection_factory = lambda: psycopg.connect(database_url)
    install_idempotency_gateway_schema(connection_factory)

    def target(request: HttpRequest) -> HttpResponse:
        response = forward_http_request_sync(
            request.method,
            target_url + request.path_with_query,
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() not in {"host", "content-length", "connection"}
            },
            body=request.body,
            timeout_seconds=5,
            max_response_bytes=policy.max_response_bytes,
        )
        return HttpResponse(response.status_code, response.headers, response.body)

    factory = lambda: IdempotencyGatewayUnitOfWork(connection_factory)
    service = IdempotencyGatewayService(
        factory,
        target,
        id_factory=lambda: str(uuid.uuid4()),
    )

    def execute(request: HttpRequest, key: str, tenant: str, actor: str) -> HttpResponse:
        return service.execute(
            ExecuteIdempotentHttp(
                gateway_id,
                policy,
                request,
                key,
                tenant,
                actor,
                IdempotencyGatewayAuthority(
                    "http-idempotency-gateway",
                    frozenset((IdempotencyGatewayScope.EXECUTE,)),
                ),
            )
        ).response

    app = create_idempotency_gateway_app(
        execute,
        policy,
        identity_attestation_token=identity_token,
    )
    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
