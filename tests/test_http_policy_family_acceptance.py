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
    DeploymentGraph,
    GraphDescriptorCodec,
    GraphValidationPolicy,
    LoadGeneratorPolicy,
    LoadMethod,
    LoadRunCommand,
    PackageServerSpec,
    PackageServerProduct,
    StartNode,
    SocketBinding,
    ValidationCode,
    WaitForHealthy,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from control_plane_kit.servers import (
    http_bulkhead_command,
    http_circuit_breaker_command,
    http_fault_injector_command,
    http_load_generator_command,
    http_multiplexer_command,
    http_rate_limiter_command,
    http_retry_command,
    http_timeout_command,
    http_traffic_logger_command,
    http_weighted_load_balancer_command,
    request_observer_command,
)
from examples.http_policy_family import http_policy_family_recipe


class HttpPolicyFamilyAcceptanceTests(unittest.TestCase):
    def test_recipe_contains_the_complete_typed_http_product_family(self) -> None:
        graph = compile_recipe(http_policy_family_recipe())
        validated = validate_graph(graph)
        products = {
            node.block_spec.product
            for node in graph.nodes.values()
            if isinstance(node.block_spec, PackageServerSpec)
        }

        self.assertTrue(validated.valid, validated.descriptor())
        self.assertEqual(
            products,
            set(PackageServerProduct)
            - {
                PackageServerProduct.SERVICE_DISCOVERY,
                PackageServerProduct.OPENTELEMETRY_COLLECTOR,
                PackageServerProduct.WEBHOOK_DELIVERY,
            },
        )
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph)
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)
        self.assertEqual(
            graph.node("entry-router").block_spec.verification.checks[0].path,
            "/probe",
        )

    def test_plan_derives_provider_health_before_consumer_start(self) -> None:
        graph = validate_graph(compile_recipe(http_policy_family_recipe()))
        plan = compile_activity_plan(
            diff_graphs(validate_graph(DeploymentGraph("empty")), graph)
        )
        starts = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, StartNode)
        }
        healthy = {
            activity.operation.target.node_id: activity
            for activity in plan.activities
            if isinstance(activity.operation, WaitForHealthy)
        }

        self.assertTrue(plan.ready_for_execution)
        self.assertEqual(set(starts), set(graph.graph.nodes))
        self.assertEqual(set(healthy), set(graph.graph.nodes))
        for edge in graph.graph.edges.values():
            requirement = graph.graph.node(edge.consumer_role).requirement_socket(
                edge.requirement_socket
            )
            if requirement.binding is not SocketBinding.ENVIRONMENT:
                continue
            with self.subTest(edge=edge.edge_id):
                self.assertIn(
                    healthy[edge.provider_role].activity_id,
                    {
                        dependency.predecessor
                        for dependency in starts[edge.consumer_role].dependencies
                    },
                )

    def test_production_policy_rejects_test_and_teaching_products(self) -> None:
        graph = compile_recipe(http_policy_family_recipe())
        result = validate_graph(graph, policy=GraphValidationPolicy.production())
        rejected = {
            finding.subject.node_id
            for finding in result.errors
            if finding.code is ValidationCode.PACKAGE_MATURITY
        }

        self.assertIn("fault-injector", rejected)
        self.assertIn("load-generator", rejected)
        self.assertIn("auth-gateway", rejected)
        self.assertIn("idempotency-gateway", rejected)
        self.assertNotIn("entry-router", rejected)

    def test_live_representative_chain_composes_real_http_servers(self) -> None:
        first = _TargetServer()
        second = _TargetServer()
        first.start()
        second.start()
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)

        observer_port = _free_port()
        observer = self._start(
            request_observer_command(port=observer_port),
            observer_port,
            CPK_CONTROL_TOKEN="observer-token",
        )
        multiplexer_port = _free_port()
        multiplexer = self._start(
            http_multiplexer_command(port=multiplexer_port),
            multiplexer_port,
            MULTIPLEXER_PRIMARY_URL=second.url,
            MULTIPLEXER_OBSERVER_A_URL=observer,
        )
        fault_port = _free_port()
        fault = self._start(
            http_fault_injector_command(port=fault_port),
            fault_port,
            FAULT_TARGET_URL=multiplexer,
            CPK_FAULT_CONTROL_TOKEN="fault-token",
        )
        bulkhead_port = _free_port()
        bulkhead = self._start(
            http_bulkhead_command(port=bulkhead_port),
            bulkhead_port,
            BULKHEAD_TARGET_URL=fault,
            CPK_CONTROL_TOKEN="bulkhead-token",
        )
        timeout_port = _free_port()
        timeout = self._start(
            http_timeout_command(port=timeout_port),
            timeout_port,
            TIMEOUT_TARGET_URL=bulkhead,
            CPK_CONTROL_TOKEN="timeout-token",
        )
        circuit_port = _free_port()
        circuit = self._start(
            http_circuit_breaker_command(port=circuit_port),
            circuit_port,
            CIRCUIT_TARGET_URL=first.url,
            CPK_CONTROL_TOKEN="circuit-token",
        )
        retry_port = _free_port()
        retry = self._start(
            http_retry_command(port=retry_port),
            retry_port,
            RETRY_TARGET_URL=circuit,
            CPK_CONTROL_TOKEN="retry-token",
        )
        logger_port = _free_port()
        logger = self._start(
            http_traffic_logger_command(port=logger_port),
            logger_port,
            LOGGER_TARGET_URL=retry,
            CPK_CONTROL_TOKEN="logger-token",
        )
        balancer_port = _free_port()
        balancer = self._start(
            http_weighted_load_balancer_command(port=balancer_port),
            balancer_port,
            BALANCER_TARGET_A_URL=logger,
            BALANCER_TARGET_B_URL=timeout,
        )
        limiter_port = _free_port()
        limiter = self._start(
            http_rate_limiter_command(port=limiter_port),
            limiter_port,
            RATE_LIMIT_TARGET_URL=balancer,
            RATE_LIMIT_REQUESTS="8",
        )
        result = self._run_load(limiter, request_count=12)

        self.assertEqual(result["evidence"]["succeeded"], 8)
        self.assertEqual(result["evidence"]["rejected"], 4)
        self.assertGreater(first.calls, 0)
        self.assertGreater(second.calls, 0)
        observer_evidence = _get_json(
            f"{observer}/__deploy/metrics",
            token="observer-token",
        )
        logger_evidence = _get_json(
            f"{logger}/__deploy/traffic-evidence?offset=0&limit=50",
            token="logger-token",
        )
        self.assertEqual(observer_evidence["count"], second.calls)
        self.assertEqual(logger_evidence["total"], first.calls)
        serialized = json.dumps((result, observer_evidence, logger_evidence))
        for secret in (
            "observer-token",
            "fault-token",
            "bulkhead-token",
            "timeout-token",
            "circuit-token",
            "retry-token",
            "logger-token",
        ):
            self.assertNotIn(secret, serialized)
        for address in (first.url, second.url, observer, logger):
            self.assertNotIn(address, serialized)

    def _start(
        self,
        command: tuple[str, ...],
        port: int,
        **values: str,
    ) -> str:
        environment = dict(os.environ)
        environment.update(values)
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(_stop_process, process)
        _wait_listening(process, port)
        return f"http://127.0.0.1:{port}"

    def _run_load(self, target_url: str, *, request_count: int) -> dict[str, object]:
        port = _free_port()
        policy = LoadGeneratorPolicy(
            ("/probe",),
            max_requests=request_count,
            max_concurrency=4,
            max_requests_per_second=100,
            max_duration_ms=5_000,
            max_response_bytes=1_024,
            max_retained_runs=1,
        )
        environment = dict(os.environ)
        environment.update(
            {
                "LOAD_TARGET_URL": target_url,
                "CPK_LOAD_CONTROL_TOKEN": "load-token",
                "CPK_LOAD_CONTROL_PORT": str(port),
                "CPK_TEST_ONLY": "1",
            }
        )
        process = subprocess.Popen(
            http_load_generator_command(policy),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(_stop_process, process)
        _wait_listening(process, port)
        command = LoadRunCommand(
            "http-family-live",
            LoadMethod.GET,
            "/probe",
            request_count,
            4,
            100,
            5_000,
            1_000,
        )
        status, _ = _post_json(
            f"http://127.0.0.1:{port}/__deploy/load-runs",
            command.descriptor(),
        )
        self.assertEqual(status, 401)
        status, _ = _post_json(
            f"http://127.0.0.1:{port}/__deploy/load-runs",
            command.descriptor(),
            token="load-token",
        )
        self.assertEqual(status, 202)
        for _ in range(500):
            result = _get_json(
                f"http://127.0.0.1:{port}/__deploy/load-runs/{command.run_id}",
                token="load-token",
            )
            if result["status"] != "running":
                return result
            time.sleep(0.01)
        raise AssertionError("HTTP family load run did not settle")


class _TargetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        owner = self.server.owner  # type: ignore[attr-defined]
        owner.record()
        body = b"ok"
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TargetServer:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def record(self) -> None:
        with self._lock:
            self.calls += 1

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


def _wait_listening(process: subprocess.Popen[bytes], port: int) -> None:
    for _ in range(500):
        if process.poll() is not None:
            stderr = b"" if process.stderr is None else process.stderr.read()
            raise AssertionError(
                f"server exited before readiness ({process.returncode}): "
                f"{stderr.decode(errors='replace')}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"server did not listen on port {port}")


def _post_json(
    url: str,
    value: dict[str, object],
    *,
    token: str | None = None,
) -> tuple[int, dict[str, object] | None]:
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(value).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        with error:
            error.read()
            return error.code, None


def _get_json(url: str, *, token: str) -> dict[str, object]:
    request = Request(
        url,
        headers={"authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=2) as response:
        return json.loads(response.read())


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        if process.stderr is not None:
            process.stderr.close()
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    if process.stderr is not None:
        process.stderr.close()


if __name__ == "__main__":
    unittest.main()
