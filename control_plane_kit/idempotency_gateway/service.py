"""Durable idempotent HTTP command service with split effect transactions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TypeAlias

from control_plane_kit.domains.idempotency import (
    IdempotencyGatewayPolicy,
    IdempotencyMethod,
    IdempotencyOutcome,
    IdempotencyRecord,
    IdempotencyRecordStatus,
    idempotency_identity,
)
from control_plane_kit.idempotency_gateway.protocols import IdempotencyUnitOfWork
from control_plane_kit.products.servers.support.http_messages import HttpHandler, HttpRequest, HttpResponse


class IdempotencyGatewayScope(StrEnum):
    EXECUTE = "idempotency:execute"


class IdempotencyGatewayError(RuntimeError):
    pass


class IdempotencyGatewayDenied(IdempotencyGatewayError):
    pass


@dataclass(frozen=True)
class IdempotencyGatewayAuthority:
    actor_id: str
    scopes: frozenset[IdempotencyGatewayScope]

    def __post_init__(self) -> None:
        if not self.actor_id or not isinstance(self.scopes, frozenset) or any(
            not isinstance(scope, IdempotencyGatewayScope) for scope in self.scopes
        ):
            raise TypeError("idempotency gateway authority must be typed")


@dataclass(frozen=True)
class ExecuteIdempotentHttp:
    gateway_id: str
    policy: IdempotencyGatewayPolicy
    request: HttpRequest
    idempotency_key: str
    tenant_identity: str
    actor_identity: str
    authority: IdempotencyGatewayAuthority


@dataclass(frozen=True)
class IdempotencyGatewayResult:
    outcome: IdempotencyOutcome
    response: HttpResponse
    record: IdempotencyRecord | None


UnitOfWorkFactory: TypeAlias = Callable[[], IdempotencyUnitOfWork]


class IdempotencyGatewayService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        target: HttpHandler,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._target = target
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: ExecuteIdempotentHttp) -> IdempotencyGatewayResult:
        if IdempotencyGatewayScope.EXECUTE not in command.authority.scopes:
            raise IdempotencyGatewayDenied("scope 'idempotency:execute' is missing")
        if len(command.request.body) > command.policy.max_request_bytes:
            return _result(IdempotencyOutcome.INELIGIBLE, 413)
        method = _eligible_method(command)
        if method is None:
            return _result(IdempotencyOutcome.INELIGIBLE, 422)
        identity = idempotency_identity(
            gateway_id=command.gateway_id,
            key=command.idempotency_key,
            tenant=command.tenant_identity,
            actor=command.actor_identity,
            method=method,
            route=_request_target(command.request),
            payload=command.request.body,
            max_key_bytes=command.policy.max_key_bytes,
        )
        now = _aware(self._clock())
        with self._unit_of_work_factory() as work:
            store = work.store
            store.lock_key(identity.gateway_id, identity.tenant_fingerprint, identity.key_fingerprint)
            store.delete_expired_terminal(identity.gateway_id, _timestamp(now))
            existing = store.get_for_key(identity.gateway_id, identity.tenant_fingerprint, identity.key_fingerprint)
            if existing is not None:
                result = _existing_result(store, existing, identity.intent_fingerprint, now)
                work.commit()
                return result
            if store.count_for_gateway(identity.gateway_id) >= command.policy.max_records:
                work.commit()
                return _result(IdempotencyOutcome.CAPACITY_EXHAUSTED, 507)
            record = IdempotencyRecord(
                self._id_factory(), identity, IdempotencyRecordStatus.IN_FLIGHT,
                _timestamp(now),
                _timestamp(now + timedelta(seconds=command.policy.retention_seconds)),
                _timestamp(now + timedelta(seconds=command.policy.in_flight_lease_seconds)),
            )
            store.add(record)
            work.commit()

        try:
            response = self._target(_strip_idempotency_key(command.request))
        except Exception:
            uncertain = self._mark_uncertain(record)
            return IdempotencyGatewayResult(
                IdempotencyOutcome.UNCERTAIN,
                HttpResponse.text("Idempotency outcome uncertain", status_code=503),
                uncertain,
            )

        if len(response.body) > command.policy.max_response_bytes:
            response = HttpResponse.text("Bad Gateway", status_code=502)
        completed_at = _aware(self._clock())
        status = IdempotencyRecordStatus.SUCCEEDED if 200 <= response.status_code < 400 else IdempotencyRecordStatus.FAILED
        reference = _bounded_result_reference(response, command.policy.result_reference_header)
        with self._unit_of_work_factory() as work:
            store = work.store
            store.lock_key(identity.gateway_id, identity.tenant_fingerprint, identity.key_fingerprint)
            completed = store.complete(
                record.request_id,
                expected=IdempotencyRecordStatus.IN_FLIGHT,
                replacement=status,
                result_status=response.status_code,
                result_reference=reference,
                completed_at=_timestamp(completed_at),
            )
            if completed is None:
                raise IdempotencyGatewayError("idempotency result lost reservation ownership")
            work.commit()
        return IdempotencyGatewayResult(IdempotencyOutcome.EXECUTED, response, completed)

    def _mark_uncertain(self, record: IdempotencyRecord) -> IdempotencyRecord:
        identity = record.identity
        with self._unit_of_work_factory() as work:
            store = work.store
            store.lock_key(identity.gateway_id, identity.tenant_fingerprint, identity.key_fingerprint)
            uncertain = store.mark_uncertain(record.request_id)
            if uncertain is None:
                raise IdempotencyGatewayError("idempotency uncertainty lost reservation ownership")
            work.commit()
            return uncertain


def _existing_result(store, record: IdempotencyRecord, intent_fingerprint: str, now: datetime) -> IdempotencyGatewayResult:
    if record.identity.intent_fingerprint != intent_fingerprint:
        return _result(IdempotencyOutcome.CONFLICT, 409, record)
    match record.status:
        case IdempotencyRecordStatus.SUCCEEDED | IdempotencyRecordStatus.FAILED:
            headers = {} if record.result_reference is None else {"idempotency-result-reference": record.result_reference}
            return IdempotencyGatewayResult(
                IdempotencyOutcome.REPLAYED,
                HttpResponse(record.result_status or 500, headers, b""),
                record,
            )
        case IdempotencyRecordStatus.UNCERTAIN:
            return _result(IdempotencyOutcome.UNCERTAIN, 409, record)
        case IdempotencyRecordStatus.IN_FLIGHT:
            if _parse(record.lease_expires_at) <= now:
                uncertain = store.mark_uncertain(record.request_id)
                if uncertain is None:
                    raise IdempotencyGatewayError("expired idempotency reservation changed concurrently")
                return _result(IdempotencyOutcome.UNCERTAIN, 409, uncertain)
            return _result(IdempotencyOutcome.IN_FLIGHT, 409, record)


def _eligible_method(command: ExecuteIdempotentHttp) -> IdempotencyMethod | None:
    try:
        method = IdempotencyMethod(command.request.method.upper())
    except ValueError:
        return None
    return method if any(route.method is method and route.path == command.request.path for route in command.policy.routes) else None


def _request_target(request: HttpRequest) -> str:
    return request.path if not request.query else f"{request.path}?{request.query}"


def _strip_idempotency_key(request: HttpRequest) -> HttpRequest:
    return HttpRequest(
        request.method, request.path, request.query,
        {key: value for key, value in request.headers.items() if key.lower() not in {"idempotency-key", "x-cpk-identity-attestation"}},
        request.body,
    )


def _bounded_result_reference(response: HttpResponse, header_name: str) -> str | None:
    value = next((value for key, value in response.headers.items() if key.lower() == header_name), None)
    if value is None:
        return None
    if not value or len(value.encode()) > 2_048 or any(character in value for character in "\r\n\0"):
        return None
    return value


def _result(outcome: IdempotencyOutcome, status: int, record: IdempotencyRecord | None = None) -> IdempotencyGatewayResult:
    return IdempotencyGatewayResult(outcome, HttpResponse.text(outcome.value, status_code=status), record)


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise IdempotencyGatewayError("idempotency clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
