"""Pure dependency scheduling over canonical plans and saga evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from control_plane_kit.planning import (
    ActivityId,
    ActivityPlan,
    PlannedActivity,
    ReviewChange,
)
from control_plane_kit.saga import (
    SagaState,
    SagaStatus,
    SagaStepId,
    SagaStepStatus,
    compensation_candidates,
)


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
    blocked_by_id = {
        value.activity.activity_id: value
        for value in blocked
    }

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
