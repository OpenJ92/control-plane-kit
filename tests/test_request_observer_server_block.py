from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from control_plane_kit import (
    CapabilityName,
    DockerRuntime,
    DeploymentRecipe,
    GraphDescriptorCodec,
    PackageServerProduct,
    SecretEnvironmentDelivery,
    compile_recipe,
    request_observer_block,
)
from control_plane_kit.servers import (
    HttpRequest,
    RequestObserverServer,
    http_multiplexer_command,
    request_observer_command,
)


class RequestObserverServerTests(unittest.TestCase):
    def test_behavior_retains_only_count_and_generated_identity(self) -> None:
        server = RequestObserverServer(max_request_bytes=8)
        request = HttpRequest(
            method="POST",
            path="/private/path",
            query="token=must-not-retain",
            headers={
                "authorization": "Bearer must-not-retain",
                "cookie": "session=must-not-retain",
                "x-correlation-id": "caller-controlled",
            },
            body=b"private",
        )

        response = server.handle(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.headers["x-cpk-correlation-id"],
            "observation-00000000000000000001",
        )
        descriptor = server.observation().descriptor()
        self.assertEqual(
            descriptor,
            {
                "count": 1,
                "latest_correlation_id": "observation-00000000000000000001",
            },
        )
        self.assertNotIn("must-not-retain", json.dumps(descriptor))
        self.assertNotIn("caller-controlled", json.dumps(descriptor))

    def test_oversized_request_is_rejected_without_observation(self) -> None:
        server = RequestObserverServer(max_request_bytes=4)

        response = server.handle(HttpRequest(method="POST", body=b"12345"))

        self.assertEqual(response.status_code, 413)
        self.assertEqual(server.observation().descriptor(), {
            "count": 0,
            "latest_correlation_id": None,
        })

    def test_block_is_a_closed_docker_product_with_opaque_control_secret(self) -> None:
        block = request_observer_block(control_secret_reference="secret://test/observer")
        graph = compile_recipe(
            DeploymentRecipe(
                "observer",
                DockerRuntime(network_name="observer-network", children=(block,)),
            )
        )
        descriptor = GraphDescriptorCodec().encode(graph)

        self.assertIs(block.spec.product, PackageServerProduct.REQUEST_OBSERVER)
        self.assertEqual(
            block.spec.capabilities,
            (CapabilityName.HEALTH_CHECKABLE, CapabilityName.METRICS_READABLE),
        )
        self.assertEqual(block.sockets.provider_names(), ("internal",))
        self.assertEqual(block.sockets.requirement_names(), ())
        self.assertIsInstance(
            block.implementation.secret_deliveries[0],
            SecretEnvironmentDelivery,
        )
        encoded = json.dumps(descriptor, sort_keys=True)
        self.assertIn("secret://test/observer", encoded)
        self.assertNotIn("control-token-value", encoded)
        self.assertIs(
            GraphDescriptorCodec().decode(descriptor).node(block.block_id).block_spec.product,
            PackageServerProduct.REQUEST_OBSERVER,
        )

    def test_live_command_authenticates_metrics_and_redacts_traffic(self) -> None:
        port = _free_port()
        command = request_observer_command(max_request_bytes=8, port=port)
        environment = dict(os.environ)
        environment["CPK_CONTROL_TOKEN"] = "observer-control-token"
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop, process)
        _wait_ready(port)

        observed = _request(
            port,
            "/copied?token=must-not-retain",
            method="POST",
            body=b"private",
            headers={"Authorization": "Bearer request-secret"},
        )
        self.assertEqual(observed[0], 202)
        self.assertEqual(
            observed[1]["correlation_id"],
            "observation-00000000000000000001",
        )

        with self.assertRaises(HTTPError) as unauthorized:
            _request(port, "/__deploy/metrics")
        with unauthorized.exception:
            self.assertEqual(unauthorized.exception.code, 401)

        status, metrics = _request(
            port,
            "/__deploy/metrics",
            headers={"Authorization": "Bearer observer-control-token"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(metrics, {
            "count": 1,
            "latest_correlation_id": "observation-00000000000000000001",
        })
        rendered = json.dumps(metrics)
        self.assertNotIn("must-not-retain", rendered)
        self.assertNotIn("request-secret", rendered)

        with self.assertRaises(HTTPError) as oversized:
            _request(port, "/copied", method="POST", body=b"123456789")
        with oversized.exception:
            self.assertEqual(oversized.exception.code, 413)
        _, after = _request(
            port,
            "/__deploy/metrics",
            headers={"X-Control-Plane-Token": "observer-control-token"},
        )
        self.assertEqual(after["count"], 1)

    def test_live_multiplexer_copies_traffic_to_observer(self) -> None:
        primary_port, observer_port = _free_ports(2)
        multiplexer_port = 8080
        observer_environment = dict(os.environ)
        observer_environment["CPK_CONTROL_TOKEN"] = "observer-control-token"
        multiplexer_environment = dict(os.environ)
        multiplexer_environment["MULTIPLEXER_PRIMARY_URL"] = (
            f"http://127.0.0.1:{primary_port}"
        )
        multiplexer_environment["MULTIPLEXER_OBSERVER_A_URL"] = (
            f"http://127.0.0.1:{observer_port}"
        )
        primary = subprocess.Popen(
            ("python", "-m", "http.server", str(primary_port)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        observer = subprocess.Popen(
            request_observer_command(port=observer_port),
            env=observer_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        multiplexer = subprocess.Popen(
            http_multiplexer_command(),
            env=multiplexer_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop, multiplexer)
        self.addCleanup(_stop, observer)
        self.addCleanup(_stop, primary)
        _wait_listening(primary_port)
        _wait_ready(observer_port)
        _wait_listening(multiplexer_port)

        request = Request(f"http://127.0.0.1:{multiplexer_port}/", method="GET")
        with urlopen(request, timeout=2) as response:
            status = response.status
            response.read()

        self.assertEqual(status, 200)
        _, metrics = _request(
            observer_port,
            "/__deploy/metrics",
            headers={"Authorization": "Bearer observer-control-token"},
        )
        self.assertEqual(metrics, {
            "count": 1,
            "latest_correlation_id": "observation-00000000000000000001",
        })


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _free_ports(count: int) -> tuple[int, ...]:
    listeners = [socket.socket() for _ in range(count)]
    try:
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        return tuple(listener.getsockname()[1] for listener in listeners)
    finally:
        for listener in listeners:
            listener.close()


def _wait_ready(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            status, _ = _request(port, "/health")
            if status == 200:
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("request observer did not become ready")


def _wait_listening(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("server did not begin listening")


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
        return response.status, json.loads(response.read())


def _stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
