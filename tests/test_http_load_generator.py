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

from fastapi.testclient import TestClient

from control_plane_kit import (
    ApplicationBlock,
    DeploymentRecipe,
    DockerRuntime,
    GraphDescriptorCodec,
    LoadGeneratorPolicy,
    LoadMethod,
    LoadRunCommand,
    LoadRunStatus,
    PackageServerProduct,
    SocketConnection,
    ValidationCode,
    compile_recipe,
    load_generator_policy_from_descriptor,
    load_run_command_from_descriptor,
    scheduled_offsets_ms,
    validate_graph,
)
from control_plane_kit.servers import (
    ProductMaturity,
    create_load_generator_app,
    http_load_generator_block,
)
from control_plane_kit.servers import (
    HttpLoadGeneratorServer,
    HttpRequest,
    HttpResponse,
    LoadRunConflict,
    LoadGeneratorCapacityExhausted,
    hello_server_block,
    http_rate_limiter_command,
    http_weighted_load_balancer_command,
)


class HttpLoadGeneratorTests(unittest.TestCase):
    def test_policy_command_codec_and_schedule_are_closed_and_deterministic(self) -> None:
        policy = _policy()
        command = _command(request_count=6, requests_per_second=4, duration_ms=1_000)

        self.assertEqual(load_generator_policy_from_descriptor(policy.descriptor()), policy)
        self.assertEqual(load_run_command_from_descriptor(command.descriptor()), command)
        self.assertEqual(scheduled_offsets_ms(command), (0, 250, 500, 750))
        self.assertEqual(command.fingerprint, load_run_command_from_descriptor(command.descriptor()).fingerprint)
        with self.assertRaisesRegex(ValueError, "unknown or missing"):
            load_run_command_from_descriptor({**command.descriptor(), "target_url": "http://example.test"})
        with self.assertRaisesRegex(ValueError, "non-control"):
            LoadRunCommand("run", LoadMethod.GET, "/__deploy/status", 1, 1, 1, 1_000, 100)

    def test_exact_trigger_replays_and_changed_intent_conflicts(self) -> None:
        calls: list[HttpRequest] = []

        def target(request: HttpRequest, _timeout: int, _maximum: int) -> HttpResponse:
            calls.append(request)
            return HttpResponse(204)

        server = HttpLoadGeneratorServer(_policy(), target)
        self.addCleanup(server.shutdown)
        command = _command(request_count=1)
        first, replayed = server.trigger(command)
        settled = server.wait(command.run_id)
        replay, was_replayed = server.trigger(command)

        self.assertFalse(replayed)
        self.assertIs(first.status, LoadRunStatus.RUNNING)
        self.assertTrue(was_replayed)
        self.assertEqual(replay, settled)
        self.assertEqual(settled.evidence.succeeded, 1)
        self.assertEqual(calls, [HttpRequest("GET", "/probe")])
        with self.assertRaises(LoadRunConflict):
            server.trigger(_command(request_count=2))

    def test_injected_clock_executes_the_pure_deterministic_schedule(self) -> None:
        clock = _FakeClock()
        dispatch_times: list[float] = []

        def target(_request: HttpRequest, _timeout: int, _maximum: int) -> HttpResponse:
            dispatch_times.append(clock())
            return HttpResponse(200)

        server = HttpLoadGeneratorServer(_policy(), target, clock=clock, sleeper=clock.sleep)
        self.addCleanup(server.shutdown)
        command = _command(request_count=3, requests_per_second=2)
        server.trigger(command)
        record = server.wait(command.run_id)

        self.assertIs(record.status, LoadRunStatus.COMPLETED)
        self.assertEqual(dispatch_times, [0.0, 0.5, 1.0])

    def test_outcomes_are_aggregate_only_and_concurrency_is_bounded(self) -> None:
        outcomes = [200, 429, TimeoutError(), 503]
        in_flight = 0
        maximum_in_flight = 0
        lock = threading.Lock()

        def target(_request: HttpRequest, _timeout: int, _maximum: int) -> HttpResponse:
            nonlocal in_flight, maximum_in_flight
            with lock:
                in_flight += 1
                maximum_in_flight = max(maximum_in_flight, in_flight)
                item = outcomes.pop(0)
            try:
                if isinstance(item, Exception):
                    raise item
                return HttpResponse(item, {"set-cookie": "secret"}, b"unretained-body")
            finally:
                with lock:
                    in_flight -= 1

        server = HttpLoadGeneratorServer(_policy(), target)
        self.addCleanup(server.shutdown)
        command = _command(request_count=4, concurrency=2)
        server.trigger(command)
        record = server.wait(command.run_id)

        self.assertIs(record.status, LoadRunStatus.COMPLETED)
        self.assertLessEqual(maximum_in_flight, 2)
        self.assertEqual(
            record.evidence.descriptor(),
            {
                "planned": 4,
                "dispatched": 4,
                "succeeded": 1,
                "rejected": 1,
                "timed_out": 1,
                "failed": 1,
                "cancelled_before_dispatch": 0,
                "deadline_skipped": 0,
            },
        )
        evidence = json.dumps(record.descriptor())
        self.assertNotIn("unretained-body", evidence)
        self.assertNotIn("set-cookie", evidence)

    def test_startup_policy_rejects_excessive_or_overlapping_runs_before_dispatch(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def target(_request: HttpRequest, _timeout: int, _maximum: int) -> HttpResponse:
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(1)
            return HttpResponse(200)

        server = HttpLoadGeneratorServer(_policy(), target)
        self.addCleanup(server.shutdown)
        with self.assertRaisesRegex(ValueError, "request count"):
            server.trigger(_command(request_count=201))
        with self.assertRaisesRegex(ValueError, "timeout"):
            server.trigger(
                LoadRunCommand("too-short", LoadMethod.GET, "/probe", 1, 1, 1, 100, 500)
            )
        self.assertEqual(calls, 0)

        server.trigger(_command(run_id="active", request_count=10, requests_per_second=10))
        self.assertTrue(entered.wait(1))
        with self.assertRaisesRegex(LoadGeneratorCapacityExhausted, "already active"):
            server.trigger(_command(run_id="overlap", request_count=1))
        release.set()
        server.cancel("active")
        server.wait("active")

    def test_cancellation_stops_future_dispatch_and_shutdown_settles_workers(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def target(_request: HttpRequest, _timeout: int, _maximum: int) -> HttpResponse:
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(1)
            return HttpResponse(200)

        server = HttpLoadGeneratorServer(_policy(), target)
        command = _command(request_count=100, concurrency=1, requests_per_second=100)
        server.trigger(command)
        self.assertTrue(entered.wait(1))
        server.cancel(command.run_id)
        release.set()
        record = server.wait(command.run_id)
        server.shutdown()

        self.assertIs(record.status, LoadRunStatus.CANCELLED)
        self.assertEqual(calls, 1)
        self.assertEqual(record.evidence.dispatched, 1)
        self.assertEqual(record.evidence.cancelled_before_dispatch, 99)
        self.assertFalse(any(thread.name.startswith("cpk-load-") for thread in threading.enumerate()))

    def test_fastapi_control_routes_fail_closed_and_replay_exact_command(self) -> None:
        calls = 0

        def target(_request: HttpRequest, _timeout: int, _maximum: int) -> HttpResponse:
            nonlocal calls
            calls += 1
            return HttpResponse(200)

        server = HttpLoadGeneratorServer(_policy(), target)
        self.addCleanup(server.shutdown)
        app = create_load_generator_app(server, control_token="load-secret", test_only=True)
        client = TestClient(app)
        descriptor = _command(request_count=1).descriptor()
        headers = {"Authorization": "Bearer load-secret"}

        self.assertEqual(client.post("/__deploy/load-runs", json=descriptor).status_code, 401)
        first = client.post("/__deploy/load-runs", json=descriptor, headers=headers)
        self.assertEqual(first.status_code, 202)
        server.wait("run-a")
        replay = client.post("/__deploy/load-runs", json=descriptor, headers=headers)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(calls, 1)
        self.assertEqual(client.get("/__deploy/load-runs/run-a").status_code, 401)
        self.assertEqual(client.get("/__deploy/load-runs/run-a", headers=headers).status_code, 200)
        with self.assertRaisesRegex(ValueError, "production-mode"):
            create_load_generator_app(server, control_token="load-secret", test_only=False)

    def test_block_is_test_only_application_with_graph_wired_target(self) -> None:
        block = http_load_generator_block(policy=_policy())
        self.assertIsInstance(block, ApplicationBlock)
        self.assertIs(block.spec.product, PackageServerProduct.HTTP_LOAD_GENERATOR)
        self.assertIs(block.spec.maturity, ProductMaturity.TEST_ONLY)
        self.assertEqual(block.sockets.requirement_names(), ("target",))
        self.assertEqual(block.sockets.provider_names(), ("control",))
        self.assertNotIn("load-secret", " ".join(block.implementation.command))

        recipe = DeploymentRecipe(
            "load-generator",
            DockerRuntime(children=(
                hello_server_block("target"),
                block,
                SocketConnection("target", "internal", block.block_id, "target"),
            )),
        )
        graph = compile_recipe(recipe)
        self.assertEqual(
            graph.node(block.block_id).non_secret_environment()["LOAD_TARGET_URL"],
            graph.node("target").endpoint("internal").url,
        )
        descriptor = GraphDescriptorCodec().encode(graph)
        self.assertEqual(GraphDescriptorCodec().decode(descriptor), graph)

        recursive = compile_recipe(
            DeploymentRecipe(
                "recursive-load-generator",
                DockerRuntime(children=(
                    block,
                    SocketConnection(block.block_id, "control", block.block_id, "target"),
                )),
            )
        )
        self.assertIn(
            ValidationCode.SELF_CONNECTION,
            tuple(finding.code for finding in validate_graph(recursive).errors),
        )

    def test_live_process_drives_rate_limiter_and_weighted_balancer(self) -> None:
        first = _CountingTarget()
        second = _CountingTarget()
        first.start()
        second.start()
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)

        balancer_port = _free_port()
        balancer_environment = dict(os.environ)
        balancer_environment.update({
            "BALANCER_TARGET_A_URL": first.url,
            "BALANCER_TARGET_B_URL": second.url,
        })
        balancer = subprocess.Popen(
            http_weighted_load_balancer_command(port=balancer_port),
            env=balancer_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, balancer)
        _wait_http(f"http://127.0.0.1:{balancer_port}/probe")
        result = self._run_live_load(f"http://127.0.0.1:{balancer_port}", request_count=6)
        self.assertEqual(result["evidence"]["succeeded"], 6)
        self.assertEqual((first.calls, second.calls), (4, 3))
        _stop_process(balancer)

        limiter_port = _free_port()
        limiter_environment = dict(os.environ)
        limiter_environment.update({
            "RATE_LIMIT_TARGET_URL": first.url,
            "RATE_LIMIT_REQUESTS": "2",
        })
        limiter = subprocess.Popen(
            http_rate_limiter_command(port=limiter_port),
            env=limiter_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(_stop_process, limiter)
        self._run_live_load(f"http://127.0.0.1:{limiter_port}", request_count=5, expected=(2, 3))

    def _run_live_load(
        self,
        target_url: str,
        *,
        request_count: int,
        expected: tuple[int, int] | None = None,
    ) -> dict[str, object]:
        port = _free_port()
        policy = _policy().descriptor()
        environment = dict(os.environ)
        environment.update({
            "LOAD_TARGET_URL": target_url,
            "CPK_LOAD_CONTROL_TOKEN": "live-load-token",
            "CPK_LOAD_CONTROL_PORT": str(port),
            "CPK_TEST_ONLY": "1",
        })
        process = subprocess.Popen(
            ("python", "-m", "control_plane_kit.load_generator_server.main", json.dumps(policy)),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(_stop_process, process)
        _wait_process_http(process, f"http://127.0.0.1:{port}/health")
        command = _command(run_id=f"live-{port}", request_count=request_count).descriptor()
        headers = {"Authorization": "Bearer live-load-token", "Content-Type": "application/json"}
        unauthorized = _http_json(f"http://127.0.0.1:{port}/__deploy/load-runs", command)
        self.assertEqual(unauthorized[0], 401)
        accepted = _http_json(
            f"http://127.0.0.1:{port}/__deploy/load-runs", command, headers=headers
        )
        self.assertEqual(accepted[0], 202)
        result = _wait_run(port, command["run_id"], headers)
        if expected is not None:
            self.assertEqual(
                (result["evidence"]["succeeded"], result["evidence"]["rejected"]),
                expected,
            )
        evidence = json.dumps(result)
        self.assertNotIn(target_url, evidence)
        self.assertNotIn("live-load-token", evidence)
        _stop_process(process)
        return result


def _policy() -> LoadGeneratorPolicy:
    return LoadGeneratorPolicy(
        ("/probe",),
        max_requests=200,
        max_concurrency=8,
        max_requests_per_second=200,
        max_duration_ms=5_000,
        max_response_bytes=1_024,
        max_retained_runs=4,
    )


def _command(
    *,
    run_id: str = "run-a",
    request_count: int = 4,
    concurrency: int = 1,
    requests_per_second: int = 100,
    duration_ms: int = 2_000,
) -> LoadRunCommand:
    return LoadRunCommand(
        run_id,
        LoadMethod.GET,
        "/probe",
        request_count,
        concurrency,
        requests_per_second,
        duration_ms,
        500,
    )


class _TargetHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.owner.calls += 1  # type: ignore[attr-defined]
        body = b"ok"
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _CountingTarget:
    def __init__(self) -> None:
        self.calls = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
        self.server.owner = self  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.value

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_http(url: str) -> None:
    for _ in range(100):
        try:
            with urlopen(url, timeout=0.2) as response:
                response.read()
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"server did not become ready: {url}")


def _wait_process_http(process: subprocess.Popen[bytes], url: str) -> None:
    for _ in range(250):
        if process.poll() is not None:
            stderr = b"" if process.stderr is None else process.stderr.read()
            raise AssertionError(
                f"server exited before readiness ({process.returncode}): {stderr.decode(errors='replace')}"
            )
        try:
            with urlopen(url, timeout=0.2) as response:
                response.read()
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"server did not become ready: {url}")


def _http_json(
    url: str,
    descriptor: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object] | None]:
    request = Request(
        url,
        data=json.dumps(descriptor).encode(),
        headers={} if headers is None else headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        with error:
            error.read()
            return error.code, None


def _wait_run(port: int, run_id: object, headers: dict[str, str]) -> dict[str, object]:
    for _ in range(200):
        request = Request(
            f"http://127.0.0.1:{port}/__deploy/load-runs/{run_id}",
            headers=headers,
        )
        with urlopen(request, timeout=2) as response:
            descriptor = json.loads(response.read())
        if descriptor["status"] != "running":
            return descriptor
        time.sleep(0.01)
    raise AssertionError("live load run did not settle")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
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
