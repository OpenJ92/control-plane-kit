from __future__ import annotations

import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
from threading import Thread
import unittest

from fastapi.testclient import TestClient

from control_plane_kit import PackageServerProduct, ProductMaturity, Protocol
from control_plane_kit.servers.tcp_switch import (
    TcpSwitchMode,
    TcpSwitchSettings,
    TcpSwitchState,
    TcpTarget,
    create_tcp_switch_app,
    tcp_switch_block,
)


class _ReplyHandler(BaseRequestHandler):
    reply = b""

    def handle(self) -> None:
        payload = self.request.recv(65_536)
        self.request.sendall(self.reply + payload)


class TcpSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._servers: list[tuple[ThreadingTCPServer, Thread]] = []

    def tearDown(self) -> None:
        for server, thread in self._servers:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def _target(self, reply: bytes) -> str:
        handler = type("ReplyHandler", (_ReplyHandler,), {"reply": reply})
        server = ThreadingTCPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        return f"tcp://127.0.0.1:{server.server_address[1]}"

    @staticmethod
    def _exchange(port: int, payload: bytes = b"payload") -> bytes:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            return client.recv(65_536)

    def test_block_retains_exact_product_protocol_and_control_contract(self) -> None:
        block = tcp_switch_block(mode=TcpSwitchMode.ROUND_ROBIN)

        self.assertIs(block.spec.product, PackageServerProduct.TCP_SWITCH)
        self.assertIs(block.spec.maturity, ProductMaturity.TEST_ONLY)
        self.assertEqual(block.sockets.provider("data").protocol, Protocol.TCP)
        self.assertEqual(
            tuple(socket.protocol for socket in block.sockets.requirements),
            (Protocol.TCP, Protocol.TCP, Protocol.TCP),
        )
        self.assertEqual(block.implementation.ports, {"data": 7000, "control": 8080})
        self.assertNotIn(
            "CPK_CONTROL_TOKEN",
            {binding.name for binding in block.implementation.environment},
        )

    def test_target_parser_rejects_credentials_paths_and_wrong_protocol(self) -> None:
        self.assertEqual(TcpTarget.parse("tcp://database:5432"), TcpTarget("database", 5432))
        for value in (
            "http://database:5432",
            "tcp://user:secret@database:5432",
            "tcp://database:5432/query",
            "tcp://database",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TcpTarget.parse(value)

    def test_active_data_path_changes_only_after_authenticated_switch(self) -> None:
        blue = self._target(b"blue:")
        green = self._target(b"green:")
        app = create_tcp_switch_app(
            TcpSwitchSettings(
                "switch",
                {"blue": blue, "green": green},
                "blue",
                TcpSwitchMode.ACTIVE_TARGET,
                "control-token",
                data_port=0,
            )
        )

        with TestClient(app) as control:
            port = app.state.tcp_data_port
            self.assertEqual(self._exchange(port), b"blue:payload")
            unauthorized = control.post(
                "/__deploy/active-target", json={"target_id": "green"}
            )
            self.assertEqual(unauthorized.status_code, 401)
            self.assertNotIn("control-token", unauthorized.text)
            self.assertEqual(self._exchange(port), b"blue:payload")

            invalid = control.post(
                "/__deploy/targets",
                json={"green": "postgres://green:5432"},
                headers={
                    "Authorization": "Bearer control-token",
                    "X-Control-Plane-Request-Id": "request-invalid",
                    "Idempotency-Key": "targets-invalid",
                },
            )
            self.assertEqual(invalid.status_code, 400)
            self.assertNotIn("postgres://green:5432", invalid.text)
            self.assertEqual(self._exchange(port), b"blue:payload")

            switched = control.post(
                "/__deploy/active-target",
                json={"target_id": "green"},
                headers={
                    "Authorization": "Bearer control-token",
                    "X-Control-Plane-Request-Id": "request-1",
                    "Idempotency-Key": "switch-1",
                },
            )
            self.assertEqual(switched.status_code, 200)
            self.assertEqual(self._exchange(port), b"green:payload")

    def test_round_robin_selects_connections_without_inspecting_bytes(self) -> None:
        state = TcpSwitchState(
            "switch",
            targets={
                "a": "tcp://target-a:7000",
                "b": "tcp://target-b:7000",
            },
            active_target="a",
            mode=TcpSwitchMode.ROUND_ROBIN,
        )

        self.assertEqual(
            tuple(state.select_target() for _ in range(4)),
            (
                TcpTarget("target-a", 7000),
                TcpTarget("target-b", 7000),
                TcpTarget("target-a", 7000),
                TcpTarget("target-b", 7000),
            ),
        )

    def test_runtime_target_replacement_is_bounded_and_validated(self) -> None:
        state = TcpSwitchState(
            "switch",
            targets={"a": "tcp://target-a:7000"},
            active_target="a",
            max_targets=2,
        )

        with self.assertRaises(ValueError):
            state.replace_targets({"a": "postgres://target-a:5432"})
        with self.assertRaises(ValueError):
            state.replace_targets(
                {
                    "a": "tcp://target-a:7000",
                    "b": "tcp://target-b:7000",
                    "c": "tcp://target-c:7000",
                }
            )


if __name__ == "__main__":
    unittest.main()
