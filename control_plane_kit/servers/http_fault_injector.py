"""Explicitly test-only HTTP fault-injection block and teaching interpreter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import random
import time

from control_plane_kit.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
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


class InjectedHttpStatus(StrEnum):
    INTERNAL_SERVER_ERROR = "500"
    BAD_GATEWAY = "502"
    SERVICE_UNAVAILABLE = "503"
    GATEWAY_TIMEOUT = "504"

    @property
    def code(self) -> int:
        return int(self.value)


class FaultKind(StrEnum):
    DELAY = "delay"
    STATUS = "status"
    CONNECTION_TERMINATION = "connection-termination"
    TRUNCATION = "truncation"
    SEEDED_PROBABILITY = "seeded-probability"


class TargetOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    HTTP_FAILURE = "http-failure"
    UNAVAILABLE = "unavailable"
    NOT_ATTEMPTED = "not-attempted"


@dataclass(frozen=True)
class DelayFault:
    delay_ms: int

    def __post_init__(self) -> None:
        _bounded("fault delay", self.delay_ms, 1, 60_000)


@dataclass(frozen=True)
class StatusFault:
    status: InjectedHttpStatus

    def __post_init__(self) -> None:
        if not isinstance(self.status, InjectedHttpStatus):
            raise TypeError("injected status must be typed")


@dataclass(frozen=True)
class ConnectionTerminationFault:
    pass


@dataclass(frozen=True)
class TruncationFault:
    retained_bytes: int

    def __post_init__(self) -> None:
        _bounded("fault retained bytes", self.retained_bytes, 0, 1_048_576)


@dataclass(frozen=True)
class SeededProbabilityFault:
    probability_basis_points: int
    seed: int
    status: InjectedHttpStatus = InjectedHttpStatus.SERVICE_UNAVAILABLE

    def __post_init__(self) -> None:
        _bounded("fault probability", self.probability_basis_points, 1, 10_000)
        _bounded("fault seed", self.seed, 0, 2_147_483_647)
        if not isinstance(self.status, InjectedHttpStatus):
            raise TypeError("probabilistic injected status must be typed")


HttpFault = (
    DelayFault
    | StatusFault
    | ConnectionTerminationFault
    | TruncationFault
    | SeededProbabilityFault
)


@dataclass(frozen=True)
class DisabledFaultInjection:
    pass


@dataclass(frozen=True)
class EnabledFaultInjection:
    fault: HttpFault

    def __post_init__(self) -> None:
        if not isinstance(
            self.fault,
            (
                DelayFault,
                StatusFault,
                ConnectionTerminationFault,
                TruncationFault,
                SeededProbabilityFault,
            ),
        ):
            raise TypeError("enabled fault injection requires a typed fault")


FaultInjectionState = DisabledFaultInjection | EnabledFaultInjection


@dataclass(frozen=True)
class FaultInjectionLimits:
    max_request_bytes: int = 65_536
    max_response_bytes: int = 65_536

    def __post_init__(self) -> None:
        _bounded("fault request byte limit", self.max_request_bytes, 1, 1_048_576)
        _bounded("fault response byte limit", self.max_response_bytes, 1, 1_048_576)


@dataclass(frozen=True)
class FaultInjectionObservation:
    request_count: int
    injected_count: int
    natural_failure_count: int
    active: FaultInjectionState
    latest_injection: FaultKind | None
    latest_target_outcome: TargetOutcome | None

    def descriptor(self) -> dict[str, object]:
        return {
            "request_count": self.request_count,
            "injected_count": self.injected_count,
            "natural_failure_count": self.natural_failure_count,
            "active": fault_injection_state_descriptor(self.active),
            "latest_injection": (
                None if self.latest_injection is None else self.latest_injection.value
            ),
            "latest_target_outcome": (
                None
                if self.latest_target_outcome is None
                else self.latest_target_outcome.value
            ),
        }


class InjectedConnectionTermination(ConnectionError):
    """Typed in-memory signal corresponding to an injected socket close."""


@dataclass
class HttpFaultInjectionServer:
    targets: Mapping[str, HttpHandler]
    target: str
    limits: FaultInjectionLimits = field(default_factory=FaultInjectionLimits)
    sleeper: Callable[[float], None] = time.sleep
    _active: FaultInjectionState = field(
        init=False, default_factory=DisabledFaultInjection
    )
    _random: random.Random | None = field(init=False, default=None)
    _request_count: int = field(init=False, default=0)
    _injected_count: int = field(init=False, default=0)
    _natural_failure_count: int = field(init=False, default=0)
    _latest_injection: FaultKind | None = field(init=False, default=None)
    _latest_target_outcome: TargetOutcome | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.target not in self.targets:
            raise KeyError(f"unknown target {self.target!r}")

    def replace_activation(self, state: FaultInjectionState) -> FaultInjectionObservation:
        if not isinstance(state, (DisabledFaultInjection, EnabledFaultInjection)):
            raise TypeError("fault activation must be typed")
        self._active = state
        self._random = (
            random.Random(state.fault.seed)
            if isinstance(state, EnabledFaultInjection)
            and isinstance(state.fault, SeededProbabilityFault)
            else None
        )
        return self.observation()

    def handle(self, request: HttpRequest) -> HttpResponse:
        self._request_count += 1
        self._latest_injection = None
        self._latest_target_outcome = None
        if len(request.body) > self.limits.max_request_bytes:
            return HttpResponse.text("Request Entity Too Large", status_code=413)

        fault = self._active.fault if isinstance(self._active, EnabledFaultInjection) else None
        if isinstance(fault, DelayFault):
            self._record_injection(FaultKind.DELAY)
            self.sleeper(fault.delay_ms / 1_000)
        elif isinstance(fault, StatusFault):
            self._record_injection(FaultKind.STATUS)
            self._latest_target_outcome = TargetOutcome.NOT_ATTEMPTED
            return _injected_status(fault.status)
        elif isinstance(fault, ConnectionTerminationFault):
            self._record_injection(FaultKind.CONNECTION_TERMINATION)
            self._latest_target_outcome = TargetOutcome.NOT_ATTEMPTED
            raise InjectedConnectionTermination("test-only injected connection termination")
        elif isinstance(fault, SeededProbabilityFault):
            if self._random is None:
                raise RuntimeError("probabilistic fault random source is not initialized")
            if self._random.randrange(10_000) < fault.probability_basis_points:
                self._record_injection(FaultKind.SEEDED_PROBABILITY)
                self._latest_target_outcome = TargetOutcome.NOT_ATTEMPTED
                return _injected_status(fault.status)

        response = self._call_target(request)
        if isinstance(fault, TruncationFault):
            self._record_injection(FaultKind.TRUNCATION)
            body = response.body[: fault.retained_bytes]
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() != "content-length"
            }
            headers["content-length"] = str(len(body))
            return HttpResponse(response.status_code, headers, body)
        return response

    def observation(self) -> FaultInjectionObservation:
        return FaultInjectionObservation(
            self._request_count,
            self._injected_count,
            self._natural_failure_count,
            self._active,
            self._latest_injection,
            self._latest_target_outcome,
        )

    def _call_target(self, request: HttpRequest) -> HttpResponse:
        try:
            response = self.targets[self.target](request)
        except Exception:  # noqa: BLE001 - target loss is closed natural evidence.
            self._natural_failure_count += 1
            self._latest_target_outcome = TargetOutcome.UNAVAILABLE
            return HttpResponse.text("Bad Gateway", status_code=502)
        if len(response.body) > self.limits.max_response_bytes:
            self._natural_failure_count += 1
            self._latest_target_outcome = TargetOutcome.UNAVAILABLE
            return HttpResponse.text("Bad Gateway", status_code=502)
        if response.status_code >= 500:
            self._natural_failure_count += 1
            self._latest_target_outcome = TargetOutcome.HTTP_FAILURE
        else:
            self._latest_target_outcome = TargetOutcome.SUCCEEDED
        return response

    def _record_injection(self, kind: FaultKind) -> None:
        self._injected_count += 1
        self._latest_injection = kind


def fault_injection_state_descriptor(state: FaultInjectionState) -> dict[str, object]:
    match state:
        case DisabledFaultInjection():
            return {"variant": "disabled"}
        case EnabledFaultInjection(fault=fault):
            return {"variant": "enabled", "fault": _fault_descriptor(fault)}


def fault_injection_state_from_descriptor(
    descriptor: Mapping[str, object],
) -> FaultInjectionState:
    variant = descriptor.get("variant")
    if variant == "disabled":
        _exact_keys(descriptor, {"variant"}, "disabled fault state")
        return DisabledFaultInjection()
    if variant == "enabled":
        _exact_keys(descriptor, {"variant", "fault"}, "enabled fault state")
        fault = descriptor["fault"]
        if not isinstance(fault, Mapping):
            raise ValueError("enabled fault state requires a fault descriptor")
        return EnabledFaultInjection(_fault_from_descriptor(fault))
    raise ValueError(f"unknown fault activation variant {variant!r}")


def http_fault_injector_block(
    block_id: str = "http-fault-injector",
    *,
    display_name: str = "HTTP Fault Injector (Test Only)",
    image: str = "python:3.14-alpine",
    limits: FaultInjectionLimits = FaultInjectionLimits(),
    control_secret_reference: str = "secret://http-fault-injector/control-token",
) -> ProxyBlock:
    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_FAULT_INJECTOR,
            maturity=ProductMaturity.TEST_ONLY,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.FAULT_STATE_READABLE,
                CapabilityName.FAULT_MUTABLE,
            ),
        ),
        DockerImageImplementation(
            image=image,
            command=http_fault_injector_command(limits),
            ports={"internal": 8080},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_FAULT_CONTROL_TOKEN",
                    SecretReference(control_secret_reference),
                ),
            ),
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("target", Protocol.HTTP, ("FAULT_TARGET_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_fault_injector_command(
    limits: FaultInjectionLimits = FaultInjectionLimits(),
    *,
    port: int = 8080,
) -> tuple[str, ...]:
    if not isinstance(limits, FaultInjectionLimits):
        raise TypeError("fault injection limits must be typed")
    _bounded("fault injector port", port, 1, 65_535)
    return render_python_command(
        "http_fault_injector.py.j2",
        max_request_bytes=limits.max_request_bytes,
        max_response_bytes=limits.max_response_bytes,
        port=port,
    )


def _injected_status(status: InjectedHttpStatus) -> HttpResponse:
    return HttpResponse.text("Injected test fault", status_code=status.code)


def _fault_descriptor(fault: HttpFault) -> dict[str, object]:
    match fault:
        case DelayFault(delay_ms=delay_ms):
            return {"kind": FaultKind.DELAY.value, "delay_ms": delay_ms}
        case StatusFault(status=status):
            return {"kind": FaultKind.STATUS.value, "status": status.value}
        case ConnectionTerminationFault():
            return {"kind": FaultKind.CONNECTION_TERMINATION.value}
        case TruncationFault(retained_bytes=retained_bytes):
            return {
                "kind": FaultKind.TRUNCATION.value,
                "retained_bytes": retained_bytes,
            }
        case SeededProbabilityFault(
            probability_basis_points=probability_basis_points,
            seed=seed,
            status=status,
        ):
            return {
                "kind": FaultKind.SEEDED_PROBABILITY.value,
                "probability_basis_points": probability_basis_points,
                "seed": seed,
                "status": status.value,
            }


def _fault_from_descriptor(descriptor: Mapping[str, object]) -> HttpFault:
    try:
        kind = FaultKind(descriptor.get("kind"))
    except ValueError as error:
        raise ValueError(f"unknown fault kind {descriptor.get('kind')!r}") from error
    match kind:
        case FaultKind.DELAY:
            _exact_keys(descriptor, {"kind", "delay_ms"}, "delay fault")
            return DelayFault(_integer(descriptor, "delay_ms"))
        case FaultKind.STATUS:
            _exact_keys(descriptor, {"kind", "status"}, "status fault")
            return StatusFault(InjectedHttpStatus(str(descriptor["status"])))
        case FaultKind.CONNECTION_TERMINATION:
            _exact_keys(descriptor, {"kind"}, "connection termination fault")
            return ConnectionTerminationFault()
        case FaultKind.TRUNCATION:
            _exact_keys(descriptor, {"kind", "retained_bytes"}, "truncation fault")
            return TruncationFault(_integer(descriptor, "retained_bytes"))
        case FaultKind.SEEDED_PROBABILITY:
            _exact_keys(
                descriptor,
                {"kind", "probability_basis_points", "seed", "status"},
                "seeded probability fault",
            )
            return SeededProbabilityFault(
                _integer(descriptor, "probability_basis_points"),
                _integer(descriptor, "seed"),
                InjectedHttpStatus(str(descriptor["status"])),
            )


def _integer(descriptor: Mapping[str, object], key: str) -> int:
    value = descriptor[key]
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer")
    return value


def _exact_keys(
    descriptor: Mapping[str, object],
    expected: set[str],
    label: str,
) -> None:
    if set(descriptor) != expected:
        raise ValueError(f"{label} keys must be exactly {sorted(expected)!r}")
