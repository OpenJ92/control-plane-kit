"""Process composition root for one test-only HTTP load-generator server."""

from __future__ import annotations

import json
import sys

import uvicorn

from control_plane_kit.adapters.http_forwarding import forward_http_request_sync
from control_plane_kit.contracts import EnvironmentContract, HttpVariable, SecretVariable, TextVariable
from control_plane_kit.load_generation import load_generator_policy_from_descriptor
from control_plane_kit.servers.http_load_generator import (
    HttpLoadGeneratorServer,
    create_load_generator_app,
)
from control_plane_kit.servers.http_messages import HttpRequest, HttpResponse


class LoadGeneratorEnvironment(EnvironmentContract):
    target_url = HttpVariable("target_url", metadata={"env": "LOAD_TARGET_URL"})
    control_token = SecretVariable(
        "control_token", metadata={"env": "CPK_LOAD_CONTROL_TOKEN"}
    )
    test_only = TextVariable("test_only", metadata={"env": "CPK_TEST_ONLY"})
    control_port = TextVariable(
        "control_port",
        required=False,
        metadata={"env": "CPK_LOAD_CONTROL_PORT"},
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("load generator requires one policy descriptor")
    policy = load_generator_policy_from_descriptor(json.loads(sys.argv[1]))
    environment = LoadGeneratorEnvironment.from_process()
    target_url = environment.get("target_url").rstrip("/")
    token = environment.get("control_token")
    port = int(environment.get("control_port") or "8080")
    if port < 1 or port > 65_535:
        raise SystemExit("CPK_LOAD_CONTROL_PORT must be between 1 and 65535")

    def target(request: HttpRequest, timeout_ms: int, max_response_bytes: int) -> HttpResponse:
        response = forward_http_request_sync(
            request.method,
            target_url + request.path_with_query,
            headers={},
            body=b"",
            timeout_seconds=timeout_ms / 1_000,
            max_response_bytes=max_response_bytes,
        )
        return HttpResponse(response.status_code, {}, b"")

    server = HttpLoadGeneratorServer(policy, target)
    app = create_load_generator_app(
        server,
        control_token=token,
        test_only=environment.get("test_only") == "1",
    )
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
