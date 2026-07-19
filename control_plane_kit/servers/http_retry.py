"""Bounded HTTP retry block and teaching interpreter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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


class RetryMethodPolicy(StrEnum):
    SAFE_ONLY = "safe-only"
    IDEMPOTENCY_KEY = "idempotency-key"


class RetryStatusPolicy(StrEnum):
    GATEWAY_ERRORS = "gateway-errors"
    SERVER_ERRORS = "server-errors"


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    per_attempt_timeout_ms: int = 2_000
    total_deadline_ms: int = 6_000
    backoff_ms: int = 25
    max_request_bytes: int = 65_536
    max_response_bytes: int = 65_536
    method_policy: RetryMethodPolicy = RetryMethodPolicy.SAFE_ONLY
    status_policy: RetryStatusPolicy = RetryStatusPolicy.GATEWAY_ERRORS

    def __post_init__(self) -> None:
        _bounded("retry attempts", self.attempts, 1, 10)
        _bounded("per-attempt timeout", self.per_attempt_timeout_ms, 1, 60_000)
        _bounded("total deadline", self.total_deadline_ms, 1, 300_000)
        _bounded("retry backoff", self.backoff_ms, 0, 30_000)
        _bounded("request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("response byte limit", self.max_response_bytes, 1, 1_048_576)
        if not isinstance(self.method_policy, RetryMethodPolicy):
            raise TypeError("retry method policy must be typed")
        if not isinstance(self.status_policy, RetryStatusPolicy):
            raise TypeError("retry status policy must be typed")


@dataclass(frozen=True)
class RetryObservation:
    request_count: int
    attempt_count: int
    retry_count: int
    exhausted_count: int
    latest_attempt_count: int
    latest_request_id: str | None

    def descriptor(self) -> dict[str, object]:
        return {
            "request_count": self.request_count,
            "attempt_count": self.attempt_count,
            "retry_count": self.retry_count,
            "exhausted_count": self.exhausted_count,
            "latest_attempt_count": self.latest_attempt_count,
            "latest_request_id": self.latest_request_id,
        }


@dataclass
class HttpRetryServer:
    targets: Mapping[str, HttpHandler]
    target: str
    policy: RetryPolicy = field(default_factory=RetryPolicy)
    _request_count: int = field(init=False, default=0)
    _attempt_count: int = field(init=False, default=0)
    _retry_count: int = field(init=False, default=0)
    _exhausted_count: int = field(init=False, default=0)
    _latest_attempt_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")

    def handle(self, request: HttpRequest) -> HttpResponse:
        if len(request.body) > self.policy.max_request_bytes:
            return HttpResponse.text("Request Entity Too Large", status_code=413)
        self._request_count += 1
        attempt_limit = self._attempt_limit(request)
        response = HttpResponse.text("Bad Gateway", status_code=502)
        for attempt in range(1, attempt_limit + 1):
            self._attempt_count += 1
            self._latest_attempt_count = attempt
            try:
                response = self.targets[self.target](request)
            except Exception:  # noqa: BLE001 - target loss is retry evidence.
                response = HttpResponse.text("Bad Gateway", status_code=502)
            if len(response.body) > self.policy.max_response_bytes:
                response = HttpResponse.text("Bad Gateway", status_code=502)
            if not self._retryable(response.status_code) or attempt == attempt_limit:
                if self._retryable(response.status_code) and attempt_limit > 1:
                    self._exhausted_count += 1
                return response
            self._retry_count += 1
        return response

    def observation(self) -> RetryObservation:
        return RetryObservation(
            self._request_count,
            self._attempt_count,
            self._retry_count,
            self._exhausted_count,
            self._latest_attempt_count,
            None if self._request_count == 0 else _request_id(self._request_count),
        )

    def _attempt_limit(self, request: HttpRequest) -> int:
        if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return self.policy.attempts
        if self.policy.method_policy is RetryMethodPolicy.IDEMPOTENCY_KEY:
            key = next(
                (
                    value
                    for name, value in request.headers.items()
                    if name.lower() == "idempotency-key"
                ),
                "",
            )
            if 1 <= len(key) <= 256:
                return self.policy.attempts
        return 1

    def _retryable(self, status: int) -> bool:
        if self.policy.status_policy is RetryStatusPolicy.SERVER_ERRORS:
            return 500 <= status <= 599
        return status in {502, 503, 504}


def http_retry_block(
    block_id: str = "http-retry",
    *,
    display_name: str = "HTTP Retry",
    image: str = "python:3.14-alpine",
    policy: RetryPolicy = RetryPolicy(),
    control_secret_reference: str = "secret://http-retry/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_RETRY,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.METRICS_READABLE,
            ),
            metadata={"behavior": "http-retry"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_retry_command(policy),
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
                RequirementSocket("target", Protocol.HTTP, ("RETRY_TARGET_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_retry_command(
    policy: RetryPolicy = RetryPolicy(),
    *,
    port: int = 8080,
) -> tuple[str, ...]:
    if not isinstance(policy, RetryPolicy):
        raise TypeError("retry policy must be typed")
    _bounded("retry port", port, 1, 65_535)
    return render_python_command(
        "http_retry.py.j2",
        attempts=policy.attempts,
        per_attempt_timeout_ms=policy.per_attempt_timeout_ms,
        total_deadline_ms=policy.total_deadline_ms,
        backoff_ms=policy.backoff_ms,
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        idempotency_key_policy=policy.method_policy is RetryMethodPolicy.IDEMPOTENCY_KEY,
        server_error_policy=policy.status_policy is RetryStatusPolicy.SERVER_ERRORS,
        port=port,
    )


def _request_id(count: int) -> str:
    return f"retry-request-{count:020d}"
