from __future__ import annotations

from dataclasses import dataclass, field
import socket
from typing import Callable
import unittest

import httpx

from control_plane_kit import (
    ActivityId,
    EffectFailed,
    EffectIdentity,
    EffectRequest,
    EffectSucceeded,
    EndpointMaterial,
    EndpointContext,
    EndpointScope,
    ImplementationMaterial,
    LiteralEndpointMaterial,
    MaterializedEffectRequest,
    NodeMaterial,
    NodeTarget,
    PinnedGraphSet,
    ProbeKind,
    ProbeObservation,
    ProbeOutcome,
    ProbePolicy,
    Protocol,
    RuntimeEndpointObservation,
    RuntimeKind,
    RuntimeMaterial,
    SecretEndpointMaterial,
    TimeoutPolicy,
    WaitForHealthy,
    application_health_probe,
    process_probe,
    transport_probe,
)
from control_plane_kit.adapters.probes import (
    HttpApplicationHealthProbeAdapter,
    ProbeAddressPolicy,
    ProbeEffectInterpreter,
    ProbeSecurityError,
    StaticRuntimeEndpointProvider,
    TcpTransportProbeAdapter,
    authorize_probe_endpoint,
)
from control_plane_kit.docker_runtime import (
    DockerProcessProbeAdapter,
    DockerResourceInspection,
    DockerResourceKind,
    docker_container_name,
    docker_node_ownership,
)
from control_plane_kit.execution import FailureCategory, ObservationStatus


@dataclass
class FakeClock:
    value: float = 100.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


@dataclass
class SequenceProcessProbe:
    outcomes: list[ProbeOutcome]
    transaction_active: Callable[[], bool] = lambda: False

    def observe(self, intent, request, *, timeout_seconds):
        if self.transaction_active():
            raise AssertionError("process probe ran inside UnitOfWork")
        outcome = self.outcomes.pop(0)
        return ProbeObservation(intent.subject_id, intent.graph_id, intent.kind, outcome)


@dataclass
class SequenceTransportProbe:
    outcomes: list[ProbeOutcome]
    transaction_active: Callable[[], bool] = lambda: False

    def observe(self, intent, *, timeout_seconds):
        if self.transaction_active():
            raise AssertionError("transport probe ran inside UnitOfWork")
        return ProbeObservation(
            intent.subject_id,
            intent.graph_id,
            intent.kind,
            self.outcomes.pop(0),
            endpoint_context=intent.endpoint.context,
        )


@dataclass
class SequenceHealthProbe:
    outcomes: list[ProbeOutcome]
    transaction_active: Callable[[], bool] = lambda: False

    def observe(self, intent, *, timeout_seconds):
        if self.transaction_active():
            raise AssertionError("health probe ran inside UnitOfWork")
        return ProbeObservation(
            intent.subject_id,
            intent.graph_id,
            intent.kind,
            self.outcomes.pop(0),
            endpoint_context=intent.endpoint.context,
        )


class ProbeEffectInterpreterTests(unittest.TestCase):
    def test_refused_transport_retries_then_records_truthful_readiness(self) -> None:
        clock = FakeClock()
        interpreter = ProbeEffectInterpreter(
            _endpoint_provider(),
            SequenceTransportProbe([ProbeOutcome.REFUSED, ProbeOutcome.REACHABLE]),
            SequenceHealthProbe([ProbeOutcome.HEALTHY]),
            SequenceProcessProbe(
                [ProbeOutcome.PROCESS_RUNNING, ProbeOutcome.PROCESS_RUNNING]
            ),
            monotonic=clock,
            sleep=clock.sleep,
        )

        result = interpreter.execute(_request(timeout=TimeoutPolicy(5, 1)))

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(clock.sleeps, [1.0])
        evidence = [value.evidence.descriptor() for value in result.observations]
        self.assertEqual(
            [value["kind"] for value in evidence],
            ["process", "transport", "application-health", "readiness"],
        )
        self.assertEqual(
            [value["outcome"] for value in evidence],
            ["process-running", "reachable", "healthy", "ready"],
        )
        self.assertTrue(all(value["attempts"] == 2 for value in evidence))

    def test_started_but_unhealthy_is_terminal_and_not_ready(self) -> None:
        clock = FakeClock()
        result = ProbeEffectInterpreter(
            _endpoint_provider(),
            SequenceTransportProbe([ProbeOutcome.REACHABLE]),
            SequenceHealthProbe([ProbeOutcome.UNHEALTHY]),
            SequenceProcessProbe([ProbeOutcome.PROCESS_RUNNING]),
            monotonic=clock,
            sleep=clock.sleep,
        ).execute(_request(timeout=TimeoutPolicy(30, 1)))

        self.assertIsInstance(result, EffectFailed)
        self.assertIs(result.failure.category, FailureCategory.TERMINAL)
        self.assertEqual(result.failure.code, "probe.application-unhealthy")
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(
            [value.status for value in result.observations],
            [
                ObservationStatus.PROCESS_STARTED,
                ObservationStatus.REACHABLE,
                ObservationStatus.UNHEALTHY,
                ObservationStatus.UNHEALTHY,
            ],
        )
        self.assertEqual(
            result.observations[-1].evidence.descriptor()["outcome"],
            "not-ready",
        )

    def test_missing_endpoint_fails_before_any_probe_attempt(self) -> None:
        result = ProbeEffectInterpreter(
            StaticRuntimeEndpointProvider({}),
            SequenceTransportProbe([]),
            SequenceHealthProbe([]),
        ).execute(_request())

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "probe.endpoint-unavailable")
        self.assertEqual(result.observations, ())

    def test_total_deadline_is_recomputed_between_probe_layers(self) -> None:
        clock = FakeClock()
        timeouts: list[float] = []

        class Process:
            def observe(self, intent, request, *, timeout_seconds):
                timeouts.append(timeout_seconds)
                clock.value += 3
                return ProbeObservation(
                    intent.subject_id,
                    intent.graph_id,
                    intent.kind,
                    ProbeOutcome.PROCESS_RUNNING,
                )

        class Transport:
            def observe(self, intent, *, timeout_seconds):
                timeouts.append(timeout_seconds)
                clock.value += 2
                return ProbeObservation(
                    intent.subject_id,
                    intent.graph_id,
                    intent.kind,
                    ProbeOutcome.REACHABLE,
                    endpoint_context=intent.endpoint.context,
                )

        result = ProbeEffectInterpreter(
            _endpoint_provider(),
            Transport(),
            SequenceHealthProbe([]),
            Process(),
            monotonic=clock,
            sleep=clock.sleep,
        ).execute(_request(timeout=TimeoutPolicy(5)))

        self.assertIsInstance(result, EffectFailed)
        self.assertEqual(result.failure.code, "probe.timed-out")
        self.assertEqual(timeouts, [5.0, 2.0])
        self.assertEqual(
            result.observations[0].evidence.descriptor()["outcome"],
            "timed-out",
        )


class ProbeAdapterTests(unittest.TestCase):
    def test_probe_address_policy_fails_closed_and_pins_public_dns(self) -> None:
        private_host = RuntimeEndpointObservation(
            "api",
            "internal",
            "graph-a",
            Protocol.HTTP,
            EndpointContext.HOST_LOCAL,
            LiteralEndpointMaterial("http://10.0.0.5:8000"),
        )
        with self.assertRaises(ProbeSecurityError) as rejected:
            authorize_probe_endpoint(
                private_host,
                ProbeAddressPolicy(allow_host_local=True),
            )
        self.assertNotIn("10.0.0.5", str(rejected.exception))

        public = RuntimeEndpointObservation(
            "api",
            "internal",
            "graph-a",
            Protocol.HTTP,
            EndpointContext.PUBLIC,
            LiteralEndpointMaterial("https://api.example.test:443"),
        )
        with self.assertRaises(ProbeSecurityError):
            authorize_probe_endpoint(
                public,
                ProbeAddressPolicy(public_hosts=frozenset({"api.example.test"})),
                public_resolver=_PublicResolver(("127.0.0.1",)),
            )
        authorized = authorize_probe_endpoint(
            public,
            ProbeAddressPolicy(public_hosts=frozenset({"api.example.test"})),
            public_resolver=_PublicResolver(("8.8.8.8",)),
        )
        self.assertEqual(authorized.connect_host, "8.8.8.8")
        self.assertEqual(authorized.sni_hostname, "api.example.test")
        self.assertNotIn("api.example.test", repr(authorized))

    def test_secret_endpoint_value_is_resolved_only_at_authorization(self) -> None:
        endpoint = RuntimeEndpointObservation(
            "api",
            "internal",
            "graph-a",
            Protocol.HTTP,
            EndpointContext.HOST_LOCAL,
            SecretEndpointMaterial("secret://runtime/api"),
        )
        resolver = _EndpointResolver("http://127.0.0.1:18000")

        target = authorize_probe_endpoint(
            endpoint,
            ProbeAddressPolicy(allow_host_local=True),
            secret_resolver=resolver,
        )

        self.assertEqual(resolver.references, ["secret://runtime/api"])
        self.assertNotIn("127.0.0.1", repr(target))
    def test_http_probe_is_bounded_redirect_free_and_status_driven(self) -> None:
        policy = ProbeAddressPolicy(allow_host_local=True)
        intent = _health_intent()

        healthy = HttpApplicationHealthProbeAdapter(
            policy,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"ok")
            ),
        ).observe(intent, timeout_seconds=1)
        unhealthy = HttpApplicationHealthProbeAdapter(
            policy,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, content=b"not ready")
            ),
        ).observe(intent, timeout_seconds=1)
        redirect = HttpApplicationHealthProbeAdapter(
            policy,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(302, headers={"location": "http://evil.test"})
            ),
        ).observe(intent, timeout_seconds=1)
        oversized = HttpApplicationHealthProbeAdapter(
            policy,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 20_000)
            ),
        ).observe(intent, timeout_seconds=1)

        self.assertIs(healthy.outcome, ProbeOutcome.HEALTHY)
        self.assertIs(unhealthy.outcome, ProbeOutcome.UNHEALTHY)
        self.assertIs(redirect.outcome, ProbeOutcome.MALFORMED)
        self.assertIs(oversized.outcome, ProbeOutcome.MALFORMED)

    def test_tcp_probe_distinguishes_reachable_refused_and_timeout(self) -> None:
        policy = ProbeAddressPolicy(allow_host_local=True)
        intent = _transport_intent()

        reachable = TcpTransportProbeAdapter(
            policy,
            connector=_Connector("reachable"),
        ).observe(intent, timeout_seconds=1)
        refused = TcpTransportProbeAdapter(
            policy,
            connector=_Connector("refused"),
        ).observe(intent, timeout_seconds=1)
        timed_out = TcpTransportProbeAdapter(
            policy,
            connector=_Connector("timeout"),
        ).observe(intent, timeout_seconds=1)

        self.assertIs(reachable.outcome, ProbeOutcome.REACHABLE)
        self.assertIs(refused.outcome, ProbeOutcome.REFUSED)
        self.assertIs(timed_out.outcome, ProbeOutcome.TIMED_OUT)

    def test_docker_process_probe_inspects_graph_owned_container(self) -> None:
        request = _request()
        ownership = docker_node_ownership(request)
        name = docker_container_name("demo", "docker", "api")
        running = DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            "container-id",
            name,
            True,
            "api:latest",
            ownership.labels(),
        )
        adapter = DockerProcessProbeAdapter("demo", _InspectClient(running))

        observation = adapter.observe(
            _process_intent(),
            request,
            timeout_seconds=1,
        )

        self.assertIs(observation.outcome, ProbeOutcome.PROCESS_RUNNING)
        unowned = DockerResourceInspection(
            DockerResourceKind.CONTAINER,
            "foreign",
            name,
            True,
            "api:latest",
            {},
        )
        unknown = DockerProcessProbeAdapter(
            "demo",
            _InspectClient(unowned),
        ).observe(_process_intent(), request, timeout_seconds=1)
        self.assertIs(unknown.outcome, ProbeOutcome.UNKNOWN)


@dataclass
class _Connection:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class _Connector:
    outcome: str

    def connect(self, host, port, *, timeout_seconds):
        if self.outcome == "refused":
            raise ConnectionRefusedError
        if self.outcome == "timeout":
            raise socket.timeout
        return _Connection()


@dataclass(frozen=True)
class _InspectClient:
    inspected: DockerResourceInspection | None

    def inspect_container(self, name, *, timeout_seconds=30):
        return self.inspected


@dataclass(frozen=True)
class _PublicResolver:
    addresses: tuple[str, ...]

    def resolve(self, hostname: str) -> tuple[str, ...]:
        return self.addresses


@dataclass
class _EndpointResolver:
    value: str
    references: list[str] = field(default_factory=list)

    def resolve_endpoint(self, reference_id: str) -> str:
        self.references.append(reference_id)
        return self.value


def _runtime() -> RuntimeMaterial:
    return RuntimeMaterial("docker", RuntimeKind.DOCKER, ("api",), "demo-network")


def _node() -> NodeMaterial:
    return NodeMaterial(
        "api",
        _runtime(),
        ImplementationMaterial("docker-image", image="api:latest"),
        (
            EndpointMaterial(
                "internal",
                Protocol.HTTP,
                EndpointScope.LOCAL,
                LiteralEndpointMaterial("http://127.0.0.1:18000"),
            ),
        ),
        (),
        "/health",
    )


def _request(*, timeout: TimeoutPolicy | None = None) -> MaterializedEffectRequest:
    action = WaitForHealthy(NodeTarget("api"))
    return MaterializedEffectRequest(
        EffectRequest(
            EffectIdentity("run-a", ActivityId("wait-api"), 1, "effect-a"),
            action,
            timeout or TimeoutPolicy(5, 1),
        ),
        PinnedGraphSet("workspace-a", "plan-a", "graph-base", "graph-a"),
        "graph-a",
        _node(),
    )


def _endpoint() -> RuntimeEndpointObservation:
    return RuntimeEndpointObservation(
        "api",
        "internal",
        "graph-a",
        Protocol.HTTP,
        EndpointContext.HOST_LOCAL,
        LiteralEndpointMaterial("http://127.0.0.1:18000"),
    )


def _endpoint_provider() -> StaticRuntimeEndpointProvider:
    return StaticRuntimeEndpointProvider({("api", "graph-a"): _endpoint()})


def _process_intent():
    return process_probe(_node(), "graph-a", ProbePolicy())


def _transport_intent():
    return transport_probe(_node(), _endpoint(), ProbePolicy())


def _health_intent():
    return application_health_probe(_node(), _endpoint(), ProbePolicy())
