"""Transactional command language for one admitted execution attempt.

This module changes durable execution truth only.  It does not interpret an
activity, call a runtime, or perform any other external effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias
from uuid import uuid4

from control_plane_kit.execution import (
    AbandonExpiredClaim,
    AcceptUncompensatedFailure,
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    BeginCompensation,
    BoundedEvidence,
    ClaimIdentity,
    ConfirmEffectFailed,
    ConfirmEffectSucceeded,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureEvidence,
    RecoveryAuthorizationDenied,
    RecoveryContext,
    RecoveryDecisionRecord,
    RecoveryDecisionRejected,
    RenewExpiredClaim,
    RemainPaused,
    ResumeSameIntent,
    RetryIdentity,
    RetryAsNewRun,
    TakeOverExpiredClaim,
    authorize_recovery_decision,
    validate_recovery_decision,
)
from control_plane_kit.saga import compensation_candidates
from control_plane_kit.stores import (
    ActivityHistoryStore,
    ApprovalDecisionKind,
    ExecutionStore,
    OperationActionKind,
    OperationActionRecord,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows.saga_journal import (
    SagaJournalError,
    project_activity_journal,
)
from control_plane_kit.workflows.commands import (
    IdempotencyKey,
    InvalidOperationCommand,
)


@dataclass(frozen=True)
class ExecutionWorkerAuthority:
    """Authenticated worker identity and its current authorization scopes."""

    worker_id: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        _required("worker_id", self.worker_id)
        object.__setattr__(self, "scopes", _scopes(self.scopes))


@dataclass(frozen=True)
class ClaimAndOpenActivityRun:
    request_id: str
    authority: ExecutionWorkerAuthority
    lease_expires_at: str
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "request_id")
        _required("lease_expires_at", self.lease_expires_at)


@dataclass(frozen=True)
class StartActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")


@dataclass(frozen=True)
class PauseActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")


@dataclass(frozen=True)
class CompleteActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")


@dataclass(frozen=True)
class FailActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    failure: FailureEvidence
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")
        if not isinstance(self.failure, FailureEvidence):
            raise InvalidOperationCommand("failure must be FailureEvidence")


@dataclass(frozen=True)
class CompleteActivityRunCompensation:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")


@dataclass(frozen=True)
class FailActivityRunCompensation:
    run_id: str
    authority: ExecutionWorkerAuthority
    failure: FailureEvidence
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")
        if not isinstance(self.failure, FailureEvidence):
            raise InvalidOperationCommand("failure must be FailureEvidence")


@dataclass(frozen=True)
class CancelActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")


@dataclass(frozen=True)
class DecideActivityRunRecovery:
    """Choose one authorized recovery path for the currently owned run."""

    run_id: str
    expected_worker_id: str
    expected_event_ordinal: int
    recovery: RecoveryDecisionRecord
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required("run_id", self.run_id)
        _required("expected_worker_id", self.expected_worker_id)
        if type(self.expected_event_ordinal) is not int or self.expected_event_ordinal < 1:
            raise InvalidOperationCommand(
                "expected_event_ordinal must be a positive integer"
            )
        if not isinstance(self.recovery, RecoveryDecisionRecord):
            raise InvalidOperationCommand(
                "recovery must be RecoveryDecisionRecord"
            )
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


RunLifecycleCommand: TypeAlias = (
    ClaimAndOpenActivityRun
    | StartActivityRun
    | PauseActivityRun
    | CompleteActivityRun
    | FailActivityRun
    | CompleteActivityRunCompensation
    | FailActivityRunCompensation
    | CancelActivityRun
    | DecideActivityRunRecovery
)
RunTransitionCommand: TypeAlias = (
    StartActivityRun
    | PauseActivityRun
    | CompleteActivityRun
    | FailActivityRun
    | CompleteActivityRunCompensation
    | FailActivityRunCompensation
    | CancelActivityRun
)


@dataclass(frozen=True)
class RunLifecycleResult:
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    event: ActivityEventRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.run.admission, AdmittedRun):
            raise InvalidOperationCommand("lifecycle result requires an admitted run")
        if self.run.admission.request_id != self.request.identity.request_id:
            raise InvalidOperationCommand("run and request identity must agree")
        if self.event.run_id != self.run.run_id:
            raise InvalidOperationCommand("event and run identity must agree")
        if self.action.action_type is not OperationActionKind.EXECUTION_RUN_TRANSITIONED:
            raise InvalidOperationCommand("lifecycle result requires transition evidence")
        if self.action.payload.get("event_id") != self.event.event_id:
            raise InvalidOperationCommand("operation evidence must identify the event")


@dataclass(frozen=True)
class RecoveryCommandResult:
    """Atomic recovery decision evidence and its immediate durable consequence."""

    request: ExecutionRequestRecord
    run: ActivityRunRecord
    decision_event: ActivityEventRecord
    consequence_event: ActivityEventRecord | None
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.run.admission.request_id != self.request.identity.request_id:
            raise InvalidOperationCommand("run and request identity must agree")
        if self.decision_event.kind is not ActivityEventKind.RECOVERY_DECISION_RECORDED:
            raise InvalidOperationCommand(
                "recovery result requires a recovery decision event"
            )
        if self.decision_event.recovery is None:
            raise InvalidOperationCommand("recovery decision evidence is missing")
        if self.action.action_type is not OperationActionKind.RECOVERY_REQUESTED:
            raise InvalidOperationCommand("recovery result requires recovery action evidence")
        if self.action.payload.get("decision_event_id") != self.decision_event.event_id:
            raise InvalidOperationCommand("operation evidence must identify the decision")
        if (
            self.action.payload.get("execution_request_id")
            != self.request.identity.request_id
            or self.action.payload.get("result_run_id") != self.run.run_id
        ):
            raise InvalidOperationCommand(
                "operation evidence must identify the recovery result"
            )
        expected = (
            None if self.consequence_event is None else self.consequence_event.event_id
        )
        if self.action.payload.get("consequence_event_id") != expected:
            raise InvalidOperationCommand(
                "operation evidence must identify the recovery consequence"
            )


class RunLifecycleError(RuntimeError):
    """Base error for durable run lifecycle commands."""


class RunLifecycleNotFound(RunLifecycleError):
    pass


class RunLifecycleConflict(RunLifecycleError):
    pass


class RunLifecycleDenied(RunLifecycleError):
    pass


class RunLifecycleIdempotencyConflict(RunLifecycleError):
    pass


@dataclass(frozen=True)
class RunTransition:
    expected: frozenset[ActivityRunStatus]
    replacement: ActivityRunStatus
    event_kind: ActivityEventKind
    settles_run: bool = False
    cancel_request: bool = False


_START = RunTransition(
    frozenset({ActivityRunStatus.CLAIMED}),
    ActivityRunStatus.RUNNING,
    ActivityEventKind.RUN_STARTED,
)
_PAUSE = RunTransition(
    frozenset({ActivityRunStatus.RUNNING}),
    ActivityRunStatus.PAUSED,
    ActivityEventKind.RUN_PAUSED,
)
_COMPLETE = RunTransition(
    frozenset({ActivityRunStatus.RUNNING}),
    ActivityRunStatus.SUCCEEDED,
    ActivityEventKind.RUN_SUCCEEDED,
    settles_run=True,
)
_FAIL = RunTransition(
    frozenset({ActivityRunStatus.RUNNING, ActivityRunStatus.PAUSED}),
    ActivityRunStatus.FAILED,
    ActivityEventKind.RUN_FAILED,
)
_COMPLETE_COMPENSATION = RunTransition(
    frozenset({ActivityRunStatus.COMPENSATING}),
    ActivityRunStatus.COMPENSATED,
    ActivityEventKind.RUN_COMPENSATION_SUCCEEDED,
    settles_run=True,
)
_FAIL_COMPENSATION = RunTransition(
    frozenset({ActivityRunStatus.COMPENSATING}),
    ActivityRunStatus.PARTIALLY_FAILED,
    ActivityEventKind.RUN_COMPENSATION_FAILED,
    settles_run=True,
)
_CANCEL = RunTransition(
    frozenset(
        {
            ActivityRunStatus.CLAIMED,
            ActivityRunStatus.RUNNING,
            ActivityRunStatus.PAUSED,
        }
    ),
    ActivityRunStatus.CANCELLED,
    ActivityEventKind.RUN_CANCELLED,
    settles_run=True,
    cancel_request=True,
)


Clock = Callable[[], str]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


def _uuid() -> str:
    return uuid4().hex


class RunLifecycleCommandService:
    """Interpret one lifecycle command in one explicit Postgres transaction."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory

    def execute(
        self, command: RunLifecycleCommand
    ) -> RunLifecycleResult | RecoveryCommandResult:
        match command:
            case DecideActivityRunRecovery():
                return self._recover(command)
            case ClaimAndOpenActivityRun():
                _authorize(command.authority)
                return self._claim_and_open(command)
            case (
                StartActivityRun()
                | PauseActivityRun()
                | CompleteActivityRun()
                | FailActivityRun()
                | CompleteActivityRunCompensation()
                | FailActivityRunCompensation()
                | CancelActivityRun()
            ):
                _authorize(command.authority)
                return self._transition(command)
        raise InvalidOperationCommand(
            f"unsupported lifecycle command {type(command).__name__}"
        )

    def _claim_and_open(
        self, command: ClaimAndOpenActivityRun
    ) -> RunLifecycleResult:
        occurred_at = self._clock()
        _require_future_lease(occurred_at, command.lease_expires_at)
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            execution = work.stores.execution
            history = work.stores.activity_history
            request = _request_for_update(execution, command.request_id)
            replay = _replay_if_present(
                execution,
                history,
                request,
                command.idempotency_key,
                fingerprint,
            )
            if replay is not None:
                return replay
            claimed = execution.claim_request(
                command.request_id,
                command.authority.worker_id,
                occurred_at,
                command.lease_expires_at,
            )
            if claimed is None:
                raise RunLifecycleConflict("execution request is not claimable")
            if execution.runs_for_request(command.request_id):
                raise RunLifecycleConflict("execution request already has a run")
            run = execution.add_run(
                ActivityRunRecord(
                    run_id=self._id_factory(),
                    plan_id=claimed.identity.plan_id,
                    admission=AdmittedRun(claimed.identity.request_id),
                    retry=RetryIdentity(1),
                    status=ActivityRunStatus.CLAIMED,
                    created_at=occurred_at,
                )
            )
            result = self._record(
                execution,
                history,
                claimed,
                run,
                command,
                fingerprint,
                ActivityEventKind.RUN_OPENED,
                occurred_at,
                evidence=BoundedEvidence.from_mapping({"attempt": 1}),
            )
            work.commit()
            return result

    def _transition(
        self,
        command: RunTransitionCommand,
    ) -> RunLifecycleResult:
        occurred_at = self._clock()
        fingerprint = _fingerprint(command)
        failure = (
            command.failure
            if isinstance(command, (FailActivityRun, FailActivityRunCompensation))
            else None
        )
        run_id = command.run_id
        with self._unit_of_work_factory() as work:
            execution = work.stores.execution
            history = work.stores.activity_history
            run = _run(execution, run_id)
            request_id = _admitted_request_id(run)
            request = _request_for_update(execution, request_id)
            replay = _replay_if_present(
                execution,
                history,
                request,
                command.idempotency_key,
                fingerprint,
            )
            if replay is not None:
                return replay
            _require_owner(request, command.authority.worker_id)
            run = _run_for_update(execution, run_id)
            transition = decide_run_transition(command, run.status)
            replacement = execution.compare_and_set_run_status(
                run.run_id,
                expected=run.status,
                replacement=transition.replacement,
                started_at=(
                    occurred_at
                    if transition.event_kind is ActivityEventKind.RUN_STARTED
                    else None
                ),
                settled_at=occurred_at if transition.settles_run else None,
            )
            if replacement is None:
                raise RunLifecycleConflict("run changed concurrently")
            if transition.cancel_request:
                cancelled = execution.cancel_claimed_request(
                    request.identity.request_id,
                    worker_id=command.authority.worker_id,
                )
                if cancelled is None:
                    raise RunLifecycleConflict("execution request cannot be cancelled")
                request = cancelled
            result = self._record(
                execution,
                history,
                request,
                replacement,
                command,
                fingerprint,
                transition.event_kind,
                occurred_at,
                failure=failure,
            )
            work.commit()
            return result

    def _recover(
        self,
        command: DecideActivityRunRecovery,
    ) -> RecoveryCommandResult:
        occurred_at = self._clock()
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            execution = work.stores.execution
            history = work.stores.activity_history
            initial = _run(execution, command.run_id)
            request = _request_for_update(
                execution, _admitted_request_id(initial)
            )
            replay = _replay_recovery_if_present(
                execution,
                history,
                request,
                command.idempotency_key,
                fingerprint,
            )
            if replay is not None:
                return replay
            _require_owner(request, command.expected_worker_id)
            run = _run_for_update(execution, command.run_id)
            _require_context_identity(run, request)
            plan = _current_approved_plan(history, request)
            events = execution.events_for_run(run.run_id)
            if events[-1].ordinal != command.expected_event_ordinal:
                raise RunLifecycleConflict(
                    "recovery journal changed after the operator decision"
                )
            try:
                journal = project_activity_journal(plan.plan, events)
            except SagaJournalError as error:
                raise RunLifecycleConflict(
                    "recovery requires coherent canonical journal evidence"
                ) from error
            context = RecoveryContext(
                run_status=run.status,
                uncertain_activity_ids=frozenset(
                    event.activity_id
                    for event in journal.uncertain
                    if event.activity_id is not None
                ),
                compensation_available=bool(
                    compensation_candidates(journal.state)
                ),
                claim_expired=_claim_is_expired(request, occurred_at),
            )
            try:
                validate_recovery_decision(command.recovery.decision, context)
                authorize_recovery_decision(
                    command.recovery.decision,
                    command.recovery.authority,
                )
            except RecoveryDecisionRejected as error:
                raise RunLifecycleConflict(str(error)) from error
            except RecoveryAuthorizationDenied as error:
                raise RunLifecycleDenied(str(error)) from error

            decision_event = execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=execution.next_event_ordinal(run.run_id),
                    kind=ActivityEventKind.RECOVERY_DECISION_RECORDED,
                    occurred_at=occurred_at,
                    recovery=command.recovery,
                )
            )
            request, run, consequence = self._apply_recovery_consequence(
                execution,
                request,
                run,
                command,
                occurred_at,
            )
            action = history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=request.identity.session_id,
                    ordinal=history.next_action_ordinal(
                        request.identity.session_id
                    ),
                    action_type=OperationActionKind.RECOVERY_REQUESTED,
                    actor_id=command.recovery.authority.operator_id,
                    payload={
                        "command": "decide_run_recovery",
                        "execution_request_id": request.identity.request_id,
                        "run_id": command.run_id,
                        "result_run_id": run.run_id,
                        "decision_id": command.recovery.decision_id,
                        "decision_event_id": decision_event.event_id,
                        "consequence_event_id": (
                            None if consequence is None else consequence.event_id
                        ),
                        "result_status": run.status.value,
                        "result_request_status": request.status.value,
                        "result_worker_id": (
                            None if request.claim is None else request.claim.worker_id
                        ),
                    },
                    created_at=occurred_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            result = RecoveryCommandResult(
                request,
                run,
                decision_event,
                consequence,
                action,
            )
            work.commit()
            return result

    def _apply_recovery_consequence(
        self,
        execution: ExecutionStore,
        request: ExecutionRequestRecord,
        run: ActivityRunRecord,
        command: DecideActivityRunRecovery,
        occurred_at: str,
    ) -> tuple[
        ExecutionRequestRecord,
        ActivityRunRecord,
        ActivityEventRecord | None,
    ]:
        decision = command.recovery.decision
        event_kind: ActivityEventKind | None = None
        activity_id: str | None = None
        evidence = BoundedEvidence()

        match decision:
            case ConfirmEffectSucceeded(activity_id=value):
                event_kind = ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED
                activity_id = value
            case ConfirmEffectFailed(activity_id=value):
                event_kind = ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED
                activity_id = value
            case ResumeSameIntent():
                run = _replace_run_status(
                    execution,
                    run,
                    replacement=ActivityRunStatus.RUNNING,
                )
                event_kind = ActivityEventKind.RUN_RESUMED
            case RetryAsNewRun():
                attempts = execution.runs_for_request(
                    request.identity.request_id
                )
                latest = max(attempts, key=lambda value: value.retry.attempt)
                if latest.run_id != run.run_id:
                    raise RunLifecycleConflict(
                        "retry must extend the latest run attempt"
                    )
                attempt = run.retry.attempt + 1
                prior_run_id = run.run_id
                run = execution.add_run(
                    ActivityRunRecord(
                        run_id=self._id_factory(),
                        plan_id=run.plan_id,
                        admission=AdmittedRun(request.identity.request_id),
                        retry=RetryIdentity(attempt, prior_run_id),
                        status=ActivityRunStatus.CLAIMED,
                        created_at=occurred_at,
                    )
                )
                event_kind = ActivityEventKind.RUN_OPENED
                evidence = BoundedEvidence.from_mapping(
                    {"attempt": attempt, "prior_run_id": prior_run_id}
                )
            case BeginCompensation():
                run = _replace_run_status(
                    execution,
                    run,
                    replacement=ActivityRunStatus.COMPENSATING,
                )
                event_kind = ActivityEventKind.RUN_COMPENSATION_STARTED
            case AcceptUncompensatedFailure():
                if run.status is ActivityRunStatus.FAILED:
                    run = _replace_run_status(
                        execution,
                        run,
                        replacement=ActivityRunStatus.UNCOMPENSATED_FAILURE,
                        settled_at=occurred_at,
                    )
                event_kind = ActivityEventKind.RUN_UNCOMPENSATED_FAILURE_ACCEPTED
            case RemainPaused():
                return request, run, None
            case RenewExpiredClaim(lease_expires_at=lease_expires_at):
                _require_future_lease(occurred_at, lease_expires_at)
                prior = request.claim
                if prior is None:
                    raise RunLifecycleConflict("execution request has no claim")
                renewed = execution.renew_expired_request_claim(
                    request.identity.request_id,
                    expected_worker_id=command.expected_worker_id,
                    observed_at=occurred_at,
                    lease_expires_at=lease_expires_at,
                )
                if renewed is None:
                    raise RunLifecycleConflict("expired claim could not be renewed")
                request = renewed
                event_kind = ActivityEventKind.REQUEST_CLAIM_RENEWED
                evidence = _claim_recovery_evidence(
                    "renewed",
                    prior,
                    replacement_worker_id=prior.worker_id,
                    replacement_lease_expires_at=lease_expires_at,
                )
            case TakeOverExpiredClaim(
                replacement_worker_id=replacement_worker_id,
                lease_expires_at=lease_expires_at,
            ):
                _require_future_lease(occurred_at, lease_expires_at)
                prior = request.claim
                if prior is None:
                    raise RunLifecycleConflict("execution request has no claim")
                taken_over = execution.take_over_expired_request_claim(
                    request.identity.request_id,
                    expected_worker_id=command.expected_worker_id,
                    replacement_worker_id=replacement_worker_id,
                    observed_at=occurred_at,
                    lease_expires_at=lease_expires_at,
                )
                if taken_over is None:
                    raise RunLifecycleConflict("expired claim could not be taken over")
                request = taken_over
                event_kind = ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER
                evidence = _claim_recovery_evidence(
                    "taken-over",
                    prior,
                    replacement_worker_id=replacement_worker_id,
                    replacement_lease_expires_at=lease_expires_at,
                )
            case AbandonExpiredClaim():
                prior = request.claim
                if prior is None:
                    raise RunLifecycleConflict("execution request has no claim")
                abandoned = execution.abandon_expired_request_claim(
                    request.identity.request_id,
                    expected_worker_id=command.expected_worker_id,
                    observed_at=occurred_at,
                )
                if abandoned is None:
                    raise RunLifecycleConflict("expired claim could not be abandoned")
                request = abandoned
                event_kind = ActivityEventKind.REQUEST_CLAIM_ABANDONED
                evidence = _claim_recovery_evidence("abandoned", prior)

        if event_kind is None:
            raise InvalidOperationCommand(
                f"unsupported recovery decision {type(decision).__name__}"
            )
        event = execution.add_event(
            ActivityEventRecord(
                event_id=self._id_factory(),
                run_id=run.run_id,
                ordinal=execution.next_event_ordinal(run.run_id),
                kind=event_kind,
                occurred_at=occurred_at,
                activity_id=activity_id,
                evidence=evidence,
            )
        )
        return request, run, event

    def _record(
        self,
        execution: ExecutionStore,
        history: ActivityHistoryStore,
        request: ExecutionRequestRecord,
        run: ActivityRunRecord,
        command: RunLifecycleCommand,
        fingerprint: str,
        event_kind: ActivityEventKind,
        occurred_at: str,
        *,
        evidence: BoundedEvidence = BoundedEvidence(),
        failure: FailureEvidence | None = None,
    ) -> RunLifecycleResult:
        event = execution.add_event(
            ActivityEventRecord(
                event_id=self._id_factory(),
                run_id=run.run_id,
                ordinal=execution.next_event_ordinal(run.run_id),
                kind=event_kind,
                occurred_at=occurred_at,
                evidence=evidence,
                failure=failure,
            )
        )
        action = history.add_action(
            OperationActionRecord(
                action_id=self._id_factory(),
                session_id=request.identity.session_id,
                ordinal=history.next_action_ordinal(request.identity.session_id),
                action_type=OperationActionKind.EXECUTION_RUN_TRANSITIONED,
                actor_id=command.authority.worker_id,
                payload={
                    "command": _command_tag(command),
                    "execution_request_id": request.identity.request_id,
                    "run_id": run.run_id,
                    "event_id": event.event_id,
                    "result_status": run.status.value,
                },
                created_at=occurred_at,
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint,
            )
        )
        return RunLifecycleResult(request, run, event, action)


def _request_for_update(
    execution: ExecutionStore, request_id: str
) -> ExecutionRequestRecord:
    try:
        return execution.get_request_for_update(request_id)
    except KeyError as error:
        raise RunLifecycleNotFound(str(error)) from error


def _run(execution: ExecutionStore, run_id: str) -> ActivityRunRecord:
    try:
        return execution.get_run(run_id)
    except KeyError as error:
        raise RunLifecycleNotFound(str(error)) from error


def _run_for_update(execution: ExecutionStore, run_id: str) -> ActivityRunRecord:
    try:
        return execution.get_run_for_update(run_id)
    except KeyError as error:
        raise RunLifecycleNotFound(str(error)) from error


def _admitted_request_id(run: ActivityRunRecord) -> str:
    return run.admission.request_id


def _require_context_identity(
    run: ActivityRunRecord,
    request: ExecutionRequestRecord,
) -> None:
    if run.admission.request_id != request.identity.request_id:
        raise RunLifecycleConflict("run and request identity disagree")
    if run.plan_id != request.identity.plan_id:
        raise RunLifecycleConflict("run and request plan identity disagree")


def _current_approved_plan(
    history: ActivityHistoryStore,
    request: ExecutionRequestRecord,
):
    try:
        plan = history.get_plan(request.identity.plan_id)
        approval = history.get_approval_request(request.approval_request_id)
        decision = history.approval_decision_for_request(approval.request_id)
    except KeyError as error:
        raise RunLifecycleNotFound(str(error)) from error
    if plan.session_id != request.identity.session_id:
        raise RunLifecycleConflict("plan and request session identity disagree")
    if (
        approval.session_id != request.identity.session_id
        or approval.plan_id != request.identity.plan_id
    ):
        raise RunLifecycleDenied("approval does not authorize this run")
    if decision is None or decision.decision_id != request.approval_decision_id:
        raise RunLifecycleDenied("execution approval is stale or missing")
    if decision.decision is not ApprovalDecisionKind.APPROVED:
        raise RunLifecycleDenied("execution approval is not approved")
    if decision.scope != approval.required_scope:
        raise RunLifecycleDenied("execution approval scope is inconsistent")
    return plan


def _replace_run_status(
    execution: ExecutionStore,
    run: ActivityRunRecord,
    *,
    replacement: ActivityRunStatus,
    settled_at: str | None = None,
) -> ActivityRunRecord:
    result = execution.compare_and_set_run_status(
        run.run_id,
        expected=run.status,
        replacement=replacement,
        settled_at=settled_at,
    )
    if result is None:
        raise RunLifecycleConflict("run changed concurrently or is already settled")
    return result


def _require_owner(request: ExecutionRequestRecord, worker_id: str) -> None:
    if request.status is not ExecutionRequestStatus.CLAIMED or request.claim is None:
        raise RunLifecycleConflict("execution request is not claimed")
    if request.claim.worker_id != worker_id:
        raise RunLifecycleDenied("execution request belongs to another worker")


def _claim_is_expired(request: ExecutionRequestRecord, observed_at: str) -> bool:
    if request.claim is None:
        raise RunLifecycleConflict("execution request has no claim")
    return _timestamp("lease_expires_at", request.claim.lease_expires_at) <= _timestamp(
        "observed_at", observed_at
    )


def _claim_recovery_evidence(
    action: str,
    prior: ClaimIdentity,
    *,
    replacement_worker_id: str | None = None,
    replacement_lease_expires_at: str | None = None,
) -> BoundedEvidence:
    values: dict[str, object] = {
        "claim_action": action,
        "prior_worker_id": prior.worker_id,
        "prior_claimed_at": prior.claimed_at,
        "prior_lease_expires_at": prior.lease_expires_at,
    }
    if replacement_worker_id is not None:
        values["replacement_worker_id"] = replacement_worker_id
    if replacement_lease_expires_at is not None:
        values["replacement_lease_expires_at"] = replacement_lease_expires_at
    return BoundedEvidence.from_mapping(values)


def _replay_if_present(
    execution: ExecutionStore,
    history: ActivityHistoryStore,
    request: ExecutionRequestRecord,
    key: IdempotencyKey,
    fingerprint: str,
) -> RunLifecycleResult | None:
    action = history.action_for_idempotency(
        request.identity.session_id,
        key.value,
    )
    if action is None:
        return None
    if action.action_type is not OperationActionKind.EXECUTION_RUN_TRANSITIONED:
        raise RunLifecycleIdempotencyConflict(
            "idempotency key already belongs to another operation command"
        )
    if action.intent_fingerprint != fingerprint:
        raise RunLifecycleIdempotencyConflict(
            "idempotency key was used for different lifecycle intent"
        )
    run_id = action.payload.get("run_id")
    event_id = action.payload.get("event_id")
    if not isinstance(run_id, str) or not isinstance(event_id, str):
        raise RunLifecycleError("lifecycle operation evidence is incomplete")
    try:
        run = execution.get_run(run_id)
        event = execution.get_event(event_id)
    except KeyError as error:
        raise RunLifecycleError("lifecycle operation evidence is orphaned") from error
    return RunLifecycleResult(request, run, event, action, replayed=True)


def _replay_recovery_if_present(
    execution: ExecutionStore,
    history: ActivityHistoryStore,
    request: ExecutionRequestRecord,
    key: IdempotencyKey,
    fingerprint: str,
) -> RecoveryCommandResult | None:
    action = history.action_for_idempotency(
        request.identity.session_id,
        key.value,
    )
    if action is None:
        return None
    if action.action_type is not OperationActionKind.RECOVERY_REQUESTED:
        raise RunLifecycleIdempotencyConflict(
            "idempotency key already belongs to another operation command"
        )
    if action.intent_fingerprint != fingerprint:
        raise RunLifecycleIdempotencyConflict(
            "idempotency key was used for different recovery intent"
        )
    run_id = action.payload.get("result_run_id")
    decision_event_id = action.payload.get("decision_event_id")
    consequence_event_id = action.payload.get("consequence_event_id")
    if not isinstance(run_id, str) or not isinstance(decision_event_id, str):
        raise RunLifecycleError("recovery operation evidence is incomplete")
    if consequence_event_id is not None and not isinstance(
        consequence_event_id, str
    ):
        raise RunLifecycleError("recovery consequence evidence is malformed")
    try:
        run = execution.get_run(run_id)
        decision_event = execution.get_event(decision_event_id)
        consequence = (
            None
            if consequence_event_id is None
            else execution.get_event(consequence_event_id)
        )
    except KeyError as error:
        raise RunLifecycleError("recovery operation evidence is orphaned") from error
    return RecoveryCommandResult(
        request,
        run,
        decision_event,
        consequence,
        action,
        replayed=True,
    )


def decide_run_transition(
    command: RunTransitionCommand,
    current: ActivityRunStatus,
) -> RunTransition:
    """Interpret a typed lifecycle command as a valid state transition."""

    match command:
        case StartActivityRun():
            transition = _START
        case PauseActivityRun():
            transition = _PAUSE
        case CompleteActivityRun():
            transition = _COMPLETE
        case FailActivityRun():
            transition = _FAIL
        case CompleteActivityRunCompensation():
            transition = _COMPLETE_COMPENSATION
        case FailActivityRunCompensation():
            transition = _FAIL_COMPENSATION
        case CancelActivityRun():
            transition = _CANCEL
        case _:
            raise InvalidOperationCommand(
                f"unsupported transition command {type(command).__name__}"
            )
    if current not in transition.expected:
        expected = ", ".join(sorted(status.value for status in transition.expected))
        raise RunLifecycleConflict(
            f"cannot apply {type(command).__name__} to {current.value}; "
            f"expected one of: {expected}"
        )
    return transition


def _authorize(authority: ExecutionWorkerAuthority) -> None:
    if "execution:operate" not in authority.scopes:
        raise RunLifecycleDenied("scope 'execution:operate' is missing")


def _command_fields(command: object, identity_name: str) -> None:
    _required(identity_name, getattr(command, identity_name))
    if not isinstance(command.authority, ExecutionWorkerAuthority):
        raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
    if not isinstance(command.idempotency_key, IdempotencyKey):
        raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


def _command_tag(command: RunLifecycleCommand) -> str:
    match command:
        case ClaimAndOpenActivityRun():
            return "claim_and_open_run"
        case StartActivityRun():
            return "start_run"
        case PauseActivityRun():
            return "pause_run"
        case CompleteActivityRun():
            return "complete_run"
        case FailActivityRun():
            return "fail_run"
        case CompleteActivityRunCompensation():
            return "complete_run_compensation"
        case FailActivityRunCompensation():
            return "fail_run_compensation"
        case CancelActivityRun():
            return "cancel_run"
        case DecideActivityRunRecovery():
            return "decide_run_recovery"
    raise InvalidOperationCommand(
        f"unsupported lifecycle command {type(command).__name__}"
    )


def _fingerprint(command: RunLifecycleCommand) -> str:
    value: dict[str, object] = {"command": _command_tag(command)}
    match command:
        case DecideActivityRunRecovery():
            value.update(
                run_id=command.run_id,
                expected_worker_id=command.expected_worker_id,
                expected_event_ordinal=command.expected_event_ordinal,
                recovery=command.recovery.descriptor(),
            )
        case ClaimAndOpenActivityRun():
            value.update(
                worker_id=command.authority.worker_id,
                request_id=command.request_id,
                lease_expires_at=command.lease_expires_at,
            )
        case FailActivityRun() | FailActivityRunCompensation():
            value.update(
                worker_id=command.authority.worker_id,
                run_id=command.run_id,
                failure={
                    "category": command.failure.category.value,
                    "code": command.failure.code,
                    "message": command.failure.message,
                    "details": command.failure.details.descriptor(),
                },
            )
        case _:
            value.update(
                worker_id=command.authority.worker_id,
                run_id=command.run_id,
            )
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _require_future_lease(claimed_at: str, lease_expires_at: str) -> None:
    claimed = _timestamp("claimed_at", claimed_at)
    expires = _timestamp("lease_expires_at", lease_expires_at)
    if expires <= claimed:
        raise InvalidOperationCommand("lease_expires_at must be after claimed_at")


def _timestamp(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidOperationCommand(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise InvalidOperationCommand(f"{name} must include a timezone")
    return parsed


def _required(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{name} must not be empty")


def _scopes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise InvalidOperationCommand("worker scopes must be an iterable of strings")
    scopes = tuple(sorted(set(values)))
    if not all(isinstance(value, str) and value.strip() for value in scopes):
        raise InvalidOperationCommand("worker scopes must be non-empty strings")
    return scopes
