"""Durable record shapes for source-of-truth stores."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from control_plane_kit.activity_plan import ActivityPlan
from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.graph_codec import DEFAULT_GRAPH_CODEC


class WorkspaceLifecycle(StrEnum):
    """Lifecycle states shared by early workspace and instance records."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ARCHIVED = "archived"
    DECONSTRUCTED = "deconstructed"
    DELETED = "deleted"
    FAILED = "failed"


class OperationSessionStatus(StrEnum):
    """Closed lifecycle vocabulary for grouped operator intent."""

    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class OperationActionKind(StrEnum):
    """Closed vocabulary for durable operator-workflow actions."""

    SESSION_STARTED = "session_started"
    SESSION_CLOSED = "session_closed"
    SESSION_CANCELLED = "session_cancelled"
    ADD_BLOCK = "add_block"
    CONNECT_SOCKET = "connect_socket"
    PATCH_VARIABLE = "patch_variable"
    CHECK_HEALTH = "check_health"
    INSPECT_CONTROL_SURFACE = "inspect_control_surface"
    PROPOSE_DESIRED_GRAPH = "propose_desired_graph"
    REQUEST_GRAPH_EDIT = "request_graph_edit"
    SET_DESIRED_GRAPH = "set_desired_graph"
    PLAN_REQUESTED = "plan_requested"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    RECOVERY_REQUESTED = "recovery_requested"


@dataclass(frozen=True)
class GraphVersionRecord:
    """One named topology version owned by the graph store."""

    graph_id: str
    workspace_id: str
    version: int
    graph_descriptor: Mapping[str, object]
    created_by: str
    created_at: str
    metadata: Mapping[str, str] = field(default_factory=dict)

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
        metadata: Mapping[str, str] | None = None,
    ) -> "GraphVersionRecord":
        """Build a persisted graph-version record from a pure graph value."""

        return cls(
            graph_id=graph_id,
            workspace_id=workspace_id,
            version=version,
            graph_descriptor=DEFAULT_GRAPH_CODEC.encode(graph),
            created_by=created_by,
            created_at=created_at,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class WorkspaceRecord:
    """Workspace truth owned by the workspace store."""

    workspace_id: str
    name: str
    lifecycle: WorkspaceLifecycle = WorkspaceLifecycle.CREATED
    current_graph_id: str | None = None
    desired_graph_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


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
    metadata: Mapping[str, str] = field(default_factory=dict)
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, OperationSessionStatus):
            raise TypeError("operation session status must be OperationSessionStatus")


@dataclass(frozen=True)
class OperationActionRecord:
    """One ordered operator action inside a session."""

    action_id: str
    session_id: str
    ordinal: int
    action_type: OperationActionKind
    actor_id: str
    payload: Mapping[str, object] = field(default_factory=dict)
    created_at: str = ""
    idempotency_key: str | None = None
    intent_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, OperationActionKind):
            raise TypeError("operation action type must be OperationActionKind")


@dataclass(frozen=True)
class ApprovalRecord:
    """Approval or rejection for a consequential plan or action."""

    approval_id: str
    session_id: str
    target_id: str
    actor_id: str
    decision: str
    scope: str
    decided_at: str
    comment: str | None = None


@dataclass(frozen=True)
class ActivityPlanRecord:
    """Persisted, inspectable plan before execution."""

    plan_id: str
    session_id: str
    base_graph_id: str
    desired_graph_id: str
    status: str
    created_at: str
    plan: ActivityPlan

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ActivityPlan):
            raise TypeError("activity plan record requires a typed ActivityPlan")


@dataclass(frozen=True)
class ActivityRunRecord:
    """Execution attempt for an approved activity plan."""

    run_id: str
    plan_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityEventRecord:
    """Structured operational memory for an activity run."""

    event_id: str
    run_id: str
    ordinal: int
    event_type: str
    occurred_at: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservationRecord:
    """Observed runtime evidence, separate from desired topology."""

    observation_id: str
    workspace_id: str
    subject_id: str
    status: str
    observed_at: str
    payload: Mapping[str, object] = field(default_factory=dict)
    stale: bool = False


@dataclass(frozen=True)
class InstanceRecord:
    """Hub-visible control-plane instance metadata."""

    instance_id: str
    owner_id: str
    lifecycle: WorkspaceLifecycle
    endpoint: str | None = None
    wake_hint: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SecretReferenceRecord:
    """A secret reference without the secret value."""

    secret_ref: str
    owner_id: str
    purpose: str
    assigned_at: str
    metadata: Mapping[str, str] = field(default_factory=dict)
