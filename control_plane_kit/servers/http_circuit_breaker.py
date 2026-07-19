"""Typed HTTP circuit-breaker block and teaching interpreter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import time
from typing import Mapping

from control_plane_kit.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
)
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


class CircuitBreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitBreakerMethodPolicy(StrEnum):
    SAFE_ONLY = "safe-only"
    ALL_METHODS = "all-methods"


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    recovery_timeout_ms: int = 30_000
    half_open_trial_budget: int = 1
    upstream_timeout_ms: int = 2_000
    max_request_bytes: int = 65_536
    max_response_bytes: int = 65_536
    method_policy: CircuitBreakerMethodPolicy = CircuitBreakerMethodPolicy.SAFE_ONLY

    def __post_init__(self) -> None:
        _bounded("failure threshold", self.failure_threshold, 1, 100)
        _bounded("recovery timeout", self.recovery_timeout_ms, 1, 600_000)
        _bounded("half-open trial budget", self.half_open_trial_budget, 1, 100)
        _bounded("upstream timeout", self.upstream_timeout_ms, 1, 60_000)
        _bounded("request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("response byte limit", self.max_response_bytes, 1, 1_048_576)
        if not isinstance(self.method_policy, CircuitBreakerMethodPolicy):
            raise TypeError("circuit breaker method policy must be typed")


@dataclass(frozen=True)
class CircuitBreakerObservation:
    state: CircuitBreakerState
    consecutive_failures: int
    half_open_trials_remaining: int
    transition_sequence: int
    latest_transition_id: str | None

    def descriptor(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "half_open_trials_remaining": self.half_open_trials_remaining,
            "transition_sequence": self.transition_sequence,
            "latest_transition_id": self.latest_transition_id,
        }


@dataclass
class HttpCircuitBreakerServer:
    """Deterministic in-memory interpreter for the circuit state language."""

    targets: Mapping[str, HttpHandler]
    target: str
    policy: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)
    state: CircuitBreakerState = field(init=False, default=CircuitBreakerState.CLOSED)
    _consecutive_failures: int = field(init=False, default=0)
    _half_open_trials_remaining: int = field(init=False, default=0)
    _opened_at_ms: int | None = field(init=False, default=None)
    _transition_sequence: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")

    def handle(self, request: HttpRequest, *, now_ms: int | None = None) -> HttpResponse:
        if len(request.body) > self.policy.max_request_bytes:
            return HttpResponse.text("Request Entity Too Large", status_code=413)
        if not self._method_allowed(request.method):
            return HttpResponse.text("Method Not Allowed", status_code=405)
        current_ms = int(time.monotonic() * 1000) if now_ms is None else now_ms
        if self.state is CircuitBreakerState.OPEN:
            assert self._opened_at_ms is not None
            if current_ms - self._opened_at_ms < self.policy.recovery_timeout_ms:
                return HttpResponse.text("Service Unavailable", status_code=503)
            self._transition(CircuitBreakerState.HALF_OPEN)
            self._half_open_trials_remaining = self.policy.half_open_trial_budget
        if self.state is CircuitBreakerState.HALF_OPEN:
            if self._half_open_trials_remaining <= 0:
                return HttpResponse.text("Service Unavailable", status_code=503)
            self._half_open_trials_remaining -= 1
        try:
            response = self.targets[self.target](request)
        except Exception:  # noqa: BLE001 - target failure becomes circuit evidence.
            self._record_failure(current_ms)
            return HttpResponse.text("Bad Gateway", status_code=502)
        if len(response.body) > self.policy.max_response_bytes:
            self._record_failure(current_ms)
            return HttpResponse.text("Bad Gateway", status_code=502)
        if response.status_code >= 500:
            self._record_failure(current_ms)
        else:
            self._record_success()
        return response

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._opened_at_ms = None
        self._half_open_trials_remaining = 0
        self._transition(CircuitBreakerState.CLOSED)

    def observation(self) -> CircuitBreakerObservation:
        return CircuitBreakerObservation(
            self.state,
            self._consecutive_failures,
            self._half_open_trials_remaining,
            self._transition_sequence,
            None if self._transition_sequence == 0 else _transition_id(self._transition_sequence),
        )

    def _record_failure(self, now_ms: int) -> None:
        if self.state is CircuitBreakerState.HALF_OPEN:
            self._open(now_ms)
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.policy.failure_threshold:
            self._open(now_ms)

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        if self.state is CircuitBreakerState.HALF_OPEN:
            self._opened_at_ms = None
            self._half_open_trials_remaining = 0
            self._transition(CircuitBreakerState.CLOSED)

    def _open(self, now_ms: int) -> None:
        self._opened_at_ms = now_ms
        self._half_open_trials_remaining = 0
        self._transition(CircuitBreakerState.OPEN)

    def _transition(self, state: CircuitBreakerState) -> None:
        if self.state is state:
            return
        self.state = state
        self._transition_sequence += 1

    def _method_allowed(self, method: str) -> bool:
        return (
            self.policy.method_policy is CircuitBreakerMethodPolicy.ALL_METHODS
            or method.upper() in {"GET", "HEAD"}
        )


def http_circuit_breaker_block(
    block_id: str = "http-circuit-breaker",
    *,
    display_name: str = "HTTP Circuit Breaker",
    image: str = "python:3.14-alpine",
    policy: CircuitBreakerPolicy = CircuitBreakerPolicy(),
    control_secret_reference: str = "secret://http-circuit-breaker/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_CIRCUIT_BREAKER,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.CIRCUIT_STATE_READABLE,
                CapabilityName.CIRCUIT_RESETTABLE,
            ),
            metadata={"behavior": "http-circuit-breaker"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_circuit_breaker_command(policy),
            ports={"internal": 8080},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_CONTROL_TOKEN",
                    SecretReference(control_secret_reference),
                ),
            ),
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("target", Protocol.HTTP, ("CIRCUIT_TARGET_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_circuit_breaker_command(
    policy: CircuitBreakerPolicy = CircuitBreakerPolicy(),
    *,
    port: int = 8080,
) -> tuple[str, ...]:
    if not isinstance(policy, CircuitBreakerPolicy):
        raise TypeError("circuit breaker policy must be typed")
    _bounded("circuit breaker port", port, 1, 65_535)
    return render_python_command(
        "http_circuit_breaker.py.j2",
        failure_threshold=policy.failure_threshold,
        recovery_timeout_ms=policy.recovery_timeout_ms,
        half_open_trial_budget=policy.half_open_trial_budget,
        upstream_timeout_ms=policy.upstream_timeout_ms,
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        allow_all_methods=policy.method_policy is CircuitBreakerMethodPolicy.ALL_METHODS,
        port=port,
    )


def _transition_id(sequence: int) -> str:
    return f"circuit-transition-{sequence:020d}"
