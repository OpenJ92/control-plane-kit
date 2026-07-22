"""Execution admission commands for approved activity plans."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_core.operations.lifecycle import (
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import (
    PlannedActivity,
    ReconcileNode,
    SwitchSocketConnection,
)
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.topology import GraphDescriptorError
from control_plane_kit_core.types import Protocol, SocketBinding
from control_plane_kit_operations.records import (
    ActivityPlanStatus,
    ApprovalDecisionKind,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    OperationActionRecord,
    OperationSessionStatus,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


class ExecutionAdmissionError(RuntimeError):
    """Base error for execution admission."""


class ExecutionAdmissionConflict(ExecutionAdmissionError):
    """Raised when durable plan, graph, approval, or session truth conflicts."""


class ExecutionAdmissionDenied(ExecutionAdmissionError):
    """Raised when admission lacks authority or current approval."""


class ExecutionAdmissionIdempotencyConflict(ExecutionAdmissionError):
    """Raised when one idempotency key is reused for different execution intent."""


class ExecutionAdmissionNotFound(ExecutionAdmissionError):
    """Raised when admission target truth is missing."""


class ExecutionReadinessRequired(ExecutionAdmissionError):
    """Raised when an externally gated activity lacks reference evidence."""


@dataclass(frozen=True)
class ExternalReadinessAttestation:
    """Reference-only proof that one activity's external precondition is ready."""

    activity_id: str
    evidence_ref: str

    def __post_init__(self) -> None:
        _required_text(self.activity_id, "activity_id")
        _required_text(self.evidence_ref, "evidence_ref")
        if len(self.evidence_ref) > 256 or not _EVIDENCE_REFERENCE.fullmatch(
            self.evidence_ref
        ):
            raise InvalidOperationCommand(
                "evidence_ref must be a bounded namespaced identifier, not a value or URL"
            )

    def descriptor(self) -> dict[str, str]:
        return {
            "activity_id": self.activity_id,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class RequestPlanExecution:
    """Request durable admission of one approved canonical plan."""

    workspace_id: str
    session_id: str
    plan_id: str
    approval_request_id: str
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]
    idempotency_key: IdempotencyKey
    readiness: tuple[ExternalReadinessAttestation, ...] = ()

    def __post_init__(self) -> None:
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.session_id, "session_id")
        _required_text(self.plan_id, "plan_id")
        _required_text(self.approval_request_id, "approval_request_id")
        _required_text(self.actor_id, "actor_id")
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        _require_idempotency_key(self.idempotency_key)
        if not all(
            isinstance(item, ExternalReadinessAttestation)
            for item in self.readiness
        ):
            raise InvalidOperationCommand(
                "readiness values must be ExternalReadinessAttestation"
            )
        ids = tuple(item.activity_id for item in self.readiness)
        if len(ids) != len(set(ids)):
            raise InvalidOperationCommand("readiness may attest each activity only once")
        object.__setattr__(
            self,
            "readiness",
            tuple(sorted(self.readiness, key=lambda item: item.activity_id)),
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "command": LifecycleOperationKind.ADMIT_EXECUTION.value,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "plan_id": self.plan_id,
            "approval_request_id": self.approval_request_id,
            "actor_id": self.actor_id,
            "actor_scopes": tuple(scope.value for scope in self.actor_scopes),
            "idempotency_key": self.idempotency_key.value,
            "readiness": tuple(item.descriptor() for item in self.readiness),
        }


@dataclass(frozen=True)
class ExecutionAdmissionResult:
    """Queued execution request plus operation-action evidence."""

    request: ExecutionRequestRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.action.action_type is not LifecycleOperationKind.ADMIT_EXECUTION:
            raise InvalidOperationCommand(
                "admission result requires ADMIT_EXECUTION action evidence"
            )
        if self.action.session_id != self.request.identity.session_id:
            raise InvalidOperationCommand("request and action must share a session")
        if (
            self.action.payload.get("execution_request_id")
            != self.request.identity.request_id
        ):
            raise InvalidOperationCommand(
                "action must reference execution request truth"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "execution_request_id": self.request.identity.request_id,
            "workspace_id": self.request.identity.workspace_id,
            "session_id": self.request.identity.session_id,
            "plan_id": self.request.identity.plan_id,
            "status": self.request.status.value,
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


class ExecutionAdmissionCommandService:
    """Admit one approved plan in one Postgres transaction."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory
        self._policy = policy or ApprovalPolicy()

    def execute(self, command: RequestPlanExecution) -> ExecutionAdmissionResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            try:
                workspace = stores.workspaces.get_for_update(command.workspace_id)
                session = stores.activity_history.get_session(command.session_id)
                plan = stores.activity_history.get_plan(command.plan_id)
                approval = stores.activity_history.get_approval_request(
                    command.approval_request_id
                )
            except KeyError as error:
                raise ExecutionAdmissionNotFound(str(error)) from error

            if PolicyScope.PLAN_EXECUTE not in command.actor_scopes:
                raise ExecutionAdmissionDenied("scope plan:execute is missing")
            stores.execution.lock_admission_idempotency(
                command.workspace_id,
                command.idempotency_key.value,
            )
            replay = stores.execution.request_for_idempotency(
                command.workspace_id,
                command.idempotency_key.value,
            )
            if replay is not None:
                result = _replay(stores.activity_history, replay, fingerprint)
                unit_of_work.commit()
                return result

            if session.workspace_id != command.workspace_id:
                raise ExecutionAdmissionConflict(
                    "session belongs to another workspace"
                )
            if session.status is not OperationSessionStatus.OPEN:
                raise ExecutionAdmissionConflict(
                    "execution admission requires an open session"
                )
            if plan.session_id != command.session_id:
                raise ExecutionAdmissionConflict("plan belongs to another session")
            if plan.status is not ActivityPlanStatus.PLANNED:
                raise ExecutionAdmissionConflict(
                    "only planned activity plans may be admitted"
                )
            if not plan.plan.activities:
                raise ExecutionAdmissionConflict(
                    "activity plan contains no executable changes"
                )
            if not plan.plan.ready_for_execution:
                raise ExecutionAdmissionConflict("plan contains unresolved review blockers")
            if (
                approval.session_id != command.session_id
                or approval.plan_id != command.plan_id
            ):
                raise ExecutionAdmissionConflict(
                    "approval does not authorize this plan and session"
                )
            requirement = self._policy.requirement_for(plan.plan)
            if (
                approval.required_scope is not requirement.required_scope
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
            if decision.scope is not approval.required_scope:
                raise ExecutionAdmissionDenied(
                    "approval decision has insufficient scope"
                )
            if (
                workspace.current_graph_id != plan.base_graph_id
                or workspace.desired_graph_id != plan.desired_graph_id
            ):
                raise ExecutionAdmissionConflict("plan graph references are stale")

            current = _graph(stores.graphs, plan.base_graph_id, command.workspace_id)
            desired = _graph(stores.graphs, plan.desired_graph_id, command.workspace_id)
            required = _readiness_required(plan.plan.activities, current, desired)
            supplied = {item.activity_id for item in command.readiness}
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
                        request_id=self._id_factory(),
                        workspace_id=command.workspace_id,
                        session_id=command.session_id,
                        plan_id=command.plan_id,
                    ),
                    status=ExecutionRequestStatus.QUEUED,
                    requested_by=command.actor_id,
                    requested_at=requested_at,
                    approval_request_id=approval.request_id,
                    approval_decision_id=decision.decision_id,
                    idempotency=ExecutionIdempotency(
                        command.idempotency_key.value,
                        fingerprint,
                    ),
                )
            )
            action = stores.activity_history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=command.session_id,
                    ordinal=ordinal,
                    action_type=LifecycleOperationKind.ADMIT_EXECUTION,
                    actor_id=command.actor_id,
                    payload={
                        "execution_request_id": request.identity.request_id,
                        "plan_id": command.plan_id,
                        "approval_request_id": approval.request_id,
                        "approval_decision_id": decision.decision_id,
                        "base_graph_id": plan.base_graph_id,
                        "desired_graph_id": plan.desired_graph_id,
                        "readiness": [item.descriptor() for item in command.readiness],
                    },
                    created_at=requested_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            unit_of_work.commit()
            return ExecutionAdmissionResult(request, action)


def _graph(store: Any, graph_id: str, workspace_id: str) -> DeploymentGraph:
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
                edges = (
                    current.edges.get(target.edge_id),
                    desired.edges.get(target.edge_id),
                )
                if any(
                    edge is not None and edge.protocol == Protocol.POSTGRES
                    for edge in edges
                ):
                    required.add(activity.activity_id.value)
            case ReconcileNode(target=target):
                if _changes_startup_postgres_endpoint(target.node_id, current, desired):
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
    history: Any,
    request: ExecutionRequestRecord,
    fingerprint: str,
) -> ExecutionAdmissionResult:
    if request.idempotency.intent_fingerprint != fingerprint:
        raise ExecutionAdmissionIdempotencyConflict(
            "idempotency key was used for different execution intent"
        )
    action = history.action_for_idempotency(
        request.identity.session_id,
        request.idempotency.key,
    )
    if action is None or action.action_type is not LifecycleOperationKind.ADMIT_EXECUTION:
        raise ExecutionAdmissionError("execution request is missing action evidence")
    if action.intent_fingerprint != fingerprint:
        raise ExecutionAdmissionError(
            "execution request evidence fingerprint is inconsistent"
        )
    return ExecutionAdmissionResult(request, action, replayed=True)


def _fingerprint(command: RequestPlanExecution) -> str:
    return _hash(
        {
            "command": LifecycleOperationKind.ADMIT_EXECUTION.value,
            "workspace_id": command.workspace_id,
            "session_id": command.session_id,
            "plan_id": command.plan_id,
            "approval_request_id": command.approval_request_id,
            "actor_id": command.actor_id,
            "readiness": [item.descriptor() for item in command.readiness],
        }
    )


def _hash(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scopes(values: Iterable[PolicyScope]) -> tuple[PolicyScope, ...]:
    if isinstance(values, (str, bytes)):
        raise InvalidOperationCommand(
            "actor_scopes must be an iterable of PolicyScope"
        )
    scopes = tuple(sorted(set(values), key=lambda scope: scope.value))
    if not all(isinstance(scope, PolicyScope) for scope in scopes):
        raise InvalidOperationCommand("actor_scopes must contain only PolicyScope")
    return scopes


def _require_idempotency_key(value: IdempotencyKey) -> None:
    if not isinstance(value, IdempotencyKey):
        raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")


_EVIDENCE_REFERENCE = re.compile(
    r"[a-z][a-z0-9-]{0,63}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,63})+"
)
