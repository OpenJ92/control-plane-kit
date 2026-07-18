"""Provider-neutral typed values for external effect interpretation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import TypeAlias

from control_plane_kit.execution import (
    BoundedEvidence,
    EndpointContext,
    FailureEvidence,
    ObservationStatus,
    ProbeKind,
    ProbeOutcome,
)
from control_plane_kit.planning import (
    ActivityId,
    ActivityOperation,
    AddSocketConnection,
    DestroyDataResource,
    PlannedActivity,
    Compensate,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    ReviewChange,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)


MAX_EFFECT_TIMEOUT_SECONDS = 300
_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class EffectValueError(ValueError):
    """Raised when an effect value cannot be represented safely."""


class UnsupportedEffectOperation(EffectValueError):
    """Raised before interpretation for work outside the effect language."""


class EffectCapability(StrEnum):
    """Closed powers that a provider-neutral interpreter may advertise."""

    NODE_LIFECYCLE = "node-lifecycle"
    RUNTIME_LIFECYCLE = "runtime-lifecycle"
    HEALTH_PROBE = "health-probe"
    SOCKET_RECONCILIATION = "socket-reconciliation"
    NODE_RECONCILIATION = "node-reconciliation"
    RUNTIME_RECONCILIATION = "runtime-reconciliation"
    TARGET_REGISTRATION = "target-registration"
    TARGET_SWITCHING = "target-switching"
    TARGET_DRAIN = "target-drain"
    OBSERVATION = "observation"
    OBSERVER_REGISTRATION = "observer-registration"
    DATA_DESTRUCTION = "data-destruction"


class EffectPurpose(StrEnum):
    """Closed reason an external effect is being requested."""

    FORWARD = "forward"
    COMPENSATION = "compensation"


class ObservationKind(StrEnum):
    """Closed kinds of operator-relevant observations."""

    HEALTH = "health"
    STATUS = "status"


@dataclass(frozen=True)
class EffectIdentity:
    """Stable run, activity, attempt, and idempotency coordinates."""

    run_id: str
    activity_id: ActivityId
    attempt: int
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_text(self.run_id, "effect run id")
        if not isinstance(self.activity_id, ActivityId):
            raise TypeError("effect activity identity must be ActivityId")
        if type(self.attempt) is not int or self.attempt < 1:
            raise EffectValueError("effect attempt must be a positive integer")
        _require_text(self.idempotency_key, "effect idempotency key")


@dataclass(frozen=True)
class TimeoutPolicy:
    """Bounded total timeout and optional polling interval."""

    total_seconds: int = 30
    interval_seconds: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.total_seconds) is not int
            or self.total_seconds < 1
            or self.total_seconds > MAX_EFFECT_TIMEOUT_SECONDS
        ):
            raise EffectValueError(
                f"effect timeout must be between 1 and {MAX_EFFECT_TIMEOUT_SECONDS} seconds"
            )
        if self.interval_seconds is not None and (
            type(self.interval_seconds) is not int
            or self.interval_seconds < 1
            or self.interval_seconds > self.total_seconds
        ):
            raise EffectValueError(
                "effect polling interval must be positive and not exceed timeout"
            )


@dataclass(frozen=True, order=True)
class EffectSecretReference:
    """Opaque secret identity; secret values never enter the effect language."""

    reference_id: str

    def __post_init__(self) -> None:
        _require_reference(self.reference_id, "secret reference")


@dataclass(frozen=True)
class EndpointReference:
    """Opaque endpoint identity resolved only by a concrete interpreter."""

    reference_id: str

    def __post_init__(self) -> None:
        _require_reference(self.reference_id, "endpoint reference")


@dataclass(frozen=True)
class RegisterTarget:
    """Register an opaque endpoint under a controller-owned target identity."""

    controller_id: str
    target_id: str
    endpoint: EndpointReference

    def __post_init__(self) -> None:
        _require_text(self.controller_id, "target controller id")
        _require_text(self.target_id, "target id")
        if not isinstance(self.endpoint, EndpointReference):
            raise TypeError("registered target endpoint must be EndpointReference")


@dataclass(frozen=True)
class ActivateTarget:
    """Select one previously registered target for new traffic."""

    controller_id: str
    target_id: str

    def __post_init__(self) -> None:
        _require_text(self.controller_id, "target controller id")
        _require_text(self.target_id, "target id")


@dataclass(frozen=True)
class DrainTarget:
    """Request bounded retirement of traffic from a registered target."""

    controller_id: str
    target_id: str

    def __post_init__(self) -> None:
        _require_text(self.controller_id, "target controller id")
        _require_text(self.target_id, "target id")


@dataclass(frozen=True)
class ObserveSubject:
    """Request a typed observation without claiming desired-state mutation."""

    subject_id: str
    kind: ObservationKind

    def __post_init__(self) -> None:
        _require_text(self.subject_id, "observation subject id")
        if not isinstance(self.kind, ObservationKind):
            raise TypeError("observation kind must be ObservationKind")


@dataclass(frozen=True)
class RegisterObserver:
    """Register an opaque endpoint under a controller-owned observer identity."""

    controller_id: str
    observer_id: str
    endpoint: EndpointReference

    def __post_init__(self) -> None:
        _require_text(self.controller_id, "observer controller id")
        _require_text(self.observer_id, "observer id")
        if not isinstance(self.endpoint, EndpointReference):
            raise TypeError("registered observer endpoint must be EndpointReference")


ControlEffectAction: TypeAlias = (
    RegisterTarget | ActivateTarget | DrainTarget | RegisterObserver | ObserveSubject
)
EffectAction: TypeAlias = ActivityOperation | ControlEffectAction


@dataclass(frozen=True)
class EffectRequest:
    """One typed external-effect request independent of provider mechanics."""

    identity: EffectIdentity
    action: EffectAction
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    secret_references: tuple[EffectSecretReference, ...] = ()
    purpose: EffectPurpose = EffectPurpose.FORWARD

    def __post_init__(self) -> None:
        if not isinstance(self.identity, EffectIdentity):
            raise TypeError("effect request identity must be EffectIdentity")
        required_capability(self.action)
        if not isinstance(self.timeout, TimeoutPolicy):
            raise TypeError("effect request timeout must be TimeoutPolicy")
        if not all(
            isinstance(value, EffectSecretReference)
            for value in self.secret_references
        ):
            raise TypeError("effect request secrets must be reference values")
        if not isinstance(self.purpose, EffectPurpose):
            raise TypeError("effect request purpose must be EffectPurpose")
        references = tuple(sorted(self.secret_references))
        if len(references) != len(set(references)):
            raise EffectValueError("effect request repeats a secret reference")
        object.__setattr__(self, "secret_references", references)

    @property
    def capability(self) -> EffectCapability:
        return required_capability(self.action)


@dataclass(frozen=True)
class EffectObservation:
    """Bounded observation suitable for later durable adaptation."""

    subject_id: str
    kind: ObservationKind
    status: ObservationStatus
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    graph_id: str | None = None
    probe_kind: ProbeKind | None = None
    probe_outcome: ProbeOutcome | None = None
    endpoint_context: EndpointContext | None = None

    def __post_init__(self) -> None:
        _require_text(self.subject_id, "effect observation subject id")
        if not isinstance(self.kind, ObservationKind):
            raise TypeError("effect observation kind must be ObservationKind")
        if not isinstance(self.status, ObservationStatus):
            raise TypeError("effect observation status must be ObservationStatus")
        if not isinstance(self.evidence, BoundedEvidence):
            raise TypeError("effect observation evidence must be BoundedEvidence")
        correlated = (self.graph_id, self.probe_kind, self.probe_outcome)
        if any(value is not None for value in correlated) and any(
            value is None for value in correlated
        ):
            raise EffectValueError(
                "correlated effect observation requires graph, probe kind, and outcome"
            )
        if self.graph_id is not None:
            _require_text(self.graph_id, "effect observation graph id")
        if self.probe_kind is not None and not isinstance(self.probe_kind, ProbeKind):
            raise TypeError("effect observation probe kind must be ProbeKind")
        if self.probe_outcome is not None and not isinstance(
            self.probe_outcome, ProbeOutcome
        ):
            raise TypeError("effect observation probe outcome must be ProbeOutcome")
        if self.endpoint_context is not None and not isinstance(
            self.endpoint_context, EndpointContext
        ):
            raise TypeError("effect observation endpoint context must be EndpointContext")


@dataclass(frozen=True)
class EffectSucceeded:
    """Evidence that an interpreter attempted and completed an effect."""

    identity: EffectIdentity
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    observations: tuple[EffectObservation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, EffectIdentity):
            raise TypeError("effect success identity must be EffectIdentity")
        if not isinstance(self.evidence, BoundedEvidence):
            raise TypeError("effect success evidence must be BoundedEvidence")
        if not all(
            isinstance(value, EffectObservation)
            for value in self.observations
        ):
            raise TypeError("effect success observations must be typed")


@dataclass(frozen=True)
class EffectFailed:
    """Evidence that an interpreter attempted an effect and it failed."""

    identity: EffectIdentity
    failure: FailureEvidence
    observations: tuple[EffectObservation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, EffectIdentity):
            raise TypeError("effect failure identity must be EffectIdentity")
        if not isinstance(self.failure, FailureEvidence):
            raise TypeError("effect failure must carry FailureEvidence")
        if not all(
            isinstance(value, EffectObservation)
            for value in self.observations
        ):
            raise TypeError("effect failure observations must be typed")


@dataclass(frozen=True)
class EffectUnsupported:
    """Preflight result proving an effect was not attempted."""

    identity: EffectIdentity
    capability: EffectCapability

    def __post_init__(self) -> None:
        if not isinstance(self.identity, EffectIdentity):
            raise TypeError("unsupported effect identity must be EffectIdentity")
        if not isinstance(self.capability, EffectCapability):
            raise TypeError("unsupported effect capability must be typed")


EffectAttemptResult: TypeAlias = EffectSucceeded | EffectFailed
EffectResult: TypeAlias = EffectAttemptResult | EffectUnsupported


def effect_request_for_activity(
    activity: PlannedActivity,
    *,
    run_id: str,
    attempt: int,
    idempotency_key: str,
    timeout: TimeoutPolicy | None = None,
    secret_references: tuple[EffectSecretReference, ...] = (),
) -> EffectRequest:
    """Lift one schedulable planned activity into an effect request."""

    if not isinstance(activity, PlannedActivity):
        raise TypeError("effect request factory requires PlannedActivity")
    return EffectRequest(
        EffectIdentity(
            run_id=run_id,
            activity_id=activity.activity_id,
            attempt=attempt,
            idempotency_key=idempotency_key,
        ),
        activity.operation,
        TimeoutPolicy() if timeout is None else timeout,
        secret_references,
        EffectPurpose.FORWARD,
    )


def effect_request_for_compensation(
    activity: PlannedActivity,
    *,
    run_id: str,
    attempt: int,
    idempotency_key: str,
    timeout: TimeoutPolicy | None = None,
    secret_references: tuple[EffectSecretReference, ...] = (),
) -> EffectRequest:
    """Lift one planned activity's canonical inverse into an effect request."""

    if not isinstance(activity, PlannedActivity):
        raise TypeError("compensation effect factory requires PlannedActivity")
    if not isinstance(activity.compensation, Compensate):
        raise UnsupportedEffectOperation(
            "planned activity has no executable compensation effect"
        )
    return EffectRequest(
        EffectIdentity(
            run_id=run_id,
            activity_id=activity.activity_id,
            attempt=attempt,
            idempotency_key=idempotency_key,
        ),
        activity.compensation.operation,
        TimeoutPolicy() if timeout is None else timeout,
        secret_references,
        EffectPurpose.COMPENSATION,
    )


def required_capability(action: EffectAction) -> EffectCapability:
    """Interpret a closed action as the capability required to attempt it."""

    match action:
        case StartNode() | StopNode() | RemoveNodeResource():
            return EffectCapability.NODE_LIFECYCLE
        case StartRuntime() | StopRuntime() | RemoveRuntimeResource():
            return EffectCapability.RUNTIME_LIFECYCLE
        case DestroyDataResource():
            return EffectCapability.DATA_DESTRUCTION
        case WaitForHealthy():
            return EffectCapability.HEALTH_PROBE
        case AddSocketConnection() | SwitchSocketConnection() | RemoveSocketConnection():
            return EffectCapability.SOCKET_RECONCILIATION
        case ReconcileNode():
            return EffectCapability.NODE_RECONCILIATION
        case ReconcileRuntime():
            return EffectCapability.RUNTIME_RECONCILIATION
        case RegisterTarget():
            return EffectCapability.TARGET_REGISTRATION
        case ActivateTarget():
            return EffectCapability.TARGET_SWITCHING
        case DrainTarget():
            return EffectCapability.TARGET_DRAIN
        case RegisterObserver():
            return EffectCapability.OBSERVER_REGISTRATION
        case ObserveSubject():
            return EffectCapability.OBSERVATION
        case ReviewChange():
            raise UnsupportedEffectOperation(
                "review-required planning work cannot become an effect request"
            )
    raise TypeError(f"unsupported effect action {action!r}")


def _require_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EffectValueError(f"{label} must be non-empty text")


def _require_reference(value: object, label: str) -> None:
    if not isinstance(value, str) or _REFERENCE_PATTERN.fullmatch(value) is None:
        raise EffectValueError(
            f"{label} must be an opaque identifier, not a value or address"
        )
