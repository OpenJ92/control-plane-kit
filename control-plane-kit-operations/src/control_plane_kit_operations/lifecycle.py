"""Run lifecycle command service for admitted execution requests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    LifecycleOperationKind,
)
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ExecutionRequestRecord,
    FailureEvidence,
    OperationActionRecord,
    OperationSessionStatus,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


class RunLifecycleError(RuntimeError):
    """Base error for execution-run lifecycle commands."""


class RunLifecycleConflict(RunLifecycleError):
    """Raised when request, claim, or run state rejects the command."""


class RunLifecycleDenied(RunLifecycleError):
    """Raised when worker authority is insufficient."""


class RunLifecycleIdempotencyConflict(RunLifecycleError):
    """Raised when one idempotency key is reused with different intent."""


class RunLifecycleNotFound(RunLifecycleError):
    """Raised when lifecycle target truth is missing."""


@dataclass(frozen=True)
class ExecutionWorkerAuthority:
    """Worker identity plus least privilege lifecycle scopes."""

    worker_id: str
    scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        _required_text(self.worker_id, "worker_id")
        if not all(isinstance(scope, PolicyScope) for scope in self.scopes):
            raise InvalidOperationCommand("worker scopes must be PolicyScope values")
        object.__setattr__(
            self,
            "scopes",
            tuple(sorted(set(self.scopes), key=lambda scope: scope.value)),
        )


@dataclass(frozen=True)
class ExecutionLeaseDuration:
    """Bounded requested lifetime for one database-timed execution lease."""

    seconds: int

    def __post_init__(self) -> None:
        if type(self.seconds) is not int or not 1 <= self.seconds <= 3600:
            raise InvalidOperationCommand("lease duration is invalid")


@dataclass(frozen=True)
class ClaimAndOpenActivityRun:
    """Claim one queued execution request and open its first activity run."""

    request_id: str
    authority: ExecutionWorkerAuthority
    lease_duration: ExecutionLeaseDuration
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required_text(self.request_id, "request_id")
        _require_authority(self.authority)
        if not isinstance(self.lease_duration, ExecutionLeaseDuration):
            raise InvalidOperationCommand(
                "claim lease_duration must be ExecutionLeaseDuration"
            )
        _require_idempotency_key(self.idempotency_key)

    def descriptor(self) -> dict[str, object]:
        return {
            "command": LifecycleOperationKind.CLAIM_RUN.value,
            "request_id": self.request_id,
            "worker_id": self.authority.worker_id,
            "lease_duration_seconds": self.lease_duration.seconds,
            "idempotency_key": self.idempotency_key.value,
        }


@dataclass(frozen=True)
class StartActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _run_command_post_init(self.run_id, self.authority, self.idempotency_key)


@dataclass(frozen=True)
class PauseActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)

    def __post_init__(self) -> None:
        _run_command_post_init(self.run_id, self.authority, self.idempotency_key)
        if not isinstance(self.evidence, BoundedEvidence):
            raise InvalidOperationCommand("pause evidence must be BoundedEvidence")


@dataclass(frozen=True)
class ResumeActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _run_command_post_init(self.run_id, self.authority, self.idempotency_key)


@dataclass(frozen=True)
class CompleteActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)

    def __post_init__(self) -> None:
        _run_command_post_init(self.run_id, self.authority, self.idempotency_key)
        if not isinstance(self.evidence, BoundedEvidence):
            raise InvalidOperationCommand("completion evidence must be BoundedEvidence")


@dataclass(frozen=True)
class FailActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey
    failure: FailureEvidence

    def __post_init__(self) -> None:
        _run_command_post_init(self.run_id, self.authority, self.idempotency_key)
        if not isinstance(self.failure, FailureEvidence):
            raise InvalidOperationCommand("failure must be FailureEvidence")


@dataclass(frozen=True)
class CancelActivityRun:
    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)

    def __post_init__(self) -> None:
        _run_command_post_init(self.run_id, self.authority, self.idempotency_key)
        if not isinstance(self.evidence, BoundedEvidence):
            raise InvalidOperationCommand("cancel evidence must be BoundedEvidence")


LifecycleCommand = (
    ClaimAndOpenActivityRun
    | StartActivityRun
    | PauseActivityRun
    | ResumeActivityRun
    | CompleteActivityRun
    | FailActivityRun
    | CancelActivityRun
)


@dataclass(frozen=True)
class RunLifecycleResult:
    """Run state, event, and operation-action evidence from one lifecycle command."""

    request: ExecutionRequestRecord
    run: ActivityRunRecord
    event: ActivityEventRecord
    action: OperationActionRecord
    replayed: bool = False

    def descriptor(self) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "execution_request_id": self.request.identity.request_id,
            "run_id": self.run.run_id,
            "run_status": self.run.status.value,
            "event_id": self.event.event_id,
            "event_type": self.event.kind.value,
            "event_ordinal": self.event.ordinal,
            "action_id": self.action.action_id,
            "action_type": self.action.action_type.value,
            "replayed": self.replayed,
        }
        if self.request.claim is not None:
            descriptor["claim_generation"] = self.request.claim.generation
        return descriptor


class RunLifecycleCommandService:
    """Application service owning run lifecycle transaction boundaries."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: LifecycleCommand) -> RunLifecycleResult:
        _require_operate_scope(command.authority)
        if isinstance(command, ClaimAndOpenActivityRun):
            return self._claim(command)
        if isinstance(command, StartActivityRun):
            return self._transition(
                command,
                action_type=LifecycleOperationKind.START_RUN,
                expected=(ActivityRunStatus.CLAIMED,),
                replacement=ActivityRunStatus.RUNNING,
                event_kind=ActivityEventKind.RUN_STARTED,
                started=True,
            )
        if isinstance(command, PauseActivityRun):
            return self._transition(
                command,
                action_type=LifecycleOperationKind.PAUSE_RUN,
                expected=(ActivityRunStatus.RUNNING,),
                replacement=ActivityRunStatus.PAUSED,
                event_kind=ActivityEventKind.RUN_PAUSED,
                evidence=command.evidence,
            )
        if isinstance(command, ResumeActivityRun):
            return self._transition(
                command,
                action_type=LifecycleOperationKind.RESUME_RUN,
                expected=(ActivityRunStatus.PAUSED,),
                replacement=ActivityRunStatus.RUNNING,
                event_kind=ActivityEventKind.RUN_RESUMED,
            )
        if isinstance(command, CompleteActivityRun):
            return self._transition(
                command,
                action_type=LifecycleOperationKind.COMPLETE_RUN,
                expected=(ActivityRunStatus.RUNNING,),
                replacement=ActivityRunStatus.SUCCEEDED,
                event_kind=ActivityEventKind.RUN_SUCCEEDED,
                settled=True,
                evidence=command.evidence,
            )
        if isinstance(command, FailActivityRun):
            return self._transition(
                command,
                action_type=LifecycleOperationKind.FAIL_RUN,
                expected=(ActivityRunStatus.RUNNING, ActivityRunStatus.PAUSED),
                replacement=ActivityRunStatus.FAILED,
                event_kind=ActivityEventKind.RUN_FAILED,
                settled=False,
                failure=command.failure,
            )
        if isinstance(command, CancelActivityRun):
            return self._transition(
                command,
                action_type=LifecycleOperationKind.CANCEL_RUN,
                expected=(ActivityRunStatus.CLAIMED, ActivityRunStatus.PAUSED),
                replacement=ActivityRunStatus.CANCELLED,
                event_kind=ActivityEventKind.RUN_CANCELLED,
                settled=True,
                evidence=command.evidence,
            )
        raise InvalidOperationCommand("unsupported lifecycle command")

    def _claim(self, command: ClaimAndOpenActivityRun) -> RunLifecycleResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            try:
                locator = stores.execution.get_request(command.request_id)
            except KeyError as error:
                raise RunLifecycleNotFound("execution request was not found") from error
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
                missing_locked_request = False
                try:
                    locked_request = stores.execution.get_request_for_update(
                        command.request_id
                    )
                except KeyError:
                    missing_locked_request = True
                if missing_locked_request:
                    raise RunLifecycleNotFound(
                        "execution request was not found"
                    )
                result = _replay(stores, locked_request, existing, fingerprint)
                unit_of_work.commit()
                return result
            session = _get_open_session_for_update(
                history,
                locator.identity.session_id,
            )
            request = _get_request(stores, command.request_id)
            if request.identity.session_id != session.session_id:
                raise RunLifecycleConflict(
                    "execution request session linkage changed"
                )
            if (
                request.status is not ExecutionRequestStatus.QUEUED
                or request.claim is not None
            ):
                raise RunLifecycleConflict("execution request is not claimable")
            if stores.execution.runs_for_request(command.request_id):
                raise RunLifecycleConflict("execution request already has a run")
            run_id_candidate = self._id_factory()
            run_id = _run_id_or_lifecycle_error(run_id_candidate)
            claimed = stores.execution.claim_request(
                command.request_id,
                command.authority.worker_id,
                command.lease_duration.seconds,
            )
            if claimed is None:
                raise RunLifecycleConflict("execution request is not claimable")
            if claimed.status is not ExecutionRequestStatus.CLAIMED:
                raise RunLifecycleConflict("execution request was not claimed")
            if claimed.claim is None:
                raise RunLifecycleConflict("claimed request lacks lease evidence")
            now = claimed.claim.claimed_at
            run = stores.execution.add_run(
                ActivityRunRecord(
                    run_id=run_id,
                    plan_id=claimed.identity.plan_id,
                    admission=AdmittedRun(claimed.identity.request_id),
                    retry=RetryIdentity(1),
                    status=ActivityRunStatus.CLAIMED,
                    created_at=now,
                    metadata=BoundedEvidence.from_mapping({"attempt": 1}),
                )
            )
            event = stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=stores.execution.next_event_ordinal(run.run_id),
                    kind=ActivityEventKind.RUN_OPENED,
                    occurred_at=now,
                    evidence=BoundedEvidence.from_mapping({"attempt": 1}),
                )
            )
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=claimed.identity.session_id,
                    ordinal=history.next_action_ordinal(
                        claimed.identity.session_id
                    ),
                    action_type=LifecycleOperationKind.CLAIM_RUN,
                    actor_id=command.authority.worker_id,
                    payload=_payload(claimed, run, event),
                    created_at=now,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            unit_of_work.commit()
            return RunLifecycleResult(claimed, run, event, action)

    def _transition(
        self,
        command: (
            StartActivityRun
            | PauseActivityRun
            | ResumeActivityRun
            | CompleteActivityRun
            | FailActivityRun
            | CancelActivityRun
        ),
        *,
        action_type: LifecycleOperationKind,
        expected: tuple[ActivityRunStatus, ...],
        replacement: ActivityRunStatus,
        event_kind: ActivityEventKind,
        started: bool = False,
        settled: bool = False,
        evidence: BoundedEvidence | None = None,
        failure: FailureEvidence | None = None,
    ) -> RunLifecycleResult:
        fingerprint = _fingerprint(command)
        now = self._clock()
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            missing_run = False
            try:
                locator_run = stores.execution.get_run(command.run_id)
            except KeyError:
                missing_run = True
            if missing_run:
                raise RunLifecycleNotFound("activity run was not found")
            locator_request = _get_request(
                stores,
                locator_run.admission.request_id,
            )
            history = stores.activity_history
            history.lock_action_idempotency(
                locator_request.identity.session_id,
                command.idempotency_key.value,
            )
            existing = history.action_for_idempotency(
                locator_request.identity.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                result = _replay(stores, locator_request, existing, fingerprint)
                unit_of_work.commit()
                return result
            session = _get_open_session_for_update(
                history,
                locator_request.identity.session_id,
            )
            run = _get_run_for_update(stores, command.run_id)
            request = _get_request(stores, run.admission.request_id)
            if request.identity.session_id != session.session_id:
                raise RunLifecycleConflict("activity run session linkage changed")
            _require_worker_owns(request, command.authority)
            transitioned = None
            for status in expected:
                transitioned = stores.execution.compare_and_set_run_status(
                    run.run_id,
                    expected=status,
                    replacement=replacement,
                    started_at=now if started else None,
                    settled_at=now if settled else None,
                )
                if transitioned is not None:
                    break
            if transitioned is None:
                raise RunLifecycleConflict("activity run state rejected transition")
            event = stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=transitioned.run_id,
                    ordinal=stores.execution.next_event_ordinal(transitioned.run_id),
                    kind=event_kind,
                    occurred_at=now,
                    evidence=evidence or BoundedEvidence(),
                    failure=failure,
                )
            )
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=request.identity.session_id,
                    ordinal=history.next_action_ordinal(
                        request.identity.session_id
                    ),
                    action_type=action_type,
                    actor_id=command.authority.worker_id,
                    payload=_payload(request, transitioned, event),
                    created_at=now,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            unit_of_work.commit()
            return RunLifecycleResult(request, transitioned, event, action)


def _get_run_for_update(stores: Any, run_id: str) -> ActivityRunRecord:
    missing_run = False
    try:
        run = stores.execution.get_run_for_update(run_id)
    except KeyError:
        missing_run = True
    if missing_run:
        raise RunLifecycleNotFound("activity run was not found")
    return run


def _get_request(stores: Any, request_id: str) -> ExecutionRequestRecord:
    try:
        return stores.execution.get_request(request_id)
    except KeyError as error:
        raise RunLifecycleNotFound("execution request was not found") from error


def _get_open_session_for_update(history: Any, session_id: str) -> Any:
    try:
        session = history.get_session_for_update(session_id)
    except KeyError as error:
        raise RunLifecycleNotFound("operation session was not found") from error
    if session.status is not OperationSessionStatus.OPEN:
        raise RunLifecycleConflict("run lifecycle requires an open session")
    return session


def _replay(
    stores: Any,
    request: ExecutionRequestRecord,
    action: OperationActionRecord,
    fingerprint: str,
) -> RunLifecycleResult:
    if action.intent_fingerprint != fingerprint:
        raise RunLifecycleIdempotencyConflict(
            "idempotency key was reused with different lifecycle intent"
        )
    if action.action_type is LifecycleOperationKind.CLAIM_RUN:
        claim_generation = _payload_positive_integer(
            action.payload,
            "claim_generation",
        )
        if (
            request.status is not ExecutionRequestStatus.CLAIMED
            or request.claim is None
            or request.claim.worker_id != action.actor_id
            or request.claim.generation != claim_generation
        ):
            raise RunLifecycleConflict("claim replay no longer has active authority")
    run_id = _payload_run_id(action.payload)
    event_id = _payload_text(action.payload, "event_id")
    missing = False
    try:
        run = stores.execution.get_run(run_id)
        event = stores.execution.get_event(event_id)
    except KeyError:
        missing = True
    if missing:
        raise RunLifecycleError("lifecycle action is missing run/event evidence")
    if (
        run.admission.request_id != request.identity.request_id
        or event.run_id != run.run_id
    ):
        raise RunLifecycleError("lifecycle action run/event evidence is incongruent")
    return RunLifecycleResult(request, run, event, action, replayed=True)


def _payload(
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    event: ActivityEventRecord,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "execution_request_id": request.identity.request_id,
        "plan_id": request.identity.plan_id,
        "run_id": run.run_id,
        "run_status": run.status.value,
        "event_id": event.event_id,
        "event_type": event.kind.value,
        "event_ordinal": event.ordinal,
    }
    if request.claim is not None:
        payload["claim_generation"] = request.claim.generation
    return payload


def _fingerprint(command: LifecycleCommand) -> str:
    if isinstance(command, ClaimAndOpenActivityRun):
        value: Mapping[str, object] = {
            "command": LifecycleOperationKind.CLAIM_RUN.value,
            "request_id": command.request_id,
            "worker_id": command.authority.worker_id,
            "lease_duration_seconds": command.lease_duration.seconds,
        }
    elif isinstance(command, StartActivityRun):
        value = _run_intent(command, LifecycleOperationKind.START_RUN)
    elif isinstance(command, PauseActivityRun):
        value = {
            **_run_intent(command, LifecycleOperationKind.PAUSE_RUN),
            "evidence": command.evidence.descriptor(),
        }
    elif isinstance(command, ResumeActivityRun):
        value = _run_intent(command, LifecycleOperationKind.RESUME_RUN)
    elif isinstance(command, CompleteActivityRun):
        value = {
            **_run_intent(command, LifecycleOperationKind.COMPLETE_RUN),
            "evidence": command.evidence.descriptor(),
        }
    elif isinstance(command, FailActivityRun):
        value = {
            **_run_intent(command, LifecycleOperationKind.FAIL_RUN),
            "failure": {
                "category": command.failure.category.value,
                "code": command.failure.code,
                "message": command.failure.message,
                "details": command.failure.details.descriptor(),
            },
        }
    elif isinstance(command, CancelActivityRun):
        value = {
            **_run_intent(command, LifecycleOperationKind.CANCEL_RUN),
            "evidence": command.evidence.descriptor(),
        }
    else:
        raise InvalidOperationCommand("unsupported lifecycle command")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run_intent(
    command: (
        StartActivityRun
        | PauseActivityRun
        | ResumeActivityRun
        | CompleteActivityRun
        | FailActivityRun
        | CancelActivityRun
    ),
    kind: LifecycleOperationKind,
) -> dict[str, object]:
    return {
        "command": kind.value,
        "run_id": command.run_id,
        "worker_id": command.authority.worker_id,
    }


def _payload_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RunLifecycleError(f"lifecycle action payload lacks {key}")
    return value


def _payload_positive_integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 1:
        raise RunLifecycleError(f"lifecycle action payload lacks valid {key}")
    return value


def _payload_run_id(payload: Mapping[str, object]) -> str:
    value = payload.get("run_id")
    try:
        run_id = RunId(value)  # type: ignore[arg-type]
    except ValueError:
        run_id = None
    if run_id is None:
        raise RunLifecycleError("lifecycle action run identity is malformed")
    return run_id.value


def _run_id_or_lifecycle_error(value: object) -> str:
    try:
        run_id = RunId(value)  # type: ignore[arg-type]
    except ValueError:
        run_id = None
    if run_id is None:
        raise RunLifecycleError("generated run identity is malformed")
    return run_id.value


def _require_worker_owns(
    request: ExecutionRequestRecord,
    authority: ExecutionWorkerAuthority,
) -> None:
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or request.claim is None
        or request.claim.worker_id != authority.worker_id
    ):
        raise RunLifecycleDenied("worker does not own the execution request claim")


def _require_operate_scope(authority: ExecutionWorkerAuthority) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise RunLifecycleDenied("scope execution:operate is missing")


def _run_command_post_init(
    run_id: str,
    authority: ExecutionWorkerAuthority,
    idempotency_key: IdempotencyKey,
) -> None:
    try:
        RunId(run_id)
    except ValueError:
        valid_run_id = False
    else:
        valid_run_id = True
    if not valid_run_id:
        raise InvalidOperationCommand("run_id is malformed")
    _require_authority(authority)
    _require_idempotency_key(idempotency_key)


def _require_authority(authority: ExecutionWorkerAuthority) -> None:
    if not isinstance(authority, ExecutionWorkerAuthority):
        raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")


def _require_idempotency_key(value: IdempotencyKey) -> None:
    if not isinstance(value, IdempotencyKey):
        raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")
