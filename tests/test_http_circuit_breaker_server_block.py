from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from control_plane_kit import (
    CapabilityName,
    DeploymentRecipe,
    DockerRuntime,
    GraphDescriptorCodec,
    PackageServerProduct,
    SecretEnvironmentDelivery,
    compile_recipe,
)
from control_plane_kit.servers import (
    CircuitBreakerMethodPolicy,
    CircuitBreakerPolicy,
    CircuitBreakerState,
    http_circuit_breaker_block,
    http_circuit_breaker_command,
)
from control_plane_kit.products.servers.support.http_messages import HttpRequest, HttpResponse
from control_plane_kit.servers import HttpCircuitBreakerServer


class HttpCircuitBreakerTests(unittest.TestCase):
    def test_closed_open_half_open_closed_is_pure_and_bounded(self) -> None:
        statuses = iter((500, 500, 200))

        def target(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("target", status_code=next(statuses))

        server = HttpCircuitBreakerServer(
            {"target": target},
            "target",
            CircuitBreakerPolicy(failure_threshold=2, recovery_timeout_ms=50),
        )

        self.assertEqual(server.handle(HttpRequest(), now_ms=0).status_code, 500)
        self.assertEqual(server.handle(HttpRequest(), now_ms=1).status_code, 500)
        self.assertIs(server.state, CircuitBreakerState.OPEN)
        self.assertEqual(server.handle(HttpRequest(), now_ms=50).status_code, 503)
        self.assertEqual(server.handle(HttpRequest(), now_ms=51).status_code, 200)
        self.assertIs(server.state, CircuitBreakerState.CLOSED)
        self.assertEqual(server.observation().descriptor(), {
            "state": "closed",
            "consecutive_failures": 0,
            "half_open_trials_remaining": 0,
            "transition_sequence": 3,
            "latest_transition_id": "circuit-transition-00000000000000000003",
        })

    def test_half_open_failure_reopens_and_safe_method_policy_is_explicit(self) -> None:
        calls = 0

        def target(_request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            return HttpResponse.text("failed", status_code=500)

        server = HttpCircuitBreakerServer(
            {"target": target},
            "target",
            CircuitBreakerPolicy(failure_threshold=1, recovery_timeout_ms=10),
        )

        self.assertEqual(server.handle(HttpRequest(method="POST"), now_ms=0).status_code, 405)
        self.assertEqual(calls, 0)
        server.handle(HttpRequest(), now_ms=0)
        server.handle(HttpRequest(), now_ms=10)

        self.assertIs(server.state, CircuitBreakerState.OPEN)
        self.assertEqual(calls, 2)
        self.assertEqual(server.observation().transition_sequence, 3)

    def test_policy_bounds_request_response_and_typed_methods(self) -> None:
        with self.assertRaisesRegex(ValueError, "failure threshold"):
            CircuitBreakerPolicy(failure_threshold=0)
        with self.assertRaisesRegex(TypeError, "method policy"):
            CircuitBreakerPolicy(method_policy="safe-only")  # type: ignore[arg-type]

        server = HttpCircuitBreakerServer(
            {"target": lambda _: HttpResponse.text("12345")},
            "target",
            CircuitBreakerPolicy(max_request_bytes=4, max_response_bytes=4),
        )
        self.assertEqual(server.handle(HttpRequest(body=b"12345")).status_code, 413)
        self.assertEqual(server.handle(HttpRequest()).status_code, 502)

        all_methods = HttpCircuitBreakerServer(
            {"target": lambda _: HttpResponse.text("accepted")},
            "target",
            CircuitBreakerPolicy(
                method_policy=CircuitBreakerMethodPolicy.ALL_METHODS,
            ),
        )
        self.assertEqual(
            all_methods.handle(HttpRequest(method="POST")).status_code,
            200,
        )

    def test_block_preserves_product_policy_and_opaque_control_secret(self) -> None:
        policy = CircuitBreakerPolicy(
            failure_threshold=4,
            method_policy=CircuitBreakerMethodPolicy.ALL_METHODS,
        )
        block = http_circuit_breaker_block(
            policy=policy,
            control_secret_reference="secret://test/circuit",
        )
        graph = compile_recipe(
            DeploymentRecipe("circuit", DockerRuntime(children=(block,)))
        )
        descriptor = GraphDescriptorCodec().encode(graph)

        self.assertIs(block.spec.product, PackageServerProduct.HTTP_CIRCUIT_BREAKER)
        self.assertEqual(block.spec.capabilities, (
            CapabilityName.HEALTH_CHECKABLE,
            CapabilityName.CIRCUIT_STATE_READABLE,
            CapabilityName.CIRCUIT_RESETTABLE,
        ))
        self.assertIsInstance(
            block.implementation.secret_deliveries[0],
            SecretEnvironmentDelivery,
        )
        self.assertIn("FAILURE_THRESHOLD = 4", block.implementation.command[2])
        self.assertIn("ALLOW_ALL_METHODS = True", block.implementation.command[2])
        encoded = json.dumps(descriptor, sort_keys=True)
        self.assertIn("secret://test/circuit", encoded)
        self.assertNotIn("control-token-value", encoded)

    def test_live_generated_server_enforces_state_auth_and_reset(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        circuit_port = _free_port()
        policy = CircuitBreakerPolicy(
            failure_threshold=2,
            recovery_timeout_ms=50,
            upstream_timeout_ms=500,
            max_request_bytes=8,
            max_response_bytes=64,
        )
        environment = dict(os.environ)
        environment["CIRCUIT_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_CONTROL_TOKEN"] = "circuit-control-token"
        process = subprocess.Popen(
            http_circuit_breaker_command(policy, port=circuit_port),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(circuit_port)

        _expect_error(circuit_port, "/", 500)
        _expect_error(circuit_port, "/", 500)
        calls_at_open = target.calls
        _expect_error(circuit_port, "/", 503)
        self.assertEqual(target.calls, calls_at_open)
        _expect_error(circuit_port, "/__deploy/circuit", 401)
        open_descriptor = _control(circuit_port)
        self.assertEqual(open_descriptor["state"], "open")
        self.assertNotIn(str(target.port), json.dumps(open_descriptor))
        self.assertNotIn("circuit-control-token", json.dumps(open_descriptor))

        target.status = 200
        time.sleep(0.06)
        self.assertEqual(_request(circuit_port, "/")[0], 200)
        self.assertEqual(_control(circuit_port)["state"], "closed")

        target.status = 500
        _expect_error(circuit_port, "/", 500)
        _expect_error(circuit_port, "/", 500)
        _expect_error(
            circuit_port,
            "/__deploy/circuit/reset",
            401,
            method="POST",
        )
        self.assertEqual(_control(circuit_port)["state"], "open")
        reset = _request(
            circuit_port,
            "/__deploy/circuit/reset",
            method="POST",
            headers={"Authorization": "Bearer circuit-control-token"},
        )[1]
        self.assertEqual(reset["state"], "closed")
        _expect_error(circuit_port, "/", 500)
        self.assertEqual(_control(circuit_port)["state"], "closed")

        calls_before_policy_rejection = target.calls
        _expect_error(circuit_port, "/", 405, method="POST")
        self.assertEqual(target.calls, calls_before_policy_rejection)
        _expect_error(circuit_port, "/", 413, body=b"123456789")
        self.assertEqual(target.calls, calls_before_policy_rejection)


class _TargetHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        self.server.owner.calls += 1  # type: ignore[attr-defined]
        body = b"target"
        self.send_response(self.server.owner.status)  # type: ignore[attr-defined]
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self.status = 500
        self.calls = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_ready(port: int) -> None:
    for _ in range(100):
        try:
            if _request(port, "/health")[0] == 200:
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("circuit breaker did not become ready")


def _control(port: int) -> dict[str, object]:
    return _request(
        port,
        "/__deploy/circuit",
        headers={"Authorization": "Bearer circuit-control-token"},
    )[1]


def _request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={} if headers is None else headers,
        method=method,
    )
    with urlopen(request, timeout=2) as response:
        payload = response.read()
        return response.status, json.loads(payload) if payload.startswith(b"{") else {}


def _expect_error(
    port: int,
    path: str,
    status: int,
    *,
    method: str = "GET",
    body: bytes | None = None,
) -> None:
    try:
        _request(port, path, method=method, body=body)
    except HTTPError as error:
        with error:
            error.read()
            if error.code != status:
                raise AssertionError(f"expected HTTP {status}, received {error.code}")
        return
    raise AssertionError(f"expected HTTP {status}")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
