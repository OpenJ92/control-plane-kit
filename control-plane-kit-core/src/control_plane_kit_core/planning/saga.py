"""Pure saga syntax, state replay, and scheduling over activity plans."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Generic, TypeAlias, TypeVar

from control_plane_kit_core.planning.activity_plan import (
    ActivityId,
    ActivityPlan,
    Compensate,
    PlannedActivity,
    ReviewChange,
)


EffectT = TypeVar("EffectT")


class SagaProgramError(ValueError):
    """Raised when saga syntax cannot represent a coherent program."""


@dataclass(frozen=True, order=True)
class SagaStepId:
    """Stable identity shared by programs, schedules, and durable events."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise SagaProgramError("saga step id must not be empty")


@dataclass(frozen=True)
class SagaStep(Generic[EffectT]):
    """One forward effect and its optional compensating effect."""

    step_id: SagaStepId
    effect: EffectT
    compensation: EffectT | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, SagaStepId):
            raise TypeError("saga step requires SagaStepId")
        if self.effect is None:
            raise SagaProgramError("saga step effect must not be None")


@dataclass(frozen=True)
class End:
    """Identity value for sequential saga composition."""


@dataclass(frozen=True)
class StepNode(Generic[EffectT]):
    """One saga step followed by another program."""

    step: SagaStep[EffectT]
    next: SagaProgram[EffectT] = End()

    def __post_init__(self) -> None:
        if not isinstance(self.step, SagaStep):
            raise TypeError("step node requires SagaStep")
        _require_program(self.next)


@dataclass(frozen=True)
class ParallelNode(Generic[EffectT]):
    """Declared fan-out followed by one shared continuation."""

    branches: tuple[SagaProgram[EffectT], ...]
    next: SagaProgram[EffectT] = End()

    def __post_init__(self) -> None:
        if len(self.branches) < 2:
            raise SagaProgramError("parallel saga nodes require at least two branches")
        for branch in self.branches:
            _require_program(branch)
            if isinstance(branch, End):
                raise SagaProgramError("parallel saga branches must not be empty")
        _require_program(self.next)


SagaProgram: TypeAlias = End | StepNode[EffectT] | ParallelNode[EffectT]


def step(value: SagaStep[EffectT]) -> StepNode[EffectT]:
    """Lift one typed step into saga syntax."""

    return StepNode(value)


def chain(*values: SagaStep[EffectT]) -> SagaProgram[EffectT]:
    """Compose typed steps sequentially without mutating an existing value."""

    program: SagaProgram[EffectT] = End()
    for value in reversed(values):
        program = StepNode(value, program)
    return program


def parallel(
    *branches: SagaProgram[EffectT] | SagaStep[EffectT],
) -> ParallelNode[EffectT]:
    """Compose at least two declared branches into one fan-out node."""

    return ParallelNode(tuple(_coerce_program(branch) for branch in branches))


def then(
    program: SagaProgram[EffectT] | SagaStep[EffectT],
    continuation: SagaProgram[EffectT] | SagaStep[EffectT],
) -> SagaProgram[EffectT]:
    """Append a continuation while preserving the original syntax tree."""

    current = _coerce_program(program)
    following = _coerce_program(continuation)
    match current:
        case End():
            return following
        case StepNode():
            return replace(current, next=then(current.next, following))
        case ParallelNode():
            return replace(current, next=then(current.next, following))
    raise TypeError(f"unsupported saga program {current!r}")


def program_steps(program: SagaProgram[EffectT]) -> tuple[SagaStep[EffectT], ...]:
    """Flatten syntax deterministically and reject duplicate stable identities."""

    _require_program(program)
    values = _walk(program)
    seen: set[SagaStepId] = set()
    for value in values:
        if value.step_id in seen:
            raise SagaProgramError(f"duplicate saga step id {value.step_id.value!r}")
        seen.add(value.step_id)
    return values


def _walk(program: SagaProgram[EffectT]) -> tuple[SagaStep[EffectT], ...]:
    match program:
        case End():
            return ()
        case StepNode(step=value, next=following):
            return (value, *_walk(following))
        case ParallelNode(branches=branches, next=following):
            branch_steps = tuple(
                value
                for branch in branches
                for value in _walk(branch)
            )
            return (*branch_steps, *_walk(following))
    raise TypeError(f"unsupported saga program {program!r}")


def _coerce_program(
    value: SagaProgram[EffectT] | SagaStep[EffectT],
) -> SagaProgram[EffectT]:
    if isinstance(value, SagaStep):
        return step(value)
    _require_program(value)
    return value


def _require_program(value: object) -> None:
    if not isinstance(value, (End, StepNode, ParallelNode)):
        raise TypeError("saga program must be End, StepNode, or ParallelNode")


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

    @classmethod
    def initial_for_plan(cls, plan: ActivityPlan) -> SagaState:
        if not isinstance(plan, ActivityPlan):
            raise TypeError("initial saga evidence requires ActivityPlan")
        return cls(
            tuple(
                SagaStepState(
                    SagaStepId(activity.activity_id.value),
                    compensation_available=isinstance(activity.compensation, Compensate),
                )
                for activity in plan.activities
            )
        )

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
            if any(value.status is SagaStepStatus.COMPENSATED for value in self.steps):
                return SagaStatus.COMPENSATED
        if (self.failed_steps or self.cancelled) and not compensation_candidates(self):
            if any(value.status is SagaStepStatus.COMPENSATED for value in self.steps):
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
                raise SagaStateError(f"cannot cancel saga in {state.status.value} state")
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
                raise SagaStateError(f"cannot cancel saga in {state.status.value} state")
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
            return _replace_step(state, step_id, SagaStepStatus.COMPENSATION_FAILED)
    raise TypeError(f"unsupported saga event {event!r}")


def evolve_all(state: SagaState, events: tuple[SagaEvent, ...]) -> SagaState:
    changed = state
    for event in events:
        changed = evolve(changed, event)
    return changed


def reconstruct(
    program: SagaProgram[EffectT],
    events: tuple[SagaEvent, ...],
) -> SagaState:
    """Rebuild the projection from immutable syntax and immutable events."""

    return evolve_all(initial_state(program), events)


def compensation_candidates(state: SagaState) -> tuple[SagaStepId, ...]:
    """Return completed compensatable steps in reverse durable completion order."""

    values = {
        value.step_id: value
        for value in state.steps
    }
    candidates: list[SagaStepId] = []
    for step_id in reversed(state.completion_order):
        step_state = values[step_id]
        if (
            step_state.compensation_available
            and step_state.status is SagaStepStatus.SUCCEEDED
        ):
            candidates.append(step_id)
    return tuple(candidates)


def _replace_step(
    state: SagaState,
    step_id: SagaStepId,
    status: SagaStepStatus,
) -> SagaState:
    state.step(step_id)
    return replace(
        state,
        steps=tuple(
            replace(value, status=status)
            if value.step_id == step_id
            else value
            for value in state.steps
        ),
    )


def _require_step_status(
    state: SagaState,
    step_id: SagaStepId,
    status: SagaStepStatus,
) -> None:
    actual = state.step(step_id)
    if actual.status is not status:
        raise SagaStateError(
            f"saga step {step_id.value!r} must be {status.value}, "
            f"not {actual.status.value}"
        )


def _require_active(state: SagaState) -> None:
    if state.status is not SagaStatus.ACTIVE:
        raise SagaStateError(f"saga is not active: {state.status.value}")
    if state.compensation_requested:
        raise SagaStateError("saga compensation already requested")


def _require_compensation_admission(state: SagaState) -> None:
    if state.compensation_requested:
        raise SagaStateError("saga compensation already requested")
    if state.status not in {
        SagaStatus.ACTIVE,
        SagaStatus.SUCCEEDED,
        SagaStatus.FAILED,
        SagaStatus.CANCELLED,
    }:
        raise SagaStateError(f"cannot request compensation from {state.status.value}")
    if not compensation_candidates(state):
        raise SagaStateError("compensation requires completed compensatable work")


class ScheduleEvidenceError(ValueError):
    """Raised when durable evidence cannot describe the supplied plan."""


class BlockReason(StrEnum):
    """Closed reasons why pending forward work cannot become ready."""

    REVIEW_REQUIRED = "review-required"
    FAILED_PREDECESSOR = "failed-predecessor"
    BLOCKED_PREDECESSOR = "blocked-predecessor"
    PLAN_REVIEW_REQUIRED = "plan-review-required"
    SAGA_FAILED = "saga-failed"
    SAGA_CANCELLED = "saga-cancelled"
    SAGA_COMPENSATING = "saga-compensating"
    SAGA_COMPENSATED = "saga-compensated"
    SAGA_COMPENSATION_FAILED = "saga-compensation-failed"


@dataclass(frozen=True)
class BlockedActivity:
    """One pending activity plus bounded structural blocking evidence."""

    activity: PlannedActivity
    reason: BlockReason
    predecessors: tuple[ActivityId, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.activity, PlannedActivity):
            raise TypeError("blocked activity requires PlannedActivity")
        if not isinstance(self.reason, BlockReason):
            raise TypeError("blocked activity reason must be BlockReason")
        if not all(isinstance(value, ActivityId) for value in self.predecessors):
            raise TypeError("blocked predecessors must be ActivityId values")


@dataclass(frozen=True)
class ExecutionSchedule:
    """Deterministic current scheduling projection without runtime effects."""

    ready: tuple[PlannedActivity, ...] = ()
    running: tuple[PlannedActivity, ...] = ()
    waiting: tuple[PlannedActivity, ...] = ()
    blocked: tuple[BlockedActivity, ...] = ()
    succeeded: tuple[PlannedActivity, ...] = ()
    failed: tuple[PlannedActivity, ...] = ()
    compensating: tuple[PlannedActivity, ...] = ()
    compensated: tuple[PlannedActivity, ...] = ()
    compensation_failed: tuple[PlannedActivity, ...] = ()
    compensation_ready: tuple[PlannedActivity, ...] = ()

    @property
    def terminal(self) -> bool:
        """Whether no forward or compensating work remains schedulable."""

        return not (
            self.ready
            or self.running
            or self.waiting
            or self.compensating
            or self.compensation_ready
        )

    @property
    def successful(self) -> bool:
        """Whether every planned activity completed forward successfully."""

        return self.terminal and not (
            self.blocked
            or self.failed
            or self.compensated
            or self.compensation_failed
        )


def derive_schedule(plan: ActivityPlan, evidence: SagaState) -> ExecutionSchedule:
    """Interpret one plan and reconstructed evidence as a current schedule."""

    if not isinstance(plan, ActivityPlan):
        raise TypeError("schedule derivation requires ActivityPlan")
    if not isinstance(evidence, SagaState):
        raise TypeError("schedule derivation requires SagaState")

    plan_by_step = {
        SagaStepId(activity.activity_id.value): activity
        for activity in plan.activities
    }
    evidence_ids = tuple(value.step_id for value in evidence.steps)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ScheduleEvidenceError("saga evidence repeats a step identity")
    if set(plan_by_step) != set(evidence_ids):
        missing = sorted(value.value for value in set(plan_by_step) - set(evidence_ids))
        foreign = sorted(value.value for value in set(evidence_ids) - set(plan_by_step))
        raise ScheduleEvidenceError(
            f"plan/evidence identity mismatch: missing={missing!r}, foreign={foreign!r}"
        )
    _validate_evidence(evidence, set(evidence_ids))

    status_by_id = {
        ActivityId(value.step_id.value): value.status
        for value in evidence.steps
    }
    blocked = _derive_blocked(plan, evidence, status_by_id)
    blocked_by_id = {value.activity.activity_id: value for value in blocked}

    ready: list[PlannedActivity] = []
    running: list[PlannedActivity] = []
    waiting: list[PlannedActivity] = []
    succeeded: list[PlannedActivity] = []
    failed: list[PlannedActivity] = []
    compensating: list[PlannedActivity] = []
    compensated: list[PlannedActivity] = []
    compensation_failed: list[PlannedActivity] = []

    for activity in plan.activities:
        status = status_by_id[activity.activity_id]
        match status:
            case SagaStepStatus.PENDING:
                if activity.activity_id in blocked_by_id:
                    continue
                predecessor_statuses = tuple(
                    status_by_id[value.predecessor]
                    for value in activity.dependencies
                )
                if all(
                    value is SagaStepStatus.SUCCEEDED
                    for value in predecessor_statuses
                ):
                    ready.append(activity)
                else:
                    waiting.append(activity)
            case SagaStepStatus.RUNNING:
                running.append(activity)
            case SagaStepStatus.SUCCEEDED:
                succeeded.append(activity)
            case SagaStepStatus.FAILED:
                failed.append(activity)
            case SagaStepStatus.COMPENSATING:
                compensating.append(activity)
            case SagaStepStatus.COMPENSATED:
                compensated.append(activity)
            case SagaStepStatus.COMPENSATION_FAILED:
                compensation_failed.append(activity)

    compensation_ready: tuple[PlannedActivity, ...] = ()
    if not running and not compensating and evidence.status in {
        SagaStatus.FAILED,
        SagaStatus.CANCELLED,
        SagaStatus.COMPENSATING,
        SagaStatus.PARTIALLY_COMPENSATED,
    }:
        compensation_ready = tuple(
            plan_by_step[step_id]
            for step_id in compensation_candidates(evidence)
        )

    return ExecutionSchedule(
        ready=tuple(ready),
        running=tuple(running),
        waiting=tuple(waiting),
        blocked=blocked,
        succeeded=tuple(succeeded),
        failed=tuple(failed),
        compensating=tuple(compensating),
        compensated=tuple(compensated),
        compensation_failed=tuple(compensation_failed),
        compensation_ready=compensation_ready,
    )


def _derive_blocked(
    plan: ActivityPlan,
    evidence: SagaState,
    status_by_id: dict[ActivityId, SagaStepStatus],
) -> tuple[BlockedActivity, ...]:
    blocked: dict[ActivityId, BlockedActivity] = {}
    review_ids = {
        activity.activity_id
        for activity in plan.activities
        if isinstance(activity.operation, ReviewChange)
    }

    for activity in plan.activities:
        if status_by_id[activity.activity_id] is not SagaStepStatus.PENDING:
            continue
        if activity.activity_id in review_ids:
            blocked[activity.activity_id] = BlockedActivity(
                activity,
                BlockReason.REVIEW_REQUIRED,
            )

    changed = True
    while changed:
        changed = False
        for activity in plan.activities:
            if (
                status_by_id[activity.activity_id] is not SagaStepStatus.PENDING
                or activity.activity_id in blocked
            ):
                continue
            failed_predecessors = tuple(
                value.predecessor
                for value in activity.dependencies
                if status_by_id[value.predecessor]
                in {
                    SagaStepStatus.FAILED,
                    SagaStepStatus.COMPENSATED,
                    SagaStepStatus.COMPENSATION_FAILED,
                }
            )
            if failed_predecessors:
                blocked[activity.activity_id] = BlockedActivity(
                    activity,
                    BlockReason.FAILED_PREDECESSOR,
                    failed_predecessors,
                )
                changed = True
                continue
            blocked_predecessors = tuple(
                value.predecessor
                for value in activity.dependencies
                if value.predecessor in blocked
            )
            if blocked_predecessors:
                blocked[activity.activity_id] = BlockedActivity(
                    activity,
                    BlockReason.BLOCKED_PREDECESSOR,
                    blocked_predecessors,
                )
                changed = True

    for activity in plan.activities:
        if (
            status_by_id[activity.activity_id] is not SagaStepStatus.PENDING
            or activity.activity_id in blocked
        ):
            continue
        reason = _global_block_reason(evidence.status, bool(review_ids))
        if reason is not None:
            blocked[activity.activity_id] = BlockedActivity(activity, reason)

    return tuple(
        blocked[activity.activity_id]
        for activity in plan.activities
        if activity.activity_id in blocked
    )


def _global_block_reason(
    status: SagaStatus,
    review_required: bool,
) -> BlockReason | None:
    if review_required:
        return BlockReason.PLAN_REVIEW_REQUIRED
    match status:
        case SagaStatus.FAILED:
            return BlockReason.SAGA_FAILED
        case SagaStatus.CANCELLED:
            return BlockReason.SAGA_CANCELLED
        case SagaStatus.COMPENSATING:
            return BlockReason.SAGA_COMPENSATING
        case SagaStatus.COMPENSATED:
            return BlockReason.SAGA_COMPENSATED
        case SagaStatus.PARTIALLY_COMPENSATED:
            return BlockReason.SAGA_COMPENSATION_FAILED
        case SagaStatus.ACTIVE | SagaStatus.SUCCEEDED:
            return None
    raise TypeError(f"unsupported saga status {status!r}")


def _validate_evidence(
    evidence: SagaState,
    evidence_ids: set[SagaStepId],
) -> None:
    completion_order = evidence.completion_order
    failed_steps = evidence.failed_steps
    if len(completion_order) != len(set(completion_order)):
        raise ScheduleEvidenceError("saga completion evidence repeats a step identity")
    if len(failed_steps) != len(set(failed_steps)):
        raise ScheduleEvidenceError("saga failure evidence repeats a step identity")
    if not set(completion_order).issubset(evidence_ids):
        raise ScheduleEvidenceError("saga completion evidence names an unknown step")
    if not set(failed_steps).issubset(evidence_ids):
        raise ScheduleEvidenceError("saga failure evidence names an unknown step")

    by_id = {value.step_id: value.status for value in evidence.steps}
    completed_statuses = {
        SagaStepStatus.SUCCEEDED,
        SagaStepStatus.COMPENSATING,
        SagaStepStatus.COMPENSATED,
        SagaStepStatus.COMPENSATION_FAILED,
    }
    completed_ids = {
        step_id
        for step_id, status in by_id.items()
        if status in completed_statuses
    }
    if set(completion_order) != completed_ids:
        raise ScheduleEvidenceError(
            "saga completion order must exactly describe completed step evidence"
        )
    failed_ids = {
        step_id
        for step_id, status in by_id.items()
        if status is SagaStepStatus.FAILED
    }
    if set(failed_steps) != failed_ids:
        raise ScheduleEvidenceError(
            "saga failed steps must exactly describe failed step evidence"
        )


class SagaJournalError(ValueError):
    """Raised when journal events are not coherent saga evidence."""


class ActivityJournalEventKind(StrEnum):
    """Closed pure event vocabulary projected into saga evidence."""

    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    STEP_UNSUPPORTED = "step_unsupported"
    STEP_UNCERTAIN = "step_uncertain"
    STEP_UNCERTAINTY_RESOLVED_SUCCEEDED = "step_uncertainty_resolved_succeeded"
    STEP_UNCERTAINTY_RESOLVED_FAILED = "step_uncertainty_resolved_failed"
    RUN_COMPENSATION_STARTED = "run_compensation_started"
    STEP_COMPENSATION_STARTED = "step_compensation_started"
    STEP_COMPENSATION_SUCCEEDED = "step_compensation_succeeded"
    STEP_COMPENSATION_FAILED = "step_compensation_failed"
    STEP_COMPENSATION_UNSUPPORTED = "step_compensation_unsupported"
    STEP_COMPENSATION_UNCERTAIN = "step_compensation_uncertain"
    STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED = (
        "step_compensation_uncertainty_resolved_succeeded"
    )
    STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED = (
        "step_compensation_uncertainty_resolved_failed"
    )


@dataclass(frozen=True)
class ActivityJournalEvent:
    """One bounded event from a canonical activity journal."""

    event_id: str
    run_id: str
    ordinal: int
    kind: ActivityJournalEventKind
    activity_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("activity journal event id must be non-empty text")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("activity journal run id must be non-empty text")
        if not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise ValueError("activity journal ordinal must be a positive integer")
        if not isinstance(self.kind, ActivityJournalEventKind):
            raise TypeError("activity journal kind must be ActivityJournalEventKind")
        if self.activity_id is not None and not self.activity_id.strip():
            raise ValueError("activity journal activity id must be non-empty text")


@dataclass(frozen=True)
class SagaJournalProjection:
    """Pure saga state plus operational conditions requiring coordination."""

    state: SagaState
    in_flight: tuple[ActivityJournalEvent, ...] = ()
    uncertain: tuple[ActivityJournalEvent, ...] = ()
    compensation_in_flight: tuple[ActivityJournalEvent, ...] = ()
    compensation_uncertain: tuple[ActivityJournalEvent, ...] = ()


def project_activity_journal(
    plan: ActivityPlan,
    events: tuple[ActivityJournalEvent, ...],
) -> SagaJournalProjection:
    """Fold canonical activity events without creating a second journal."""

    if not isinstance(plan, ActivityPlan):
        raise TypeError("saga journal projection requires ActivityPlan")
    if not all(isinstance(event, ActivityJournalEvent) for event in events):
        raise TypeError("saga journal events must be ActivityJournalEvent values")
    if tuple(event.ordinal for event in events) != tuple(
        sorted(event.ordinal for event in events)
    ) or len({event.ordinal for event in events}) != len(events):
        raise SagaJournalError("activity journal ordinals must be unique and increasing")
    if len({event.run_id for event in events}) > 1:
        raise SagaJournalError("activity journal cannot mix run identities")

    plan_ids = {activity.activity_id.value for activity in plan.activities}
    saga_events: list[SagaEvent] = []
    uncertain_by_step: dict[str, ActivityJournalEvent] = {}
    event_by_step: dict[str, ActivityJournalEvent] = {}
    compensation_uncertain_by_step: dict[str, ActivityJournalEvent] = {}
    compensation_event_by_step: dict[str, ActivityJournalEvent] = {}
    started_steps: set[str] = set()

    for event in events:
        if event.activity_id is not None and event.activity_id not in plan_ids:
            raise SagaJournalError(
                f"activity event references foreign step {event.activity_id!r}"
            )
        if event.kind is ActivityJournalEventKind.RUN_COMPENSATION_STARTED:
            saga_events.append(SagaCompensationRequested())
            continue
        if event.activity_id is None:
            continue

        step_id = SagaStepId(event.activity_id)
        match event.kind:
            case ActivityJournalEventKind.STEP_STARTED:
                saga_events.append(SagaStepStarted(step_id))
                event_by_step[event.activity_id] = event
                started_steps.add(event.activity_id)
            case ActivityJournalEventKind.STEP_SUCCEEDED:
                saga_events.append(SagaStepSucceeded(step_id))
                event_by_step.pop(event.activity_id, None)
            case ActivityJournalEventKind.STEP_FAILED:
                saga_events.append(SagaStepFailed(step_id))
                event_by_step.pop(event.activity_id, None)
            case ActivityJournalEventKind.STEP_UNSUPPORTED:
                if event.activity_id not in started_steps:
                    saga_events.append(SagaStepStarted(step_id))
                saga_events.append(SagaStepFailed(step_id))
                event_by_step.pop(event.activity_id, None)
            case ActivityJournalEventKind.STEP_UNCERTAIN:
                uncertain_by_step[event.activity_id] = event
                event_by_step.pop(event.activity_id, None)
            case ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED:
                if event.activity_id not in uncertain_by_step:
                    raise SagaJournalError(
                        "success resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaStepSucceeded(step_id))
                uncertain_by_step.pop(event.activity_id)
            case ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED:
                if event.activity_id not in uncertain_by_step:
                    raise SagaJournalError(
                        "failure resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaStepFailed(step_id))
                uncertain_by_step.pop(event.activity_id)
            case ActivityJournalEventKind.STEP_COMPENSATION_STARTED:
                saga_events.append(SagaCompensationStarted(step_id))
                compensation_event_by_step[event.activity_id] = event
            case ActivityJournalEventKind.STEP_COMPENSATION_SUCCEEDED:
                saga_events.append(SagaCompensationSucceeded(step_id))
                compensation_event_by_step.pop(event.activity_id, None)
            case ActivityJournalEventKind.STEP_COMPENSATION_FAILED:
                saga_events.append(SagaCompensationFailed(step_id))
                compensation_event_by_step.pop(event.activity_id, None)
            case ActivityJournalEventKind.STEP_COMPENSATION_UNSUPPORTED:
                if event.activity_id not in compensation_event_by_step:
                    saga_events.append(SagaCompensationStarted(step_id))
                saga_events.append(SagaCompensationFailed(step_id))
                compensation_event_by_step.pop(event.activity_id, None)
            case ActivityJournalEventKind.STEP_COMPENSATION_UNCERTAIN:
                compensation_uncertain_by_step[event.activity_id] = event
                compensation_event_by_step.pop(event.activity_id, None)
            case (
                ActivityJournalEventKind
                .STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED
            ):
                if event.activity_id not in compensation_uncertain_by_step:
                    raise SagaJournalError(
                        "compensation success resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaCompensationSucceeded(step_id))
                compensation_uncertain_by_step.pop(event.activity_id)
            case (
                ActivityJournalEventKind
                .STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED
            ):
                if event.activity_id not in compensation_uncertain_by_step:
                    raise SagaJournalError(
                        "compensation failure resolution requires prior uncertain evidence"
                    )
                saga_events.append(SagaCompensationFailed(step_id))
                compensation_uncertain_by_step.pop(event.activity_id)

    state = evolve_all(SagaState.initial_for_plan(plan), tuple(saga_events))
    running_ids = {
        value.step_id.value
        for value in state.steps
        if value.status is SagaStepStatus.RUNNING
    }
    return SagaJournalProjection(
        state,
        tuple(
            event_by_step[value]
            for value in sorted(running_ids)
            if value in event_by_step
        ),
        tuple(uncertain_by_step[key] for key in sorted(uncertain_by_step)),
        tuple(
            compensation_event_by_step[value]
            for value in sorted(compensation_event_by_step)
        ),
        tuple(
            compensation_uncertain_by_step[key]
            for key in sorted(compensation_uncertain_by_step)
        ),
    )
