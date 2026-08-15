"""Shared approval and journal laws for execution-lease recovery."""

from __future__ import annotations

from typing import Any, Mapping

from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    LifecycleOperationKind,
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
from control_plane_kit_operations.activity_run_retry import ActivityRunRetryResult
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
    OperationActionRecord,
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
    if decision_kind not in {
        *_CONSEQUENCE_KIND,
        RecoveryDecisionKind.RETRY_AS_NEW_RUN,
    } or lifecycle_kinds != (
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


def require_replay_run_evolution(
    stores: Any,
    request: ExecutionRequestRecord,
    retained_run: ActivityRunRecord,
) -> None:
    actions = _replay_actions_for_session(
        stores,
        request.identity.session_id,
    )
    current = retained_run
    visited = {current.run_id}
    for _ in range(len(actions)):
        candidates = tuple(
            action
            for action in actions
            if _retry_action_prior_run_id(action) == current.run_id
        )
        if not candidates:
            break
        if len(candidates) != 1:
            raise RunLifecycleConflict(
                "persisted recovery run evolution changed"
            )
        action = candidates[0]
        payload = action.payload
        assert isinstance(payload, Mapping)
        run_id = payload.get("run_id")
        decision_event_id = payload.get("decision_event_id")
        opened_event_id = payload.get("opened_event_id")
        if (
            type(run_id) is not str
            or type(decision_event_id) is not str
            or type(opened_event_id) is not str
            or run_id in visited
        ):
            raise RunLifecycleConflict(
                "persisted recovery run evolution changed"
            )
        try:
            successor = _replay_run_for_update(
                stores,
                request.identity.request_id,
                run_id,
            )
            decision_event = _replay_event(stores, decision_event_id)
            opened_event = _replay_event(stores, opened_event_id)
        except (RunLifecycleConflict, RunLifecycleNotFound):
            successor = None
        if successor is None:
            raise RunLifecycleConflict(
                "persisted recovery run evolution changed"
            )
        try:
            ActivityRunRetryResult(
                request,
                current,
                successor,
                decision_event,
                opened_event,
                action,
                replayed=True,
            )
        except OperationsRecordError:
            valid_retry = False
        else:
            valid_retry = True
        if not valid_retry:
            raise RunLifecycleConflict(
                "persisted recovery run evolution changed"
            )
        visited.add(successor.run_id)
        current = successor
    latest_run = _replay_latest_run_for_update(
        stores,
        request.identity.request_id,
    )
    if current != latest_run:
        raise RunLifecycleConflict("persisted recovery run evolution changed")


def _retry_action_prior_run_id(action: OperationActionRecord) -> str | None:
    if action.action_type is not LifecycleOperationKind.RECORD_RECOVERY_DECISION:
        return None
    payload = action.payload
    if not isinstance(payload, Mapping):
        return None
    recovery = payload.get("recovery")
    if not isinstance(recovery, Mapping):
        return None
    if recovery.get("decision") != RecoveryDecisionKind.RETRY_AS_NEW_RUN.value:
        return None
    retained_run_id = recovery.get("retained_run_id")
    return retained_run_id if type(retained_run_id) is str else None


def _replay_actions_for_session(
    stores: Any,
    session_id: str,
) -> tuple[OperationActionRecord, ...]:
    try:
        actions = stores.activity_history.actions_for_session(session_id)
    except (OperationsRecordError, ValueError):
        pass
    else:
        return actions
    raise RunLifecycleConflict("persisted recovery run evolution changed")


def _replay_run_for_update(
    stores: Any,
    request_id: str,
    run_id: str,
) -> ActivityRunRecord:
    try:
        run = stores.execution.get_run_for_request_for_update(request_id, run_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return run
    if failure == "missing":
        raise RunLifecycleNotFound("recovery retained run was not found")
    raise RunLifecycleConflict("recovery retained run history is invalid")


def _replay_latest_run_for_update(
    stores: Any,
    request_id: str,
) -> ActivityRunRecord:
    try:
        run = stores.execution.get_latest_run_for_request_for_update(request_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return run
    if failure == "missing":
        raise RunLifecycleNotFound("activity run was not found")
    raise RunLifecycleConflict("activity run history is invalid")


def _replay_event(stores: Any, event_id: str) -> ActivityEventRecord:
    try:
        event = stores.execution.get_event(event_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return event
    if failure == "missing":
        raise RunLifecycleNotFound("recovery event was not found")
    raise RunLifecycleConflict("recovery event history is invalid")


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
