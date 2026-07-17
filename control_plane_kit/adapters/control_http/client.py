"""Bounded authenticated HTTP interpreter for the block-control protocol."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping, Protocol

import httpx

from control_plane_kit.adapters.control_http.security import (
    ControlAddressPolicy,
    ControlEndpointObservation,
    CredentialReference,
    PublicAddressResolver,
    SecretResolver,
    authorize_control_endpoint,
)
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.control_routes import control_path
from control_plane_kit.effects import (
    ActivateTarget,
    DrainTarget,
    EffectCapability,
    EffectFailed,
    EffectObservation,
    EffectResult,
    EffectSucceeded,
    EffectUnsupported,
    LiteralEndpointMaterial,
    MaterializedEffectRequest,
    NodeMaterial,
    ObservationKind,
    ObserveSubject,
    RegisterTarget,
    RegisterObserver,
    SocketConnectionMaterial,
)
from control_plane_kit.execution import (
    BoundedEvidence,
    FailureCategory,
    FailureEvidence,
    ObservationStatus,
)
from control_plane_kit.planning import (
    AddSocketConnection,
    RemoveSocketConnection,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit.types import Protocol as NetworkProtocol


@dataclass(frozen=True)
class ControlAuthority:
    """Observed authority and opaque credential selected for one subject."""

    observation: ControlEndpointObservation
    credential: CredentialReference


class ControlAuthorityProvider(Protocol):
    """Resolve live authority metadata without exposing persistence to adapters."""

    def authority_for(self, subject_id: str) -> ControlAuthority: ...


@dataclass(frozen=True)
class ControlHttpLimits:
    """Hard request, response, and log bounds for untrusted control servers."""

    request_bytes: int = 16_384
    response_bytes: int = 65_536
    log_lines: int = 200
    text_characters: int = 2_048

    def __post_init__(self) -> None:
        for value in (
            self.request_bytes,
            self.response_bytes,
            self.log_lines,
            self.text_characters,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("HTTP control limits must be positive integers")


class ControlHttpReadError(RuntimeError):
    """Bounded read failure for operator reads outside the effect grammar."""

    def __init__(self, failure: FailureEvidence) -> None:
        self.failure = failure
        super().__init__(failure.message)


@dataclass(frozen=True)
class StaticControlAuthorityProvider:
    """Small runtime registry useful for local deployments and live tests."""

    authorities: Mapping[str, ControlAuthority]

    def authority_for(self, subject_id: str) -> ControlAuthority:
        try:
            return self.authorities[subject_id]
        except KeyError as error:
            raise KeyError("control authority is not registered") from error


class BlockControlHttpInterpreter:
    """Interpret graph-pinned control effects through bounded HTTP calls."""

    capabilities = frozenset(
        {
            EffectCapability.HEALTH_PROBE,
            EffectCapability.SOCKET_RECONCILIATION,
            EffectCapability.TARGET_REGISTRATION,
            EffectCapability.TARGET_SWITCHING,
            EffectCapability.TARGET_DRAIN,
            EffectCapability.OBSERVATION,
            EffectCapability.OBSERVER_REGISTRATION,
        }
    )

    def __init__(
        self,
        authorities: ControlAuthorityProvider,
        secrets: SecretResolver,
        policy: ControlAddressPolicy,
        *,
        public_resolver: PublicAddressResolver | None = None,
        limits: ControlHttpLimits | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._authorities = authorities
        self._secrets = secrets
        self._policy = policy
        self._public_resolver = public_resolver
        self._limits = limits or ControlHttpLimits()
        self._transport = transport

    def execute(self, request: MaterializedEffectRequest) -> EffectResult:
        """Discover capability, then perform at most one mutation or read."""

        try:
            operation = _operation_for(request)
            endpoint = self._endpoint(operation.subject_id)
            advertised = self._capabilities(endpoint, request)
            if operation.remote_capability not in advertised:
                return EffectUnsupported(request.identity, request.capability)
            payload = self._execute_operation(endpoint, request, operation)
            return EffectSucceeded(
                request.identity,
                BoundedEvidence.from_mapping(
                    {
                        "operation": operation.name,
                        "subject_id": operation.subject_id,
                        "status_code": payload.status_code,
                    }
                ),
                payload.observations,
            )
        except _ControlTransportFailure as error:
            return EffectFailed(request.identity, error.failure)
        except (KeyError, TypeError, ValueError) as error:
            return EffectFailed(
                request.identity,
                _failure(
                    FailureCategory.TERMINAL,
                    "control.invalid-material",
                    "Pinned effect material cannot be interpreted by block control.",
                ),
            )

    def read_status(
        self,
        subject_id: str,
        *,
        request_id: str,
        idempotency_key: str,
        timeout_seconds: int = 10,
    ) -> BoundedEvidence:
        return self._operator_read(
            subject_id,
            control_path("status"),
            request_id,
            idempotency_key,
            timeout_seconds,
            _status_descriptor,
        )

    def read_logs(
        self,
        subject_id: str,
        *,
        request_id: str,
        idempotency_key: str,
        timeout_seconds: int = 10,
    ) -> BoundedEvidence:
        return self._operator_read(
            subject_id,
            control_path("logs"),
            request_id,
            idempotency_key,
            timeout_seconds,
            lambda value: _logs_descriptor(value, self._limits),
        )

    def _operator_read(self, subject_id, path, request_id, idempotency_key, timeout, project):
        try:
            endpoint = self._endpoint(subject_id)
            response = self._json_request(
                endpoint,
                "GET",
                path,
                request_id=request_id,
                idempotency_key=idempotency_key,
                timeout_seconds=timeout,
            )
            return BoundedEvidence.from_mapping(project(response.payload))
        except _ControlTransportFailure as error:
            raise ControlHttpReadError(error.failure) from error

    def _endpoint(self, subject_id: str):
        try:
            authority = self._authorities.authority_for(subject_id)
        except Exception as error:
            raise _ControlTransportFailure(
                _failure(
                    FailureCategory.TERMINAL,
                    "control.authority-unavailable",
                    "No authorized control authority is available for the subject.",
                )
            ) from error
        try:
            return authorize_control_endpoint(
                authority.observation,
                self._policy,
                authority.credential,
                self._secrets,
            )
        except Exception as error:
            raise _ControlTransportFailure(
                _failure(
                    FailureCategory.TERMINAL,
                    "control.authority-rejected",
                    "The control authority or credential was rejected by policy.",
                )
            ) from error

    def _capabilities(self, endpoint, request) -> frozenset[CapabilityName]:
        response = self._json_request(
            endpoint,
            "GET",
            control_path("capabilities"),
            request_id=f"{request.identity.idempotency_key}:capabilities",
            idempotency_key=request.identity.idempotency_key,
            timeout_seconds=request.timeout.total_seconds,
        )
        return _capability_descriptor(response.payload)

    def _execute_operation(self, endpoint, request, operation):
        if operation.name == "register-target":
            current = self._json_request(
                endpoint,
                "GET",
                control_path("targets"),
                request_id=f"{request.identity.idempotency_key}:targets",
                idempotency_key=request.identity.idempotency_key,
                timeout_seconds=request.timeout.total_seconds,
            )
            targets = _targets_descriptor(current.payload)
            targets[operation.target_id] = operation.target_address
            response = self._json_request(
                endpoint,
                "POST",
                control_path("targets"),
                request_id=request.identity.run_id,
                idempotency_key=request.identity.idempotency_key,
                timeout_seconds=request.timeout.total_seconds,
                body=targets,
            )
            _mutation_descriptor(_targets_descriptor, response.payload)
            return _OperationResponse(response.status_code)
        if operation.name == "activate-target":
            response = self._json_request(
                endpoint,
                "POST",
                control_path("active-target"),
                request_id=request.identity.run_id,
                idempotency_key=request.identity.idempotency_key,
                timeout_seconds=request.timeout.total_seconds,
                body={"target_id": operation.target_id},
            )
            _mutation_descriptor(_active_target_descriptor, response.payload)
            return _OperationResponse(response.status_code)
        if operation.name == "register-observer":
            current = self._json_request(
                endpoint,
                "GET",
                control_path("observers"),
                request_id=f"{request.identity.idempotency_key}:observers",
                idempotency_key=request.identity.idempotency_key,
                timeout_seconds=request.timeout.total_seconds,
            )
            observers = _observers_descriptor(current.payload)
            observers[operation.target_id] = operation.target_address
            response = self._json_request(
                endpoint,
                "POST",
                control_path("observers"),
                request_id=request.identity.run_id,
                idempotency_key=request.identity.idempotency_key,
                timeout_seconds=request.timeout.total_seconds,
                body=observers,
            )
            _mutation_descriptor(_observers_descriptor, response.payload)
            return _OperationResponse(response.status_code)
        if operation.name == "drain-target":
            response = self._json_request(
                endpoint,
                "POST",
                control_path("drain-target"),
                request_id=request.identity.run_id,
                idempotency_key=request.identity.idempotency_key,
                timeout_seconds=request.timeout.total_seconds,
                body={"target_id": operation.target_id},
            )
            _mutation_descriptor(_drain_descriptor, response.payload)
            return _OperationResponse(response.status_code)
        if operation.name in ("health", "status"):
            response = self._json_request(
                endpoint,
                "GET",
                control_path(operation.name),
                request_id=request.identity.run_id,
                idempotency_key=request.identity.idempotency_key,
                timeout_seconds=request.timeout.total_seconds,
            )
            descriptor = (
                _health_descriptor(response.payload)
                if operation.name == "health"
                else _status_descriptor(response.payload)
            )
            status = _observation_status(descriptor.get("status"))
            return _OperationResponse(
                response.status_code,
                (
                    EffectObservation(
                        operation.subject_id,
                        operation.observation_kind,
                        status,
                        BoundedEvidence.from_mapping(
                            {"control_status": descriptor.get("status", "unknown")}
                        ),
                    ),
                ),
            )
        raise TypeError("unsupported block-control operation")

    def _json_request(
        self,
        endpoint,
        method: str,
        path: str,
        *,
        request_id: str,
        idempotency_key: str,
        timeout_seconds: int,
        body: Mapping[str, object] | None = None,
    ) -> "_JsonResponse":
        target = endpoint.transport_target(self._public_resolver)
        headers = endpoint.request_headers(
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        if target.host_header is not None:
            headers["Host"] = target.host_header
        content = None
        if body is not None:
            content = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            if len(content) > self._limits.request_bytes:
                raise _ControlTransportFailure(
                    _failure(
                        FailureCategory.TERMINAL,
                        "control.request-too-large",
                        "The bounded control request exceeds its configured limit.",
                    )
                )
        timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(timeout_seconds, 5),
            read=timeout_seconds,
            write=timeout_seconds,
            pool=min(timeout_seconds, 5),
        )
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                request = client.build_request(
                    method,
                    f"{target.base_url}{path}",
                    headers=headers,
                    content=content,
                )
                if target.sni_hostname is not None:
                    request.extensions["sni_hostname"] = target.sni_hostname
                response = client.send(request, stream=True)
                try:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > self._limits.response_bytes:
                            raise _ControlTransportFailure(
                                _after_send_failure(
                                    method,
                                    "control.response-too-large",
                                    "The control response exceeded its configured limit.",
                                )
                            )
                finally:
                    response.close()
        except _ControlTransportFailure:
            raise
        except httpx.TimeoutException as error:
            raise _ControlTransportFailure(
                _after_send_failure(method, "control.timeout", "The control request timed out.")
            ) from error
        except httpx.HTTPError as error:
            raise _ControlTransportFailure(
                _after_send_failure(method, "control.unreachable", "The control authority was unreachable.")
            ) from error
        if 300 <= response.status_code < 400:
            raise _ControlTransportFailure(
                _after_send_failure(method, "control.redirect-rejected", "Control redirects are not permitted.")
            )
        if response.status_code in (401, 403):
            raise _ControlTransportFailure(
                _failure(FailureCategory.TERMINAL, "control.unauthorized", "The control authority rejected authentication.")
            )
        if response.status_code >= 500:
            raise _ControlTransportFailure(
                _after_send_failure(method, "control.remote-failure", "The control authority reported a server failure.")
            )
        if response.status_code >= 400:
            raise _ControlTransportFailure(
                _failure(FailureCategory.TERMINAL, "control.rejected", "The control authority rejected the request.")
            )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise _malformed(method)
        try:
            payload = json.loads(bytes(raw))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _malformed(method) from error
        if not isinstance(payload, dict):
            raise _malformed(method)
        return _JsonResponse(response.status_code, payload)


@dataclass(frozen=True)
class _ControlOperation:
    name: str
    subject_id: str
    remote_capability: CapabilityName
    target_id: str = ""
    target_address: str = ""
    observation_kind: ObservationKind = ObservationKind.STATUS


@dataclass(frozen=True)
class _JsonResponse:
    status_code: int
    payload: dict[str, object]


@dataclass(frozen=True)
class _OperationResponse:
    status_code: int
    observations: tuple[EffectObservation, ...] = ()


class _ControlTransportFailure(RuntimeError):
    def __init__(self, failure: FailureEvidence) -> None:
        self.failure = failure
        super().__init__(failure.message)


def _operation_for(request: MaterializedEffectRequest) -> _ControlOperation:
    action = request.action
    material = request.material
    match action:
        case RegisterTarget(controller_id=controller, target_id=target):
            return _ControlOperation(
                "register-target",
                controller,
                CapabilityName.TARGET_MUTABLE,
                target,
                _target_address(material),
            )
        case ActivateTarget(controller_id=controller, target_id=target):
            return _ControlOperation("activate-target", controller, CapabilityName.SWITCHABLE, target)
        case DrainTarget(controller_id=controller, target_id=target):
            return _ControlOperation("drain-target", controller, CapabilityName.DRAINABLE, target)
        case RegisterObserver(controller_id=controller, observer_id=observer):
            return _ControlOperation(
                "register-observer",
                controller,
                CapabilityName.OBSERVER_MUTABLE,
                observer,
                _target_address(material),
            )
        case ObserveSubject(subject_id=subject, kind=kind):
            return _ControlOperation(
                "health" if kind is ObservationKind.HEALTH else "status",
                subject,
                CapabilityName.HEALTH_CHECKABLE,
                observation_kind=kind,
            )
        case AddSocketConnection():
            edge = _edge(material)
            return _ControlOperation(
                "register-target",
                edge.consumer_node_id,
                CapabilityName.TARGET_MUTABLE,
                edge.provider_node_id,
                _target_address(edge),
            )
        case SwitchSocketConnection():
            edge = _edge(material)
            return _ControlOperation(
                "activate-target",
                edge.consumer_node_id,
                CapabilityName.SWITCHABLE,
                edge.provider_node_id,
            )
        case RemoveSocketConnection():
            edge = _edge(material)
            return _ControlOperation(
                "drain-target",
                edge.consumer_node_id,
                CapabilityName.DRAINABLE,
                edge.provider_node_id,
            )
        case WaitForHealthy():
            if not isinstance(material, NodeMaterial):
                raise TypeError("health effect requires node material")
            return _ControlOperation(
                "health",
                material.node_id,
                CapabilityName.HEALTH_CHECKABLE,
                observation_kind=ObservationKind.HEALTH,
            )
    raise TypeError("effect operation is not implemented by block control")


def _edge(material) -> SocketConnectionMaterial:
    if not isinstance(material, SocketConnectionMaterial):
        raise TypeError("socket effect requires socket connection material")
    return material


def _target_address(material) -> str:
    edge = _edge(material)
    endpoint = edge.provider_endpoint
    if endpoint.protocol not in (NetworkProtocol.HTTP, NetworkProtocol.TCP, NetworkProtocol.POSTGRES):
        raise TypeError("target protocol cannot be registered")
    if not isinstance(endpoint.address, LiteralEndpointMaterial):
        raise TypeError("target endpoint must be plan-pinned literal material")
    return endpoint.address.value


def _capability_descriptor(payload: dict[str, object]) -> frozenset[CapabilityName]:
    _exact_keys(payload, {"block_id", "capabilities"})
    _text(payload.get("block_id"))
    raw = payload.get("capabilities")
    if not isinstance(raw, list) or len(raw) > 64:
        raise _malformed()
    values = set()
    allowed = {"name", "label", "description", "route_set", "route_path"}
    for item in raw:
        if not isinstance(item, dict) or not set(item).issubset(allowed):
            raise _malformed()
        try:
            values.add(CapabilityName(_text(item.get("name"))))
        except ValueError as error:
            raise _malformed() from error
    return frozenset(values)


def _targets_descriptor(payload: dict[str, object]) -> dict[str, str]:
    _exact_keys(payload, {"block_id", "active_target", "targets"})
    _text(payload.get("block_id"))
    active = payload.get("active_target")
    if not isinstance(active, str):
        raise _malformed()
    targets = payload.get("targets")
    if not isinstance(targets, dict) or len(targets) > 256:
        raise _malformed()
    result = {}
    for key, value in targets.items():
        result[_text(key)] = _text(value, maximum=4096)
    return result


def _active_target_descriptor(payload: dict[str, object]) -> None:
    _exact_keys(payload, {"block_id", "active_target"})
    _text(payload.get("block_id"))
    _text(payload.get("active_target"))


def _observers_descriptor(payload: dict[str, object]) -> dict[str, str]:
    _exact_keys(payload, {"block_id", "observers"})
    _text(payload.get("block_id"))
    observers = payload.get("observers")
    if not isinstance(observers, dict) or len(observers) > 256:
        raise _malformed()
    return {_text(key): _text(value, maximum=4096) for key, value in observers.items()}


def _drain_descriptor(payload: dict[str, object]) -> None:
    _exact_keys(payload, {"block_id", "draining_target"})
    _text(payload.get("block_id"))
    _text(payload.get("draining_target"))


def _health_descriptor(payload: dict[str, object]) -> dict[str, str]:
    _exact_keys(payload, {"block_id", "status"})
    return {"block_id": _text(payload.get("block_id")), "status": _text(payload.get("status"))}


def _status_descriptor(payload: dict[str, object]) -> dict[str, object]:
    if "block_id" not in payload or len(payload) > 64:
        raise _malformed()
    block_id = _text(payload.get("block_id"))
    status = payload.get("status", "unknown")
    if not isinstance(status, str):
        status = "unknown"
    return {"block_id": block_id, "status": status[:128]}


def _logs_descriptor(payload: dict[str, object], limits: ControlHttpLimits) -> dict[str, object]:
    _exact_keys(payload, {"block_id", "lines"})
    block_id = _text(payload.get("block_id"))
    lines = payload.get("lines")
    if not isinstance(lines, list) or len(lines) > limits.log_lines:
        raise _malformed()
    return {"block_id": block_id, "lines": [_text(value, maximum=limits.text_characters) for value in lines]}


def _observation_status(value: object) -> ObservationStatus:
    if not isinstance(value, str):
        return ObservationStatus.UNKNOWN
    normalized = value.lower()
    if normalized in ("ok", "healthy"):
        return ObservationStatus.HEALTHY
    if normalized in ("unhealthy", "failed", "error"):
        return ObservationStatus.UNHEALTHY
    if normalized in ("starting", "process_started", "reachable"):
        return ObservationStatus(normalized)
    return ObservationStatus.UNKNOWN


def _exact_keys(value: dict[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise _malformed()


def _text(value: object, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _malformed()
    return value


def _malformed(method: str = "GET") -> _ControlTransportFailure:
    return _ControlTransportFailure(
        _after_send_failure(method, "control.malformed-response", "The control authority returned a malformed response.")
    )


def _failure(category: FailureCategory, code: str, message: str) -> FailureEvidence:
    return FailureEvidence(category, code, message)


def _after_send_failure(method: str, code: str, message: str) -> FailureEvidence:
    category = (
        FailureCategory.UNCERTAIN
        if method == "POST"
        else FailureCategory.RETRYABLE
    )
    return _failure(category, code, message)


def _mutation_descriptor(project, payload):
    try:
        return project(payload)
    except _ControlTransportFailure as error:
        raise _ControlTransportFailure(
            FailureEvidence(
                FailureCategory.UNCERTAIN,
                error.failure.code,
                error.failure.message,
                error.failure.details,
            )
        ) from error
