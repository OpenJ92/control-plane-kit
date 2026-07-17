from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest

from fastapi.testclient import TestClient

from control_plane_kit.servers.managed_http_router import (
    ManagedRouterSettings,
    create_managed_http_router_app,
    managed_http_router_block,
)


class _Handler(BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format, *args):
        return


class ManagedHttpRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.servers = []

    def tearDown(self) -> None:
        for server, thread in self.servers:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def _server(self, body: bytes) -> str:
        handler = type("Handler", (_Handler,), {"body": body})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}"

    def test_same_data_path_changes_only_after_authenticated_switch(self) -> None:
        app = create_managed_http_router_app(
            ManagedRouterSettings(
                "router",
                {
                    "hello-blue": self._server(b"Hello, blue!"),
                    "hello-green": self._server(b"Hello, green!"),
                },
                "hello-blue",
                "synthetic-token",
            )
        )
        client = TestClient(app)

        self.assertEqual(client.get("/").text, "Hello, blue!")
        self.assertEqual(
            client.post(
                "/__deploy/active-target",
                json={"target_id": "hello-green"},
            ).status_code,
            401,
        )
        switched = client.post(
            "/__deploy/active-target",
            json={"target_id": "hello-green"},
            headers={
                "Authorization": "Bearer synthetic-token",
                "X-Control-Plane-Request-Id": "request-1",
                "Idempotency-Key": "switch-1",
            },
        )
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(client.get("/").text, "Hello, green!")

    def test_block_declares_graph_wired_targets_and_opaque_control_secret(self) -> None:
        block = managed_http_router_block()

        self.assertEqual(
            block.sockets.requirement_names(),
            ("target-blue", "target-green", "active"),
        )
        self.assertEqual(block.sockets.provider_names(), ("internal",))


if __name__ == "__main__":
    unittest.main()
