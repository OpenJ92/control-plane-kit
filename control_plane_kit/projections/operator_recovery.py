"""Pure operator recovery projection over canonical execution truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from control_plane_kit.execution import (
    AbandonExpiredClaim,
    AcceptUncompensatedFailure,
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    BeginCompensation,
    ConfirmEffectFailed,
    ConfirmEffectSucceeded,
    ExecutionRequestRecord,
    RecoveryContext,
    RecoveryDecision,
    RecoveryDecisionRecord,
    RecoveryDecisionRejected,
    RecoveryScope,
    RemainPaused,
    ResumeSameIntent,
    RetryAsNewRun,
    validate_recovery_decision,
)
from control_plane_kit.core.planning import ActivityPlan, PlannedActivity
from control_plane_kit.projections.saga_journal import project_activity_journal
from control_plane_kit.saga import SagaStatus
from control_plane_kit.scheduling import ExecutionSchedule, derive_schedule


class OperatorRecoveryProjectionError(ValueError):
    """Raised when canonical values cannot form one coherent recovery view."""


class OperatorClaimStatus(StrEnum):
    """Closed operator meaning of current execution ownership."""

    UNCLAIMED = "unclaimed"
    ACTIVE = "active"
    EXPIRED = "expired"


class OperatorRecoveryOptionKind(StrEnum):
    """Closed recovery choices that an operator may request."""

    CONFIRM_EFFECT_SUCCEEDED = "confirm-effect-succeeded"
    CONFIRM_EFFECT_FAILED = "confirm-effect-failed"
    RESUME_SAME_INTENT = "resume-same-intent"
    RETRY_AS_NEW_RUN = "retry-as-new-run"
    BEGIN_COMPENSATION = "begin-compensation"
    ACCEPT_UNCOMPENSATED_FAILURE = "accept-uncompensated-failure"
    REMAIN_PAUSED = "remain-paused"
    RENEW_EXPIRED_CLAIM = "renew-expired-claim"
    TAKE_OVER_EXPIRED_CLAIM = "take-over-expired-claim"
    ABANDON_EXPIRED_CLAIM = "abandon-expired-claim"


@dataclass(frozen=True)
class ClaimObservation:
    """The explicit observation time used to interpret a bounded lease."""

    observed_at: str

    def __post_init__(self) -> None:
        _timestamp("observed_at", self.observed_at)


@dataclass(frozen=True)
class OperatorRecoveryOption:
    """A legal decision shape and its required authority, not an authorization."""

    kind: OperatorRecoveryOptionKind
    required_scope: RecoveryScope
    activity_id: str | None = None
    required_parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperatorSchedule:
    """Activity identities grouped by the canonical scheduler."""

    ready: tuple[str, ...]
    running: tuple[str, ...]
    waiting: tuple[str, ...]
    blocked: tuple[str, ...]
    succeeded: tuple[str, ...]
    failed: tuple[str, ...]
    compensating: tuple[str, ...]
    compensated: tuple[str, ...]
    compensation_failed: tuple[str, ...]
    compensation_ready: tuple[str, ...]


@dataclass(frozen=True)
class OperatorRecoveryView:
    """Current recovery meaning derived entirely from immutable canonical facts."""

    run_id: str
    run_status: ActivityRunStatus
    saga_status: SagaStatus
    claim_status: OperatorClaimStatus
    schedule: OperatorSchedule
    forward_in_flight: tuple[str, ...]
    forward_uncertain: tuple[str, ...]
    compensation_in_flight: tuple[str, ...]
    compensation_uncertain: tuple[str, ...]
    original_failures: tuple[ActivityEventRecord, ...]
    compensation_failures: tuple[ActivityEventRecord, ...]
    non_compensatable_activity_ids: tuple[str, ...]
    decisions: tuple[RecoveryDecisionRecord, ...]
    allowed_decisions: tuple[OperatorRecoveryOption, ...]


def project_operator_recovery(
    plan: ActivityPlan,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    events: tuple[ActivityEventRecord, ...],
    observation: ClaimObservation,
) -> OperatorRecoveryView:
    """Interpret plan, journal, and lease evidence as one operator recovery view."""

    _require_coherent_identity(plan, request, run, events)
    journal = project_activity_journal(plan, events)
    schedule = derive_schedule(plan, journal.state)
    claim_status = _claim_status(request, observation)
    uncertain_ids = tuple(
        event.activity_id
        for event in (*journal.uncertain, *journal.compensation_uncertain)
        if event.activity_id is not None
    )
    context = RecoveryContext(
        run.status,
        uncertain_activity_ids=frozenset(uncertain_ids),
        compensation_available=bool(schedule.compensation_ready),
        claim_expired=claim_status is OperatorClaimStatus.EXPIRED,
    )
    return OperatorRecoveryView(
        run_id=run.run_id,
        run_status=run.status,
        saga_status=journal.state.status,
        claim_status=claim_status,
        schedule=_operator_schedule(schedule),
        forward_in_flight=_activity_ids(journal.in_flight),
        forward_uncertain=_activity_ids(journal.uncertain),
        compensation_in_flight=_activity_ids(journal.compensation_in_flight),
        compensation_uncertain=_activity_ids(journal.compensation_uncertain),
        original_failures=tuple(
            event for event in events if _is_original_failure(event)
        ),
        compensation_failures=tuple(
            event for event in events if _is_compensation_failure(event)
        ),
        non_compensatable_activity_ids=_non_compensatable_activity_ids(events),
        decisions=tuple(
            event.recovery
            for event in events
            if event.recovery is not None
        ),
        allowed_decisions=_allowed_decisions(context, uncertain_ids),
    )


def _require_coherent_identity(
    plan: ActivityPlan,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    events: tuple[ActivityEventRecord, ...],
) -> None:
    if not isinstance(plan, ActivityPlan):
        raise TypeError("operator recovery projection requires ActivityPlan")
    if run.admission.request_id != request.identity.request_id:
        raise OperatorRecoveryProjectionError("run and request ownership differ")
    if run.plan_id != request.identity.plan_id:
        raise OperatorRecoveryProjectionError("run and request plan identities differ")
    if any(event.run_id != run.run_id for event in events):
        raise OperatorRecoveryProjectionError("journal contains a foreign run")


def _claim_status(
    request: ExecutionRequestRecord,
    observation: ClaimObservation,
) -> OperatorClaimStatus:
    if request.claim is None:
        return OperatorClaimStatus.UNCLAIMED
    expires_at = _timestamp("lease_expires_at", request.claim.lease_expires_at)
    observed_at = _timestamp("observed_at", observation.observed_at)
    return (
        OperatorClaimStatus.EXPIRED
        if expires_at <= observed_at
        else OperatorClaimStatus.ACTIVE
    )


def _allowed_decisions(
    context: RecoveryContext,
    uncertain_ids: tuple[str, ...],
) -> tuple[OperatorRecoveryOption, ...]:
    if context.claim_expired:
        validate_recovery_decision(AbandonExpiredClaim(), context)
        return (
            OperatorRecoveryOption(
                OperatorRecoveryOptionKind.RENEW_EXPIRED_CLAIM,
                RecoveryScope.RENEW_CLAIM,
                required_parameters=("lease_expires_at",),
            ),
            OperatorRecoveryOption(
                OperatorRecoveryOptionKind.TAKE_OVER_EXPIRED_CLAIM,
                RecoveryScope.TAKE_OVER_CLAIM,
                required_parameters=("replacement_worker_id", "lease_expires_at"),
            ),
            OperatorRecoveryOption(
                OperatorRecoveryOptionKind.ABANDON_EXPIRED_CLAIM,
                RecoveryScope.ABANDON_CLAIM,
            ),
        )
    candidates: list[tuple[RecoveryDecision, OperatorRecoveryOption]] = []
    for activity_id in uncertain_ids:
        candidates.extend(
            (
                (
                    ConfirmEffectSucceeded(activity_id),
                    OperatorRecoveryOption(
                        OperatorRecoveryOptionKind.CONFIRM_EFFECT_SUCCEEDED,
                        RecoveryScope.RESOLVE_UNCERTAINTY,
                        activity_id,
                    ),
                ),
                (
                    ConfirmEffectFailed(activity_id),
                    OperatorRecoveryOption(
                        OperatorRecoveryOptionKind.CONFIRM_EFFECT_FAILED,
                        RecoveryScope.RESOLVE_UNCERTAINTY,
                        activity_id,
                    ),
                ),
            )
        )
    candidates.extend(
        (
            (
                ResumeSameIntent(),
                OperatorRecoveryOption(
                    OperatorRecoveryOptionKind.RESUME_SAME_INTENT,
                    RecoveryScope.OPERATE,
                ),
            ),
            (
                RetryAsNewRun(),
                OperatorRecoveryOption(
                    OperatorRecoveryOptionKind.RETRY_AS_NEW_RUN,
                    RecoveryScope.OPERATE,
                ),
            ),
            (
                BeginCompensation(),
                OperatorRecoveryOption(
                    OperatorRecoveryOptionKind.BEGIN_COMPENSATION,
                    RecoveryScope.COMPENSATE,
                ),
            ),
            (
                AcceptUncompensatedFailure(),
                OperatorRecoveryOption(
                    OperatorRecoveryOptionKind.ACCEPT_UNCOMPENSATED_FAILURE,
                    RecoveryScope.ACCEPT_LOSS,
                ),
            ),
            (
                RemainPaused(),
                OperatorRecoveryOption(
                    OperatorRecoveryOptionKind.REMAIN_PAUSED,
                    RecoveryScope.OPERATE,
                ),
            ),
        )
    )
    allowed: list[OperatorRecoveryOption] = []
    for decision, option in candidates:
        try:
            validate_recovery_decision(decision, context)
        except RecoveryDecisionRejected:
            continue
        allowed.append(option)
    return tuple(allowed)


def _operator_schedule(schedule: ExecutionSchedule) -> OperatorSchedule:
    return OperatorSchedule(
        ready=_planned_ids(schedule.ready),
        running=_planned_ids(schedule.running),
        waiting=_planned_ids(schedule.waiting),
        blocked=tuple(value.activity.activity_id.value for value in schedule.blocked),
        succeeded=_planned_ids(schedule.succeeded),
        failed=_planned_ids(schedule.failed),
        compensating=_planned_ids(schedule.compensating),
        compensated=_planned_ids(schedule.compensated),
        compensation_failed=_planned_ids(schedule.compensation_failed),
        compensation_ready=_planned_ids(schedule.compensation_ready),
    )


def _planned_ids(values: tuple[PlannedActivity, ...]) -> tuple[str, ...]:
    return tuple(value.activity_id.value for value in values)


def _activity_ids(events: tuple[ActivityEventRecord, ...]) -> tuple[str, ...]:
    return tuple(event.activity_id for event in events if event.activity_id is not None)


def _is_original_failure(event: ActivityEventRecord) -> bool:
    return event.failure is not None and event.kind not in {
        ActivityEventKind.STEP_COMPENSATION_FAILED,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAIN,
        ActivityEventKind.RUN_COMPENSATION_FAILED,
    }


def _is_compensation_failure(event: ActivityEventRecord) -> bool:
    return event.failure is not None and event.kind in {
        ActivityEventKind.STEP_COMPENSATION_FAILED,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAIN,
        ActivityEventKind.RUN_COMPENSATION_FAILED,
    }


def _non_compensatable_activity_ids(
    events: tuple[ActivityEventRecord, ...],
) -> tuple[str, ...]:
    result: list[str] = []
    for event in events:
        if event.failure is None or event.failure.code != "compensation.non-compensatable-work":
            continue
        values = event.failure.details.descriptor().get("activity_ids")
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            raise OperatorRecoveryProjectionError(
                "non-compensatable failure contains malformed activity identities"
            )
        result.extend(values)
    return tuple(dict.fromkeys(result))


def _timestamp(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise OperatorRecoveryProjectionError(
            f"{name} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise OperatorRecoveryProjectionError(f"{name} must include a timezone")
    return parsed
