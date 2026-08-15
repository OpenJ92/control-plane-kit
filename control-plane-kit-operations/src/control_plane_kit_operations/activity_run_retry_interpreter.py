"""Transactional interpreter for linked activity-run retry."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations._execution_lease_recovery_support import (
    locked_recovery_approval,
    require_recovery_eligible_journal,
)
from control_plane_kit_operations.activity_run_retry import (
    ActivityRunRetryResult,
    RetryFailedActivityRun,
)
from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleDenied,
    RunLifecycleIdempotencyConflict,
    RunLifecycleNotFound,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ExecutionLeaseRecoveryEvidence,
    ExecutionRequestRecord,
    OperationActionRecord,
    OperationSessionStatus,
    OperationsRecordError,
    RetryIdentity,
)


class ActivityRunRetryCommandService:
    """Interpret one admitted retry as one atomic linked-run transaction."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._id_factory = id_factory

    def execute(self, command: RetryFailedActivityRun) -> ActivityRunRetryResult:
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
                        "idempotency key was reused with different retry intent"
                    )
                result = _replay(stores, command, locator, existing)
                unit_of_work.commit()
                return result

            try:
                session = history.get_session_for_update(
                    locator.identity.session_id
                )
            except KeyError:
                session_failure = "missing"
            except OperationsRecordError:
                session_failure = "invalid"
            else:
                session_failure = None
            if session_failure == "missing":
                raise RunLifecycleNotFound("operation session was not found")
            if session_failure == "invalid":
                raise RunLifecycleConflict("operation session history is invalid")
            if session.status is not OperationSessionStatus.OPEN:
                raise RunLifecycleConflict("operation session is not open")

            request = _request_for_update(stores, command.request_id)
            prior_run = _run_for_request_for_update(
                stores,
                command.request_id,
                command.prior_run_id.value,
            )
            latest_run = _latest_run_for_update(stores, command.request_id)
            _require_first_state(
                command,
                locator,
                request,
                prior_run,
                latest_run,
                session.session_id,
            )
            _, _, plan = locked_recovery_approval(stores, request)
            require_recovery_eligible_journal(
                RecoveryDecisionKind.RETRY_AS_NEW_RUN,
                command.expected_fence,
                prior_run,
                plan,
                _events_for_run(stores, prior_run.run_id),
            )

            observation = stores.execution.observe_request_lease_for_update(
                request.identity.request_id
            )
            if observation.request != request:
                raise RunLifecycleConflict("execution request changed before retry")
            if observation.expired:
                raise RunLifecycleConflict("execution claim has expired")
            event_ordinal = stores.execution.next_event_ordinal(prior_run.run_id)
            action_ordinal = history.next_action_ordinal(
                request.identity.session_id
            )
            result = self._plan_result(
                command,
                request,
                prior_run,
                observed_at=observation.observed_at,
                event_ordinal=event_ordinal,
                action_ordinal=action_ordinal,
            )
            if stores.execution.add_run(result.run) != result.run:
                raise RunLifecycleConflict("retry record was not preserved")
            if stores.execution.add_event(result.decision_event) != result.decision_event:
                raise RunLifecycleConflict("retry record was not preserved")
            if stores.execution.add_event(result.opened_event) != result.opened_event:
                raise RunLifecycleConflict("retry record was not preserved")
            if history.add_action(result.action) != result.action:
                raise RunLifecycleConflict("retry record was not preserved")
            unit_of_work.commit()
            return result

    def _plan_result(
        self,
        command: RetryFailedActivityRun,
        request: ExecutionRequestRecord,
        prior_run: ActivityRunRecord,
        *,
        observed_at: str,
        event_ordinal: int,
        action_ordinal: int,
    ) -> ActivityRunRetryResult:
        run_id = self._id_factory()
        decision_event_id = self._id_factory()
        opened_event_id = self._id_factory()
        action_id = self._id_factory()
        metadata = BoundedEvidence.from_mapping(
            {
                "attempt": prior_run.retry.attempt + 1,
                "prior_run_id": prior_run.run_id,
            }
        )
        run = ActivityRunRecord(
            run_id,
            request.identity.plan_id,
            AdmittedRun(request.identity.request_id),
            RetryIdentity(prior_run.retry.attempt + 1, prior_run.run_id),
            ActivityRunStatus.CLAIMED,
            observed_at,
            metadata=metadata,
        )
        recovery = ExecutionLeaseRecoveryEvidence(
            RecoveryDecisionKind.RETRY_AS_NEW_RUN,
            RunId(prior_run.run_id),
            command.expected_fence,
            command.expected_fence,
        )
        decision_event = ActivityEventRecord(
            decision_event_id,
            prior_run.run_id,
            event_ordinal,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            observed_at,
            recovery=recovery,
        )
        opened_event = ActivityEventRecord(
            opened_event_id,
            run.run_id,
            1,
            ActivityEventKind.RUN_OPENED,
            observed_at,
            evidence=metadata,
        )
        action = OperationActionRecord(
            action_id,
            request.identity.session_id,
            action_ordinal,
            LifecycleOperationKind.RECORD_RECOVERY_DECISION,
            command.authority.actor_id,
            _action_payload(request, prior_run, run, decision_event, opened_event),
            observed_at,
            command.idempotency_key.value,
            command.intent_fingerprint(),
        )
        return ActivityRunRetryResult(
            request,
            prior_run,
            run,
            decision_event,
            opened_event,
            action,
        )


def _replay(
    stores: Any,
    command: RetryFailedActivityRun,
    locator: ExecutionRequestRecord,
    action: OperationActionRecord,
) -> ActivityRunRetryResult:
    request = _request_for_update(stores, command.request_id)
    prior_run = _run_for_request_for_update(
        stores,
        command.request_id,
        command.prior_run_id.value,
    )
    payload = action.payload
    if not isinstance(payload, Mapping):
        raise RunLifecycleConflict("persisted retry action is malformed")
    run_id = payload.get("run_id")
    decision_event_id = payload.get("decision_event_id")
    opened_event_id = payload.get("opened_event_id")
    if (
        type(run_id) is not str
        or type(decision_event_id) is not str
        or type(opened_event_id) is not str
    ):
        raise RunLifecycleConflict("persisted retry action is malformed")
    run = _run_for_request_for_update(stores, command.request_id, run_id)
    latest_run = _latest_run_for_update(stores, command.request_id)
    if run != latest_run:
        raise RunLifecycleConflict("persisted retry successor changed")
    locked_recovery_approval(stores, request)
    decision_event = _event(stores, decision_event_id)
    opened_event = _event(stores, opened_event_id)
    result = _result(
        request,
        prior_run,
        run,
        decision_event,
        opened_event,
        action,
        replayed=True,
    )
    _require_replay_command(command, locator, result)
    return result


def _require_scope(command: RetryFailedActivityRun) -> None:
    if RecoveryScope.OPERATE not in command.authority.scopes:
        raise RunLifecycleDenied("required recovery scope is missing")


def _require_first_state(
    command: RetryFailedActivityRun,
    locator: ExecutionRequestRecord,
    request: ExecutionRequestRecord,
    prior_run: ActivityRunRecord,
    latest_run: ActivityRunRecord,
    session_id: str,
) -> None:
    claim = request.claim
    if (
        locator.identity != request.identity
        or request.identity.session_id != session_id
        or request.status is not ExecutionRequestStatus.CLAIMED
        or claim is None
        or claim.fence != command.expected_fence
        or prior_run != latest_run
        or prior_run.run_id != command.prior_run_id.value
        or prior_run.admission.request_id != request.identity.request_id
        or prior_run.plan_id != request.identity.plan_id
        or prior_run.status is not ActivityRunStatus.FAILED
        or prior_run.started_at is None
        or prior_run.settled_at is not None
        or prior_run.retry.attempt >= 2_147_483_647
    ):
        raise RunLifecycleConflict("activity run retry target changed")


def _require_replay_command(
    command: RetryFailedActivityRun,
    locator: ExecutionRequestRecord,
    result: ActivityRunRetryResult,
) -> None:
    claim = result.request.claim
    recovery = result.decision_event.recovery
    assert recovery is not None
    if (
        locator.identity != result.request.identity
        or result.request.status is not ExecutionRequestStatus.CLAIMED
        or claim is None
        or claim.fence != command.expected_fence
        or result.prior_run.run_id != command.prior_run_id.value
        or recovery.prior_fence != command.expected_fence
        or recovery.replacement_fence != command.expected_fence
        or result.action.actor_id != command.authority.actor_id
        or result.action.idempotency_key != command.idempotency_key.value
        or result.action.intent_fingerprint != command.intent_fingerprint()
    ):
        raise RunLifecycleConflict("persisted retry history changed")


def _request(stores: Any, request_id: str) -> ExecutionRequestRecord:
    try:
        request = stores.execution.get_request(request_id)
    except KeyError:
        failure = "missing"
    except OperationsRecordError:
        failure = "invalid"
    else:
        return request
    if failure == "missing":
        raise RunLifecycleNotFound("execution request was not found")
    raise RunLifecycleConflict("execution request history is invalid")


def _request_for_update(stores: Any, request_id: str) -> ExecutionRequestRecord:
    try:
        request = stores.execution.get_request_for_update(request_id)
    except KeyError:
        failure = "missing"
    except OperationsRecordError:
        failure = "invalid"
    else:
        return request
    if failure == "missing":
        raise RunLifecycleNotFound("execution request was not found")
    raise RunLifecycleConflict("execution request history is invalid")


def _run_for_request_for_update(
    stores: Any,
    request_id: str,
    run_id: str,
) -> ActivityRunRecord:
    try:
        run = stores.execution.get_run_for_request_for_update(request_id, run_id)
    except KeyError:
        failure = "missing"
    except OperationsRecordError:
        failure = "invalid"
    else:
        return run
    if failure == "missing":
        raise RunLifecycleNotFound("activity run was not found")
    raise RunLifecycleConflict("activity run history is invalid")


def _latest_run_for_update(stores: Any, request_id: str) -> ActivityRunRecord:
    try:
        run = stores.execution.get_latest_run_for_request_for_update(request_id)
    except KeyError:
        failure = "missing"
    except OperationsRecordError:
        failure = "invalid"
    else:
        return run
    if failure == "missing":
        raise RunLifecycleNotFound("activity run was not found")
    raise RunLifecycleConflict("activity run history is invalid")


def _events_for_run(
    stores: Any,
    run_id: str,
) -> tuple[ActivityEventRecord, ...]:
    try:
        events = stores.execution.events_for_run(run_id)
    except (OperationsRecordError, ValueError):
        pass
    else:
        return events
    raise RunLifecycleConflict("activity event history is invalid")


def _event(stores: Any, event_id: str) -> ActivityEventRecord:
    try:
        event = stores.execution.get_event(event_id)
    except KeyError:
        failure = "missing"
    except (OperationsRecordError, ValueError):
        failure = "invalid"
    else:
        return event
    if failure == "missing":
        raise RunLifecycleNotFound("retry event was not found")
    raise RunLifecycleConflict("retry event history is invalid")


def _result(
    request: ExecutionRequestRecord,
    prior_run: ActivityRunRecord,
    run: ActivityRunRecord,
    decision_event: ActivityEventRecord,
    opened_event: ActivityEventRecord,
    action: OperationActionRecord,
    *,
    replayed: bool,
) -> ActivityRunRetryResult:
    try:
        return ActivityRunRetryResult(
            request,
            prior_run,
            run,
            decision_event,
            opened_event,
            action,
            replayed,
        )
    except OperationsRecordError:
        pass
    raise RunLifecycleConflict("persisted retry history is incongruent")


def _action_payload(
    request: ExecutionRequestRecord,
    prior_run: ActivityRunRecord,
    run: ActivityRunRecord,
    decision_event: ActivityEventRecord,
    opened_event: ActivityEventRecord,
) -> dict[str, object]:
    recovery = decision_event.recovery
    assert recovery is not None
    return {
        "execution_request_id": request.identity.request_id,
        "plan_id": request.identity.plan_id,
        "prior_run_id": prior_run.run_id,
        "run_id": run.run_id,
        "prior_attempt": prior_run.retry.attempt,
        "attempt": run.retry.attempt,
        "decision_event_id": decision_event.event_id,
        "decision_event_kind": decision_event.kind.value,
        "decision_event_ordinal": decision_event.ordinal,
        "opened_event_id": opened_event.event_id,
        "opened_event_kind": opened_event.kind.value,
        "opened_event_ordinal": opened_event.ordinal,
        "recovery": recovery.descriptor(),
    }


__all__ = ["ActivityRunRetryCommandService"]
