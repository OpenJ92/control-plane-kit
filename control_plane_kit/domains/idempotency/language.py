"""Closed durable values for HTTP request idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib


class IdempotencyMethod(StrEnum):
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class IdempotencyRecordStatus(StrEnum):
    IN_FLIGHT = "in-flight"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class IdempotencyOutcome(StrEnum):
    EXECUTED = "executed"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    IN_FLIGHT = "in-flight"
    UNCERTAIN = "uncertain"
    CAPACITY_EXHAUSTED = "capacity-exhausted"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class IdempotencyRoutePolicy:
    path: str
    method: IdempotencyMethod

    def __post_init__(self) -> None:
        if not self.path.startswith("/") or "?" in self.path:
            raise ValueError("idempotency route must be an absolute path")
        if not isinstance(self.method, IdempotencyMethod):
            raise TypeError("idempotency method must be typed")


@dataclass(frozen=True)
class IdempotencyGatewayPolicy:
    routes: tuple[IdempotencyRoutePolicy, ...]
    retention_seconds: int = 86_400
    in_flight_lease_seconds: int = 30
    max_records: int = 10_000
    max_key_bytes: int = 256
    max_request_bytes: int = 1_048_576
    max_response_bytes: int = 1_048_576
    result_reference_header: str = "location"

    def __post_init__(self) -> None:
        if not self.routes or any(not isinstance(route, IdempotencyRoutePolicy) for route in self.routes):
            raise TypeError("idempotency routes must be a nonempty typed tuple")
        if len(set(self.routes)) != len(self.routes):
            raise ValueError("idempotency routes must be unique")
        _bounded("idempotency retention", self.retention_seconds, 1, 31_536_000)
        _bounded("idempotency in-flight lease", self.in_flight_lease_seconds, 1, 3_600)
        _bounded("idempotency record capacity", self.max_records, 1, 1_000_000)
        _bounded("idempotency key byte limit", self.max_key_bytes, 1, 4_096)
        _bounded("idempotency request byte limit", self.max_request_bytes, 1, 10_485_760)
        _bounded("idempotency response byte limit", self.max_response_bytes, 1, 10_485_760)
        if self.result_reference_header not in {"location", "idempotency-result-reference"}:
            raise ValueError("idempotency result reference header must be closed")

    def descriptor(self) -> dict[str, object]:
        return {
            "routes": [
                {"path": route.path, "method": route.method.value}
                for route in self.routes
            ],
            "retention_seconds": self.retention_seconds,
            "in_flight_lease_seconds": self.in_flight_lease_seconds,
            "max_records": self.max_records,
            "max_key_bytes": self.max_key_bytes,
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "result_reference_header": self.result_reference_header,
        }


def idempotency_policy_from_descriptor(value: object) -> IdempotencyGatewayPolicy:
    if not isinstance(value, dict) or set(value) != {
        "routes", "retention_seconds", "in_flight_lease_seconds", "max_records",
        "max_key_bytes", "max_request_bytes", "max_response_bytes",
        "result_reference_header",
    }:
        raise ValueError("idempotency policy descriptor has unknown or missing fields")
    route_values = value["routes"]
    if not isinstance(route_values, list):
        raise TypeError("idempotency routes descriptor must be a list")
    routes = []
    for route in route_values:
        if not isinstance(route, dict) or set(route) != {"path", "method"}:
            raise ValueError("idempotency route descriptor has unknown or missing fields")
        if not isinstance(route["path"], str) or not isinstance(route["method"], str):
            raise TypeError("idempotency route descriptor values must be strings")
        routes.append(IdempotencyRoutePolicy(route["path"], IdempotencyMethod(route["method"])))
    integers = {
        name: value[name]
        for name in (
            "retention_seconds", "in_flight_lease_seconds", "max_records",
            "max_key_bytes", "max_request_bytes", "max_response_bytes",
        )
    }
    if any(type(item) is not int for item in integers.values()):
        raise TypeError("idempotency policy bounds must be integers")
    reference_header = value["result_reference_header"]
    if not isinstance(reference_header, str):
        raise TypeError("idempotency result reference header must be a string")
    return IdempotencyGatewayPolicy(tuple(routes), **integers, result_reference_header=reference_header)


@dataclass(frozen=True)
class IdempotencyIdentity:
    gateway_id: str
    key_fingerprint: str
    tenant_fingerprint: str
    actor_fingerprint: str
    method: IdempotencyMethod
    route_fingerprint: str
    payload_fingerprint: str
    intent_fingerprint: str


@dataclass(frozen=True)
class IdempotencyRecord:
    request_id: str
    identity: IdempotencyIdentity
    status: IdempotencyRecordStatus
    created_at: str
    expires_at: str
    lease_expires_at: str
    result_status: int | None = None
    result_reference: str | None = None
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, IdempotencyIdentity):
            raise TypeError("idempotency record identity must be typed")
        if not isinstance(self.status, IdempotencyRecordStatus):
            raise TypeError("idempotency record status must be typed")
        terminal = self.status in {IdempotencyRecordStatus.SUCCEEDED, IdempotencyRecordStatus.FAILED}
        if terminal != (self.result_status is not None and self.completed_at is not None):
            raise ValueError("terminal idempotency record requires result status and completion time")
        if self.result_reference is not None and len(self.result_reference.encode()) > 2_048:
            raise ValueError("idempotency result reference must be bounded")


def idempotency_identity(
    *,
    gateway_id: str,
    key: str,
    tenant: str,
    actor: str,
    method: IdempotencyMethod,
    route: str,
    payload: bytes,
    max_key_bytes: int,
) -> IdempotencyIdentity:
    if not key or len(key.encode()) > max_key_bytes:
        raise ValueError("idempotency key must be nonempty and bounded")
    for label, value in (("gateway", gateway_id), ("tenant", tenant), ("actor", actor), ("route", route)):
        if not value or len(value.encode()) > 2_048:
            raise ValueError(f"idempotency {label} identity must be nonempty and bounded")
    values = {
        "key": _digest(key.encode()),
        "tenant": _digest(tenant.encode()),
        "actor": _digest(actor.encode()),
        "route": _digest(route.encode()),
        "payload": _digest(payload),
    }
    intent = _digest("\0".join((gateway_id, values["tenant"], values["actor"], method.value, values["route"], values["payload"])).encode())
    return IdempotencyIdentity(gateway_id, values["key"], values["tenant"], values["actor"], method, values["route"], values["payload"], intent)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
