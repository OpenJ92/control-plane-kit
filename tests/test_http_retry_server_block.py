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
    PackageServerProduct,
)
from control_plane_kit.servers import (
    CircuitBreakerPolicy,
    CircuitBreakerState,
    HttpCircuitBreakerServer,
    HttpRetryServer,
    RetryMethodPolicy,
    RetryPolicy,
    RetryStatusPolicy,
    http_retry_block,
    http_retry_command,
)
from control_plane_kit.products.servers.support.http_messages import HttpRequest, HttpResponse


class HttpRetryTests(unittest.TestCase):
    def test_success_recovery_and_exhaustion_are_finite(self) -> None:
        responses = iter((503, 200, 503, 503, 503))

        def target(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("target", status_code=next(responses))

        server = HttpRetryServer(
            {"target": target},
            "target",
            RetryPolicy(attempts=3),
        )

        self.assertEqual(server.handle(HttpRequest()).status_code, 200)
        self.assertEqual(server.handle(HttpRequest()).status_code, 503)
        self.assertEqual(server.observation().descriptor(), {
            "request_count": 2,
            "attempt_count": 5,
            "retry_count": 3,
            "exhausted_count": 1,
            "latest_attempt_count": 3,
            "latest_request_id": "retry-request-00000000000000000002",
        })

    def test_non_idempotent_retry_requires_explicit_key_policy(self) -> None:
        calls = 0

        def target(_request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            return HttpResponse.text("failed", status_code=503)

        safe_only = HttpRetryServer(
            {"target": target},
            "target",
            RetryPolicy(attempts=3),
        )
        self.assertEqual(
            safe_only.handle(HttpRequest(method="POST")).status_code,
            503,
        )
        self.assertEqual(calls, 1)

        keyed = HttpRetryServer(
            {"target": target},
            "target",
            RetryPolicy(
                attempts=3,
                method_policy=RetryMethodPolicy.IDEMPOTENCY_KEY,
            ),
        )
        keyed.handle(HttpRequest(method="POST"))
        self.assertEqual(calls, 2)
        keyed.handle(HttpRequest(method="POST", headers={"Idempotency-Key": "opaque"}))
        self.assertEqual(calls, 5)
        self.assertNotIn("opaque", json.dumps(keyed.observation().descriptor()))

    def test_policy_and_payloads_are_bounded_closed_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "retry attempts"):
            RetryPolicy(attempts=0)
        with self.assertRaisesRegex(TypeError, "method policy"):
            RetryPolicy(method_policy="safe-only")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "status policy"):
            RetryPolicy(status_policy="gateway-errors")  # type: ignore[arg-type]

        server = HttpRetryServer(
            {"target": lambda _: HttpResponse.text("12345")},
            "target",
            RetryPolicy(max_request_bytes=4, max_response_bytes=4),
        )
        self.assertEqual(server.handle(HttpRequest(body=b"12345")).status_code, 413)
        self.assertEqual(server.handle(HttpRequest()).status_code, 502)

    def test_graph_order_expresses_retry_and_circuit_composition(self) -> None:
        outside_responses = iter((502, 200))

        def outside_target(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("target", status_code=next(outside_responses))

        inner_circuit = HttpCircuitBreakerServer(
            {"target": outside_target},
            "target",
            CircuitBreakerPolicy(failure_threshold=1, recovery_timeout_ms=10_000),
        )
        outer_retry = HttpRetryServer(
            {"circuit": inner_circuit.handle},
            "circuit",
            RetryPolicy(attempts=2),
        )

        self.assertEqual(outer_retry.handle(HttpRequest()).status_code, 503)
        self.assertIs(inner_circuit.state, CircuitBreakerState.OPEN)

        inside_responses = iter((502, 200))

        def inside_target(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("target", status_code=next(inside_responses))

        inner_retry = HttpRetryServer(
            {"target": inside_target},
            "target",
            RetryPolicy(attempts=2),
        )
        outer_circuit = HttpCircuitBreakerServer(
            {"retry": inner_retry.handle},
            "retry",
            CircuitBreakerPolicy(failure_threshold=1),
        )

        self.assertEqual(outer_circuit.handle(HttpRequest()).status_code, 200)
        self.assertIs(outer_circuit.state, CircuitBreakerState.CLOSED)

    def test_block_has_closed_product_and_policy_identity(self) -> None:
        policy = RetryPolicy(
            attempts=4,
            method_policy=RetryMethodPolicy.IDEMPOTENCY_KEY,
            status_policy=RetryStatusPolicy.SERVER_ERRORS,
        )
        block = http_retry_block(policy=policy)

        self.assertIs(block.spec.product, PackageServerProduct.HTTP_RETRY)
        self.assertEqual(block.sockets.requirement_names(), ("target",))
        self.assertIn("ATTEMPTS = 4", block.implementation.command[2])
        self.assertIn("IDEMPOTENCY_KEY_POLICY = True", block.implementation.command[2])
        self.assertIn("SERVER_ERROR_POLICY = True", block.implementation.command[2])

    def test_live_generated_server_bounds_retries_timeouts_and_evidence(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        retry_port = _free_port()
        policy = RetryPolicy(
            attempts=3,
            per_attempt_timeout_ms=30,
            total_deadline_ms=150,
            backoff_ms=0,
            max_request_bytes=8,
            max_response_bytes=64,
            method_policy=RetryMethodPolicy.IDEMPOTENCY_KEY,
        )
        environment = dict(os.environ)
        environment["RETRY_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_CONTROL_TOKEN"] = "retry-control-token"
        process = subprocess.Popen(
            http_retry_command(policy, port=retry_port),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(retry_port)

        target.set_responses(503, 200)
        self.assertEqual(_request(retry_port, "/")[0], 200)
        self.assertEqual(target.calls, 2)
        metrics = _metrics(retry_port)
        self.assertEqual(metrics["latest_attempt_count"], 2)
        self.assertEqual(metrics["retry_count"], 1)
        self.assertNotIn(str(target.port), json.dumps(metrics))
        self.assertNotIn("retry-control-token", json.dumps(metrics))
        _expect_error(retry_port, "/__deploy/metrics", 401)

        target.set_responses(503, 503, 503)
        before = target.calls
        _expect_error(retry_port, "/", 503)
        self.assertEqual(target.calls - before, 3)

        target.set_responses(503, 200, 200)
        before = target.calls
        _expect_error(retry_port, "/", 503, method="POST")
        self.assertEqual(target.calls - before, 1)
        self.assertEqual(
            _request(
                retry_port,
                "/",
                method="POST",
                headers={"Idempotency-Key": "do-not-retain"},
            )[0],
            200,
        )
        self.assertNotIn("do-not-retain", json.dumps(_metrics(retry_port)))

        before = target.calls
        _expect_error(retry_port, "/", 413, body=b"123456789")
        self.assertEqual(target.calls, before)

        target.set_responses(200, 200, 200)
        target.delay_seconds = 0.1
        before = target.calls
        metrics_before_timeout = _metrics(retry_port)
        _expect_error(retry_port, "/", 502)
        timeout_calls = target.calls - before
        self.assertGreaterEqual(timeout_calls, 2)
        self.assertLessEqual(timeout_calls, 3)
        metrics_after_timeout = _metrics(retry_port)
        self.assertEqual(metrics_after_timeout["latest_attempt_count"], timeout_calls)
        self.assertEqual(
            metrics_after_timeout["retry_count"]
            - metrics_before_timeout["retry_count"],
            timeout_calls - 1,
        )


class _TargetHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        status = owner.next_status()
        if owner.delay_seconds:
            time.sleep(owner.delay_seconds)
        body = b"target"
        self.send_response(status)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            owner.record_disconnected_write()

    do_GET = _respond
    do_POST = _respond

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self.calls = 0
        self.disconnected_writes = 0
        self.delay_seconds = 0.0
        self._statuses: list[int] = [200]
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def set_responses(self, *statuses: int) -> None:
        with self._lock:
            self._statuses = list(statuses)
            self.delay_seconds = 0.0

    def next_status(self) -> int:
        with self._lock:
            self.calls += 1
            return self._statuses.pop(0) if self._statuses else 200

    def record_disconnected_write(self) -> None:
        with self._lock:
            self.disconnected_writes += 1

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
    raise RuntimeError("retry server did not become ready")


def _metrics(port: int) -> dict[str, object]:
    return _request(
        port,
        "/__deploy/metrics",
        headers={"Authorization": "Bearer retry-control-token"},
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
