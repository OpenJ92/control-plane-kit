"""Transactional application command for authoritative activity planning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from control_plane_kit.planning.compiler import compile_activity_plan
from control_plane_kit.core.topology.codec import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorCodec,
    GraphDescriptorError,
)
from control_plane_kit.core.topology.diff import diff_graphs
from control_plane_kit.stores import (
    ActivityHistoryStore,
    ActivityPlanRecord,
    GraphTopologyStore,
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionStatus,
    PostgresUnitOfWork,
)
from control_plane_kit.core.topology.validation import GraphValidationError, validate_graph
from control_plane_kit.workflows.commands import IdempotencyKey, InvalidOperationCommand


@dataclass(frozen=True)
class RequestActivityPlan:
    """Request a plan for the workspace graph pointers the operator observed."""

    session_id: str
    workspace_id: str
    actor_id: str
    expected_current_graph_id: str
    expected_desired_graph_id: str
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        for name in (
            "session_id",
            "workspace_id",
            "actor_id",
            "expected_current_graph_id",
            "expected_desired_graph_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidOperationCommand(f"{name} must not be empty")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")

    def descriptor(self) -> dict[str, str]:
        return {
            "command": "request_activity_plan",
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "expected_current_graph_id": self.expected_current_graph_id,
            "expected_desired_graph_id": self.expected_desired_graph_id,
            "idempotency_key": self.idempotency_key.value,
        }


@dataclass(frozen=True)
class ActivityPlanningResult:
    """Durable plan and operation-action evidence from one committed command."""

    plan_record: ActivityPlanRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.action.action_type is not OperationActionKind.PLAN_REQUESTED:
            raise InvalidOperationCommand(
                "activity planning result requires PLAN_REQUESTED action evidence"
            )
        if self.action.session_id != self.plan_record.session_id:
            raise InvalidOperationCommand("plan and action must belong to one session")
        evidence = self.action.payload
        if evidence.get("plan_id") != self.plan_record.plan_id:
            raise InvalidOperationCommand("action evidence must reference the persisted plan")
        if evidence.get("base_graph_id") != self.plan_record.base_graph_id:
            raise InvalidOperationCommand("action evidence must reference the plan base graph")
        if evidence.get("desired_graph_id") != self.plan_record.desired_graph_id:
            raise InvalidOperationCommand("action evidence must reference the plan desired graph")

    def descriptor(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_record.plan_id,
            "session_id": self.plan_record.session_id,
            "base_graph_id": self.plan_record.base_graph_id,
            "desired_graph_id": self.plan_record.desired_graph_id,
            "ready_for_execution": self.plan_record.plan.ready_for_execution,
            "activity_count": len(self.plan_record.plan.activities),
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


class ActivityPlanningError(RuntimeError):
    """Base error for transactional activity-plan derivation."""


class ActivityPlanningWorkspaceNotFound(ActivityPlanningError):
    """Raised when workspace truth does not exist."""


class ActivityPlanningSessionConflict(ActivityPlanningError):
    """Raised when the operation session cannot own planning intent."""


class ActivityPlanningGraphStateConflict(ActivityPlanningError):
    """Raised when graph pointers are absent, stale, or cross workspace truth."""


class ActivityPlanningGraphInvalid(ActivityPlanningError):
    """Raised when durable graph data cannot enter the typed planning pipeline."""


class ActivityPlanningIdempotencyConflict(ActivityPlanningError):
    """Raised when an idempotency key is reused for different planning intent."""


Clock = Callable[[], str]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


def _uuid() -> str:
    return uuid4().hex


class ActivityPlanningCommandService:
    """Derive and persist one plan inside one caller-owned Postgres transaction."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
        graph_codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory
        self._graph_codec = graph_codec

    def execute(self, command: RequestActivityPlan) -> ActivityPlanningResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            stores = work.stores
            try:
                workspace = stores.workspace.get_for_update(command.workspace_id)
            except KeyError as error:
                raise ActivityPlanningWorkspaceNotFound(
                    f"workspace {command.workspace_id!r} does not exist"
                ) from error
            try:
                session = stores.activity_history.get_session(command.session_id)
            except KeyError as error:
                raise ActivityPlanningSessionConflict(
                    f"operation session {command.session_id!r} does not exist"
                ) from error
            if session.workspace_id != command.workspace_id:
                raise ActivityPlanningSessionConflict(
                    "operation session and plan must belong to the same workspace"
                )
            replay = stores.activity_history.action_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if replay is not None:
                return _planning_replay(stores.activity_history, replay, fingerprint)
            if session.status is not OperationSessionStatus.OPEN:
                raise ActivityPlanningSessionConflict(
                    f"operation session {command.session_id!r} is not open"
                )
            if (
                workspace.current_graph_id != command.expected_current_graph_id
                or workspace.desired_graph_id != command.expected_desired_graph_id
            ):
                raise ActivityPlanningGraphStateConflict(
                    "workspace graph pointers changed since the operator requested planning"
                )

            current_record = _graph_record(
                stores.graph_topology,
                command.expected_current_graph_id,
                command.workspace_id,
            )
            desired_record = _graph_record(
                stores.graph_topology,
                command.expected_desired_graph_id,
                command.workspace_id,
            )
            try:
                current = validate_graph(
                    self._graph_codec.decode(current_record.graph_descriptor),
                    codec=self._graph_codec,
                )
                desired = validate_graph(
                    self._graph_codec.decode(desired_record.graph_descriptor),
                    codec=self._graph_codec,
                )
                diff = diff_graphs(current, desired)
            except (GraphDescriptorError, GraphValidationError) as error:
                raise ActivityPlanningGraphInvalid(str(error)) from error
            plan = compile_activity_plan(diff)
            ordinal = stores.activity_history.next_action_ordinal(command.session_id)
            locked_session = stores.activity_history.get_session(command.session_id)
            replay = stores.activity_history.action_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if replay is not None:
                return _planning_replay(stores.activity_history, replay, fingerprint)
            if locked_session.status is not OperationSessionStatus.OPEN:
                raise ActivityPlanningSessionConflict(
                    f"operation session {command.session_id!r} is not open"
                )
            created_at = self._clock()
            plan_record = stores.activity_history.add_plan(
                ActivityPlanRecord(
                    plan_id=self._id_factory(),
                    session_id=command.session_id,
                    base_graph_id=current_record.graph_id,
                    desired_graph_id=desired_record.graph_id,
                    status="planned",
                    created_at=created_at,
                    plan=plan,
                )
            )
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=command.session_id,
                    ordinal=ordinal,
                    action_type=OperationActionKind.PLAN_REQUESTED,
                    actor_id=command.actor_id,
                    payload={
                        "workspace_id": command.workspace_id,
                        "plan_id": plan_record.plan_id,
                        "base_graph_id": plan_record.base_graph_id,
                        "desired_graph_id": plan_record.desired_graph_id,
                        "ready_for_execution": plan.ready_for_execution,
                        "activity_count": len(plan.activities),
                    },
                    created_at=created_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            work.commit()
            return ActivityPlanningResult(plan_record, action)


def _graph_record(
    store: GraphTopologyStore,
    graph_id: str,
    workspace_id: str,
) -> GraphVersionRecord:
    try:
        record = store.get(graph_id)
    except KeyError as error:
        raise ActivityPlanningGraphStateConflict(
            f"workspace graph pointer {graph_id!r} has no graph truth"
        ) from error
    if not isinstance(record, GraphVersionRecord) or record.workspace_id != workspace_id:
        raise ActivityPlanningGraphStateConflict(
            f"graph {graph_id!r} does not belong to workspace {workspace_id!r}"
        )
    return record


def _fingerprint(command: RequestActivityPlan) -> str:
    encoded = json.dumps(
        {
            "command": "request_activity_plan",
            "session_id": command.session_id,
            "workspace_id": command.workspace_id,
            "actor_id": command.actor_id,
            "expected_current_graph_id": command.expected_current_graph_id,
            "expected_desired_graph_id": command.expected_desired_graph_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _planning_replay(
    history: ActivityHistoryStore,
    action: OperationActionRecord,
    fingerprint: str,
) -> ActivityPlanningResult:
    if action.action_type is not OperationActionKind.PLAN_REQUESTED:
        raise ActivityPlanningIdempotencyConflict(
            "idempotency key was already used for another operation action"
        )
    if action.intent_fingerprint != fingerprint:
        raise ActivityPlanningIdempotencyConflict(
            "idempotency key was already used for different planning intent"
        )
    plan_id = action.payload.get("plan_id")
    if not isinstance(plan_id, str):
        raise ActivityPlanningError("planning action evidence has no typed plan reference")
    try:
        plan = history.get_plan(plan_id)
    except KeyError as error:
        raise ActivityPlanningError(
            "planning action references missing durable plan truth"
        ) from error
    return ActivityPlanningResult(plan, action, replayed=True)
