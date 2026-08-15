"""Transactional interpreter for the execution-lease recovery language."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations.execution_lease_recovery import (
    AbandonExpiredExecutionClaim,
    ExecutionLeaseRecoveryCommand,
    ExecutionLeaseRecoveryResult,
    RenewActiveExecutionClaim,
    RenewExpiredExecutionClaim,
    TakeOverExpiredExecutionClaim,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleDenied,
    RunLifecycleIdempotencyConflict,
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
    ClaimIdentity,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestRecord,
    OperationActionRecord,
    OperationSessionStatus,
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

_COMPENSATION_KINDS = frozenset(
    {
        ActivityEventKind.RUN_COMPENSATION_STARTED,
        ActivityEventKind.RUN_COMPENSATION_SUCCEEDED,
        ActivityEventKind.RUN_COMPENSATION_FAILED,
        ActivityEventKind.STEP_COMPENSATION_STARTED,
        ActivityEventKind.STEP_COMPENSATION_SUCCEEDED,
        ActivityEventKind.STEP_COMPENSATION_FAILED,
        ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAIN,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED,
        ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED,
    }
)

_FORWARD_FAILURE_KINDS = frozenset(
    {
        ActivityEventKind.STEP_FAILED,
        ActivityEventKind.STEP_UNSUPPORTED,
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
    }
)


class ExecutionLeaseRecoveryCommandService:
    """Interpret one admitted recovery command as one atomic transaction."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._id_factory = id_factory

    def execute(
        self,
        command: ExecutionLeaseRecoveryCommand,
    ) -> ExecutionLeaseRecoveryResult:
        _require_scope(command)
        fingerprint = command.intent_fingerprint()
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            locator = _request(stores, command.request_id)
            history = stores.activity_history
            history.lock_action_idempotency(
                locator.identity.session_id,
                command.idempotency_key.value,
            )
            existing = history.action_for_idempotency(
                locator.identity.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                if existing.intent_fingerprint != fingerprint:
                    raise RunLifecycleIdempotencyConflict(
                        "idempotency key was reused with different recovery intent"
                    )
                result = _replay(stores, command, locator, existing)
                unit_of_work.commit()
                return result

            session = _open_session(history, locator.identity.session_id)
            request = _request_for_update(stores, command.request_id)
            run = _latest_run_for_update(stores, command.request_id)
            _require_locked_identity(command, locator, request, run, session.session_id)
            _require_first_state(command, request, run)
            approval, decision, plan = _approval(stores, request)
            _require_journal(command, run, plan, stores.execution.events_for_run(run.run_id))

            observation = stores.execution.observe_request_lease_for_update(
                request.identity.request_id
            )
            if observation.request != request:
                raise RunLifecycleConflict("execution request changed before recovery")
            _require_expiry(command, observation.expired)

            result = self._plan_result(
                stores,
                command,
                request,
                run,
                observed_at=observation.observed_at,
            )
            persisted = _persist_claim(stores, command, result)
            if persisted != result.request:
                raise RunLifecycleConflict("execution claim changed during recovery")
            if stores.execution.add_event(result.decision_event) != result.decision_event:
                raise RunLifecycleConflict("recovery decision event was not preserved")
            if stores.execution.add_event(result.consequence_event) != result.consequence_event:
                raise RunLifecycleConflict("recovery consequence event was not preserved")
            if history.add_action(result.action) != result.action:
                raise RunLifecycleConflict("recovery action was not preserved")
            unit_of_work.commit()
            return result

    def _plan_result(
        self,
        stores: Any,
        command: ExecutionLeaseRecoveryCommand,
        request: ExecutionRequestRecord,
        run: ActivityRunRecord,
        *,
        observed_at: str,
    ) -> ExecutionLeaseRecoveryResult:
        prior_fence = command.expected_fence
        replacement_fence = _replacement_fence(command)
        planned_request = _planned_request(
            request,
            command,
            replacement_fence,
            observed_at,
        )
        recovery = ExecutionLeaseRecoveryEvidence(
            _decision_kind(command),
            command.retained_run_id,
            prior_fence,
            replacement_fence,
        )
        first_ordinal = stores.execution.next_event_ordinal(run.run_id)
        action_ordinal = stores.activity_history.next_action_ordinal(
            request.identity.session_id
        )
        decision_event = ActivityEventRecord(
            self._id_factory(),
            run.run_id,
            first_ordinal,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            observed_at,
            recovery=recovery,
        )
        consequence_event = ActivityEventRecord(
            self._id_factory(),
            run.run_id,
            first_ordinal + 1,
            _CONSEQUENCE_KIND[recovery.decision_kind],
            observed_at,
        )
        action = OperationActionRecord(
            self._id_factory(),
            request.identity.session_id,
            action_ordinal,
            LifecycleOperationKind.RECORD_RECOVERY_DECISION,
            command.authority.actor_id,
            _action_payload(
                planned_request,
                run,
                decision_event,
                consequence_event,
                command,
            ),
            observed_at,
            command.idempotency_key.value,
            command.intent_fingerprint(),
        )
        return _result(
            planned_request,
            run,
            decision_event,
            consequence_event,
            action,
        )


def _replay(
    stores: Any,
    command: ExecutionLeaseRecoveryCommand,
    locator: ExecutionRequestRecord,
    action: OperationActionRecord,
) -> ExecutionLeaseRecoveryResult:
    request = _request_for_update(stores, command.request_id)
    run = _latest_run_for_update(stores, command.request_id)
    if (
        locator.identity != request.identity
        or run.run_id != command.retained_run_id.value
        or action.session_id != request.identity.session_id
        or action.idempotency_key != command.idempotency_key.value
        or action.actor_id != command.authority.actor_id
    ):
        raise RunLifecycleConflict("persisted recovery identity changed")
    _approval(stores, request)
    payload = action.payload
    if not isinstance(payload, Mapping):
        raise RunLifecycleConflict("persisted recovery action is malformed")
    decision_id = payload.get("decision_event_id")
    consequence_id = payload.get("consequence_event_id")
    if type(decision_id) is not str or type(consequence_id) is not str:
        raise RunLifecycleConflict("persisted recovery action is malformed")
    decision_event = _event(stores, decision_id)
    consequence_event = _event(stores, consequence_id)
    return dataclasses.replace(
        _result(request, run, decision_event, consequence_event, action),
        replayed=True,
    )


def _persist_claim(
    stores: Any,
    command: ExecutionLeaseRecoveryCommand,
    result: ExecutionLeaseRecoveryResult,
) -> ExecutionRequestRecord:
    if isinstance(command, AbandonExpiredExecutionClaim):
        persisted = stores.execution.abandon_request_claim(
            command.request_id,
            expected_fence=command.expected_fence,
            observed_at=result.decision_event.occurred_at,
        )
    else:
        replacement = result.decision_event.recovery.replacement_fence
        assert replacement is not None
        persisted = stores.execution.rotate_request_claim(
            command.request_id,
            expected_fence=command.expected_fence,
            replacement_fence=replacement,
            observed_at=result.decision_event.occurred_at,
            lease_duration_seconds=command.lease_duration.seconds,
        )
    if persisted is None:
        raise RunLifecycleConflict("execution claim changed during recovery")
    return persisted


def _require_scope(command: ExecutionLeaseRecoveryCommand) -> None:
    required = {
        RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: RecoveryScope.RENEW_CLAIM,
        RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: RecoveryScope.RENEW_CLAIM,
        RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: RecoveryScope.TAKE_OVER_CLAIM,
        RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: RecoveryScope.ABANDON_CLAIM,
    }[_decision_kind(command)]
    if required not in command.authority.scopes:
        raise RunLifecycleDenied("required recovery scope is missing")


def _require_locked_identity(
    command: ExecutionLeaseRecoveryCommand,
    locator: ExecutionRequestRecord,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    session_id: str,
) -> None:
    if (
        locator.identity != request.identity
        or request.identity.session_id != session_id
        or run.admission.request_id != request.identity.request_id
        or run.plan_id != request.identity.plan_id
        or run.run_id != command.retained_run_id.value
    ):
        raise RunLifecycleConflict("recovery target identity changed")


def _require_first_state(
    command: ExecutionLeaseRecoveryCommand,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
) -> None:
    if request.status is not ExecutionRequestStatus.CLAIMED or request.claim is None:
        raise RunLifecycleConflict("execution request is not claimed")
    if request.claim.fence != command.expected_fence:
        raise RunLifecycleConflict("execution claim fence changed")
    if request.claim.fence.generation >= 2**63 - 1:
        raise RunLifecycleConflict("execution claim generation is exhausted")
    expected_run_status = (
        ActivityRunStatus.CLAIMED
        if isinstance(command, RenewActiveExecutionClaim)
        else ActivityRunStatus.FAILED
    )
    if run.status is not expected_run_status:
        raise RunLifecycleConflict("retained run status rejects recovery")
    if expected_run_status is ActivityRunStatus.FAILED and (
        run.started_at is None or run.settled_at is not None
    ):
        raise RunLifecycleConflict("failed retained run is not recoverable")


def _require_expiry(
    command: ExecutionLeaseRecoveryCommand,
    expired: bool,
) -> None:
    if isinstance(command, RenewActiveExecutionClaim):
        if expired:
            raise RunLifecycleConflict("active execution claim has expired")
    elif not expired:
        raise RunLifecycleConflict("expired execution claim remains active")


def _approval(
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


def _require_journal(
    command: ExecutionLeaseRecoveryCommand,
    run: ActivityRunRecord,
    plan: ActivityPlanRecord,
    events: tuple[ActivityEventRecord, ...],
) -> None:
    if not events or any(event.run_id != run.run_id for event in events):
        raise RunLifecycleConflict("retained run journal is invalid")
    planned = {activity.activity_id.value for activity in plan.plan.activities}
    if any(
        event.activity_id is not None and event.activity_id not in planned
        for event in events
    ):
        raise RunLifecycleConflict("retained run journal names a foreign activity")
    if isinstance(command, RenewActiveExecutionClaim):
        if tuple(event.kind for event in events) != (ActivityEventKind.RUN_OPENED,):
            raise RunLifecycleConflict("active retained run has effect history")
        return
    if events[-1].kind is not ActivityEventKind.RUN_FAILED:
        raise RunLifecycleConflict("failed retained run lacks terminal evidence")
    if any(event.kind in _COMPENSATION_KINDS for event in events):
        raise RunLifecycleConflict("retained run entered compensation")
    for activity_id in {
        event.activity_id
        for event in events
        if event.kind is ActivityEventKind.STEP_STARTED
    }:
        terminal = tuple(
            event.kind
            for event in events
            if event.activity_id == activity_id and event.kind in _FORWARD_FAILURE_KINDS
        )
        if len(terminal) != 1:
            raise RunLifecycleConflict("retained run effect failure is unresolved")
    if not any(event.kind is ActivityEventKind.STEP_STARTED for event in events):
        raise RunLifecycleConflict("failed retained run has no attempted effect")


def _planned_request(
    request: ExecutionRequestRecord,
    command: ExecutionLeaseRecoveryCommand,
    replacement: ExecutionLeaseFence | None,
    observed_at: str,
) -> ExecutionRequestRecord:
    if replacement is None:
        return dataclasses.replace(
            request,
            status=ExecutionRequestStatus.ABANDONED,
            claim=None,
        )
    return dataclasses.replace(
        request,
        claim=ClaimIdentity(
            replacement.worker_id,
            replacement.generation,
            observed_at,
            _expires_at(observed_at, command.lease_duration.seconds),
        ),
    )


def _replacement_fence(
    command: ExecutionLeaseRecoveryCommand,
) -> ExecutionLeaseFence | None:
    if isinstance(command, AbandonExpiredExecutionClaim):
        return None
    worker_id = (
        command.next_worker_id
        if isinstance(command, TakeOverExpiredExecutionClaim)
        else command.expected_fence.worker_id
    )
    return ExecutionLeaseFence(worker_id, command.expected_fence.generation + 1)


def _expires_at(observed_at: str, duration_seconds: int) -> str:
    try:
        parsed = datetime.fromisoformat(observed_at[:-1] + "+00:00")
        expires = (parsed + timedelta(seconds=duration_seconds)).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    else:
        timespec = "microseconds" if expires.microsecond else "seconds"
        return expires.isoformat(timespec=timespec).replace("+00:00", "Z")
    raise RunLifecycleConflict("recovery observation time is invalid")


def _action_payload(
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    decision: ActivityEventRecord,
    consequence: ActivityEventRecord,
    command: ExecutionLeaseRecoveryCommand,
) -> dict[str, object]:
    recovery = decision.recovery
    assert recovery is not None
    payload: dict[str, object] = {
        "execution_request_id": request.identity.request_id,
        "plan_id": request.identity.plan_id,
        "retained_run_id": run.run_id,
        "decision_event_id": decision.event_id,
        "decision_event_kind": decision.kind.value,
        "decision_event_ordinal": decision.ordinal,
        "consequence_event_id": consequence.event_id,
        "consequence_event_kind": consequence.kind.value,
        "consequence_event_ordinal": consequence.ordinal,
        "recovery": recovery.descriptor(),
    }
    if not isinstance(command, AbandonExpiredExecutionClaim):
        payload["lease_duration_seconds"] = command.lease_duration.seconds
    return payload


def _decision_kind(command: ExecutionLeaseRecoveryCommand) -> RecoveryDecisionKind:
    if isinstance(command, RenewActiveExecutionClaim):
        return RecoveryDecisionKind.RENEW_ACTIVE_CLAIM
    if isinstance(command, RenewExpiredExecutionClaim):
        return RecoveryDecisionKind.RENEW_EXPIRED_CLAIM
    if isinstance(command, TakeOverExpiredExecutionClaim):
        return RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM
    return RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM


def _request(stores: Any, request_id: str) -> ExecutionRequestRecord:
    try:
        request = stores.execution.get_request(request_id)
    except KeyError:
        read_failure = "missing"
    except OperationsRecordError:
        read_failure = "invalid"
    else:
        return request
    if read_failure == "missing":
        raise RunLifecycleNotFound("execution request was not found")
    raise RunLifecycleConflict("execution request history is invalid")


def _request_for_update(stores: Any, request_id: str) -> ExecutionRequestRecord:
    try:
        request = stores.execution.get_request_for_update(request_id)
    except KeyError:
        read_failure = "missing"
    except OperationsRecordError:
        read_failure = "invalid"
    else:
        return request
    if read_failure == "missing":
        raise RunLifecycleNotFound("execution request was not found")
    raise RunLifecycleConflict("execution request history is invalid")


def _latest_run_for_update(stores: Any, request_id: str) -> ActivityRunRecord:
    try:
        run = stores.execution.get_latest_run_for_request_for_update(request_id)
    except KeyError:
        read_failure = "missing"
    except OperationsRecordError:
        read_failure = "invalid"
    else:
        return run
    if read_failure == "missing":
        raise RunLifecycleNotFound("activity run was not found")
    raise RunLifecycleConflict("activity run history is invalid")


def _event(stores: Any, event_id: str) -> ActivityEventRecord:
    try:
        event = stores.execution.get_event(event_id)
    except KeyError:
        read_failure = "missing"
    except OperationsRecordError:
        read_failure = "invalid"
    else:
        return event
    if read_failure == "missing":
        raise RunLifecycleNotFound("recovery event was not found")
    raise RunLifecycleConflict("recovery event history is invalid")


def _open_session(history: Any, session_id: str) -> Any:
    try:
        session = history.get_session_for_update(session_id)
    except KeyError:
        read_failure = "missing"
    except OperationsRecordError:
        read_failure = "invalid"
    else:
        read_failure = None
    if read_failure == "missing":
        raise RunLifecycleNotFound("operation session was not found")
    if read_failure == "invalid":
        raise RunLifecycleConflict("operation session history is invalid")
    if session.status is not OperationSessionStatus.OPEN:
        raise RunLifecycleConflict("operation session is not open")
    return session


def _result(
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    decision: ActivityEventRecord,
    consequence: ActivityEventRecord,
    action: OperationActionRecord,
) -> ExecutionLeaseRecoveryResult:
    try:
        return ExecutionLeaseRecoveryResult(
            request,
            run,
            decision,
            consequence,
            action,
        )
    except OperationsRecordError:
        pass
    raise RunLifecycleConflict("persisted recovery history is incongruent")


__all__ = ["ExecutionLeaseRecoveryCommandService"]
