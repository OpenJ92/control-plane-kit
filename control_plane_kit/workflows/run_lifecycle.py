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
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    BoundedEvidence,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureEvidence,
    RetryIdentity,
)
from control_plane_kit.stores import (
    ActivityHistoryStore,
    ExecutionStore,
    OperationActionKind,
    OperationActionRecord,
    PostgresUnitOfWork,
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
class RetryActivityRun:
    prior_run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "prior_run_id")


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
class ResumeActivityRun:
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
class BeginActivityRunCompensation:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _command_fields(self, "run_id")


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


RunLifecycleCommand: TypeAlias = (
    ClaimAndOpenActivityRun
    | RetryActivityRun
    | StartActivityRun
    | PauseActivityRun
    | ResumeActivityRun
    | CompleteActivityRun
    | FailActivityRun
    | BeginActivityRunCompensation
    | CompleteActivityRunCompensation
    | FailActivityRunCompensation
    | CancelActivityRun
)
RunTransitionCommand: TypeAlias = (
    StartActivityRun
    | PauseActivityRun
    | ResumeActivityRun
    | CompleteActivityRun
    | FailActivityRun
    | BeginActivityRunCompensation
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
_RESUME = RunTransition(
    frozenset({ActivityRunStatus.PAUSED}),
    ActivityRunStatus.RUNNING,
    ActivityEventKind.RUN_RESUMED,
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
_BEGIN_COMPENSATION = RunTransition(
    frozenset({ActivityRunStatus.FAILED}),
    ActivityRunStatus.COMPENSATING,
    ActivityEventKind.COMPENSATION_STARTED,
)
_COMPLETE_COMPENSATION = RunTransition(
    frozenset({ActivityRunStatus.COMPENSATING}),
    ActivityRunStatus.COMPENSATED,
    ActivityEventKind.COMPENSATION_SUCCEEDED,
    settles_run=True,
)
_FAIL_COMPENSATION = RunTransition(
    frozenset({ActivityRunStatus.COMPENSATING}),
    ActivityRunStatus.PARTIALLY_FAILED,
    ActivityEventKind.COMPENSATION_FAILED,
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

    def execute(self, command: RunLifecycleCommand) -> RunLifecycleResult:
        _authorize(command.authority)
        match command:
            case ClaimAndOpenActivityRun():
                return self._claim_and_open(command)
            case RetryActivityRun():
                return self._retry(command)
            case (
                StartActivityRun()
                | PauseActivityRun()
                | ResumeActivityRun()
                | CompleteActivityRun()
                | FailActivityRun()
                | BeginActivityRunCompensation()
                | CompleteActivityRunCompensation()
                | FailActivityRunCompensation()
                | CancelActivityRun()
            ):
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

    def _retry(self, command: RetryActivityRun) -> RunLifecycleResult:
        occurred_at = self._clock()
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            execution = work.stores.execution
            history = work.stores.activity_history
            prior = _run(execution, command.prior_run_id)
            request_id = _admitted_request_id(prior)
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
            prior = _run_for_update(execution, command.prior_run_id)
            if prior.status not in {
                ActivityRunStatus.FAILED,
                ActivityRunStatus.PARTIALLY_FAILED,
            }:
                raise RunLifecycleConflict(
                    "only failed or partially failed runs may be retried"
                )
            attempts = execution.runs_for_request(request_id)
            latest = max(attempts, key=lambda value: value.retry.attempt)
            if latest.run_id != prior.run_id:
                raise RunLifecycleConflict("retry must extend the latest run attempt")
            attempt = prior.retry.attempt + 1
            run = execution.add_run(
                ActivityRunRecord(
                    run_id=self._id_factory(),
                    plan_id=prior.plan_id,
                    admission=AdmittedRun(request_id),
                    retry=RetryIdentity(attempt, prior.run_id),
                    status=ActivityRunStatus.CLAIMED,
                    created_at=occurred_at,
                )
            )
            result = self._record(
                execution,
                history,
                request,
                run,
                command,
                fingerprint,
                ActivityEventKind.RUN_OPENED,
                occurred_at,
                evidence=BoundedEvidence.from_mapping(
                    {"attempt": attempt, "prior_run_id": prior.run_id}
                ),
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
    if not isinstance(run.admission, AdmittedRun):
        raise RunLifecycleConflict("legacy imported runs cannot be transitioned")
    return run.admission.request_id


def _require_owner(request: ExecutionRequestRecord, worker_id: str) -> None:
    if request.status is not ExecutionRequestStatus.CLAIMED or request.claim is None:
        raise RunLifecycleConflict("execution request is not claimed")
    if request.claim.worker_id != worker_id:
        raise RunLifecycleDenied("execution request belongs to another worker")


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
        case ResumeActivityRun():
            transition = _RESUME
        case CompleteActivityRun():
            transition = _COMPLETE
        case FailActivityRun():
            transition = _FAIL
        case BeginActivityRunCompensation():
            transition = _BEGIN_COMPENSATION
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
        case RetryActivityRun():
            return "retry_run"
        case StartActivityRun():
            return "start_run"
        case PauseActivityRun():
            return "pause_run"
        case ResumeActivityRun():
            return "resume_run"
        case CompleteActivityRun():
            return "complete_run"
        case FailActivityRun():
            return "fail_run"
        case BeginActivityRunCompensation():
            return "begin_run_compensation"
        case CompleteActivityRunCompensation():
            return "complete_run_compensation"
        case FailActivityRunCompensation():
            return "fail_run_compensation"
        case CancelActivityRun():
            return "cancel_run"
    raise InvalidOperationCommand(
        f"unsupported lifecycle command {type(command).__name__}"
    )


def _fingerprint(command: RunLifecycleCommand) -> str:
    value: dict[str, object] = {
        "command": _command_tag(command),
        "worker_id": command.authority.worker_id,
    }
    match command:
        case ClaimAndOpenActivityRun():
            value.update(
                request_id=command.request_id,
                lease_expires_at=command.lease_expires_at,
            )
        case RetryActivityRun():
            value["prior_run_id"] = command.prior_run_id
        case FailActivityRun() | FailActivityRunCompensation():
            value.update(
                run_id=command.run_id,
                failure={
                    "category": command.failure.category.value,
                    "code": command.failure.code,
                    "message": command.failure.message,
                    "details": command.failure.details.descriptor(),
                },
            )
        case _:
            value["run_id"] = command.run_id
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
