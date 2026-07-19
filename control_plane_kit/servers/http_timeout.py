"""Bounded one-attempt HTTP timeout proxy and teaching interpreter."""

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


class TimeoutOutcome(StrEnum):
    FORWARDED = "forwarded"
    REQUEST_REJECTED = "request-rejected"
    RESPONSE_REJECTED = "response-rejected"
    UPSTREAM_TIMEOUT = "upstream-timeout"
    UPSTREAM_DISCONNECTED = "upstream-disconnected"
    TOTAL_DEADLINE_EXCEEDED = "total-deadline-exceeded"
    CLIENT_DISCONNECTED = "client-disconnected"


@dataclass(frozen=True)
class HttpTimeoutPolicy:
    upstream_timeout_ms: int = 2_000
    total_deadline_ms: int = 3_000
    max_request_bytes: int = 65_536
    max_response_bytes: int = 65_536

    def __post_init__(self) -> None:
        _bounded("upstream timeout", self.upstream_timeout_ms, 1, 60_000)
        _bounded("total request deadline", self.total_deadline_ms, 1, 300_000)
        _bounded("timeout proxy request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("timeout proxy response byte limit", self.max_response_bytes, 1, 1_048_576)


@dataclass(frozen=True)
class TimeoutObservation:
    request_count: int
    timeout_count: int
    disconnect_count: int
    latest_outcome: TimeoutOutcome | None
    latest_duration_ms: int | None
    latest_request_id: str | None

    def descriptor(self) -> dict[str, object]:
        return {
            "request_count": self.request_count,
            "timeout_count": self.timeout_count,
            "disconnect_count": self.disconnect_count,
            "latest_outcome": None if self.latest_outcome is None else self.latest_outcome.value,
            "latest_duration_ms": self.latest_duration_ms,
            "latest_request_id": self.latest_request_id,
        }


@dataclass
class HttpTimeoutServer:
    targets: Mapping[str, HttpHandler]
    target: str
    policy: HttpTimeoutPolicy = field(default_factory=HttpTimeoutPolicy)
    _request_count: int = field(init=False, default=0)
    _timeout_count: int = field(init=False, default=0)
    _disconnect_count: int = field(init=False, default=0)
    _latest_outcome: TimeoutOutcome | None = field(init=False, default=None)
    _latest_duration_ms: int | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")

    def handle(self, request: HttpRequest) -> HttpResponse:
        started = time.monotonic()
        if len(request.body) > self.policy.max_request_bytes:
            return self._complete(
                HttpResponse.text("Request Entity Too Large", status_code=413),
                TimeoutOutcome.REQUEST_REJECTED,
                started,
            )
        try:
            response = self.targets[self.target](request)
        except TimeoutError:
            return self._complete(
                HttpResponse.text("Gateway Timeout", status_code=504),
                TimeoutOutcome.UPSTREAM_TIMEOUT,
                started,
            )
        except (ConnectionError, OSError):
            return self._complete(
                HttpResponse.text("Bad Gateway", status_code=502),
                TimeoutOutcome.UPSTREAM_DISCONNECTED,
                started,
            )
        elapsed_ms = int((time.monotonic() - started) * 1_000)
        if elapsed_ms >= self.policy.total_deadline_ms:
            return self._complete(
                HttpResponse.text("Gateway Timeout", status_code=504),
                TimeoutOutcome.TOTAL_DEADLINE_EXCEEDED,
                started,
            )
        if elapsed_ms >= self.policy.upstream_timeout_ms:
            return self._complete(
                HttpResponse.text("Gateway Timeout", status_code=504),
                TimeoutOutcome.UPSTREAM_TIMEOUT,
                started,
            )
        if len(response.body) > self.policy.max_response_bytes:
            return self._complete(
                HttpResponse.text("Bad Gateway", status_code=502),
                TimeoutOutcome.RESPONSE_REJECTED,
                started,
            )
        return self._complete(response, TimeoutOutcome.FORWARDED, started)

    def observation(self) -> TimeoutObservation:
        return TimeoutObservation(
            self._request_count,
            self._timeout_count,
            self._disconnect_count,
            self._latest_outcome,
            self._latest_duration_ms,
            None if self._request_count == 0 else _request_id(self._request_count),
        )

    def _complete(
        self,
        response: HttpResponse,
        outcome: TimeoutOutcome,
        started: float,
    ) -> HttpResponse:
        self._request_count += 1
        if outcome in {TimeoutOutcome.UPSTREAM_TIMEOUT, TimeoutOutcome.TOTAL_DEADLINE_EXCEEDED}:
            self._timeout_count += 1
        if outcome is TimeoutOutcome.UPSTREAM_DISCONNECTED:
            self._disconnect_count += 1
        self._latest_outcome = outcome
        self._latest_duration_ms = min(
            int((time.monotonic() - started) * 1_000),
            self.policy.total_deadline_ms,
        )
        return response


def http_timeout_block(
    block_id: str = "http-timeout",
    *,
    display_name: str = "HTTP Timeout",
    image: str = "python:3.14-alpine",
    policy: HttpTimeoutPolicy = HttpTimeoutPolicy(),
    control_secret_reference: str = "secret://http-timeout/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_TIMEOUT,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.METRICS_READABLE,
            ),
            metadata={"behavior": "http-timeout"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_timeout_command(policy),
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
                RequirementSocket("target", Protocol.HTTP, ("TIMEOUT_TARGET_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_timeout_command(
    policy: HttpTimeoutPolicy = HttpTimeoutPolicy(),
    *,
    port: int = 8080,
) -> tuple[str, ...]:
    if not isinstance(policy, HttpTimeoutPolicy):
        raise TypeError("timeout policy must be typed")
    _bounded("timeout proxy port", port, 1, 65_535)
    return render_python_command(
        "http_timeout.py.j2",
        upstream_timeout_ms=policy.upstream_timeout_ms,
        total_deadline_ms=policy.total_deadline_ms,
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        port=port,
    )


def _request_id(count: int) -> str:
    return f"timeout-request-{count:020d}"
