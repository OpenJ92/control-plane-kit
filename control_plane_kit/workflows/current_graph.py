"""Evidence-backed advancement of one workspace current-graph projection.

The workspace pointer is a cached projection.  A successful admitted run and
its append-only activity history are the authority for advancing it.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
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
)
from control_plane_kit.core.planning import ActivityPlan
from control_plane_kit.stores import (
    ActivityPlanRecord,
    GraphTopologyStore,
    OperationActionKind,
    OperationActionRecord,
    PostgresStoreBundle,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows.commands import IdempotencyKey, InvalidOperationCommand
from control_plane_kit.workflows.run_lifecycle import ExecutionWorkerAuthority
from control_plane_kit.saga import SagaStateError
from control_plane_kit.scheduling import ScheduleEvidenceError, derive_schedule
from control_plane_kit.projections.saga_journal import (
    SagaJournalError,
    project_activity_journal,
)


@dataclass(frozen=True)
class AdvanceCurrentGraph:
    """Request one guarded projection advance from complete run evidence."""

    workspace_id: str
    run_id: str
    plan_id: str
    expected_current_graph_id: str
    desired_graph_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "run_id",
            "plan_id",
            "expected_current_graph_id",
            "desired_graph_id",
        ):
            _required(name, getattr(self, name))
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
        if self.expected_current_graph_id == self.desired_graph_id:
            raise InvalidOperationCommand(
                "current graph advancement requires distinct graph identities"
            )


@dataclass(frozen=True)
class CurrentGraphAdvancementResult:
    """Stable evidence returned for an original command or its replay."""

    workspace_id: str
    from_graph_id: str
    to_graph_id: str
    run_id: str
    plan_id: str
    event: ActivityEventRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.event.kind is not ActivityEventKind.CURRENT_GRAPH_ADVANCED:
            raise InvalidOperationCommand(
                "current graph advancement requires canonical activity evidence"
            )
        if self.action.action_type is not OperationActionKind.CURRENT_GRAPH_ADVANCED:
            raise InvalidOperationCommand(
                "current graph advancement requires canonical operation evidence"
            )
        if self.action.payload.get("event_id") != self.event.event_id:
            raise InvalidOperationCommand("operation and activity evidence must agree")
        expected = {
            "workspace_id": self.workspace_id,
            "from_graph_id": self.from_graph_id,
            "to_graph_id": self.to_graph_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
        }
        if self.event.run_id != self.run_id:
            raise InvalidOperationCommand("advancement event belongs to another run")
        if self.event.evidence.descriptor() != expected:
            raise InvalidOperationCommand(
                "advancement event does not encode the claimed graph transition"
            )


class CurrentGraphAdvancementError(RuntimeError):
    """Base error for guarded current-graph advancement."""


class CurrentGraphAdvancementNotFound(CurrentGraphAdvancementError):
    pass


class CurrentGraphAdvancementConflict(CurrentGraphAdvancementError):
    pass


class CurrentGraphAdvancementDenied(CurrentGraphAdvancementError):
    pass


class CurrentGraphAdvancementIncomplete(CurrentGraphAdvancementError):
    pass


class CurrentGraphAdvancementIdempotencyConflict(CurrentGraphAdvancementError):
    pass


Clock = Callable[[], str]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


def _uuid() -> str:
    return uuid4().hex


class CurrentGraphAdvancementCommandService:
    """Advance one pointer and append its evidence in one transaction."""

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
        self,
        command: AdvanceCurrentGraph,
    ) -> CurrentGraphAdvancementResult:
        _authorize(command.authority)
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            stores = work.stores
            try:
                workspace = stores.workspace.get_for_update(command.workspace_id)
                initial_run = stores.execution.get_run(command.run_id)
            except KeyError as error:
                raise CurrentGraphAdvancementNotFound(str(error)) from error
            if not isinstance(initial_run.admission, AdmittedRun):
                raise CurrentGraphAdvancementConflict(
                    "legacy imported runs cannot establish current graph truth"
                )
            try:
                request = stores.execution.get_request_for_update(
                    initial_run.admission.request_id
                )
                run = stores.execution.get_run_for_update(command.run_id)
                plan = stores.activity_history.get_plan(command.plan_id)
            except KeyError as error:
                raise CurrentGraphAdvancementNotFound(str(error)) from error

            replay = _replay_if_present(
                stores,
                request.identity.session_id,
                command.idempotency_key,
                fingerprint,
            )
            if replay is not None:
                return replay

            _require_owner(request, command.authority.worker_id)
            _require_identity(command, workspace, request, run, plan)
            _require_graph_ownership(
                stores.graph_topology,
                command.workspace_id,
                command.expected_current_graph_id,
                command.desired_graph_id,
            )
            events = stores.execution.events_for_run(command.run_id)
            _require_complete_success(plan.plan, run, events)

            advanced = stores.workspace.compare_and_set_current_graph(
                command.workspace_id,
                expected_graph_id=command.expected_current_graph_id,
                replacement_graph_id=command.desired_graph_id,
            )
            if advanced is None:
                raise CurrentGraphAdvancementConflict(
                    "workspace current graph changed concurrently"
                )

            occurred_at = self._clock()
            evidence = BoundedEvidence.from_mapping(
                {
                    "workspace_id": command.workspace_id,
                    "plan_id": command.plan_id,
                    "run_id": command.run_id,
                    "from_graph_id": command.expected_current_graph_id,
                    "to_graph_id": command.desired_graph_id,
                }
            )
            event = stores.execution.add_event(
                ActivityEventRecord(
                    event_id=self._id_factory(),
                    run_id=command.run_id,
                    ordinal=stores.execution.next_event_ordinal(command.run_id),
                    kind=ActivityEventKind.CURRENT_GRAPH_ADVANCED,
                    occurred_at=occurred_at,
                    evidence=evidence,
                )
            )
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=request.identity.session_id,
                    ordinal=stores.activity_history.next_action_ordinal(
                        request.identity.session_id
                    ),
                    action_type=OperationActionKind.CURRENT_GRAPH_ADVANCED,
                    actor_id=command.authority.worker_id,
                    payload={
                        **evidence.descriptor(),
                        "run_id": command.run_id,
                        "event_id": event.event_id,
                    },
                    created_at=occurred_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            work.commit()
            return _result(event, action)


def _require_identity(
    command: AdvanceCurrentGraph,
    workspace: WorkspaceRecord,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    plan: ActivityPlanRecord,
) -> None:
    if request.identity.workspace_id != command.workspace_id:
        raise CurrentGraphAdvancementConflict("execution request belongs elsewhere")
    if request.identity.plan_id != command.plan_id or run.plan_id != command.plan_id:
        raise CurrentGraphAdvancementConflict("run is not pinned to the supplied plan")
    if plan.session_id != request.identity.session_id:
        raise CurrentGraphAdvancementConflict("plan and request session do not agree")
    if plan.base_graph_id != command.expected_current_graph_id:
        raise CurrentGraphAdvancementConflict("plan base graph does not match command")
    if plan.desired_graph_id != command.desired_graph_id:
        raise CurrentGraphAdvancementConflict("plan desired graph does not match command")
    if workspace.current_graph_id != command.expected_current_graph_id:
        raise CurrentGraphAdvancementConflict("workspace current graph is stale")
    if workspace.desired_graph_id != command.desired_graph_id:
        raise CurrentGraphAdvancementConflict("workspace desired graph changed")


def _require_complete_success(
    plan: ActivityPlan,
    run: ActivityRunRecord,
    events: tuple[ActivityEventRecord, ...],
) -> None:
    if run.status is not ActivityRunStatus.SUCCEEDED or run.settled_at is None:
        raise CurrentGraphAdvancementIncomplete("run is not durably settled as succeeded")
    if not events or events[-1].kind is not ActivityEventKind.RUN_SUCCEEDED:
        raise CurrentGraphAdvancementIncomplete(
            "run success must be the latest durable execution event"
        )
    if sum(event.kind is ActivityEventKind.RUN_SUCCEEDED for event in events) != 1:
        raise CurrentGraphAdvancementIncomplete(
            "run success requires exactly one terminal success event"
        )
    disqualifying = {
        ActivityEventKind.STEP_FAILED,
        ActivityEventKind.STEP_UNSUPPORTED,
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
        ActivityEventKind.STEP_COMPENSATION_STARTED,
        ActivityEventKind.STEP_COMPENSATION_SUCCEEDED,
        ActivityEventKind.STEP_COMPENSATION_FAILED,
        ActivityEventKind.RUN_COMPENSATION_STARTED,
        ActivityEventKind.RUN_COMPENSATION_SUCCEEDED,
        ActivityEventKind.RUN_COMPENSATION_FAILED,
        ActivityEventKind.RUN_UNCOMPENSATED_FAILURE_ACCEPTED,
        ActivityEventKind.RUN_FAILED,
        ActivityEventKind.RUN_CANCELLED,
    }
    if any(event.kind in disqualifying for event in events):
        raise CurrentGraphAdvancementIncomplete(
            "failed, uncertain, cancelled, or compensating history cannot advance truth"
        )
    try:
        journal = project_activity_journal(plan, events)
        schedule = derive_schedule(plan, journal.state)
    except (SagaJournalError, SagaStateError, ScheduleEvidenceError) as error:
        raise CurrentGraphAdvancementIncomplete(
            "durable saga evidence is structurally incoherent"
        ) from error
    if journal.in_flight or journal.uncertain or not schedule.successful:
        raise CurrentGraphAdvancementIncomplete(
            "durable saga evidence is not a complete successful schedule"
        )
    expected = Counter(activity.activity_id.value for activity in plan.activities)
    succeeded = Counter(
        event.activity_id
        for event in events
        if event.kind
        in {
            ActivityEventKind.STEP_SUCCEEDED,
            ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
        }
    )
    if succeeded != expected:
        raise CurrentGraphAdvancementIncomplete(
            "durable step-success evidence does not exactly cover the activity plan"
        )


def _require_graph_ownership(
    store: GraphTopologyStore,
    workspace_id: str,
    *graph_ids: str,
) -> None:
    try:
        records = tuple(store.get(graph_id) for graph_id in graph_ids)
    except KeyError as error:
        raise CurrentGraphAdvancementNotFound(str(error)) from error
    if any(record.workspace_id != workspace_id for record in records):
        raise CurrentGraphAdvancementConflict("plan graph belongs to another workspace")


def _require_owner(request: ExecutionRequestRecord, worker_id: str) -> None:
    if request.status is not ExecutionRequestStatus.CLAIMED or request.claim is None:
        raise CurrentGraphAdvancementConflict("execution request is not claimed")
    if request.claim.worker_id != worker_id:
        raise CurrentGraphAdvancementDenied("execution request belongs to another worker")


def _authorize(authority: ExecutionWorkerAuthority) -> None:
    if "execution:operate" not in authority.scopes:
        raise CurrentGraphAdvancementDenied("scope 'execution:operate' is missing")


def _replay_if_present(
    stores: PostgresStoreBundle,
    session_id: str,
    key: IdempotencyKey,
    fingerprint: str,
) -> CurrentGraphAdvancementResult | None:
    action = stores.activity_history.action_for_idempotency(session_id, key.value)
    if action is None:
        return None
    if action.action_type is not OperationActionKind.CURRENT_GRAPH_ADVANCED:
        raise CurrentGraphAdvancementIdempotencyConflict(
            "idempotency key already belongs to another operation command"
        )
    if action.intent_fingerprint != fingerprint:
        raise CurrentGraphAdvancementIdempotencyConflict(
            "idempotency key was used for different graph advancement intent"
        )
    event_id = action.payload.get("event_id")
    if not isinstance(event_id, str):
        raise CurrentGraphAdvancementError("advancement operation evidence is incomplete")
    try:
        event = stores.execution.get_event(event_id)
    except KeyError as error:
        raise CurrentGraphAdvancementError(
            "advancement operation evidence is orphaned"
        ) from error
    return _result(event, action, replayed=True)


def _result(
    event: ActivityEventRecord,
    action: OperationActionRecord,
    *,
    replayed: bool = False,
) -> CurrentGraphAdvancementResult:
    payload = action.payload
    required = (
        "workspace_id",
        "from_graph_id",
        "to_graph_id",
        "run_id",
        "plan_id",
    )
    if not all(isinstance(payload.get(name), str) for name in required):
        raise CurrentGraphAdvancementError("advancement evidence is incomplete")
    return CurrentGraphAdvancementResult(
        workspace_id=payload["workspace_id"],
        from_graph_id=payload["from_graph_id"],
        to_graph_id=payload["to_graph_id"],
        run_id=payload["run_id"],
        plan_id=payload["plan_id"],
        event=event,
        action=action,
        replayed=replayed,
    )


def _fingerprint(command: AdvanceCurrentGraph) -> str:
    value = {
        "command": "advance_current_graph",
        "workspace_id": command.workspace_id,
        "run_id": command.run_id,
        "plan_id": command.plan_id,
        "expected_current_graph_id": command.expected_current_graph_id,
        "desired_graph_id": command.desired_graph_id,
        "worker_id": command.authority.worker_id,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _required(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{name} must be non-empty text")
