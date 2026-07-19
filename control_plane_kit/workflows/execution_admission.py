"""Transactional admission of approved plans into the execution queue.

Admission records durable intent only.  It never claims work and never invokes
an external effect.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from uuid import uuid4

from control_plane_kit.execution import (
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
)
from control_plane_kit.planning import (
    PlannedActivity,
    ReconcileNode,
    SwitchSocketConnection,
)
from control_plane_kit.policies import ApprovalPolicy
from control_plane_kit.stores import (
    ActivityHistoryStore,
    ApprovalDecisionKind,
    GraphTopologyStore,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionStatus,
    PostgresUnitOfWork,
)
from control_plane_kit.topology.codec import DEFAULT_GRAPH_CODEC, GraphDescriptorError
from control_plane_kit.topology.graph import DeploymentGraph
from control_plane_kit.types import Protocol, SocketBinding
from control_plane_kit.workflows.commands import IdempotencyKey, InvalidOperationCommand


@dataclass(frozen=True)
class ExternalReadinessAttestation:
    """Reference-only proof that one activity's external precondition is ready."""

    activity_id: str
    evidence_ref: str

    def __post_init__(self) -> None:
        _required("activity_id", self.activity_id)
        _required("evidence_ref", self.evidence_ref)
        if len(self.evidence_ref) > 256 or not _EVIDENCE_REFERENCE.fullmatch(
            self.evidence_ref
        ):
            raise InvalidOperationCommand(
                "evidence_ref must be a bounded namespaced identifier, not a value or URL"
            )


@dataclass(frozen=True)
class RequestPlanExecution:
    """Request durable admission of one approved canonical plan."""

    workspace_id: str
    session_id: str
    plan_id: str
    approval_request_id: str
    actor_id: str
    actor_scopes: tuple[str, ...]
    idempotency_key: IdempotencyKey
    readiness: tuple[ExternalReadinessAttestation, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "session_id",
            "plan_id",
            "approval_request_id",
            "actor_id",
        ):
            _required(name, getattr(self, name))
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        if not all(isinstance(value, ExternalReadinessAttestation) for value in self.readiness):
            raise InvalidOperationCommand("readiness values must be typed attestations")
        ids = tuple(value.activity_id for value in self.readiness)
        if len(ids) != len(set(ids)):
            raise InvalidOperationCommand("readiness may attest each activity only once")
        object.__setattr__(
            self,
            "readiness",
            tuple(sorted(self.readiness, key=lambda value: value.activity_id)),
        )


@dataclass(frozen=True)
class ExecutionAdmissionResult:
    request: ExecutionRequestRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.action.action_type is not OperationActionKind.EXECUTION_REQUESTED:
            raise InvalidOperationCommand(
                "admission result requires execution-request action evidence"
            )
        if self.action.session_id != self.request.identity.session_id:
            raise InvalidOperationCommand("execution request and action must share a session")
        if (
            self.action.payload.get("execution_request_id")
            != self.request.identity.request_id
        ):
            raise InvalidOperationCommand("action must reference execution request truth")


class ExecutionAdmissionError(RuntimeError):
    """Base error for execution admission."""


class ExecutionAdmissionNotFound(ExecutionAdmissionError):
    pass


class ExecutionAdmissionConflict(ExecutionAdmissionError):
    pass


class ExecutionAdmissionDenied(ExecutionAdmissionError):
    pass


class ExecutionAdmissionIdempotencyConflict(ExecutionAdmissionError):
    pass


class ExecutionReadinessRequired(ExecutionAdmissionError):
    pass


Clock = Callable[[], str]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


def _uuid() -> str:
    return uuid4().hex


class ExecutionAdmissionCommandService:
    """Admit one plan and its audit action in one Postgres transaction."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory
        self._policy = policy or ApprovalPolicy()

    def execute(self, command: RequestPlanExecution) -> ExecutionAdmissionResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            stores = work.stores
            try:
                workspace = stores.workspace.get_for_update(command.workspace_id)
                session = stores.activity_history.get_session(command.session_id)
                plan = stores.activity_history.get_plan(command.plan_id)
                approval = stores.activity_history.get_approval_request(
                    command.approval_request_id
                )
            except KeyError as error:
                raise ExecutionAdmissionNotFound(str(error)) from error

            if "plan:execute" not in command.actor_scopes:
                raise ExecutionAdmissionDenied("scope 'plan:execute' is missing")
            replay = stores.execution.request_for_idempotency(
                command.workspace_id, command.idempotency_key.value
            )
            if replay is not None:
                return _replay(stores.activity_history, replay, fingerprint)

            if session.workspace_id != command.workspace_id:
                raise ExecutionAdmissionConflict("session belongs to another workspace")
            if session.status is not OperationSessionStatus.OPEN:
                raise ExecutionAdmissionConflict("execution admission requires an open session")
            if plan.session_id != command.session_id:
                raise ExecutionAdmissionConflict("plan belongs to another session")
            if plan.status != "planned":
                raise ExecutionAdmissionConflict("only planned activity plans may be admitted")
            if not plan.plan.activities:
                raise ExecutionAdmissionConflict(
                    "activity plan contains no executable changes"
                )
            if not plan.plan.ready_for_execution:
                raise ExecutionAdmissionConflict("plan contains unresolved review blockers")
            if approval.session_id != command.session_id or approval.plan_id != command.plan_id:
                raise ExecutionAdmissionConflict("approval does not authorize this plan and session")
            requirement = self._policy.requirement_for(plan.plan)
            if (
                approval.required_scope != requirement.required_scope
                or approval.destructive is not requirement.destructive
                or approval.max_risk is not requirement.max_risk
            ):
                raise ExecutionAdmissionDenied(
                    "approval evidence does not match canonical plan risk"
                )
            decision = stores.activity_history.approval_decision_for_request(
                command.approval_request_id
            )
            if decision is None:
                raise ExecutionAdmissionDenied("plan has no approval decision")
            if decision.decision is not ApprovalDecisionKind.APPROVED:
                raise ExecutionAdmissionDenied("plan approval was rejected")
            if decision.scope != approval.required_scope:
                raise ExecutionAdmissionDenied("approval decision has insufficient scope")

            if (
                workspace.current_graph_id != plan.base_graph_id
                or workspace.desired_graph_id != plan.desired_graph_id
            ):
                raise ExecutionAdmissionConflict("plan graph references are stale")
            current = _graph(stores.graph_topology, plan.base_graph_id, command.workspace_id)
            desired = _graph(stores.graph_topology, plan.desired_graph_id, command.workspace_id)
            required = _readiness_required(plan.plan.activities, current, desired)
            supplied = {value.activity_id for value in command.readiness}
            unexpected = supplied - required
            if unexpected:
                raise ExecutionAdmissionConflict(
                    "readiness evidence does not apply to activities: "
                    + ", ".join(sorted(unexpected))
                )
            missing = required - supplied
            if missing:
                raise ExecutionReadinessRequired(
                    "external readiness evidence is required for activities: "
                    + ", ".join(sorted(missing))
                )

            ordinal = stores.activity_history.next_action_ordinal(command.session_id)
            requested_at = self._clock()
            request = stores.execution.add_request(
                ExecutionRequestRecord(
                    identity=ExecutionRequestIdentity(
                        self._id_factory(),
                        command.workspace_id,
                        command.session_id,
                        command.plan_id,
                    ),
                    status=ExecutionRequestStatus.QUEUED,
                    requested_by=command.actor_id,
                    requested_at=requested_at,
                    approval_request_id=approval.request_id,
                    approval_decision_id=decision.decision_id,
                    idempotency=ExecutionIdempotency(
                        command.idempotency_key.value, fingerprint
                    ),
                )
            )
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=command.session_id,
                    ordinal=ordinal,
                    action_type=OperationActionKind.EXECUTION_REQUESTED,
                    actor_id=command.actor_id,
                    payload={
                        "execution_request_id": request.identity.request_id,
                        "plan_id": command.plan_id,
                        "approval_request_id": approval.request_id,
                        "approval_decision_id": decision.decision_id,
                        "base_graph_id": plan.base_graph_id,
                        "desired_graph_id": plan.desired_graph_id,
                        "readiness": [
                            {
                                "activity_id": value.activity_id,
                                "evidence_ref": value.evidence_ref,
                            }
                            for value in command.readiness
                        ],
                    },
                    created_at=requested_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            work.commit()
            return ExecutionAdmissionResult(request, action)


def _graph(
    store: GraphTopologyStore,
    graph_id: str,
    workspace_id: str,
) -> DeploymentGraph:
    try:
        record = store.get(graph_id)
        graph = DEFAULT_GRAPH_CODEC.decode(record.graph_descriptor)
    except (KeyError, GraphDescriptorError) as error:
        raise ExecutionAdmissionConflict(
            f"graph {graph_id!r} is unavailable or invalid"
        ) from error
    if record.workspace_id != workspace_id:
        raise ExecutionAdmissionConflict("plan graph belongs to another workspace")
    return graph


def _readiness_required(
    activities: tuple[PlannedActivity, ...],
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> set[str]:
    required: set[str] = set()
    for activity in activities:
        match activity.operation:
            case SwitchSocketConnection(target=target):
                edges = [
                    graph.edges.get(target.edge_id)
                    for graph in (current, desired)
                ]
                if any(
                    edge is not None and edge.protocol == Protocol.POSTGRES
                    for edge in edges
                ):
                    required.add(activity.activity_id.value)
            case ReconcileNode(target=target):
                if _changes_startup_postgres_endpoint(
                    target.node_id,
                    current,
                    desired,
                ):
                    required.add(activity.activity_id.value)
    return required


def _changes_startup_postgres_endpoint(
    consumer_id: str,
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> bool:
    for edge_id in set(current.edges) | set(desired.edges):
        before = current.edges.get(edge_id)
        after = desired.edges.get(edge_id)
        if before == after:
            continue
        for edge in (before, after):
            if (
                edge is not None
                and edge.consumer_role == consumer_id
                and edge.protocol == Protocol.POSTGRES
                and edge.binding is SocketBinding.ENVIRONMENT
            ):
                return True
    return False


def _replay(
    history: ActivityHistoryStore,
    request: ExecutionRequestRecord,
    fingerprint: str,
) -> ExecutionAdmissionResult:
    if request.idempotency.intent_fingerprint != fingerprint:
        raise ExecutionAdmissionIdempotencyConflict(
            "idempotency key was used for different execution intent"
        )
    action = history.action_for_idempotency(
        request.identity.session_id, request.idempotency.key
    )
    if (
        action is None
        or action.action_type is not OperationActionKind.EXECUTION_REQUESTED
    ):
        raise ExecutionAdmissionError("execution request is missing operation evidence")
    if action.intent_fingerprint != fingerprint:
        raise ExecutionAdmissionError("execution request evidence fingerprint is inconsistent")
    return ExecutionAdmissionResult(request, action, replayed=True)


def _fingerprint(command: RequestPlanExecution) -> str:
    value = {
        "command": "request_plan_execution",
        "workspace_id": command.workspace_id,
        "session_id": command.session_id,
        "plan_id": command.plan_id,
        "approval_request_id": command.approval_request_id,
        "actor_id": command.actor_id,
        "readiness": [
            {"activity_id": item.activity_id, "evidence_ref": item.evidence_ref}
            for item in command.readiness
        ],
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _required(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{name} must not be empty")


def _scopes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise InvalidOperationCommand("actor scopes must be an iterable of strings")
    scopes = tuple(sorted(set(values)))
    if not all(isinstance(value, str) and value.strip() for value in scopes):
        raise InvalidOperationCommand("actor scopes must be non-empty strings")
    return scopes


_EVIDENCE_REFERENCE = re.compile(
    r"[a-z][a-z0-9-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,63})+"
)
