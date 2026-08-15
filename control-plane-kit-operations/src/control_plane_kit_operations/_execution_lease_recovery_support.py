"""Shared approval and journal laws for execution-lease recovery."""

from __future__ import annotations

from typing import Any

from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    RecoveryDecisionKind,
)
from control_plane_kit_core.planning import (
    SagaJournalError,
    SagaStateError,
    SagaStatus,
    project_activity_journal,
)
from control_plane_kit_operations.activity_journal import (
    EVENT_KIND_TO_JOURNAL_KIND,
    activity_journal_events,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleNotFound,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    BoundedEvidence,
    ExecutionRequestRecord,
    OperationsRecordError,
)


_CONSEQUENCE_KIND = {
    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_RENEWED
    ),
    RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_RENEWED
    ),
    RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER
    ),
    RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_ABANDONED
    ),
}

_RECOVERY_EVENT_KINDS = frozenset(
    {
        ActivityEventKind.RECOVERY_DECISION_RECORDED,
        *_CONSEQUENCE_KIND.values(),
    }
)


def locked_recovery_approval(
    stores: Any,
    request: ExecutionRequestRecord,
) -> tuple[ApprovalRequestRecord, ApprovalDecisionRecord, ActivityPlanRecord]:
    try:
        approval = stores.activity_history.get_approval_request(
            request.approval_request_id
        )
        decision = stores.activity_history.approval_decision_for_request(
            approval.request_id
        )
        plan = stores.activity_history.get_plan(request.identity.plan_id)
    except KeyError:
        read_failure = "missing"
    except (OperationsRecordError, ValueError):
        read_failure = "invalid"
    else:
        read_failure = None
    if read_failure == "missing":
        raise RunLifecycleNotFound("recovery approval history was not found")
    if read_failure == "invalid":
        raise RunLifecycleConflict("recovery approval history is invalid")
    if decision is None:
        raise RunLifecycleNotFound("recovery approval decision was not found")
    if (
        approval.request_id != request.approval_request_id
        or approval.session_id != request.identity.session_id
        or decision.decision_id != request.approval_decision_id
        or decision.request_id != approval.request_id
        or decision.decision is not ApprovalDecisionKind.APPROVED
        or decision.scope is not approval.required_scope
        or plan.plan_id != request.identity.plan_id
        or plan.session_id != request.identity.session_id
    ):
        raise RunLifecycleConflict("recovery approval history changed")
    subject = approval.subject
    if isinstance(subject, ActivityPlanApprovalSubject):
        if subject.plan_id != plan.plan_id:
            raise RunLifecycleConflict("recovery plan approval changed")
    elif not isinstance(subject, GatewayKeyRotationApprovalSubject):
        raise RunLifecycleConflict("recovery approval subject is unsupported")
    return approval, decision, plan


def require_recovery_eligible_journal(
    decision_kind: RecoveryDecisionKind,
    expected_fence: ExecutionLeaseFence,
    run: ActivityRunRecord,
    plan: ActivityPlanRecord,
    events: tuple[ActivityEventRecord, ...],
) -> None:
    if (
        type(decision_kind) is not RecoveryDecisionKind
        or type(expected_fence) is not ExecutionLeaseFence
        or not events
        or any(event.run_id != run.run_id for event in events)
        or tuple(event.ordinal for event in events)
        != tuple(range(1, len(events) + 1))
    ):
        raise RunLifecycleConflict("retained run journal is invalid")
    base_events = _journal_without_recovery_pairs(events, expected_fence)
    if base_events is None:
        raise RunLifecycleConflict("retained run journal is invalid")
    try:
        projection = project_activity_journal(
            plan.plan,
            activity_journal_events(base_events),
        )
    except (SagaJournalError, SagaStateError):
        projection = None
    if projection is None:
        raise RunLifecycleConflict("retained run journal is invalid")
    lifecycle_kinds = tuple(
        event.kind
        for event in base_events
        if event.kind not in EVENT_KIND_TO_JOURNAL_KIND
    )
    if decision_kind is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM:
        if activity_journal_events(base_events) or lifecycle_kinds != (
            ActivityEventKind.RUN_OPENED,
        ):
            raise RunLifecycleConflict("active retained run has effect history")
        return
    if decision_kind not in _CONSEQUENCE_KIND or lifecycle_kinds != (
        ActivityEventKind.RUN_OPENED,
        ActivityEventKind.RUN_STARTED,
        ActivityEventKind.RUN_FAILED,
    ):
        raise RunLifecycleConflict("failed retained run lacks terminal evidence")
    if (
        projection.state.status is not SagaStatus.FAILED
        or projection.state.compensation_requested
        or projection.in_flight
        or projection.uncertain
        or projection.compensation_in_flight
        or projection.compensation_uncertain
    ):
        raise RunLifecycleConflict("retained run effect failure is unresolved")


def _journal_without_recovery_pairs(
    events: tuple[ActivityEventRecord, ...],
    expected_fence: ExecutionLeaseFence,
) -> tuple[ActivityEventRecord, ...] | None:
    base_events: list[ActivityEventRecord] = []
    has_prior_recovery = False
    prior_replacement: ExecutionLeaseFence | None = None
    run_opened = False
    run_started = False
    run_failed = False
    index = 0
    while index < len(events):
        decision = events[index]
        if decision.kind is not ActivityEventKind.RECOVERY_DECISION_RECORDED:
            if decision.kind in _RECOVERY_EVENT_KINDS:
                return None
            base_events.append(decision)
            run_opened = (
                run_opened or decision.kind is ActivityEventKind.RUN_OPENED
            )
            run_started = (
                run_started or decision.kind is ActivityEventKind.RUN_STARTED
            )
            run_failed = (
                run_failed or decision.kind is ActivityEventKind.RUN_FAILED
            )
            index += 1
            continue
        if index + 1 >= len(events):
            return None
        consequence = events[index + 1]
        recovery = decision.recovery
        if (
            recovery is None
            or recovery.decision_kind is RecoveryDecisionKind.RETRY_AS_NEW_RUN
            or (has_prior_recovery and recovery.prior_fence != prior_replacement)
            or (
                recovery.decision_kind
                is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM
                and (not run_opened or run_started or run_failed)
            )
            or (
                recovery.decision_kind
                is not RecoveryDecisionKind.RENEW_ACTIVE_CLAIM
                and (not run_opened or not run_started or not run_failed)
            )
            or consequence.kind is not _CONSEQUENCE_KIND[recovery.decision_kind]
            or consequence.ordinal != decision.ordinal + 1
            or consequence.occurred_at != decision.occurred_at
            or consequence.evidence != BoundedEvidence()
            or consequence.failure is not None
        ):
            return None
        has_prior_recovery = True
        prior_replacement = recovery.replacement_fence
        index += 2
    if has_prior_recovery and prior_replacement != expected_fence:
        return None
    return tuple(base_events)


__all__ = ()
