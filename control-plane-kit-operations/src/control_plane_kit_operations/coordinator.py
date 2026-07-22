"""Durable execution coordinator service without runtime-specific effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol

from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
)
from control_plane_kit_core.planning import ActivityId, ActivityPlan, PlannedActivity
from control_plane_kit_core.planning.saga import (
    ActivityJournalEvent,
    ActivityJournalEventKind,
    ExecutionSchedule,
    SagaJournalProjection,
    derive_schedule,
    project_activity_journal,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.lifecycle import (
    CompleteActivityRun,
    ExecutionWorkerAuthority,
    FailActivityRun,
    RunLifecycleCommandService,
    RunLifecycleConflict,
)
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    BoundedEvidence,
    ExecutionRequestRecord,
    FailureEvidence,
    GraphVersionRecord,
)
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


class ExecutionCoordinatorError(RuntimeError):
    """Base error for operations-owned coordinator execution."""


class ExecutionCoordinatorNotFound(ExecutionCoordinatorError):
    """Raised when durable coordinator truth is missing."""


class ExecutionCoordinatorConflict(ExecutionCoordinatorError):
    """Raised when durable state rejects coordinator progress."""


class ExecutionCoordinatorDenied(ExecutionCoordinatorError):
    """Raised when worker authority is insufficient."""


class CoordinatorStatus(StrEnum):
    """Closed coordinator result statuses for the operations service boundary."""

    COMPLETED = "completed"
    FAILED = "failed"
    PROGRESSED = "progressed"
    IN_FLIGHT = "in-flight"
    UNCERTAIN = "uncertain"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ExecuteActivityRun:
    """Advance one claimed, running activity run by at most max_effects steps."""

    run_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey
    max_effects: int = 1

    def __post_init__(self) -> None:
        _required_text(self.run_id, "run_id")
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
        if type(self.max_effects) is not int or self.max_effects < 1:
            raise InvalidOperationCommand("max_effects must be a positive integer")


@dataclass(frozen=True)
class ActivityRealizationContext:
    """Pinned durable material handed to an execution adapter after intent."""

    activity: PlannedActivity
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    plan_record: ActivityPlanRecord
    base_graph: GraphVersionRecord
    desired_graph: GraphVersionRecord
    registered_products: tuple[RegisteredProduct, ...]
    authority: ExecutionWorkerAuthority
    intent_event: ActivityEventRecord

    @property
    def plan(self) -> ActivityPlan:
        return self.plan_record.plan

    def __post_init__(self) -> None:
        if not isinstance(self.activity, PlannedActivity):
            raise InvalidOperationCommand("realization activity must be PlannedActivity")
        if not isinstance(self.request, ExecutionRequestRecord):
            raise InvalidOperationCommand("realization request must be ExecutionRequestRecord")
        if not isinstance(self.run, ActivityRunRecord):
            raise InvalidOperationCommand("realization run must be ActivityRunRecord")
        if not isinstance(self.plan_record, ActivityPlanRecord):
            raise InvalidOperationCommand("realization plan must be ActivityPlanRecord")
        if not isinstance(self.base_graph, GraphVersionRecord):
            raise InvalidOperationCommand("realization base graph must be GraphVersionRecord")
        if not isinstance(self.desired_graph, GraphVersionRecord):
            raise InvalidOperationCommand("realization desired graph must be GraphVersionRecord")
        products = tuple(self.registered_products)
        if not all(isinstance(value, RegisteredProduct) for value in products):
            raise InvalidOperationCommand("realization products must be RegisteredProduct values")
        object.__setattr__(self, "registered_products", products)
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("realization authority must be ExecutionWorkerAuthority")
        if not isinstance(self.intent_event, ActivityEventRecord):
            raise InvalidOperationCommand("realization intent must be ActivityEventRecord")
        workspace_id = self.request.identity.workspace_id
        if self.run.admission.request_id != self.request.identity.request_id:
            raise InvalidOperationCommand("realization run must belong to request")
        if self.run.plan_id != self.plan_record.plan_id:
            raise InvalidOperationCommand("realization run must use the pinned plan")
        if self.request.identity.plan_id != self.plan_record.plan_id:
            raise InvalidOperationCommand("realization request must use the pinned plan")
        if self.plan_record.base_graph_id != self.base_graph.graph_id:
            raise InvalidOperationCommand("realization base graph must match plan")
        if self.plan_record.desired_graph_id != self.desired_graph.graph_id:
            raise InvalidOperationCommand("realization desired graph must match plan")
        if self.base_graph.workspace_id != workspace_id:
            raise InvalidOperationCommand("realization base graph must match workspace")
        if self.desired_graph.workspace_id != workspace_id:
            raise InvalidOperationCommand("realization desired graph must match workspace")
        for product in products:
            if product.workspace_id != workspace_id:
                raise InvalidOperationCommand("realization product must match workspace")
        if self.intent_event.run_id != self.run.run_id:
            raise InvalidOperationCommand("realization intent must match run")
        if self.intent_event.kind is not ActivityEventKind.STEP_STARTED:
            raise InvalidOperationCommand("realization intent must be step_started")
        if self.intent_event.activity_id != self.activity.activity_id.value:
            raise InvalidOperationCommand("realization intent must match activity")


@dataclass(frozen=True)
class ActivityExecutionOutcome:
    """Proof-adapter outcome expressed with the core effect result vocabulary."""

    kind: EffectResultKind
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    failure: FailureEvidence | None = None

    @classmethod
    def succeeded(
        cls,
        evidence: BoundedEvidence | None = None,
    ) -> "ActivityExecutionOutcome":
        return cls(EffectResultKind.SUCCEEDED, evidence or BoundedEvidence())

    @classmethod
    def failed(cls, failure: FailureEvidence) -> "ActivityExecutionOutcome":
        return cls(EffectResultKind.FAILED, BoundedEvidence(), failure)

    @classmethod
    def unsupported(cls, failure: FailureEvidence) -> "ActivityExecutionOutcome":
        return cls(EffectResultKind.UNSUPPORTED, BoundedEvidence(), failure)

    @classmethod
    def uncertain(cls, failure: FailureEvidence) -> "ActivityExecutionOutcome":
        return cls(EffectResultKind.UNCERTAIN, BoundedEvidence(), failure)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EffectResultKind):
            raise InvalidOperationCommand("outcome kind must be EffectResultKind")
        if not isinstance(self.evidence, BoundedEvidence):
            raise InvalidOperationCommand("outcome evidence must be BoundedEvidence")
        if self.failure is not None and not isinstance(self.failure, FailureEvidence):
            raise InvalidOperationCommand("outcome failure must be FailureEvidence")
        if self.kind in {
            EffectResultKind.FAILED,
            EffectResultKind.UNSUPPORTED,
            EffectResultKind.UNCERTAIN,
        } and self.failure is None:
            raise InvalidOperationCommand("non-success outcomes require failure evidence")
        if self.kind is EffectResultKind.SUCCEEDED and self.failure is not None:
            raise InvalidOperationCommand("successful outcomes must not carry failure")
        if self.kind not in {
            EffectResultKind.SUCCEEDED,
            EffectResultKind.FAILED,
            EffectResultKind.UNSUPPORTED,
            EffectResultKind.UNCERTAIN,
        }:
            raise InvalidOperationCommand("adapter outcome is not executable")


class ActivityExecutionAdapter(Protocol):
    """Effect-proof adapter called only after durable intent commits."""

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome: ...


@dataclass(frozen=True)
class ExecutionCoordinatorResult:
    """Visible result of one coordinator command."""

    run: ActivityRunRecord
    status: CoordinatorStatus
    effects_attempted: int = 0
    activity_id: str | None = None

    def descriptor(self) -> dict[str, object]:
        return {
            "run_id": self.run.run_id,
            "run_status": self.run.status.value,
            "coordinator_status": self.status.value,
            "effects_attempted": self.effects_attempted,
            "activity_id": self.activity_id,
        }


class ExecutionCoordinator:
    """Operations-owned durable coordinator over core plan and saga languages."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        lifecycle: RunLifecycleCommandService,
        adapter: ActivityExecutionAdapter,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lifecycle = lifecycle
        self._adapter = adapter
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: ExecuteActivityRun) -> ExecutionCoordinatorResult:
        _require_operate_scope(command.authority)
        attempted = 0
        current: ExecutionCoordinatorResult | None = None
        for _ in range(command.max_effects):
            context = self._load_context(command)
            current = self._classify_current(context)
            if current.status is not CoordinatorStatus.PROGRESSED:
                return current
            activity = current.activity_id
            if activity is None:
                raise ExecutionCoordinatorConflict("ready activity identity is missing")
            planned = context.plan.activity(ActivityId(activity))
            intent_event = self._record_step_event(
                command,
                planned,
                ActivityEventKind.STEP_STARTED,
                BoundedEvidence.from_mapping({"phase": "intent"}),
            )
            realization = context.realization_context(planned, intent_event)
            attempted += 1
            try:
                outcome = self._adapter.execute(realization)
            except Exception as error:  # noqa: BLE001 - adapter errors become uncertainty evidence.
                outcome = ActivityExecutionOutcome.uncertain(
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "adapter-result-unknown",
                        "adapter raised before a durable result was recorded",
                        BoundedEvidence.from_mapping(
                            {"exception_type": type(error).__name__}
                        ),
                    )
                )
            self._record_outcome(command, planned, outcome)
            if outcome.kind is EffectResultKind.SUCCEEDED:
                continue
            classified = self._classify_current(self._load_context(command))
            run = classified.run
            if outcome.kind is EffectResultKind.UNSUPPORTED:
                status = CoordinatorStatus.UNSUPPORTED
            elif outcome.kind is EffectResultKind.UNCERTAIN:
                status = CoordinatorStatus.UNCERTAIN
            else:
                status = CoordinatorStatus.FAILED
            return ExecutionCoordinatorResult(
                run,
                status,
                attempted,
                planned.activity_id.value,
            )
        context = self._load_context(command)
        current = self._classify_current(context)
        return ExecutionCoordinatorResult(
            current.run,
            CoordinatorStatus.PROGRESSED
            if current.status is CoordinatorStatus.PROGRESSED
            else current.status,
            attempted,
            current.activity_id,
        )

    def _classify_current(
        self,
        context: "_CoordinatorContext",
    ) -> ExecutionCoordinatorResult:
        run = context.run
        if run.status is ActivityRunStatus.CLAIMED:
            raise ExecutionCoordinatorConflict("activity run must be started")
        if run.status is ActivityRunStatus.PAUSED:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)
        if run.status is ActivityRunStatus.SUCCEEDED:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.COMPLETED)
        if run.status is ActivityRunStatus.FAILED:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.FAILED)
        if run.status in {ActivityRunStatus.CANCELLED, ActivityRunStatus.COMPENSATING}:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)
        if run.status is not ActivityRunStatus.RUNNING:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)

        if context.projection.uncertain:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.UNCERTAIN,
                activity_id=context.projection.uncertain[0].activity_id,
            )
        if context.schedule.failed:
            failure = FailureEvidence(
                FailureCategory.TERMINAL,
                "activity-step-failed",
                "one or more planned activities failed",
                BoundedEvidence.from_mapping(
                    {
                        "activity_ids": [
                            value.activity_id.value for value in context.schedule.failed
                        ]
                    }
                ),
            )
            try:
                result = self._lifecycle.execute(
                    FailActivityRun(
                        run.run_id,
                        context.authority,
                        IdempotencyKey(f"coordinator:{run.run_id}:fail"),
                        failure,
                    )
                )
                run = result.run
            except RunLifecycleConflict:
                run = self._fresh_run(run.run_id)
            return ExecutionCoordinatorResult(run, CoordinatorStatus.FAILED)
        if context.schedule.running:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.IN_FLIGHT,
                activity_id=context.schedule.running[0].activity_id.value,
            )
        if context.schedule.successful:
            result = self._lifecycle.execute(
                CompleteActivityRun(
                    run.run_id,
                    context.authority,
                    IdempotencyKey(f"coordinator:{run.run_id}:complete"),
                    BoundedEvidence.from_mapping({"result": "all-activities-succeeded"}),
                )
            )
            return ExecutionCoordinatorResult(result.run, CoordinatorStatus.COMPLETED)
        if context.schedule.ready:
            return ExecutionCoordinatorResult(
                run,
                CoordinatorStatus.PROGRESSED,
                activity_id=context.schedule.ready[0].activity_id.value,
            )
        if context.schedule.blocked or context.schedule.waiting:
            return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)
        return ExecutionCoordinatorResult(run, CoordinatorStatus.BLOCKED)

    def _record_step_event(
        self,
        command: ExecuteActivityRun,
        activity: PlannedActivity,
        kind: ActivityEventKind,
        evidence: BoundedEvidence | None = None,
        failure: FailureEvidence | None = None,
    ) -> ActivityEventRecord:
        now = self._clock()
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            run = _get_run_for_update(stores, command.run_id)
            request = _get_request(stores, run.admission.request_id)
            _require_worker_owns(request, command.authority)
            if run.status is not ActivityRunStatus.RUNNING:
                raise ExecutionCoordinatorConflict("run is not executable")
            event = stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=run.run_id,
                    ordinal=stores.execution.next_event_ordinal(run.run_id),
                    kind=kind,
                    occurred_at=now,
                    activity_id=activity.activity_id.value,
                    evidence=evidence or BoundedEvidence(),
                    failure=failure,
                )
            )
            unit_of_work.commit()
            return event

    def _record_outcome(
        self,
        command: ExecuteActivityRun,
        activity: PlannedActivity,
        outcome: ActivityExecutionOutcome,
    ) -> None:
        if outcome.kind is EffectResultKind.SUCCEEDED:
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_SUCCEEDED,
                outcome.evidence,
            )
            return
        if outcome.kind is EffectResultKind.FAILED:
            assert outcome.failure is not None
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_FAILED,
                failure=outcome.failure,
            )
            return
        if outcome.kind is EffectResultKind.UNSUPPORTED:
            assert outcome.failure is not None
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_UNSUPPORTED,
                failure=outcome.failure,
            )
            return
        if outcome.kind is EffectResultKind.UNCERTAIN:
            assert outcome.failure is not None
            self._record_step_event(
                command,
                activity,
                ActivityEventKind.STEP_UNCERTAIN,
                failure=outcome.failure,
            )
            return
        raise ExecutionCoordinatorConflict("unsupported adapter outcome")

    def _load_context(self, command: ExecuteActivityRun) -> "_CoordinatorContext":
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            run = _get_run_for_update(stores, command.run_id)
            request = _get_request(stores, run.admission.request_id)
            _require_worker_owns(request, command.authority)
            try:
                plan_record = stores.activity_history.get_plan(run.plan_id)
            except KeyError as error:
                raise ExecutionCoordinatorNotFound("activity plan was not found") from error
            try:
                base_graph = stores.graphs.get(plan_record.base_graph_id)
                desired_graph = stores.graphs.get(plan_record.desired_graph_id)
            except KeyError as error:
                raise ExecutionCoordinatorNotFound("pinned graph was not found") from error
            registered_products = stores.registered_products.list_active(
                request.identity.workspace_id
            )
            events = stores.execution.events_for_run(run.run_id)
        journal = _journal_events(events)
        projection = project_activity_journal(plan_record.plan, journal)
        schedule = derive_schedule(plan_record.plan, projection.state)
        return _CoordinatorContext(
            request=request,
            run=run,
            plan_record=plan_record,
            base_graph=base_graph,
            desired_graph=desired_graph,
            registered_products=registered_products,
            events=events,
            projection=projection,
            schedule=schedule,
            authority=command.authority,
        )

    def _fresh_run(self, run_id: str) -> ActivityRunRecord:
        with self._unit_of_work_factory() as unit_of_work:
            return _get_run(unit_of_work.stores, run_id)


@dataclass(frozen=True)
class _CoordinatorContext:
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    plan_record: ActivityPlanRecord
    base_graph: GraphVersionRecord
    desired_graph: GraphVersionRecord
    registered_products: tuple[RegisteredProduct, ...]
    events: tuple[ActivityEventRecord, ...]
    projection: SagaJournalProjection
    schedule: ExecutionSchedule
    authority: ExecutionWorkerAuthority

    def __post_init__(self) -> None:
        workspace_id = self.request.identity.workspace_id
        if self.run.admission.request_id != self.request.identity.request_id:
            raise ExecutionCoordinatorConflict("run must belong to execution request")
        if self.run.plan_id != self.plan_record.plan_id:
            raise ExecutionCoordinatorConflict("run must use pinned activity plan")
        if self.request.identity.plan_id != self.plan_record.plan_id:
            raise ExecutionCoordinatorConflict("request must use pinned activity plan")
        if self.plan_record.base_graph_id != self.base_graph.graph_id:
            raise ExecutionCoordinatorConflict("base graph must match activity plan")
        if self.plan_record.desired_graph_id != self.desired_graph.graph_id:
            raise ExecutionCoordinatorConflict("desired graph must match activity plan")
        if self.base_graph.workspace_id != workspace_id:
            raise ExecutionCoordinatorConflict("base graph must match execution workspace")
        if self.desired_graph.workspace_id != workspace_id:
            raise ExecutionCoordinatorConflict("desired graph must match execution workspace")
        for product in self.registered_products:
            if product.workspace_id != workspace_id:
                raise ExecutionCoordinatorConflict("registered product must match workspace")

    @property
    def plan(self) -> ActivityPlan:
        return self.plan_record.plan

    def realization_context(
        self,
        activity: PlannedActivity,
        intent_event: ActivityEventRecord,
    ) -> ActivityRealizationContext:
        return ActivityRealizationContext(
            activity=activity,
            request=self.request,
            run=self.run,
            plan_record=self.plan_record,
            base_graph=self.base_graph,
            desired_graph=self.desired_graph,
            registered_products=self.registered_products,
            authority=self.authority,
            intent_event=intent_event,
        )


_EVENT_KIND_TO_JOURNAL_KIND = {
    ActivityEventKind.STEP_STARTED: ActivityJournalEventKind.STEP_STARTED,
    ActivityEventKind.STEP_SUCCEEDED: ActivityJournalEventKind.STEP_SUCCEEDED,
    ActivityEventKind.STEP_FAILED: ActivityJournalEventKind.STEP_FAILED,
    ActivityEventKind.STEP_UNSUPPORTED: ActivityJournalEventKind.STEP_UNSUPPORTED,
    ActivityEventKind.STEP_UNCERTAIN: ActivityJournalEventKind.STEP_UNCERTAIN,
    ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED: (
        ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED: (
        ActivityJournalEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED
    ),
    ActivityEventKind.RUN_COMPENSATION_STARTED: (
        ActivityJournalEventKind.RUN_COMPENSATION_STARTED
    ),
    ActivityEventKind.STEP_COMPENSATION_STARTED: (
        ActivityJournalEventKind.STEP_COMPENSATION_STARTED
    ),
    ActivityEventKind.STEP_COMPENSATION_SUCCEEDED: (
        ActivityJournalEventKind.STEP_COMPENSATION_SUCCEEDED
    ),
    ActivityEventKind.STEP_COMPENSATION_FAILED: (
        ActivityJournalEventKind.STEP_COMPENSATION_FAILED
    ),
    ActivityEventKind.STEP_COMPENSATION_UNSUPPORTED: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNSUPPORTED
    ),
    ActivityEventKind.STEP_COMPENSATION_UNCERTAIN: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNCERTAIN
    ),
    ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED
    ),
    ActivityEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED: (
        ActivityJournalEventKind.STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED
    ),
}


def _journal_events(
    events: tuple[ActivityEventRecord, ...],
) -> tuple[ActivityJournalEvent, ...]:
    journal: list[ActivityJournalEvent] = []
    for event in events:
        kind = _EVENT_KIND_TO_JOURNAL_KIND.get(event.kind)
        if kind is None:
            continue
        journal.append(
            ActivityJournalEvent(
                event_id=event.event_id,
                run_id=event.run_id,
                ordinal=event.ordinal,
                kind=kind,
                activity_id=event.activity_id,
            )
        )
    return tuple(journal)


def _get_run(stores: Any, run_id: str) -> ActivityRunRecord:
    try:
        return stores.execution.get_run(run_id)
    except KeyError as error:
        raise ExecutionCoordinatorNotFound("activity run was not found") from error


def _get_run_for_update(stores: Any, run_id: str) -> ActivityRunRecord:
    try:
        return stores.execution.get_run_for_update(run_id)
    except KeyError as error:
        raise ExecutionCoordinatorNotFound("activity run was not found") from error


def _get_request(stores: Any, request_id: str) -> ExecutionRequestRecord:
    try:
        return stores.execution.get_request(request_id)
    except KeyError as error:
        raise ExecutionCoordinatorNotFound("execution request was not found") from error


def _require_worker_owns(
    request: ExecutionRequestRecord,
    authority: ExecutionWorkerAuthority,
) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise ExecutionCoordinatorDenied("scope execution:operate is missing")
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or request.claim is None
        or request.claim.worker_id != authority.worker_id
    ):
        raise ExecutionCoordinatorDenied("worker does not own the execution request claim")


def _require_operate_scope(authority: ExecutionWorkerAuthority) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise ExecutionCoordinatorDenied("scope execution:operate is missing")


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")
