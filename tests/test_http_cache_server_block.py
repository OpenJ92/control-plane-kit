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
    CacheOutcome,
    CacheVaryHeader,
    HttpCachePolicy,
    HttpCacheServer,
    ProductMaturity,
    http_cache_block,
    http_cache_command,
)
from control_plane_kit.products.servers.support.http_messages import HttpRequest, HttpResponse


class HttpCacheTests(unittest.TestCase):
    def test_hit_miss_expiry_and_stale_refresh_are_deterministic(self) -> None:
        now = [0.0]
        calls = 0

        def target(_request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            return HttpResponse.text(f"value-{calls}")

        server = HttpCacheServer(
            {"target": target},
            "target",
            HttpCachePolicy(ttl_ms=100, stale_while_revalidate_ms=100),
            clock=lambda: now[0],
        )

        self.assertEqual(server.handle(HttpRequest()).body, b"value-1")
        self.assertEqual(server.handle(HttpRequest()).body, b"value-1")
        self.assertEqual(calls, 1)
        self.assertIs(server.observation().latest_outcome, CacheOutcome.HIT)

        now[0] = 0.15
        self.assertEqual(server.handle(HttpRequest()).body, b"value-1")
        self.assertEqual(calls, 2)
        self.assertIs(
            server.observation().latest_outcome,
            CacheOutcome.STALE_REFRESHED,
        )
        self.assertEqual(server.handle(HttpRequest()).body, b"value-2")

        now[0] = 0.40
        self.assertEqual(server.handle(HttpRequest()).body, b"value-3")
        self.assertEqual(server.observation().miss_count, 2)

    def test_sensitive_private_and_unknown_vary_responses_fail_closed(self) -> None:
        calls = 0

        def target(request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            match request.path:
                case "/private":
                    return HttpResponse(
                        200,
                        {"cache-control": "max-age=60, private"},
                        b"private",
                    )
                case "/cookie":
                    return HttpResponse(200, {"set-cookie": "session=x"}, b"cookie")
                case "/tenant":
                    return HttpResponse(200, {"vary": "X-Tenant-ID"}, b"tenant")
                case _:
                    return HttpResponse.text("public")

        server = HttpCacheServer({"target": target}, "target")
        for path in ("/private", "/cookie", "/tenant"):
            with self.subTest(path=path):
                server.handle(HttpRequest(path=path))
                server.handle(HttpRequest(path=path))
        server.handle(
            HttpRequest(path="/public", headers={"Authorization": "secret"})
        )
        server.handle(
            HttpRequest(path="/public", headers={"Authorization": "secret"})
        )

        self.assertEqual(calls, 8)
        self.assertEqual(server.observation().entry_count, 0)
        self.assertEqual(server.observation().bypass_count, 8)
        self.assertNotIn(
            "secret",
            json.dumps(server.observation().descriptor()),
        )

    def test_explicit_safe_vary_headers_partition_hashed_keys(self) -> None:
        calls = 0

        def target(request: HttpRequest) -> HttpResponse:
            nonlocal calls
            calls += 1
            accept = request.headers.get("Accept", "")
            return HttpResponse(200, {"vary": "Accept"}, accept.encode())

        server = HttpCacheServer(
            {"target": target},
            "target",
            HttpCachePolicy(vary_headers=(CacheVaryHeader.ACCEPT,)),
        )
        json_request = HttpRequest(headers={"Accept": "application/json"})
        text_request = HttpRequest(headers={"Accept": "text/plain"})

        self.assertEqual(server.handle(json_request).body, b"application/json")
        self.assertEqual(server.handle(text_request).body, b"text/plain")
        self.assertEqual(server.handle(json_request).body, b"application/json")
        self.assertEqual(calls, 2)
        self.assertEqual(server.observation().entry_count, 2)

    def test_capacity_eviction_and_purge_are_exact(self) -> None:
        server = HttpCacheServer(
            {
                "target": lambda request: HttpResponse.text(
                    request.path.removeprefix("/")
                )
            },
            "target",
            HttpCachePolicy(
                max_object_bytes=2,
                total_capacity_bytes=2,
                max_entries=1,
            ),
        )

        server.handle(HttpRequest(path="/a"))
        server.handle(HttpRequest(path="/b"))
        observation = server.observation()
        self.assertEqual(observation.entry_count, 1)
        self.assertEqual(observation.retained_bytes, 1)
        self.assertEqual(observation.eviction_count, 1)

        purged = server.purge()
        self.assertEqual(purged.entry_count, 0)
        self.assertEqual(purged.retained_bytes, 0)
        self.assertEqual(purged.purge_count, 1)

    def test_policy_and_block_identity_are_closed_and_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "cache TTL"):
            HttpCachePolicy(ttl_ms=0)
        with self.assertRaisesRegex(ValueError, "cache total capacity"):
            HttpCachePolicy(max_object_bytes=10, total_capacity_bytes=9)
        with self.assertRaisesRegex(TypeError, "vary headers"):
            HttpCachePolicy(vary_headers=("accept",))  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "cache policy"):
            http_cache_command("cache")  # type: ignore[arg-type]

        block = http_cache_block(
            policy=HttpCachePolicy(
                ttl_ms=123,
                vary_headers=(CacheVaryHeader.ACCEPT_ENCODING,),
            )
        )
        self.assertIs(block.spec.product, PackageServerProduct.HTTP_CACHE)
        self.assertIs(block.spec.maturity, ProductMaturity.TEACHING)
        self.assertEqual(block.sockets.requirement_names(), ("target",))
        source = block.implementation.command[2]
        self.assertIn("TTL_SECONDS = 123 / 1000", source)
        self.assertIn('VARY_HEADERS = ["accept-encoding"]', source)

    def test_live_generated_server_caches_bounds_purges_and_redacts(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        port = _free_port()
        environment = dict(os.environ)
        environment["CACHE_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_CACHE_CONTROL_TOKEN"] = "cache-control-token"
        process = subprocess.Popen(
            http_cache_command(
                HttpCachePolicy(
                    ttl_ms=5_000,
                    max_object_bytes=64,
                    total_capacity_bytes=64,
                    max_entries=2,
                    vary_headers=(CacheVaryHeader.ACCEPT,),
                ),
                port=port,
            ),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(port)

        first = _request(port, "/public")
        second = _request(port, "/public")
        self.assertEqual(first, second)
        self.assertEqual(target.calls_for("/public"), 1)

        _request(port, "/private")
        _request(port, "/private")
        self.assertEqual(target.calls_for("/private"), 2)

        _request(port, "/tenant", headers={"X-Tenant-ID": "tenant-secret"})
        _request(port, "/tenant", headers={"X-Tenant-ID": "tenant-secret"})
        self.assertEqual(target.calls_for("/tenant"), 2)

        _request(port, "/auth", headers={"Authorization": "Bearer secret"})
        _request(port, "/auth", headers={"Authorization": "Bearer secret"})
        self.assertEqual(target.calls_for("/auth"), 2)

        _expect_error(port, "/__deploy/cache", 401)
        _expect_error(port, "/__deploy/cache/purge", 401, method="POST")
        state = _cache_state(port)
        evidence = json.dumps(state)
        self.assertGreaterEqual(state["entry_count"], 1)
        self.assertNotIn("tenant-secret", evidence)
        self.assertNotIn("Bearer secret", evidence)
        self.assertNotIn(str(target.port), evidence)
        self.assertNotIn("cache-control-token", evidence)

        purged = _purge(port)
        self.assertEqual(purged["entry_count"], 0)
        self.assertEqual(purged["retained_bytes"], 0)
        self.assertEqual(purged["purge_count"], 1)


class _TargetHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        owner.record(path)
        headers: dict[str, str] = {}
        if path == "/private":
            headers["cache-control"] = "private"
        elif path == "/tenant":
            headers["vary"] = "X-Tenant-ID"
        elif path == "/accept":
            headers["vary"] = "Accept"
        body = f"{path}:{owner.calls_for(path)}".encode()
        self.send_response(200)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self._calls: dict[str, int] = {}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def record(self, path: str) -> None:
        with self._lock:
            self._calls[path] = self._calls.get(path, 0) + 1

    def calls_for(self, path: str) -> int:
        with self._lock:
            return self._calls.get(path, 0)

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
    raise RuntimeError("cache server did not become ready")


def _request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        headers={} if headers is None else headers,
        method=method,
    )
    with urlopen(request, timeout=2) as response:
        return response.status, response.read()


def _cache_state(port: int) -> dict[str, object]:
    return json.loads(
        _request(
            port,
            "/__deploy/cache",
            headers={"Authorization": "Bearer cache-control-token"},
        )[1]
    )


def _purge(port: int) -> dict[str, object]:
    return json.loads(
        _request(
            port,
            "/__deploy/cache/purge",
            method="POST",
            headers={"Authorization": "Bearer cache-control-token"},
        )[1]
    )


def _expect_error(port: int, path: str, status: int, *, method: str = "GET") -> None:
    try:
        _request(port, path, method=method)
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
