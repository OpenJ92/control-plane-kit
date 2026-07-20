"""Bounded inline HTTP traffic logger and teaching interpreter."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import time
from typing import Mapping

from control_plane_kit.core.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.products.servers.support.command_rendering import render_python_command
from control_plane_kit.products.servers.support.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.core.types import Protocol


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


class TrafficPathPolicy(StrEnum):
    REDACTED = "redacted"
    STABLE_HASH = "stable-hash"


class TrafficMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    OTHER = "OTHER"


class TrafficStatusClass(StrEnum):
    INFORMATIONAL = "informational"
    SUCCESS = "success"
    REDIRECTION = "redirection"
    CLIENT_ERROR = "client-error"
    SERVER_ERROR = "server-error"


@dataclass(frozen=True)
class TrafficEvidencePolicy:
    capacity: int = 256
    page_limit: int = 50
    upstream_timeout_ms: int = 2_000
    max_request_bytes: int = 65_536
    max_response_bytes: int = 65_536
    path_policy: TrafficPathPolicy = TrafficPathPolicy.REDACTED

    def __post_init__(self) -> None:
        _bounded("traffic evidence capacity", self.capacity, 1, 10_000)
        _bounded("traffic evidence page limit", self.page_limit, 1, 1_000)
        if self.page_limit > self.capacity:
            raise ValueError("traffic evidence page limit cannot exceed capacity")
        _bounded("traffic logger upstream timeout", self.upstream_timeout_ms, 1, 60_000)
        _bounded("traffic logger request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("traffic logger response byte limit", self.max_response_bytes, 1, 1_048_576)
        if not isinstance(self.path_policy, TrafficPathPolicy):
            raise TypeError("traffic path policy must be typed")


@dataclass(frozen=True)
class TrafficEvidence:
    sequence: int
    correlation_id: str
    method: TrafficMethod
    status_class: TrafficStatusClass
    duration_ms: int
    request_bytes: int
    response_bytes: int
    path_digest: str | None = None

    def descriptor(self) -> dict[str, object]:
        value: dict[str, object] = {
            "sequence": self.sequence,
            "correlation_id": self.correlation_id,
            "method": self.method.value,
            "status_class": self.status_class.value,
            "duration_ms": self.duration_ms,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
        }
        if self.path_digest is not None:
            value["path_digest"] = self.path_digest
        return value


@dataclass(frozen=True)
class TrafficEvidencePage:
    offset: int
    limit: int
    total: int
    evicted: int
    items: tuple[TrafficEvidence, ...]

    def descriptor(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "limit": self.limit,
            "total": self.total,
            "evicted": self.evicted,
            "items": [item.descriptor() for item in self.items],
        }


@dataclass
class HttpTrafficLoggerServer:
    targets: Mapping[str, HttpHandler]
    target: str
    policy: TrafficEvidencePolicy = field(default_factory=TrafficEvidencePolicy)
    _sequence: int = field(init=False, default=0)
    _evicted: int = field(init=False, default=0)
    _evidence: deque[TrafficEvidence] = field(init=False)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")
        self._evidence = deque(maxlen=self.policy.capacity)

    def handle(self, request: HttpRequest) -> HttpResponse:
        started = time.monotonic()
        if len(request.body) > self.policy.max_request_bytes:
            response = HttpResponse.text("Request Entity Too Large", status_code=413)
        else:
            try:
                response = self.targets[self.target](request)
            except Exception:  # noqa: BLE001 - target loss becomes bounded evidence.
                response = HttpResponse.text("Bad Gateway", status_code=502)
            if len(response.body) > self.policy.max_response_bytes:
                response = HttpResponse.text("Bad Gateway", status_code=502)
        duration_ms = min(
            int((time.monotonic() - started) * 1_000),
            self.policy.upstream_timeout_ms,
        )
        self._record(request, response, duration_ms)
        return response

    def read(self, *, offset: int = 0, limit: int | None = None) -> TrafficEvidencePage:
        actual_limit = self.policy.page_limit if limit is None else limit
        _bounded("traffic evidence offset", offset, 0, self.policy.capacity)
        _bounded("traffic evidence read limit", actual_limit, 1, self.policy.page_limit)
        values = tuple(self._evidence)
        return TrafficEvidencePage(
            offset,
            actual_limit,
            len(values),
            self._evicted,
            values[offset : offset + actual_limit],
        )

    def _record(
        self,
        request: HttpRequest,
        response: HttpResponse,
        duration_ms: int,
    ) -> None:
        self._sequence += 1
        if len(self._evidence) == self.policy.capacity:
            self._evicted += 1
        self._evidence.append(
            TrafficEvidence(
                sequence=self._sequence,
                correlation_id=_correlation_id(self._sequence),
                method=_method(request.method),
                status_class=_status_class(response.status_code),
                duration_ms=duration_ms,
                request_bytes=len(request.body),
                response_bytes=len(response.body),
                path_digest=_path_digest(request.path, self.policy.path_policy),
            )
        )


def http_traffic_logger_block(
    block_id: str = "http-traffic-logger",
    *,
    display_name: str = "HTTP Traffic Logger",
    image: str = "python:3.14-alpine",
    policy: TrafficEvidencePolicy = TrafficEvidencePolicy(),
    control_secret_reference: str = "secret://http-traffic-logger/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_TRAFFIC_LOGGER,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TRAFFIC_EVIDENCE_READABLE,
            ),
            metadata={"behavior": "http-traffic-logger"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_traffic_logger_command(policy),
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
                RequirementSocket("target", Protocol.HTTP, ("LOGGER_TARGET_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_traffic_logger_command(
    policy: TrafficEvidencePolicy = TrafficEvidencePolicy(),
    *,
    port: int = 8080,
) -> tuple[str, ...]:
    if not isinstance(policy, TrafficEvidencePolicy):
        raise TypeError("traffic evidence policy must be typed")
    _bounded("traffic logger port", port, 1, 65_535)
    return render_python_command(
        "http_traffic_logger.py.j2",
        capacity=policy.capacity,
        page_limit=policy.page_limit,
        upstream_timeout_ms=policy.upstream_timeout_ms,
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        hash_paths=policy.path_policy is TrafficPathPolicy.STABLE_HASH,
        port=port,
    )


def _method(value: str) -> TrafficMethod:
    try:
        return TrafficMethod(value.upper())
    except ValueError:
        return TrafficMethod.OTHER


def _status_class(status: int) -> TrafficStatusClass:
    if status < 200:
        return TrafficStatusClass.INFORMATIONAL
    if status < 300:
        return TrafficStatusClass.SUCCESS
    if status < 400:
        return TrafficStatusClass.REDIRECTION
    if status < 500:
        return TrafficStatusClass.CLIENT_ERROR
    return TrafficStatusClass.SERVER_ERROR


def _path_digest(path: str, policy: TrafficPathPolicy) -> str | None:
    if policy is TrafficPathPolicy.REDACTED:
        return None
    return sha256(path.encode()).hexdigest()


def _correlation_id(sequence: int) -> str:
    return f"traffic-{sequence:020d}"
