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
    HttpTrafficLoggerServer,
    TrafficEvidencePolicy,
    TrafficMethod,
    TrafficPathPolicy,
    TrafficStatusClass,
    http_traffic_logger_block,
    http_traffic_logger_command,
)
from control_plane_kit.products.servers.support.http_messages import HttpRequest, HttpResponse


class HttpTrafficLoggerTests(unittest.TestCase):
    def test_one_block_type_logs_inbound_and_outbound_by_graph_position(self) -> None:
        dependency = lambda request: HttpResponse.text(f"dependency:{request.path}")
        outbound = HttpTrafficLoggerServer({"dependency": dependency}, "dependency")

        def application(request: HttpRequest) -> HttpResponse:
            dependency_response = outbound.handle(
                HttpRequest(path="/service", headers=request.headers)
            )
            return HttpResponse.text("application:" + dependency_response.body.decode())

        inbound = HttpTrafficLoggerServer({"application": application}, "application")

        response = inbound.handle(HttpRequest(path="/entry"))

        self.assertEqual(response.body, b"application:dependency:/service")
        self.assertEqual(inbound.read().items[0].method, TrafficMethod.GET)
        self.assertEqual(outbound.read().items[0].method, TrafficMethod.GET)
        self.assertIsNone(inbound.read().items[0].path_digest)
        self.assertIsNone(outbound.read().items[0].path_digest)

    def test_evidence_is_bounded_paginated_and_evicts_oldest(self) -> None:
        server = HttpTrafficLoggerServer(
            {"target": lambda _: HttpResponse.text("ok")},
            "target",
            TrafficEvidencePolicy(capacity=2, page_limit=1),
        )
        for _ in range(3):
            server.handle(HttpRequest())

        first = server.read(offset=0, limit=1)
        second = server.read(offset=1, limit=1)

        self.assertEqual(first.total, 2)
        self.assertEqual(first.evicted, 1)
        self.assertEqual(first.items[0].sequence, 2)
        self.assertEqual(second.items[0].sequence, 3)
        with self.assertRaisesRegex(ValueError, "read limit"):
            server.read(limit=2)

    def test_redacted_and_hashed_paths_never_retain_sensitive_values(self) -> None:
        request = HttpRequest(
            method="PRIVATE-METHOD",
            path="/users/private-token",
            query="secret=query-value",
            headers={"Authorization": "Bearer private", "Cookie": "session=private"},
            body=b"private-body",
        )
        redacted = HttpTrafficLoggerServer(
            {"target": lambda _: HttpResponse.text("private-response")},
            "target",
        )
        hashed = HttpTrafficLoggerServer(
            {"target": lambda _: HttpResponse.text("private-response")},
            "target",
            TrafficEvidencePolicy(path_policy=TrafficPathPolicy.STABLE_HASH),
        )

        redacted.handle(request)
        hashed.handle(request)
        redacted_descriptor = json.dumps(redacted.read().descriptor())
        hashed_item = hashed.read().items[0]

        self.assertNotIn("private", redacted_descriptor)
        self.assertNotIn("secret", redacted_descriptor)
        self.assertNotIn("path_digest", redacted_descriptor)
        self.assertEqual(hashed_item.method, TrafficMethod.OTHER)
        self.assertEqual(len(hashed_item.path_digest or ""), 64)
        self.assertNotIn("private-token", hashed_item.path_digest or "")

    def test_local_rejection_and_target_loss_are_visible_without_raising(self) -> None:
        calls = 0

        def target(_request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            raise OSError("private target failure")

        server = HttpTrafficLoggerServer(
            {"target": target},
            "target",
            TrafficEvidencePolicy(max_request_bytes=4),
        )

        self.assertEqual(server.handle(HttpRequest(body=b"12345")).status_code, 413)
        self.assertEqual(calls, 0)
        self.assertEqual(server.handle(HttpRequest()).status_code, 502)
        self.assertEqual(calls, 1)
        self.assertEqual(
            tuple(item.status_class for item in server.read().items),
            (TrafficStatusClass.CLIENT_ERROR, TrafficStatusClass.SERVER_ERROR),
        )
        self.assertNotIn("private target failure", json.dumps(server.read().descriptor()))

    def test_policy_and_block_identity_are_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "capacity"):
            TrafficEvidencePolicy(capacity=0)
        with self.assertRaisesRegex(ValueError, "cannot exceed capacity"):
            TrafficEvidencePolicy(capacity=2, page_limit=3)
        with self.assertRaisesRegex(TypeError, "path policy"):
            TrafficEvidencePolicy(path_policy="redacted")  # type: ignore[arg-type]

        block = http_traffic_logger_block(
            policy=TrafficEvidencePolicy(path_policy=TrafficPathPolicy.STABLE_HASH)
        )

        self.assertIs(block.spec.product, PackageServerProduct.HTTP_TRAFFIC_LOGGER)
        self.assertEqual(block.sockets.requirement_names(), ("target",))
        self.assertIn("HASH_PATHS = True", block.implementation.command[2])

    def test_live_generated_server_forwards_and_exposes_only_authenticated_pages(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        logger_port = _free_port()
        policy = TrafficEvidencePolicy(
            capacity=2,
            page_limit=1,
            max_request_bytes=8,
            max_response_bytes=64,
            path_policy=TrafficPathPolicy.STABLE_HASH,
        )
        environment = dict(os.environ)
        environment["LOGGER_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_CONTROL_TOKEN"] = "logger-control-token"
        process = subprocess.Popen(
            http_traffic_logger_command(policy, port=logger_port),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(logger_port)

        status, body = _request(
            logger_port,
            "/private-path?secret=query",
            method="POST",
            body=b"payload",
            headers={
                "Authorization": "Bearer caller-secret",
                "Cookie": "session=caller-secret",
            },
        )
        self.assertEqual((status, body), (201, b"POST:payload"))
        self.assertEqual(target.latest_path, "/private-path?secret=query")
        self.assertEqual(target.latest_authorization, "Bearer caller-secret")

        self.assertEqual(_request(logger_port, "/two")[0], 201)
        self.assertEqual(_request(logger_port, "/three")[0], 201)
        _expect_error(logger_port, "/__deploy/traffic-evidence", 401)
        page = _logs(logger_port, offset=0, limit=1)

        self.assertEqual(page["total"], 2)
        self.assertEqual(page["evicted"], 1)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["sequence"], 2)
        serialized = json.dumps(page)
        self.assertNotIn("private-path", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("payload", serialized)
        self.assertNotIn(str(target.port), serialized)
        self.assertEqual(len(page["items"][0]["path_digest"]), 64)

        before = target.calls
        _expect_error(logger_port, "/oversized", 413, body=b"123456789")
        self.assertEqual(target.calls, before)
        _expect_error(
            logger_port,
            "/__deploy/traffic-evidence?limit=2",
            400,
            headers={"Authorization": "Bearer logger-control-token"},
        )


class _TargetHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length)
        owner.record(
            self.path,
            self.headers.get("authorization", ""),
        )
        payload = self.command.encode() + b":" + body
        self.send_response(201)
        self.send_header("content-type", "application/octet-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = _respond
    do_POST = _respond

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self.calls = 0
        self.latest_path = ""
        self.latest_authorization = ""
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def record(self, path: str, authorization: str) -> None:
        with self._lock:
            self.calls += 1
            self.latest_path = path
            self.latest_authorization = authorization

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
    raise RuntimeError("traffic logger did not become ready")


def _logs(port: int, *, offset: int, limit: int) -> dict[str, object]:
    return json.loads(
        _request(
            port,
            f"/__deploy/traffic-evidence?offset={offset}&limit={limit}",
            headers={"Authorization": "Bearer logger-control-token"},
        )[1]
    )


def _request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={} if headers is None else headers,
        method=method,
    )
    with urlopen(request, timeout=2) as response:
        return response.status, response.read()


def _expect_error(
    port: int,
    path: str,
    status: int,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    try:
        _request(port, path, body=body, headers=headers)
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
