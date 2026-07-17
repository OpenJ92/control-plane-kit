"""Durable coordination between pure schedules and bounded effect interpreters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Callable
from uuid import uuid4

from control_plane_kit.effects import (
    EffectFailed,
    EffectInterpreter,
    EffectRequest,
    EffectSucceeded,
    EffectUnsupported,
    PreparedEffect,
    TimeoutPolicy,
    dispatch_prepared_effect,
    effect_request_for_activity,
    prepare_effect,
)
from control_plane_kit.execution import (
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    BoundedEvidence,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureCategory,
    FailureEvidence,
    ObservationRecord,
)
from control_plane_kit.planning import ActivityPlan, PlannedActivity
from control_plane_kit.scheduling import ExecutionSchedule, derive_schedule
from control_plane_kit.stores import ActivityPlanRecord, PostgresUnitOfWork
from control_plane_kit.workflows.commands import IdempotencyKey, InvalidOperationCommand
from control_plane_kit.workflows.run_lifecycle import (
    CompleteActivityRun,
    ExecutionWorkerAuthority,
    FailActivityRun,
    RunLifecycleCommandService,
)
from control_plane_kit.workflows.saga_journal import (
    SagaJournalProjection,
    project_activity_journal,
)


class CoordinatorStatus(StrEnum):
    """Closed outcomes of one bounded coordinator invocation."""

    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    PAUSED = "paused"
    IN_FLIGHT = "in_flight"
    UNCERTAIN = "uncertain"
    PROGRESSED = "progressed"
    UNSUPPORTED = "unsupported"


class CoordinatorCheckpoint(StrEnum):
    """Deterministic crash windows used to prove recovery semantics."""

    AFTER_INTENT_COMMIT = "after_intent_commit"
    AFTER_EFFECT = "after_effect"
    BEFORE_RESULT_COMMIT = "before_result_commit"
    AFTER_RESULT_COMMIT = "after_result_commit"


@dataclass(frozen=True)
class ExecuteActivityRun:
    """Request bounded progress on one already admitted and running run."""

    run_id: str
    authority: ExecutionWorkerAuthority
    timeout: TimeoutPolicy = TimeoutPolicy()
    max_effects: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise InvalidOperationCommand("run_id must be non-empty text")
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
        if not isinstance(self.timeout, TimeoutPolicy):
            raise InvalidOperationCommand("timeout must be TimeoutPolicy")
        if type(self.max_effects) is not int or self.max_effects < 1:
            raise InvalidOperationCommand("max_effects must be a positive integer")


@dataclass(frozen=True)
class ExecutionCoordinatorResult:
    """Bounded progress result derived from durable state after coordination."""

    status: CoordinatorStatus
    run: ActivityRunRecord
    effects_attempted: int = 0
    activity_id: str | None = None


class ExecutionCoordinatorError(RuntimeError):
    """Base failure for durable coordinator commands."""


class ExecutionCoordinatorNotFound(ExecutionCoordinatorError):
    pass


class ExecutionCoordinatorConflict(ExecutionCoordinatorError):
    pass


class ExecutionCoordinatorDenied(ExecutionCoordinatorError):
    pass


class InjectedCoordinatorCrash(ExecutionCoordinatorError):
    """Intentional process-loss simulation at one durable crash window."""


@dataclass(frozen=True)
class _Context:
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    plan_record: ActivityPlanRecord
    events: tuple[ActivityEventRecord, ...]
    journal: SagaJournalProjection
    schedule: ExecutionSchedule


Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid4().hex


class ExecutionCoordinator:
    """Advance a run without holding a transaction across an effect."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        lifecycle: RunLifecycleCommandService,
        interpreter: EffectInterpreter,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _uuid,
        injected_crash: CoordinatorCheckpoint | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lifecycle = lifecycle
        self._interpreter = interpreter
        self._clock = clock
        self._id_factory = id_factory
        self._injected_crash = injected_crash

    def execute(self, command: ExecuteActivityRun) -> ExecutionCoordinatorResult:
        _authorize(command.authority)
        attempted = 0
        while attempted < command.max_effects:
            context = self._load_context(command)
            terminal = self._terminal_result(context, command, attempted)
            if terminal is not None:
                return terminal
            if context.journal.uncertain:
                return ExecutionCoordinatorResult(
                    CoordinatorStatus.UNCERTAIN,
                    context.run,
                    attempted,
                    context.journal.uncertain[0].activity_id,
                )
            if context.journal.in_flight:
                return self._resolve_in_flight(context, command, attempted)
            if not context.schedule.ready:
                return ExecutionCoordinatorResult(
                    CoordinatorStatus.BLOCKED,
                    context.run,
                    attempted,
                )

            activity = context.schedule.ready[0]
            request = effect_request_for_activity(
                activity,
                run_id=context.run.run_id,
                attempt=context.run.retry.attempt,
                idempotency_key=_effect_key(context.run, activity),
                timeout=command.timeout,
            )
            prepared = prepare_effect(request, self._interpreter)
            if isinstance(prepared, EffectUnsupported):
                self._record_unsupported(command, activity, prepared)
                failed = self._fail_run(
                    command,
                    FailureEvidence(
                        FailureCategory.TERMINAL,
                        "effect.unsupported-capability",
                        "The selected interpreter does not support this effect.",
                        BoundedEvidence.from_mapping(
                            {"capability": prepared.capability.value}
                        ),
                    ),
                )
                return ExecutionCoordinatorResult(
                    CoordinatorStatus.UNSUPPORTED,
                    failed,
                    attempted,
                    activity.activity_id.value,
                )

            intent = self._record_intent(command, activity, request)
            self._raise_if(CoordinatorCheckpoint.AFTER_INTENT_COMMIT)
            result = dispatch_prepared_effect(prepared)
            attempted += 1
            self._raise_if(CoordinatorCheckpoint.AFTER_EFFECT)
            self._record_result(command, intent, result)
            self._raise_if(CoordinatorCheckpoint.AFTER_RESULT_COMMIT)
            if isinstance(result, EffectFailed):
                failed = self._fail_run(command, result.failure)
                return ExecutionCoordinatorResult(
                    CoordinatorStatus.FAILED,
                    failed,
                    attempted,
                    activity.activity_id.value,
                )

        context = self._load_context(command)
        return ExecutionCoordinatorResult(
            CoordinatorStatus.PROGRESSED,
            context.run,
            attempted,
        )

    def _load_context(self, command: ExecuteActivityRun) -> _Context:
        with self._unit_of_work_factory() as work:
            execution = work.stores.execution
            try:
                run = execution.get_run(command.run_id)
                request = execution.get_request(_request_id(run))
                plan_record = work.stores.activity_history.get_plan(run.plan_id)
            except KeyError as error:
                raise ExecutionCoordinatorNotFound(str(error)) from error
            _require_owner(request, command.authority.worker_id)
            _require_context_identity(run, request, plan_record)
            events = execution.events_for_run(run.run_id)
        journal = project_activity_journal(plan_record.plan, events)
        return _Context(
            request,
            run,
            plan_record,
            events,
            journal,
            derive_schedule(plan_record.plan, journal.state),
        )

    def _terminal_result(
        self,
        context: _Context,
        command: ExecuteActivityRun,
        attempted: int,
    ) -> ExecutionCoordinatorResult | None:
        match context.run.status:
            case ActivityRunStatus.PAUSED:
                return ExecutionCoordinatorResult(
                    CoordinatorStatus.PAUSED, context.run, attempted
                )
            case ActivityRunStatus.SUCCEEDED:
                return ExecutionCoordinatorResult(
                    CoordinatorStatus.COMPLETED, context.run, attempted
                )
            case ActivityRunStatus.FAILED | ActivityRunStatus.PARTIALLY_FAILED:
                return ExecutionCoordinatorResult(
                    CoordinatorStatus.FAILED, context.run, attempted
                )
            case ActivityRunStatus.RUNNING:
                if context.schedule.failed:
                    failure, status = _journal_failure(context.events)
                    failed = self._fail_run(command, failure)
                    return ExecutionCoordinatorResult(
                        status,
                        failed,
                        attempted,
                        context.schedule.failed[0].activity_id.value,
                    )
                if context.schedule.successful:
                    completed = self._lifecycle.execute(
                        CompleteActivityRun(
                            context.run.run_id,
                            command.authority,
                            IdempotencyKey(f"coordinator:{context.run.run_id}:complete"),
                        )
                    )
                    return ExecutionCoordinatorResult(
                        CoordinatorStatus.COMPLETED,
                        completed.run,
                        attempted,
                    )
                return None
            case _:
                raise ExecutionCoordinatorConflict(
                    f"run is {context.run.status.value}, not executable"
                )

    def _resolve_in_flight(
        self,
        context: _Context,
        command: ExecuteActivityRun,
        attempted: int,
    ) -> ExecutionCoordinatorResult:
        intent = context.journal.in_flight[0]
        expires_at = intent.evidence.descriptor().get("lease_expires_at")
        if not isinstance(expires_at, str):
            raise ExecutionCoordinatorConflict("step intent has no attempt lease")
        if self._clock() < _timestamp(expires_at):
            return ExecutionCoordinatorResult(
                CoordinatorStatus.IN_FLIGHT,
                context.run,
                attempted,
                intent.activity_id,
            )
        uncertain = self._record_uncertain(command, intent)
        return ExecutionCoordinatorResult(
            CoordinatorStatus.UNCERTAIN,
            context.run,
            attempted,
            uncertain.activity_id,
        )

    def _record_intent(
        self,
        command: ExecuteActivityRun,
        activity: PlannedActivity,
        request: EffectRequest,
    ) -> ActivityEventRecord:
        occurred_at = self._clock()
        expires_at = occurred_at + timedelta(seconds=request.timeout.total_seconds)
        with self._unit_of_work_factory() as work:
            run, execution_request, plan = _locked_context(work, command.run_id)
            _require_owner(execution_request, command.authority.worker_id)
            _require_running(run)
            journal = project_activity_journal(
                plan.plan,
                work.stores.execution.events_for_run(run.run_id),
            )
            schedule = derive_schedule(plan.plan, journal.state)
            if journal.in_flight or journal.uncertain or activity not in schedule.ready:
                raise ExecutionCoordinatorConflict("activity is no longer ready")
            event = work.stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=work.stores.execution.next_event_ordinal(run.run_id),
                    kind=ActivityEventKind.STEP_STARTED,
                    activity_id=activity.activity_id.value,
                    occurred_at=_iso(occurred_at),
                    evidence=BoundedEvidence.from_mapping(
                        {
                            "attempt_id": request.identity.idempotency_key,
                            "lease_expires_at": _iso(expires_at),
                        }
                    ),
                )
            )
            work.commit()
            return event

    def _record_result(
        self,
        command: ExecuteActivityRun,
        intent: ActivityEventRecord,
        result: EffectSucceeded | EffectFailed,
    ) -> ActivityEventRecord:
        occurred_at = self._clock()
        with self._unit_of_work_factory() as work:
            run, request, _ = _locked_context(work, command.run_id)
            _require_owner(request, command.authority.worker_id)
            _require_running(run)
            _require_open_intent(
                work.stores.execution.events_for_run(run.run_id),
                intent,
            )
            match result:
                case EffectSucceeded():
                    kind = ActivityEventKind.STEP_SUCCEEDED
                    evidence = _result_evidence(intent, result.evidence)
                    failure = None
                case EffectFailed():
                    kind = ActivityEventKind.STEP_FAILED
                    evidence = _result_evidence(intent, BoundedEvidence())
                    failure = result.failure
            event = work.stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=work.stores.execution.next_event_ordinal(run.run_id),
                    kind=kind,
                    activity_id=intent.activity_id,
                    occurred_at=_iso(occurred_at),
                    evidence=evidence,
                    failure=failure,
                )
            )
            if isinstance(result, EffectSucceeded):
                for observation in result.observations:
                    work.stores.observed_state.put(
                        ObservationRecord(
                            observation_id=self._id_factory(),
                            workspace_id=request.identity.workspace_id,
                            subject_id=observation.subject_id,
                            status=observation.status,
                            observed_at=_iso(occurred_at),
                            evidence=observation.evidence,
                        )
                    )
            self._raise_if(CoordinatorCheckpoint.BEFORE_RESULT_COMMIT)
            work.commit()
            return event

    def _record_unsupported(
        self,
        command: ExecuteActivityRun,
        activity: PlannedActivity,
        unsupported: EffectUnsupported,
    ) -> ActivityEventRecord:
        with self._unit_of_work_factory() as work:
            run, request, plan = _locked_context(work, command.run_id)
            _require_owner(request, command.authority.worker_id)
            _require_running(run)
            journal = project_activity_journal(
                plan.plan,
                work.stores.execution.events_for_run(run.run_id),
            )
            schedule = derive_schedule(plan.plan, journal.state)
            if activity not in schedule.ready:
                raise ExecutionCoordinatorConflict("unsupported activity is no longer ready")
            event = work.stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=work.stores.execution.next_event_ordinal(run.run_id),
                    kind=ActivityEventKind.STEP_UNSUPPORTED,
                    activity_id=activity.activity_id.value,
                    occurred_at=_iso(self._clock()),
                    evidence=BoundedEvidence.from_mapping(
                        {"capability": unsupported.capability.value}
                    ),
                )
            )
            work.commit()
            return event

    def _record_uncertain(
        self,
        command: ExecuteActivityRun,
        intent: ActivityEventRecord,
    ) -> ActivityEventRecord:
        with self._unit_of_work_factory() as work:
            run, request, _ = _locked_context(work, command.run_id)
            _require_owner(request, command.authority.worker_id)
            _require_running(run)
            _require_open_intent(
                work.stores.execution.events_for_run(run.run_id),
                intent,
            )
            event = work.stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=work.stores.execution.next_event_ordinal(run.run_id),
                    kind=ActivityEventKind.STEP_UNCERTAIN,
                    activity_id=intent.activity_id,
                    occurred_at=_iso(self._clock()),
                    evidence=BoundedEvidence.from_mapping(
                        {
                            "reason": "effect-result-missing-after-attempt-lease",
                            "attempt_event_id": intent.event_id,
                        }
                    ),
                )
            )
            work.commit()
            return event

    def _fail_run(
        self,
        command: ExecuteActivityRun,
        failure: FailureEvidence,
    ) -> ActivityRunRecord:
        return self._lifecycle.execute(
            FailActivityRun(
                command.run_id,
                command.authority,
                failure,
                IdempotencyKey(f"coordinator:{command.run_id}:fail"),
            )
        ).run

    def _raise_if(self, checkpoint: CoordinatorCheckpoint) -> None:
        if self._injected_crash is checkpoint:
            raise InjectedCoordinatorCrash(checkpoint.value)


def _locked_context(work: PostgresUnitOfWork, run_id: str):
    try:
        initial = work.stores.execution.get_run(run_id)
        request = work.stores.execution.get_request_for_update(_request_id(initial))
        run = work.stores.execution.get_run_for_update(run_id)
        plan = work.stores.activity_history.get_plan(run.plan_id)
    except KeyError as error:
        raise ExecutionCoordinatorNotFound(str(error)) from error
    _require_context_identity(run, request, plan)
    return run, request, plan


def _require_context_identity(
    run: ActivityRunRecord,
    request: ExecutionRequestRecord,
    plan: ActivityPlanRecord,
) -> None:
    if not isinstance(run.admission, AdmittedRun):
        raise ExecutionCoordinatorConflict("legacy run cannot execute effects")
    if run.admission.request_id != request.identity.request_id:
        raise ExecutionCoordinatorConflict("run and request identity disagree")
    if run.plan_id != request.identity.plan_id or plan.plan_id != run.plan_id:
        raise ExecutionCoordinatorConflict("run, request, and plan identity disagree")
    if plan.session_id != request.identity.session_id:
        raise ExecutionCoordinatorConflict("plan and request session disagree")


def _request_id(run: ActivityRunRecord) -> str:
    if not isinstance(run.admission, AdmittedRun):
        raise ExecutionCoordinatorConflict("legacy run cannot execute effects")
    return run.admission.request_id


def _require_owner(request: ExecutionRequestRecord, worker_id: str) -> None:
    if request.status is not ExecutionRequestStatus.CLAIMED or request.claim is None:
        raise ExecutionCoordinatorConflict("execution request is not claimed")
    if request.claim.worker_id != worker_id:
        raise ExecutionCoordinatorDenied("execution request belongs to another worker")


def _authorize(authority: ExecutionWorkerAuthority) -> None:
    if "execution:operate" not in authority.scopes:
        raise ExecutionCoordinatorDenied("scope 'execution:operate' is missing")


def _require_running(run: ActivityRunRecord) -> None:
    if run.status is not ActivityRunStatus.RUNNING:
        raise ExecutionCoordinatorConflict("run is not running")


def _require_open_intent(
    events: tuple[ActivityEventRecord, ...],
    intent: ActivityEventRecord,
) -> None:
    matching = tuple(
        event for event in events if event.activity_id == intent.activity_id
    )
    if not matching or matching[-1].event_id != intent.event_id:
        raise ExecutionCoordinatorConflict("effect intent is no longer open")
    if intent.kind is not ActivityEventKind.STEP_STARTED:
        raise ExecutionCoordinatorConflict("effect intent is not a start event")


def _effect_key(run: ActivityRunRecord, activity: PlannedActivity) -> str:
    return f"{run.run_id}:{activity.activity_id.value}:{run.retry.attempt}"


def _result_evidence(
    intent: ActivityEventRecord,
    provider_evidence: BoundedEvidence,
) -> BoundedEvidence:
    attempt_key = intent.evidence.descriptor().get("attempt_id")
    if not isinstance(attempt_key, str):
        raise ExecutionCoordinatorConflict("step intent has no effect identity")
    return BoundedEvidence.from_mapping(
        {
            "attempt_event_id": intent.event_id,
            "effect_idempotency_key": attempt_key,
            "provider": provider_evidence.descriptor(),
        }
    )


def _journal_failure(
    events: tuple[ActivityEventRecord, ...],
) -> tuple[FailureEvidence, CoordinatorStatus]:
    for event in reversed(events):
        if event.kind is ActivityEventKind.STEP_FAILED:
            if event.failure is None:
                raise ExecutionCoordinatorConflict("failed step has no failure evidence")
            return event.failure, CoordinatorStatus.FAILED
        if event.kind is ActivityEventKind.STEP_UNSUPPORTED:
            capability = event.evidence.descriptor().get("capability")
            return (
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "effect.unsupported-capability",
                    "The selected interpreter does not support this effect.",
                    BoundedEvidence.from_mapping({"capability": capability}),
                ),
                CoordinatorStatus.UNSUPPORTED,
            )
    raise ExecutionCoordinatorConflict("failed schedule has no durable failure event")


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExecutionCoordinatorConflict("attempt lease is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise ExecutionCoordinatorConflict("attempt lease must include timezone")
    return parsed


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ExecutionCoordinatorConflict("coordinator clock must include timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
