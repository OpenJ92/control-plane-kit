"""Durable operations record shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import (
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.types import WorkspaceLifecycle


class OperationsRecordError(ValueError):
    """Raised when a durable operations record is malformed."""


class OperationSessionStatus(StrEnum):
    """Closed lifecycle vocabulary for grouped operator intent."""

    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ActivityPlanStatus(StrEnum):
    """Closed lifecycle vocabulary for persisted activity plans."""

    PLANNED = "planned"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ApprovalDecisionKind(StrEnum):
    """Closed approval decision vocabulary."""

    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class WorkspaceRecord:
    """Workspace truth and graph pointers owned by operations."""

    workspace_id: str
    name: str
    lifecycle: WorkspaceLifecycle = WorkspaceLifecycle.CREATED
    current_graph_id: str | None = None
    desired_graph_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.name, "name")
        if not isinstance(self.lifecycle, WorkspaceLifecycle):
            raise OperationsRecordError("workspace lifecycle must be WorkspaceLifecycle")
        _validate_optional_text(self.current_graph_id, "current_graph_id")
        _validate_optional_text(self.desired_graph_id, "desired_graph_id")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("workspace metadata must be mapping")


@dataclass(frozen=True)
class GraphVersionRecord:
    """One immutable graph descriptor version owned by a workspace."""

    graph_id: str
    workspace_id: str
    version: int
    graph_descriptor: Mapping[str, object]
    created_by: str
    created_at: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(self.graph_id, "graph_id")
        _validate_text(self.workspace_id, "workspace_id")
        if type(self.version) is not int or self.version < 1:
            raise OperationsRecordError("graph version must be a positive integer")
        if not isinstance(self.graph_descriptor, Mapping):
            raise OperationsRecordError("graph_descriptor must be mapping")
        _validate_text(self.created_by, "created_by")
        _validate_text(self.created_at, "created_at")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("graph metadata must be mapping")

    @classmethod
    def from_graph(
        cls,
        *,
        graph_id: str,
        workspace_id: str,
        version: int,
        graph: DeploymentGraph,
        created_by: str,
        created_at: str,
        metadata: Mapping[str, object] | None = None,
    ) -> "GraphVersionRecord":
        if not isinstance(graph, DeploymentGraph):
            raise OperationsRecordError("graph version requires DeploymentGraph")
        return cls(
            graph_id=graph_id,
            workspace_id=workspace_id,
            version=version,
            graph_descriptor=DEFAULT_GRAPH_CODEC.encode(graph),
            created_by=created_by,
            created_at=created_at,
            metadata={} if metadata is None else metadata,
        )


@dataclass(frozen=True)
class OperationSessionRecord:
    """Grouped operator intent before planning or execution."""

    session_id: str
    workspace_id: str
    actor_id: str
    title: str
    status: OperationSessionStatus
    created_at: str
    closed_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.session_id, "session_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.actor_id, "actor_id")
        _validate_text(self.title, "title")
        if not isinstance(self.status, OperationSessionStatus):
            raise OperationsRecordError(
                "operation session status must be OperationSessionStatus"
            )
        _validate_text(self.created_at, "created_at")
        _validate_optional_text(self.closed_at, "closed_at")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")
        if not isinstance(self.metadata, Mapping):
            raise OperationsRecordError("operation session metadata must be mapping")
        if self.status is OperationSessionStatus.OPEN and self.closed_at is not None:
            raise OperationsRecordError("open operation sessions must not have closed_at")
        if self.status is not OperationSessionStatus.OPEN and self.closed_at is None:
            raise OperationsRecordError(
                "terminal operation sessions require closed_at"
            )


@dataclass(frozen=True)
class OperationActionRecord:
    """One ordered operator action inside a session."""

    action_id: str
    session_id: str
    ordinal: int
    action_type: OperatorCommandKind | LifecycleOperationKind
    actor_id: str
    payload: Mapping[str, object] = field(default_factory=dict)
    created_at: str = ""
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.action_id, "action_id")
        _validate_text(self.session_id, "session_id")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise OperationsRecordError("operation action ordinal must be positive")
        if not isinstance(self.action_type, (OperatorCommandKind, LifecycleOperationKind)):
            raise OperationsRecordError(
                "operation action type must be a closed command or lifecycle kind"
            )
        _validate_text(self.actor_id, "actor_id")
        if not isinstance(self.payload, Mapping):
            raise OperationsRecordError("operation action payload must be mapping")
        _validate_text(self.created_at, "created_at")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")


@dataclass(frozen=True)
class ActivityPlanRecord:
    """Persisted, inspectable plan before execution."""

    plan_id: str
    session_id: str
    base_graph_id: str
    desired_graph_id: str
    status: ActivityPlanStatus
    created_at: str
    plan: ActivityPlan

    def __post_init__(self) -> None:
        _validate_text(self.plan_id, "plan_id")
        _validate_text(self.session_id, "session_id")
        _validate_text(self.base_graph_id, "base_graph_id")
        _validate_text(self.desired_graph_id, "desired_graph_id")
        if not isinstance(self.status, ActivityPlanStatus):
            raise OperationsRecordError("activity plan status must be ActivityPlanStatus")
        _validate_text(self.created_at, "created_at")
        if not isinstance(self.plan, ActivityPlan):
            raise OperationsRecordError("activity plan record requires ActivityPlan")


@dataclass(frozen=True)
class ApprovalRequestRecord:
    """Immutable request for authority over one persisted plan."""

    request_id: str
    session_id: str
    plan_id: str
    requested_by: str
    requested_at: str
    required_scope: PolicyScope
    max_risk: RiskLevel
    destructive: bool
    comment: str | None = None
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.request_id, "request_id")
        _validate_text(self.session_id, "session_id")
        _validate_text(self.plan_id, "plan_id")
        _validate_text(self.requested_by, "requested_by")
        _validate_text(self.requested_at, "requested_at")
        if not isinstance(self.required_scope, PolicyScope):
            raise OperationsRecordError("approval request scope must be PolicyScope")
        if not isinstance(self.max_risk, RiskLevel):
            raise OperationsRecordError("approval request max_risk must be RiskLevel")
        if type(self.destructive) is not bool:
            raise OperationsRecordError("approval request destructive must be bool")
        _validate_optional_text(self.comment, "comment")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")


@dataclass(frozen=True)
class ApprovalDecisionRecord:
    """Immutable answer to exactly one approval request."""

    decision_id: str
    request_id: str
    actor_id: str
    decision: ApprovalDecisionKind
    scope: PolicyScope
    decided_at: str
    comment: str | None = None
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.decision_id, "decision_id")
        _validate_text(self.request_id, "request_id")
        _validate_text(self.actor_id, "actor_id")
        if not isinstance(self.decision, ApprovalDecisionKind):
            raise OperationsRecordError("approval decision must be ApprovalDecisionKind")
        if not isinstance(self.scope, PolicyScope):
            raise OperationsRecordError("approval decision scope must be PolicyScope")
        _validate_text(self.decided_at, "decided_at")
        _validate_optional_text(self.comment, "comment")
        _validate_optional_text(self.idempotency_key, "idempotency_key")
        _validate_optional_text(self.intent_fingerprint, "intent_fingerprint")


@dataclass(frozen=True)
class ExecutionIdempotency:
    """Scoped retry identity plus conflict fingerprint for execution admission."""

    key: str
    intent_fingerprint: str

    def __post_init__(self) -> None:
        _validate_text(self.key, "execution idempotency key")
        _validate_text(self.intent_fingerprint, "execution intent_fingerprint")


@dataclass(frozen=True)
class ClaimIdentity:
    """Worker ownership and bounded lease evidence for a claimed request."""

    worker_id: str
    claimed_at: str
    lease_expires_at: str

    def __post_init__(self) -> None:
        _validate_text(self.worker_id, "worker_id")
        _validate_text(self.claimed_at, "claimed_at")
        _validate_text(self.lease_expires_at, "lease_expires_at")


@dataclass(frozen=True)
class ExecutionRequestIdentity:
    """Stable ownership coordinates for one execution request."""

    request_id: str
    workspace_id: str
    session_id: str
    plan_id: str

    def __post_init__(self) -> None:
        _validate_text(self.request_id, "request_id")
        _validate_text(self.workspace_id, "workspace_id")
        _validate_text(self.session_id, "session_id")
        _validate_text(self.plan_id, "plan_id")


@dataclass(frozen=True)
class ExecutionRequestRecord:
    """Durable admitted intent to execute one approved canonical plan."""

    identity: ExecutionRequestIdentity
    status: ExecutionRequestStatus
    requested_by: str
    requested_at: str
    approval_request_id: str
    approval_decision_id: str
    idempotency: ExecutionIdempotency
    claim: ClaimIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionRequestIdentity):
            raise OperationsRecordError("execution request identity must be typed")
        if not isinstance(self.status, ExecutionRequestStatus):
            raise OperationsRecordError(
                "execution request status must be ExecutionRequestStatus"
            )
        _validate_text(self.requested_by, "requested_by")
        _validate_text(self.requested_at, "requested_at")
        _validate_text(self.approval_request_id, "approval_request_id")
        _validate_text(self.approval_decision_id, "approval_decision_id")
        if not isinstance(self.idempotency, ExecutionIdempotency):
            raise OperationsRecordError("execution request idempotency must be typed")
        if self.status is ExecutionRequestStatus.CLAIMED:
            if not isinstance(self.claim, ClaimIdentity):
                raise OperationsRecordError(
                    "claimed execution request requires claim identity"
                )
        elif self.claim is not None:
            raise OperationsRecordError(
                "only a claimed execution request may carry a claim"
            )


def _validate_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise OperationsRecordError(f"{field} must be nonempty bounded text")
    if any(ord(character) < 32 for character in value):
        raise OperationsRecordError(f"{field} must not contain control characters")


def _validate_optional_text(value: str | None, field: str) -> None:
    if value is None:
        return
    _validate_text(value, field)
