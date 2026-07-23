"""Guarded current-graph advancement from durable execution evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import (
    ActivityPlan,
    SagaJournalError,
    SagaStateError,
    ScheduleEvidenceError,
    derive_schedule,
    project_activity_journal,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    BoundedEvidence,
    ExecutionRequestRecord,
    OperationActionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


class CurrentGraphAdvancementError(RuntimeError):
    """Base error for guarded current-graph advancement."""


class CurrentGraphAdvancementNotFound(CurrentGraphAdvancementError):
    """Raised when required workspace, graph, plan, request, run, or event is absent."""


class CurrentGraphAdvancementConflict(CurrentGraphAdvancementError):
    """Raised when pinned graph, plan, request, or workspace truth disagrees."""


class CurrentGraphAdvancementDenied(CurrentGraphAdvancementError):
    """Raised when worker authority cannot advance this run."""


class CurrentGraphAdvancementIncomplete(CurrentGraphAdvancementError):
    """Raised when durable activity evidence does not prove successful realization."""


class CurrentGraphAdvancementIdempotencyConflict(CurrentGraphAdvancementError):
    """Raised when one idempotency key is reused for a different advancement."""


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
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.run_id, "run_id")
        _required_text(self.plan_id, "plan_id")
        _required_text(self.expected_current_graph_id, "expected_current_graph_id")
        _required_text(self.desired_graph_id, "desired_graph_id")
        if self.expected_current_graph_id == self.desired_graph_id:
            raise InvalidOperationCommand(
                "current graph advancement requires distinct graph identities"
            )
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


@dataclass(frozen=True)
class CurrentGraphAdvancementResult:
    """Stable evidence returned for an original command or exact replay."""

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
            raise CurrentGraphAdvancementError(
                "advancement result requires current-graph activity evidence"
            )
        if self.action.action_type is not LifecycleOperationKind.ADVANCE_CURRENT_GRAPH:
            raise CurrentGraphAdvancementError(
                "advancement result requires current-graph operation evidence"
            )
        if self.action.payload.get("event_id") != self.event.event_id:
            raise CurrentGraphAdvancementError("advancement event/action disagree")
        expected = {
            "workspace_id": self.workspace_id,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "from_graph_id": self.from_graph_id,
            "to_graph_id": self.to_graph_id,
        }
        if self.event.run_id != self.run_id:
            raise CurrentGraphAdvancementError("advancement event belongs elsewhere")
        if self.event.evidence.descriptor() != expected:
            raise CurrentGraphAdvancementError(
                "advancement event does not encode the claimed graph transition"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "from_graph_id": self.from_graph_id,
            "to_graph_id": self.to_graph_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "event_id": self.event.event_id,
            "action_id": self.action.action_id,
            "replayed": self.replayed,
        }


class CurrentGraphAdvancementCommandService:
    """Advance a workspace current-graph projection in one transaction."""

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

    def execute(
        self,
        command: AdvanceCurrentGraph,
    ) -> CurrentGraphAdvancementResult:
        _require_operate_scope(command.authority)
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            try:
                workspace = stores.workspaces.get_for_update(command.workspace_id)
                run = stores.execution.get_run_for_update(command.run_id)
                request = stores.execution.get_request(run.admission.request_id)
                plan = stores.activity_history.get_plan(command.plan_id)
            except KeyError as error:
                raise CurrentGraphAdvancementNotFound(str(error)) from error

            existing = stores.activity_history.action_for_idempotency(
                request.identity.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                result = _replay(stores, existing, fingerprint)
                unit_of_work.commit()
                return result

            _require_worker_owns(request, command.authority)
            _require_identity(command, workspace, request, run, plan)
            _require_graph_ownership(
                stores.graphs,
                command.workspace_id,
                command.expected_current_graph_id,
                command.desired_graph_id,
            )
            events = stores.execution.events_for_run(command.run_id)
            _require_complete_success(plan.plan, run, events)

            advanced = stores.workspaces.compare_and_set_current_graph(
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
                    self._id_factory(),
                    command.run_id,
                    stores.execution.next_event_ordinal(command.run_id),
                    ActivityEventKind.CURRENT_GRAPH_ADVANCED,
                    occurred_at,
                    evidence=evidence,
                )
            )
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    self._id_factory(),
                    request.identity.session_id,
                    stores.activity_history.next_action_ordinal(
                        request.identity.session_id
                    ),
                    LifecycleOperationKind.ADVANCE_CURRENT_GRAPH,
                    command.authority.worker_id,
                    payload={
                        **evidence.descriptor(),
                        "event_id": event.event_id,
                    },
                    created_at=occurred_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            unit_of_work.commit()
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


def _require_graph_ownership(
    graph_store: Any,
    workspace_id: str,
    *graph_ids: str,
) -> None:
    try:
        records = tuple(graph_store.get(graph_id) for graph_id in graph_ids)
    except KeyError as error:
        raise CurrentGraphAdvancementNotFound(str(error)) from error
    if any(record.workspace_id != workspace_id for record in records):
        raise CurrentGraphAdvancementConflict("plan graph belongs to another workspace")


def _require_worker_owns(
    request: ExecutionRequestRecord,
    authority: ExecutionWorkerAuthority,
) -> None:
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or request.claim is None
        or request.claim.worker_id != authority.worker_id
    ):
        raise CurrentGraphAdvancementDenied(
            "worker does not own the execution request claim"
        )


def _require_complete_success(
    plan: ActivityPlan,
    run: ActivityRunRecord,
    events: tuple[ActivityEventRecord, ...],
) -> None:
    if run.status is not ActivityRunStatus.SUCCEEDED or run.settled_at is None:
        raise CurrentGraphAdvancementIncomplete("run is not settled as succeeded")
    if not events or events[-1].kind is not ActivityEventKind.RUN_SUCCEEDED:
        raise CurrentGraphAdvancementIncomplete(
            "run success must be latest durable execution event"
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
            "failed, unsupported, or compensating history cannot advance truth"
        )
    try:
        projection = project_activity_journal(plan, activity_journal_events(events))
        schedule = derive_schedule(plan, projection.state)
    except (SagaJournalError, SagaStateError, ScheduleEvidenceError) as error:
        raise CurrentGraphAdvancementIncomplete(
            "durable saga evidence is structurally incoherent"
        ) from error
    if projection.in_flight or projection.uncertain or not schedule.successful:
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


def _replay(
    stores: Any,
    action: OperationActionRecord,
    fingerprint: str,
) -> CurrentGraphAdvancementResult:
    if action.action_type is not LifecycleOperationKind.ADVANCE_CURRENT_GRAPH:
        raise CurrentGraphAdvancementIdempotencyConflict(
            "idempotency key already belongs to another operation"
        )
    if action.intent_fingerprint != fingerprint:
        raise CurrentGraphAdvancementIdempotencyConflict(
            "idempotency key was reused with different advancement intent"
        )
    event_id = _payload_text(action.payload, "event_id")
    try:
        event = stores.execution.get_event(event_id)
    except KeyError as error:
        raise CurrentGraphAdvancementError(
            "advancement operation evidence is missing its event"
        ) from error
    return _result(event, action, replayed=True)


def _result(
    event: ActivityEventRecord,
    action: OperationActionRecord,
    *,
    replayed: bool = False,
) -> CurrentGraphAdvancementResult:
    return CurrentGraphAdvancementResult(
        workspace_id=_payload_text(action.payload, "workspace_id"),
        from_graph_id=_payload_text(action.payload, "from_graph_id"),
        to_graph_id=_payload_text(action.payload, "to_graph_id"),
        run_id=_payload_text(action.payload, "run_id"),
        plan_id=_payload_text(action.payload, "plan_id"),
        event=event,
        action=action,
        replayed=replayed,
    )


def _fingerprint(command: AdvanceCurrentGraph) -> str:
    value: Mapping[str, object] = {
        "command": LifecycleOperationKind.ADVANCE_CURRENT_GRAPH.value,
        "workspace_id": command.workspace_id,
        "run_id": command.run_id,
        "plan_id": command.plan_id,
        "expected_current_graph_id": command.expected_current_graph_id,
        "desired_graph_id": command.desired_graph_id,
        "worker_id": command.authority.worker_id,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _payload_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise CurrentGraphAdvancementError(f"advancement action payload lacks {key}")
    return value


def _require_operate_scope(authority: ExecutionWorkerAuthority) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise CurrentGraphAdvancementDenied("scope execution:operate is missing")


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")
