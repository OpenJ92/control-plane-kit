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
    HttpTimeoutPolicy,
    HttpTimeoutServer,
    TimeoutOutcome,
    http_timeout_block,
    http_timeout_command,
)
from control_plane_kit.products.servers.support.http_messages import HttpRequest, HttpResponse


class HttpTimeoutTests(unittest.TestCase):
    def test_success_timeout_disconnect_and_bounds_are_distinct(self) -> None:
        def timeout(_request: HttpRequest) -> HttpResponse:
            raise TimeoutError

        def disconnected(_request: HttpRequest) -> HttpResponse:
            raise ConnectionError

        timeout_server = HttpTimeoutServer({"target": timeout}, "target")
        disconnected_server = HttpTimeoutServer({"target": disconnected}, "target")
        bounded = HttpTimeoutServer(
            {"target": lambda _: HttpResponse.text("12345")},
            "target",
            HttpTimeoutPolicy(max_request_bytes=4, max_response_bytes=4),
        )

        self.assertEqual(timeout_server.handle(HttpRequest()).status_code, 504)
        self.assertEqual(
            timeout_server.observation().latest_outcome,
            TimeoutOutcome.UPSTREAM_TIMEOUT,
        )
        self.assertEqual(disconnected_server.handle(HttpRequest()).status_code, 502)
        self.assertEqual(
            disconnected_server.observation().latest_outcome,
            TimeoutOutcome.UPSTREAM_DISCONNECTED,
        )
        self.assertEqual(bounded.handle(HttpRequest(body=b"12345")).status_code, 413)
        self.assertEqual(bounded.handle(HttpRequest()).status_code, 502)
        self.assertEqual(bounded.observation().request_count, 2)

    def test_per_attempt_and_total_deadline_remain_separate(self) -> None:
        def slow(_request: HttpRequest) -> HttpResponse:
            time.sleep(0.02)
            return HttpResponse.text("late")

        attempt = HttpTimeoutServer(
            {"target": slow},
            "target",
            HttpTimeoutPolicy(upstream_timeout_ms=5, total_deadline_ms=100),
        )
        total = HttpTimeoutServer(
            {"target": slow},
            "target",
            HttpTimeoutPolicy(upstream_timeout_ms=100, total_deadline_ms=5),
        )

        self.assertEqual(attempt.handle(HttpRequest()).status_code, 504)
        self.assertEqual(attempt.observation().latest_outcome, TimeoutOutcome.UPSTREAM_TIMEOUT)
        self.assertEqual(total.handle(HttpRequest()).status_code, 504)
        self.assertEqual(
            total.observation().latest_outcome,
            TimeoutOutcome.TOTAL_DEADLINE_EXCEEDED,
        )

    def test_policy_and_block_identity_are_closed_and_finite(self) -> None:
        with self.assertRaisesRegex(ValueError, "upstream timeout"):
            HttpTimeoutPolicy(upstream_timeout_ms=0)
        with self.assertRaisesRegex(ValueError, "total request deadline"):
            HttpTimeoutPolicy(total_deadline_ms=0)
        with self.assertRaisesRegex(TypeError, "timeout policy"):
            http_timeout_command("timeout")  # type: ignore[arg-type]

        block = http_timeout_block(
            policy=HttpTimeoutPolicy(upstream_timeout_ms=123, total_deadline_ms=456)
        )

        self.assertIs(block.spec.product, PackageServerProduct.HTTP_TIMEOUT)
        self.assertEqual(block.sockets.requirement_names(), ("target",))
        self.assertIn("UPSTREAM_TIMEOUT_SECONDS = 123 / 1000", block.implementation.command[2])
        self.assertIn("TOTAL_DEADLINE_SECONDS = 456 / 1000", block.implementation.command[2])

    def test_live_generated_server_bounds_effect_and_reports_client_disconnect(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        proxy_port = _free_port()
        environment = dict(os.environ)
        environment["TIMEOUT_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_CONTROL_TOKEN"] = "timeout-control-token"
        process = subprocess.Popen(
            http_timeout_command(
                HttpTimeoutPolicy(
                    upstream_timeout_ms=30,
                    total_deadline_ms=100,
                    max_request_bytes=8,
                    max_response_bytes=64,
                ),
                port=proxy_port,
            ),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(proxy_port)

        self.assertEqual(_request(proxy_port, "/ok"), (200, b"target"))
        self.assertEqual(_metrics(proxy_port)["latest_outcome"], "forwarded")
        _expect_error(proxy_port, "/__deploy/metrics", 401)

        before = target.calls
        _expect_error(proxy_port, "/oversized", 413, body=b"123456789")
        self.assertEqual(target.calls, before)
        self.assertEqual(_metrics(proxy_port)["latest_outcome"], "request-rejected")

        target.delay_seconds = 0.1
        _expect_error(proxy_port, "/slow", 504)
        self.assertEqual(_metrics(proxy_port)["latest_outcome"], "upstream-timeout")

        with socket.create_connection(("127.0.0.1", proxy_port), timeout=1) as client:
            client.sendall(b"GET /cancel HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        for _ in range(100):
            if _metrics(proxy_port)["latest_outcome"] == "client-disconnected":
                break
            time.sleep(0.02)
        self.assertEqual(_metrics(proxy_port)["latest_outcome"], "client-disconnected")
        evidence = json.dumps(_metrics(proxy_port))
        self.assertNotIn(str(target.port), evidence)
        self.assertNotIn("timeout-control-token", evidence)


class _TargetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        owner.record_call()
        if owner.delay_seconds:
            time.sleep(owner.delay_seconds)
        body = b"target"
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            owner.record_disconnected_write()

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self.calls = 0
        self.disconnected_writes = 0
        self.delay_seconds = 0.0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def record_call(self) -> None:
        with self._lock:
            self.calls += 1

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
    raise RuntimeError("timeout proxy did not become ready")


def _metrics(port: int) -> dict[str, object]:
    return json.loads(
        _request(
            port,
            "/__deploy/metrics",
            headers={"Authorization": "Bearer timeout-control-token"},
        )[1]
    )


def _request(
    port: int,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={} if headers is None else headers,
    )
    with urlopen(request, timeout=2) as response:
        return response.status, response.read()


def _expect_error(
    port: int,
    path: str,
    status: int,
    *,
    body: bytes | None = None,
) -> None:
    try:
        _request(port, path, body=body)
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
