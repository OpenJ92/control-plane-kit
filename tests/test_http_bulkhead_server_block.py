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
    BulkheadOutcome,
    HttpBulkheadPolicy,
    HttpBulkheadServer,
    http_bulkhead_block,
    http_bulkhead_command,
)
from control_plane_kit.servers import HttpRequest, HttpResponse


class HttpBulkheadTests(unittest.TestCase):
    def test_ticketed_queue_rejects_overflow_and_releases_all_permits(self) -> None:
        release = threading.Event()
        entered = threading.Event()

        def target(_request: HttpRequest) -> HttpResponse:
            entered.set()
            release.wait(1)
            return HttpResponse.text("ok")

        server = HttpBulkheadServer(
            {"target": target},
            "target",
            HttpBulkheadPolicy(maximum_in_flight=1, queue_capacity=1),
        )
        results: list[int] = []
        first = threading.Thread(target=lambda: results.append(server.handle(HttpRequest()).status_code))
        second = threading.Thread(target=lambda: results.append(server.handle(HttpRequest()).status_code))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        _wait_for(lambda: server.observation().waiting == 1)

        self.assertEqual(server.handle(HttpRequest()).status_code, 503)
        release.set()
        first.join(1)
        second.join(1)

        self.assertEqual(sorted(results), [200, 200])
        observation = server.observation()
        self.assertEqual(observation.in_flight, 0)
        self.assertEqual(observation.waiting, 0)
        self.assertEqual(observation.accepted_count, 2)
        self.assertEqual(observation.rejected_count, 1)

    def test_queue_timeout_and_target_failure_recover_without_negative_permits(self) -> None:
        release = threading.Event()
        entered = threading.Event()

        def slow(_request: HttpRequest) -> HttpResponse:
            entered.set()
            release.wait(1)
            return HttpResponse.text("ok")

        server = HttpBulkheadServer(
            {"target": slow},
            "target",
            HttpBulkheadPolicy(maximum_in_flight=1, queue_capacity=1, queue_timeout_ms=20),
        )
        first = threading.Thread(target=lambda: server.handle(HttpRequest()))
        first.start()
        self.assertTrue(entered.wait(1))
        self.assertEqual(server.handle(HttpRequest()).status_code, 504)
        self.assertEqual(server.observation().latest_outcome, BulkheadOutcome.QUEUE_TIMEOUT)
        release.set()
        first.join(1)
        self.assertEqual(server.observation().in_flight, 0)

        calls = 0

        def unstable(_request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError
            return HttpResponse.text("recovered")

        recovery = HttpBulkheadServer({"target": unstable}, "target")
        self.assertEqual(recovery.handle(HttpRequest()).status_code, 502)
        self.assertEqual(recovery.handle(HttpRequest()).status_code, 200)
        self.assertEqual(recovery.observation().in_flight, 0)

    def test_policy_and_block_identity_are_closed_and_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "maximum in-flight"):
            HttpBulkheadPolicy(maximum_in_flight=0)
        with self.assertRaisesRegex(ValueError, "queue capacity"):
            HttpBulkheadPolicy(queue_capacity=-1)
        with self.assertRaisesRegex(TypeError, "bulkhead policy"):
            http_bulkhead_command("bulkhead")  # type: ignore[arg-type]

        block = http_bulkhead_block(
            policy=HttpBulkheadPolicy(maximum_in_flight=3, queue_capacity=4)
        )
        self.assertIs(block.spec.product, PackageServerProduct.HTTP_BULKHEAD)
        self.assertEqual(block.sockets.requirement_names(), ("target",))
        self.assertIn("MAXIMUM_IN_FLIGHT = 3", block.implementation.command[2])
        self.assertIn("QUEUE_CAPACITY = 4", block.implementation.command[2])

    def test_live_generated_server_enforces_concurrency_queue_and_auth(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        proxy_port = _free_port()
        environment = dict(os.environ)
        environment["BULKHEAD_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_CONTROL_TOKEN"] = "bulkhead-control-token"
        process = subprocess.Popen(
            http_bulkhead_command(
                HttpBulkheadPolicy(
                    maximum_in_flight=1,
                    queue_capacity=1,
                    queue_timeout_ms=500,
                ),
                port=proxy_port,
            ),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(proxy_port)

        target.release.clear()
        results: list[int] = []
        first = threading.Thread(target=lambda: results.append(_request(proxy_port, "/one")[0]))
        second = threading.Thread(target=lambda: results.append(_request(proxy_port, "/two")[0]))
        first.start()
        self.assertTrue(target.entered.wait(1))
        second.start()
        _wait_for(lambda: _metrics(proxy_port)["waiting"] == 1)
        _expect_error(proxy_port, "/three", 503)
        target.release.set()
        first.join(2)
        second.join(2)

        self.assertEqual(sorted(results), [200, 200])
        metrics = _metrics(proxy_port)
        self.assertEqual(metrics["in_flight"], 0)
        self.assertEqual(metrics["waiting"], 0)
        self.assertEqual(metrics["accepted_count"], 2)
        self.assertEqual(metrics["rejected_count"], 1)
        _expect_error(proxy_port, "/__deploy/metrics", 401)
        evidence = json.dumps(metrics)
        self.assertNotIn(str(target.port), evidence)
        self.assertNotIn("bulkhead-control-token", evidence)


class _TargetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        owner.entered.set()
        owner.release.wait(2)
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
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.disconnected_writes = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def record_disconnected_write(self) -> None:
        with self._lock:
            self.disconnected_writes += 1

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _wait_for(predicate) -> None:
    for _ in range(100):
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_ready(port: int) -> None:
    _wait_for(lambda: _ready(port))


def _ready(port: int) -> bool:
    try:
        return _request(port, "/health")[0] == 200
    except OSError:
        return False


def _metrics(port: int) -> dict[str, object]:
    return json.loads(
        _request(
            port,
            "/__deploy/metrics",
            headers={"Authorization": "Bearer bulkhead-control-token"},
        )[1]
    )


def _request(port: int, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        headers={} if headers is None else headers,
    )
    with urlopen(request, timeout=2) as response:
        return response.status, response.read()


def _expect_error(port: int, path: str, status: int) -> None:
    try:
        _request(port, path)
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
