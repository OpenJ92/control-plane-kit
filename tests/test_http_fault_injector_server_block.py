from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import unittest
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    GraphDescriptorCodec,
    GraphValidationPolicy,
    PackageServerProduct,
    PackageServerSpec,
    SocketConnection,
    compile_recipe,
    validate_graph,
)
from control_plane_kit.servers import (
    CircuitBreakerPolicy,
    CircuitBreakerState,
    ConnectionTerminationFault,
    DelayFault,
    DisabledFaultInjection,
    EnabledFaultInjection,
    FaultInjectionLimits,
    FaultKind,
    HttpCircuitBreakerServer,
    HttpFaultInjectionServer,
    HttpRequest,
    HttpResponse,
    HttpRetryServer,
    InjectedConnectionTermination,
    InjectedHttpStatus,
    ProductMaturity,
    RetryPolicy,
    SeededProbabilityFault,
    StatusFault,
    TargetOutcome,
    TruncationFault,
    fault_injection_state_descriptor,
    fault_injection_state_from_descriptor,
    hello_server_block,
    http_fault_injector_block,
    http_fault_injector_command,
)


class HttpFaultInjectionTests(unittest.TestCase):
    def test_disabled_default_and_injected_and_natural_evidence_are_independent(self) -> None:
        statuses = iter((200, 503))
        sleeps: list[float] = []

        def target(_request: HttpRequest) -> HttpResponse:
            return HttpResponse.text("target", status_code=next(statuses))

        server = HttpFaultInjectionServer(
            {"target": target},
            "target",
            sleeper=sleeps.append,
        )
        self.assertEqual(server.handle(HttpRequest()).status_code, 200)
        self.assertEqual(
            fault_injection_state_descriptor(server.observation().active),
            {"variant": "disabled"},
        )

        server.replace_activation(EnabledFaultInjection(DelayFault(25)))
        self.assertEqual(server.handle(HttpRequest()).status_code, 503)
        observation = server.observation()
        self.assertEqual(sleeps, [0.025])
        self.assertIs(observation.latest_injection, FaultKind.DELAY)
        self.assertIs(observation.latest_target_outcome, TargetOutcome.HTTP_FAILURE)
        self.assertEqual(observation.injected_count, 1)
        self.assertEqual(observation.natural_failure_count, 1)

    def test_closed_fault_variants_have_exact_behavior(self) -> None:
        server = HttpFaultInjectionServer(
            {"target": lambda _: HttpResponse.text("abcdef")},
            "target",
        )
        server.replace_activation(
            EnabledFaultInjection(
                StatusFault(InjectedHttpStatus.SERVICE_UNAVAILABLE)
            )
        )
        self.assertEqual(server.handle(HttpRequest()).status_code, 503)
        self.assertIs(
            server.observation().latest_target_outcome,
            TargetOutcome.NOT_ATTEMPTED,
        )

        server.replace_activation(EnabledFaultInjection(TruncationFault(3)))
        truncated = server.handle(HttpRequest())
        self.assertEqual(truncated.body, b"abc")
        self.assertEqual(truncated.headers["content-length"], "3")

        server.replace_activation(
            EnabledFaultInjection(ConnectionTerminationFault())
        )
        with self.assertRaises(InjectedConnectionTermination):
            server.handle(HttpRequest())

    def test_seeded_probability_replays_exact_selection_sequence(self) -> None:
        policy = EnabledFaultInjection(
            SeededProbabilityFault(
                probability_basis_points=5_000,
                seed=12345,
                status=InjectedHttpStatus.BAD_GATEWAY,
            )
        )

        def run() -> list[int]:
            server = HttpFaultInjectionServer(
                {"target": lambda _: HttpResponse.text("ok")},
                "target",
            )
            server.replace_activation(policy)
            return [server.handle(HttpRequest()).status_code for _ in range(16)]

        first = run()
        second = run()
        self.assertEqual(first, second)
        self.assertIn(200, first)
        self.assertIn(502, first)

    def test_fault_descriptor_language_is_exact_closed_and_bounded(self) -> None:
        values = (
            DisabledFaultInjection(),
            EnabledFaultInjection(DelayFault(10)),
            EnabledFaultInjection(
                StatusFault(InjectedHttpStatus.INTERNAL_SERVER_ERROR)
            ),
            EnabledFaultInjection(ConnectionTerminationFault()),
            EnabledFaultInjection(TruncationFault(4)),
            EnabledFaultInjection(
                SeededProbabilityFault(2_500, 91, InjectedHttpStatus.GATEWAY_TIMEOUT)
            ),
        )
        for value in values:
            with self.subTest(value=value):
                descriptor = fault_injection_state_descriptor(value)
                self.assertEqual(
                    fault_injection_state_descriptor(
                        fault_injection_state_from_descriptor(descriptor)
                    ),
                    descriptor,
                )

        with self.assertRaisesRegex(ValueError, "keys must be exactly"):
            fault_injection_state_from_descriptor(
                {"variant": "disabled", "enabled": False}
            )
        with self.assertRaisesRegex(ValueError, "fault probability"):
            SeededProbabilityFault(0, 1)
        with self.assertRaisesRegex(ValueError, "fault retained bytes"):
            TruncationFault(1_048_577)
        with self.assertRaisesRegex(TypeError, "injected status"):
            StatusFault("503")  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "maturity must be typed"):
            PackageServerSpec(
                role_id="invalid",
                product=PackageServerProduct.HTTP_FAULT_INJECTOR,
                maturity="test-only",  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ValueError, "allow at least one"):
            GraphValidationPolicy(())

    def test_graph_composition_drives_retry_and_circuit_interactions(self) -> None:
        fault = HttpFaultInjectionServer(
            {"target": lambda _: HttpResponse.text("ok")},
            "target",
        )
        fault.replace_activation(
            EnabledFaultInjection(
                StatusFault(InjectedHttpStatus.SERVICE_UNAVAILABLE)
            )
        )
        retry = HttpRetryServer(
            {"fault": fault.handle},
            "fault",
            RetryPolicy(attempts=3),
        )
        circuit = HttpCircuitBreakerServer(
            {"retry": retry.handle},
            "retry",
            CircuitBreakerPolicy(failure_threshold=1),
        )

        self.assertEqual(circuit.handle(HttpRequest()).status_code, 503)
        self.assertEqual(retry.observation().attempt_count, 3)
        self.assertEqual(fault.observation().injected_count, 3)
        self.assertIs(circuit.state, CircuitBreakerState.OPEN)

    def test_block_maturity_survives_graph_codec_and_production_policy_rejects_it(self) -> None:
        block = http_fault_injector_block()
        target = hello_server_block(block_id="fault-target")
        graph = compile_recipe(
            DeploymentRecipe(
                "fault-test",
                DockerRuntime(
                    children=(
                        target,
                        block,
                        SocketConnection(
                            "fault-target",
                            "internal",
                            "http-fault-injector",
                            "target",
                        ),
                    )
                ),
            )
        )
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph)
        restored = codec.decode(descriptor)

        self.assertIs(block.spec.product, PackageServerProduct.HTTP_FAULT_INJECTOR)
        self.assertIs(block.spec.maturity, ProductMaturity.TEST_ONLY)
        self.assertEqual(
            descriptor["nodes"][block.block_id]["block_spec"]["maturity"],
            "test-only",
        )
        self.assertIs(
            restored.node(block.block_id).block_spec.maturity,
            ProductMaturity.TEST_ONLY,
        )
        self.assertTrue(validate_graph(graph).valid)
        production = validate_graph(
            graph,
            policy=GraphValidationPolicy.production(),
        )
        self.assertFalse(production.valid)
        self.assertEqual(
            {finding.code.value for finding in production.errors},
            {"package-maturity"},
        )
        self.assertEqual(
            {
                finding.subject.node_id
                for finding in production.errors
            },
            {"fault-target", "http-fault-injector"},
        )

    def test_live_generated_server_mutates_only_with_auth_and_cleans_up(self) -> None:
        target = _TargetServer()
        target.start()
        self.addCleanup(target.stop)
        port = _free_port()
        environment = dict(os.environ)
        environment["FAULT_TARGET_URL"] = f"http://127.0.0.1:{target.port}"
        environment["CPK_FAULT_CONTROL_TOKEN"] = "fault-control-token"
        process = subprocess.Popen(
            http_fault_injector_command(FaultInjectionLimits(), port=port),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, process)
        _wait_ready(port)

        self.assertEqual(_request(port, "/")[0], 200)
        self.assertEqual(_fault_state(port)["active"], {"variant": "disabled"})
        _expect_error(
            port,
            "/__deploy/fault",
            401,
            method="POST",
            payload={"variant": "disabled"},
        )

        status_policy = {
            "variant": "enabled",
            "fault": {"kind": "status", "status": "503"},
        }
        self.assertEqual(_set_fault(port, status_policy)["active"], status_policy)
        before = target.calls
        _expect_error(port, "/", 503)
        self.assertEqual(target.calls, before)
        state = _fault_state(port)
        self.assertEqual(state["latest_injection"], "status")
        self.assertEqual(state["latest_target_outcome"], "not-attempted")

        target.status = 503
        _set_fault(port, {"variant": "disabled"})
        _expect_error(port, "/", 503)
        state = _fault_state(port)
        self.assertIsNone(state["latest_injection"])
        self.assertEqual(state["latest_target_outcome"], "http-failure")

        target.status = 200
        target.body = b"abcdef"
        _set_fault(
            port,
            {
                "variant": "enabled",
                "fault": {"kind": "truncation", "retained_bytes": 2},
            },
        )
        self.assertEqual(_request(port, "/")[1], b"ab")

        _set_fault(
            port,
            {
                "variant": "enabled",
                "fault": {"kind": "connection-termination"},
            },
        )
        with self.assertRaises((RemoteDisconnected, ConnectionResetError)):
            _request(port, "/")

        sequence_policy = {
            "variant": "enabled",
            "fault": {
                "kind": "seeded-probability",
                "probability_basis_points": 5_000,
                "seed": 27,
                "status": "502",
            },
        }
        first = _status_sequence(port, sequence_policy)
        second = _status_sequence(port, sequence_policy)
        self.assertEqual(first, second)
        self.assertIn(200, first)
        self.assertIn(502, first)


class _TargetHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        with owner.lock:
            owner.calls += 1
            status = owner.status
            body = owner.body
        self.send_response(status)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _respond
    do_POST = _respond

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self.calls = 0
        self.status = 200
        self.body = b"target"
        self.lock = threading.Lock()
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
    raise RuntimeError("fault injector did not become ready")


def _request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    body = None if payload is None else json.dumps(payload).encode()
    request_headers = {} if headers is None else dict(headers)
    if body is not None:
        request_headers["content-type"] = "application/json"
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    with urlopen(request, timeout=2) as response:
        return response.status, response.read()


def _fault_state(port: int) -> dict[str, object]:
    return json.loads(
        _request(
            port,
            "/__deploy/fault",
            headers={"Authorization": "Bearer fault-control-token"},
        )[1]
    )


def _set_fault(port: int, payload: dict[str, object]) -> dict[str, object]:
    return json.loads(
        _request(
            port,
            "/__deploy/fault",
            method="POST",
            payload=payload,
            headers={"Authorization": "Bearer fault-control-token"},
        )[1]
    )


def _status_sequence(port: int, payload: dict[str, object]) -> list[int]:
    _set_fault(port, payload)
    result: list[int] = []
    for _ in range(12):
        try:
            result.append(_request(port, "/")[0])
        except HTTPError as error:
            with error:
                error.read()
                result.append(error.code)
    return result


def _expect_error(
    port: int,
    path: str,
    status: int,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> None:
    try:
        _request(port, path, method=method, payload=payload)
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
