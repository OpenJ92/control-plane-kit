"""Ticketed HTTP concurrency bulkhead and teaching interpreter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import threading
import time
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, PackageServerProduct, PackageServerSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")


class BulkheadOutcome(StrEnum):
    FORWARDED = "forwarded"
    REJECTED = "rejected"
    QUEUE_TIMEOUT = "queue-timeout"
    TARGET_FAILED = "target-failed"
    REQUEST_REJECTED = "request-rejected"
    RESPONSE_REJECTED = "response-rejected"


@dataclass(frozen=True)
class HttpBulkheadPolicy:
    maximum_in_flight: int = 16
    queue_capacity: int = 32
    queue_timeout_ms: int = 1_000
    max_request_bytes: int = 65_536
    max_response_bytes: int = 65_536

    def __post_init__(self) -> None:
        _bounded("bulkhead maximum in-flight", self.maximum_in_flight, 1, 10_000)
        _bounded("bulkhead queue capacity", self.queue_capacity, 0, 10_000)
        _bounded("bulkhead queue timeout", self.queue_timeout_ms, 1, 60_000)
        _bounded("bulkhead request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("bulkhead response byte limit", self.max_response_bytes, 1, 1_048_576)


@dataclass(frozen=True)
class BulkheadObservation:
    in_flight: int
    waiting: int
    accepted_count: int
    rejected_count: int
    queue_timeout_count: int
    latest_outcome: BulkheadOutcome | None
    latest_request_id: str | None

    def descriptor(self) -> dict[str, object]:
        return {
            "in_flight": self.in_flight,
            "waiting": self.waiting,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "queue_timeout_count": self.queue_timeout_count,
            "latest_outcome": None if self.latest_outcome is None else self.latest_outcome.value,
            "latest_request_id": self.latest_request_id,
        }


@dataclass
class HttpBulkheadServer:
    targets: Mapping[str, HttpHandler]
    target: str
    policy: HttpBulkheadPolicy = field(default_factory=HttpBulkheadPolicy)
    _condition: threading.Condition = field(init=False, default_factory=threading.Condition)
    _in_flight: int = field(init=False, default=0)
    _waiting: int = field(init=False, default=0)
    _next_ticket: int = field(init=False, default=0)
    _serving_ticket: int = field(init=False, default=0)
    _cancelled_tickets: set[int] = field(init=False, default_factory=set)
    _accepted_count: int = field(init=False, default=0)
    _rejected_count: int = field(init=False, default=0)
    _queue_timeout_count: int = field(init=False, default=0)
    _latest_outcome: BulkheadOutcome | None = field(init=False, default=None)
    _request_sequence: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")

    def handle(self, request: HttpRequest) -> HttpResponse:
        if len(request.body) > self.policy.max_request_bytes:
            self._record(BulkheadOutcome.REQUEST_REJECTED)
            return HttpResponse.text("Request Entity Too Large", status_code=413)
        admission = self._acquire()
        if admission is BulkheadOutcome.REJECTED:
            self._record(admission)
            return HttpResponse.text("Service Unavailable", status_code=503)
        if admission is BulkheadOutcome.QUEUE_TIMEOUT:
            self._record(admission)
            return HttpResponse.text("Gateway Timeout", status_code=504)
        try:
            try:
                response = self.targets[self.target](request)
            except Exception:  # noqa: BLE001 - target loss is a closed outcome.
                self._record(BulkheadOutcome.TARGET_FAILED)
                return HttpResponse.text("Bad Gateway", status_code=502)
            if len(response.body) > self.policy.max_response_bytes:
                self._record(BulkheadOutcome.RESPONSE_REJECTED)
                return HttpResponse.text("Bad Gateway", status_code=502)
            self._record(BulkheadOutcome.FORWARDED)
            return response
        finally:
            self._release()

    def observation(self) -> BulkheadObservation:
        with self._condition:
            return BulkheadObservation(
                self._in_flight,
                self._waiting,
                self._accepted_count,
                self._rejected_count,
                self._queue_timeout_count,
                self._latest_outcome,
                None if self._request_sequence == 0 else _request_id(self._request_sequence),
            )

    def _acquire(self) -> BulkheadOutcome | None:
        with self._condition:
            if self._waiting == 0 and self._in_flight < self.policy.maximum_in_flight:
                self._in_flight += 1
                self._accepted_count += 1
                return None
            if self._waiting >= self.policy.queue_capacity:
                self._rejected_count += 1
                return BulkheadOutcome.REJECTED
            ticket = self._next_ticket
            self._next_ticket += 1
            self._waiting += 1
            deadline = time.monotonic() + self.policy.queue_timeout_ms / 1_000
            while ticket != self._serving_ticket or self._in_flight >= self.policy.maximum_in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._waiting -= 1
                    self._queue_timeout_count += 1
                    self._cancelled_tickets.add(ticket)
                    self._advance_cancelled()
                    self._condition.notify_all()
                    return BulkheadOutcome.QUEUE_TIMEOUT
                self._condition.wait(remaining)
            self._waiting -= 1
            self._serving_ticket += 1
            self._advance_cancelled()
            self._in_flight += 1
            self._accepted_count += 1
            self._condition.notify_all()
            return None

    def _release(self) -> None:
        with self._condition:
            if self._in_flight <= 0:
                raise RuntimeError("bulkhead permit count would become negative")
            self._in_flight -= 1
            self._condition.notify_all()

    def _advance_cancelled(self) -> None:
        while self._serving_ticket in self._cancelled_tickets:
            self._cancelled_tickets.remove(self._serving_ticket)
            self._serving_ticket += 1

    def _record(self, outcome: BulkheadOutcome) -> None:
        with self._condition:
            self._request_sequence += 1
            self._latest_outcome = outcome


def http_bulkhead_block(
    block_id: str = "http-bulkhead",
    *,
    display_name: str = "HTTP Bulkhead",
    image: str = "python:3.14-alpine",
    policy: HttpBulkheadPolicy = HttpBulkheadPolicy(),
    control_secret_reference: str = "secret://http-bulkhead/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_BULKHEAD,
            display_name=display_name,
            health_path="/health",
            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.METRICS_READABLE),
            metadata={"behavior": "http-bulkhead"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_bulkhead_command(policy),
            ports={"internal": 8080},
            secret_deliveries=(SecretEnvironmentDelivery("CPK_CONTROL_TOKEN", SecretReference(control_secret_reference)),),
        ),
        BlockSockets(
            requirements=(RequirementSocket("target", Protocol.HTTP, ("BULKHEAD_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_bulkhead_command(policy: HttpBulkheadPolicy = HttpBulkheadPolicy(), *, port: int = 8080) -> tuple[str, ...]:
    if not isinstance(policy, HttpBulkheadPolicy):
        raise TypeError("bulkhead policy must be typed")
    _bounded("bulkhead port", port, 1, 65_535)
    return render_python_command(
        "http_bulkhead.py.j2",
        maximum_in_flight=policy.maximum_in_flight,
        queue_capacity=policy.queue_capacity,
        queue_timeout_ms=policy.queue_timeout_ms,
        max_request_bytes=policy.max_request_bytes,
        max_response_bytes=policy.max_response_bytes,
        port=port,
    )


def _request_id(sequence: int) -> str:
    return f"bulkhead-request-{sequence:020d}"
