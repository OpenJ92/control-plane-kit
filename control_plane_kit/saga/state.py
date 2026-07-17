"""Pure command decisions and event evolution for saga programs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeAlias, TypeVar

from control_plane_kit.saga.program import SagaProgram, SagaStepId, program_steps


EffectT = TypeVar("EffectT")


class SagaStateError(ValueError):
    """Raised when a command or event violates the closed saga lifecycle."""


class SagaStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"


class SagaStatus(StrEnum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    PARTIALLY_COMPENSATED = "partially_compensated"


@dataclass(frozen=True)
class SagaStepState:
    step_id: SagaStepId
    status: SagaStepStatus = SagaStepStatus.PENDING
    compensation_available: bool = False


@dataclass(frozen=True)
class SagaState:
    """Projection reconstructed entirely from one program and its events."""

    steps: tuple[SagaStepState, ...]
    completion_order: tuple[SagaStepId, ...] = ()
    failed_steps: tuple[SagaStepId, ...] = ()
    cancelled: bool = False
    compensation_requested: bool = False

    def step(self, step_id: SagaStepId) -> SagaStepState:
        for value in self.steps:
            if value.step_id == step_id:
                return value
        raise SagaStateError(f"unknown saga step {step_id.value!r}")

    @property
    def status(self) -> SagaStatus:
        statuses = {value.status for value in self.steps}
        if SagaStepStatus.COMPENSATING in statuses:
            return SagaStatus.COMPENSATING
        if SagaStepStatus.COMPENSATION_FAILED in statuses:
            return SagaStatus.PARTIALLY_COMPENSATED
        if self.compensation_requested:
            if compensation_candidates(self):
                return SagaStatus.COMPENSATING
            if any(
                value.status is SagaStepStatus.COMPENSATED for value in self.steps
            ):
                return SagaStatus.COMPENSATED
        if (self.failed_steps or self.cancelled) and not compensation_candidates(self):
            if any(
                value.status is SagaStepStatus.COMPENSATED for value in self.steps
            ):
                return SagaStatus.COMPENSATED
        if self.failed_steps:
            return SagaStatus.FAILED
        if self.cancelled:
            return SagaStatus.CANCELLED
        if self.steps and statuses == {SagaStepStatus.SUCCEEDED}:
            return SagaStatus.SUCCEEDED
        return SagaStatus.ACTIVE


@dataclass(frozen=True)
class StartSagaStep:
    step_id: SagaStepId


@dataclass(frozen=True)
class SucceedSagaStep:
    step_id: SagaStepId


@dataclass(frozen=True)
class FailSagaStep:
    step_id: SagaStepId


@dataclass(frozen=True)
class CancelSaga:
    pass


@dataclass(frozen=True)
class RequestSagaCompensation:
    pass


@dataclass(frozen=True)
class BeginSagaCompensation:
    step_id: SagaStepId


@dataclass(frozen=True)
class SucceedSagaCompensation:
    step_id: SagaStepId


@dataclass(frozen=True)
class FailSagaCompensation:
    step_id: SagaStepId


SagaCommand: TypeAlias = (
    StartSagaStep
    | SucceedSagaStep
    | FailSagaStep
    | CancelSaga
    | RequestSagaCompensation
    | BeginSagaCompensation
    | SucceedSagaCompensation
    | FailSagaCompensation
)


@dataclass(frozen=True)
class SagaStepStarted:
    step_id: SagaStepId


@dataclass(frozen=True)
class SagaStepSucceeded:
    step_id: SagaStepId


@dataclass(frozen=True)
class SagaStepFailed:
    step_id: SagaStepId


@dataclass(frozen=True)
class SagaCancelled:
    pass


@dataclass(frozen=True)
class SagaCompensationRequested:
    pass


@dataclass(frozen=True)
class SagaCompensationStarted:
    step_id: SagaStepId


@dataclass(frozen=True)
class SagaCompensationSucceeded:
    step_id: SagaStepId


@dataclass(frozen=True)
class SagaCompensationFailed:
    step_id: SagaStepId


SagaEvent: TypeAlias = (
    SagaStepStarted
    | SagaStepSucceeded
    | SagaStepFailed
    | SagaCancelled
    | SagaCompensationRequested
    | SagaCompensationStarted
    | SagaCompensationSucceeded
    | SagaCompensationFailed
)


def initial_state(program: SagaProgram[EffectT]) -> SagaState:
    """Construct the empty projection for a validated immutable program."""

    return SagaState(
        tuple(
            SagaStepState(
                value.step_id,
                compensation_available=value.compensation is not None,
            )
            for value in program_steps(program)
        )
    )


def decide(state: SagaState, command: SagaCommand) -> tuple[SagaEvent, ...]:
    """Validate one requested state transition and emit immutable facts."""

    match command:
        case StartSagaStep(step_id=step_id):
            _require_active(state)
            _require_step_status(state, step_id, SagaStepStatus.PENDING)
            return (SagaStepStarted(step_id),)
        case SucceedSagaStep(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.RUNNING)
            return (SagaStepSucceeded(step_id),)
        case FailSagaStep(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.RUNNING)
            return (SagaStepFailed(step_id),)
        case CancelSaga():
            if state.status is not SagaStatus.ACTIVE:
                raise SagaStateError(
                    f"cannot cancel saga in {state.status.value} state"
                )
            return (SagaCancelled(),)
        case RequestSagaCompensation():
            _require_compensation_admission(state)
            return (SagaCompensationRequested(),)
        case BeginSagaCompensation(step_id=step_id):
            candidates = compensation_candidates(state)
            if not candidates or candidates[0] != step_id:
                raise SagaStateError(
                    "compensation must follow reverse durable completion order"
                )
            return (SagaCompensationStarted(step_id),)
        case SucceedSagaCompensation(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.COMPENSATING)
            return (SagaCompensationSucceeded(step_id),)
        case FailSagaCompensation(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.COMPENSATING)
            return (SagaCompensationFailed(step_id),)
    raise TypeError(f"unsupported saga command {command!r}")


def evolve(state: SagaState, event: SagaEvent) -> SagaState:
    """Apply one validated immutable event to a current projection."""

    match event:
        case SagaStepStarted(step_id=step_id):
            _require_active(state)
            _require_step_status(state, step_id, SagaStepStatus.PENDING)
            return _replace_step(state, step_id, SagaStepStatus.RUNNING)
        case SagaStepSucceeded(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.RUNNING)
            changed = _replace_step(state, step_id, SagaStepStatus.SUCCEEDED)
            return replace(
                changed,
                completion_order=(*changed.completion_order, step_id),
            )
        case SagaStepFailed(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.RUNNING)
            changed = _replace_step(state, step_id, SagaStepStatus.FAILED)
            return replace(changed, failed_steps=(*changed.failed_steps, step_id))
        case SagaCancelled():
            if state.status is not SagaStatus.ACTIVE:
                raise SagaStateError(
                    f"cannot apply cancellation in {state.status.value} state"
                )
            return replace(state, cancelled=True)
        case SagaCompensationRequested():
            _require_compensation_admission(state)
            return replace(state, compensation_requested=True)
        case SagaCompensationStarted(step_id=step_id):
            candidates = compensation_candidates(state)
            if not candidates or candidates[0] != step_id:
                raise SagaStateError(
                    "compensation must follow reverse durable completion order"
                )
            return _replace_step(state, step_id, SagaStepStatus.COMPENSATING)
        case SagaCompensationSucceeded(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.COMPENSATING)
            return _replace_step(state, step_id, SagaStepStatus.COMPENSATED)
        case SagaCompensationFailed(step_id=step_id):
            _require_step_status(state, step_id, SagaStepStatus.COMPENSATING)
            return _replace_step(
                state,
                step_id,
                SagaStepStatus.COMPENSATION_FAILED,
            )
    raise TypeError(f"unsupported saga event {event!r}")


def evolve_all(state: SagaState, events: tuple[SagaEvent, ...]) -> SagaState:
    """Fold a durable event sequence into one current projection."""

    current = state
    for event in events:
        current = evolve(current, event)
    return current


def reconstruct(
    program: SagaProgram[EffectT],
    events: tuple[SagaEvent, ...],
) -> SagaState:
    """Rebuild current saga state without hidden process memory."""

    return evolve_all(initial_state(program), events)


def compensation_candidates(state: SagaState) -> tuple[SagaStepId, ...]:
    """Return uncompensated steps in reverse durable completion order."""

    by_id = {value.step_id: value for value in state.steps}
    return tuple(
        step_id
        for step_id in reversed(state.completion_order)
        if by_id[step_id].compensation_available
        and by_id[step_id].status is SagaStepStatus.SUCCEEDED
    )


def _replace_step(
    state: SagaState,
    step_id: SagaStepId,
    replacement: SagaStepStatus,
) -> SagaState:
    state.step(step_id)
    return replace(
        state,
        steps=tuple(
            replace(value, status=replacement)
            if value.step_id == step_id
            else value
            for value in state.steps
        ),
    )


def _require_active(state: SagaState) -> None:
    if state.status is not SagaStatus.ACTIVE:
        raise SagaStateError(f"saga is {state.status.value}, not active")


def _require_compensation_admission(state: SagaState) -> None:
    if state.compensation_requested:
        raise SagaStateError("saga compensation was already requested")
    if not compensation_candidates(state):
        raise SagaStateError(
            "saga compensation requires completed compensatable work"
        )


def _require_step_status(
    state: SagaState,
    step_id: SagaStepId,
    expected: SagaStepStatus,
) -> None:
    current = state.step(step_id)
    if current.status is not expected:
        raise SagaStateError(
            f"saga step {step_id.value!r} is {current.status.value}, "
            f"not {expected.value}"
        )
