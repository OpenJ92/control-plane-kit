"""Read-only projections over durable operations truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol

from control_plane_kit_core.planning import (
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ActivityImpact,
    ReviewChange,
    RiskLevel,
    plan_recovery_transition,
)
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorError,
    GraphDescriptorCodec,
    validate_graph,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRequestRecord,
    BoundedEvidence,
    FailureEvidence,
    GraphVersionRecord,
    ObservationFreshness,
    ObservationRecord,
    ObservationStaleReason,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)

_REDACTED = "<redacted>"
_SECRET_MARKERS = ("secret", "token", "password", "private_key", "credential", "api_key")
_ADDRESS_KEYS = ("address", "url", "environment", "env_assignments")


class ReadModelError(ValueError):
    """Raised when durable truth cannot support a requested read model."""


class WorkspaceStore(Protocol):
    def get(self, workspace_id: str) -> WorkspaceRecord: ...


class GraphTopologyStore(Protocol):
    def get(self, graph_id: str) -> GraphVersionRecord: ...


class ActivityHistoryStore(Protocol):
    def get_session(self, session_id: str) -> OperationSessionRecord: ...
    def sessions_for_workspace(self, workspace_id: str) -> tuple[OperationSessionRecord, ...]: ...
    def actions_for_session(self, session_id: str) -> tuple[object, ...]: ...
    def get_plan(self, plan_id: str) -> ActivityPlanRecord: ...
    def plans_for_session(self, session_id: str) -> tuple[ActivityPlanRecord, ...]: ...
    def get_approval_request(self, request_id: str) -> ApprovalRequestRecord: ...
    def approval_requests_for_session(self, session_id: str) -> tuple[ApprovalRequestRecord, ...]: ...
    def approval_decision_for_request(self, request_id: str) -> object | None: ...


class ExecutionStore(Protocol):
    def get_request(self, request_id: str) -> object: ...
    def runs_for_plan(self, plan_id: str) -> tuple[ActivityRunRecord, ...]: ...
    def events_for_run(self, run_id: str) -> tuple[ActivityEventRecord, ...]: ...


class ObservedStateStore(Protocol):
    def latest_for_workspace(self, workspace_id: str) -> tuple[ObservationRecord, ...]: ...


@dataclass(frozen=True)
class WorkspaceSummary:
    """Small workspace identity and lifecycle summary."""

    workspace_id: str
    name: str
    lifecycle: str
    current_graph_id: str | None
    desired_graph_id: str | None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "lifecycle": self.lifecycle,
            "current_graph_id": self.current_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "metadata": _redact_descriptor_value("metadata", self.metadata),
        }


@dataclass(frozen=True)
class GraphPointerReadModel:
    """Read model for a graph pointer that may not yet be assigned."""

    pointer: str
    assigned: bool
    graph_id: str | None = None
    version: int | None = None
    graph_name: str | None = None
    graph_descriptor: Mapping[str, object] | None = None
    operator_graph: Mapping[str, object] | None = None

    def descriptor(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "pointer": self.pointer,
            "assigned": self.assigned,
            "graph_id": self.graph_id,
            "version": self.version,
            "graph_name": self.graph_name,
        }
        if self.graph_descriptor is not None:
            payload["graph_descriptor"] = dict(self.graph_descriptor)
        if self.operator_graph is not None:
            payload["operator_graph"] = dict(self.operator_graph)
        return payload


@dataclass(frozen=True)
class WorkspaceReadModel:
    workspace: WorkspaceSummary
    current_graph: GraphPointerReadModel
    desired_graph: GraphPointerReadModel

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace": self.workspace.descriptor(),
            "current_graph": self.current_graph.descriptor(),
            "desired_graph": self.desired_graph.descriptor(),
        }


@dataclass(frozen=True)
class ActivityTimelineReadModel:
    workspace_id: str
    limit: int
    sessions: tuple[Mapping[str, object], ...]

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "limit": self.limit,
            "sessions": [dict(session) for session in self.sessions],
        }


@dataclass(frozen=True)
class FocusedCollectionReadModel:
    workspace_id: str
    kind: str
    limit: int
    offset: int
    total: int
    items: tuple[Mapping[str, object], ...]

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "limit": self.limit,
            "offset": self.offset,
            "total": self.total,
            "has_more": self.offset + len(self.items) < self.total,
            "items": [dict(item) for item in self.items],
        }


@dataclass(frozen=True)
class FocusedDetailReadModel:
    workspace_id: str
    kind: str
    payload: Mapping[str, object]

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            **dict(self.payload),
        }


@dataclass(frozen=True)
class ObservedStateReadModel:
    workspace_id: str
    observations: tuple[Mapping[str, object], ...]

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "observations": [dict(observation) for observation in self.observations],
        }


@dataclass(frozen=True)
class ControlSurfaceReadModel:
    workspace_id: str
    pointer: str
    assigned: bool
    graph_id: str | None = None
    graph_name: str | None = None
    nodes: tuple[Mapping[str, object], ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "pointer": self.pointer,
            "assigned": self.assigned,
            "graph_id": self.graph_id,
            "graph_name": self.graph_name,
            "nodes": [dict(node) for node in self.nodes],
        }


@dataclass(frozen=True)
class ObservationFreshnessPolicy:
    """Maximum age for evidence to describe the current graph."""

    maximum_age: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.maximum_age <= timedelta(0):
            raise ValueError("observation maximum age must be positive")


@dataclass(frozen=True)
class ProjectedObservation:
    """Observation interpreted at one explicit read instant."""

    record: ObservationRecord
    freshness: ObservationFreshness
    stale_reason: ObservationStaleReason | None


class InstanceReadService:
    """Compose canonical operations stores into read-only instance views."""

    def __init__(
        self,
        *,
        workspace_store: WorkspaceStore,
        graph_topology_store: GraphTopologyStore,
        activity_history_store: ActivityHistoryStore | None = None,
        execution_store: ExecutionStore | None = None,
        observed_state_store: ObservedStateStore | None = None,
        graph_codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
        clock=lambda: datetime.now(timezone.utc),
        observation_freshness: ObservationFreshnessPolicy = ObservationFreshnessPolicy(),
    ) -> None:
        self._workspace_store = workspace_store
        self._graph_topology_store = graph_topology_store
        self._activity_history_store = activity_history_store
        self._execution_store = execution_store
        self._observed_state_store = observed_state_store
        self._graph_codec = graph_codec
        self._clock = clock
        self._observation_freshness = observation_freshness

    def workspace(self, workspace_id: str) -> WorkspaceReadModel:
        workspace = self._workspace(workspace_id)
        return WorkspaceReadModel(
            workspace=_workspace_summary(workspace),
            current_graph=self.current_graph(workspace_id),
            desired_graph=self.desired_graph(workspace_id),
        )

    def current_graph(self, workspace_id: str) -> GraphPointerReadModel:
        workspace = self._workspace(workspace_id)
        return self._graph_pointer("current", workspace.current_graph_id)

    def desired_graph(self, workspace_id: str) -> GraphPointerReadModel:
        workspace = self._workspace(workspace_id)
        return self._graph_pointer("desired", workspace.desired_graph_id)

    def operator_graph(
        self,
        workspace_id: str,
        *,
        pointer: str = "current",
    ) -> GraphPointerReadModel:
        workspace = self._workspace(workspace_id)
        return self._graph_pointer(
            pointer,
            _graph_id_for_pointer(workspace, pointer),
            include_operator_graph=True,
        )

    def activity_timeline(
        self,
        workspace_id: str,
        *,
        limit: int = 50,
    ) -> ActivityTimelineReadModel:
        limit = _positive_limit(limit)
        self._workspace(workspace_id)
        store = self._activity_history()
        execution = self._execution()
        sessions = store.sessions_for_workspace(workspace_id)[:limit]
        return ActivityTimelineReadModel(
            workspace_id=workspace_id,
            limit=limit,
            sessions=tuple(
                _session_descriptor(store, execution, session, limit=limit)
                for session in sessions
            ),
        )

    def open_sessions(
        self,
        workspace_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> FocusedCollectionReadModel:
        limit, offset = _page(limit, offset)
        self._workspace(workspace_id)
        sessions = tuple(
            session
            for session in self._activity_history().sessions_for_workspace(workspace_id)
            if session.status is OperationSessionStatus.OPEN
        )
        return FocusedCollectionReadModel(
            workspace_id=workspace_id,
            kind="open-sessions",
            limit=limit,
            offset=offset,
            total=len(sessions),
            items=tuple(
                _session_summary_descriptor(session)
                for session in sessions[offset : offset + limit]
            ),
        )

    def session_detail(
        self,
        workspace_id: str,
        session_id: str,
        *,
        limit: int = 50,
    ) -> FocusedDetailReadModel:
        limit = _bounded_limit(limit)
        self._workspace(workspace_id)
        store = self._activity_history()
        session = _session_in_workspace(store, workspace_id, session_id)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="session-detail",
            payload={
                "session": _session_descriptor(
                    store,
                    self._execution(),
                    session,
                    limit=limit,
                )
            },
        )

    def plan_detail(
        self,
        workspace_id: str,
        plan_id: str,
        *,
        limit: int = 50,
    ) -> FocusedDetailReadModel:
        limit = _bounded_limit(limit)
        self._workspace(workspace_id)
        store = self._activity_history()
        plan = _plan_in_workspace(store, workspace_id, plan_id)
        payload = _plan_descriptor(
            store,
            self._execution(),
            plan,
            workspace_id=workspace_id,
            limit=limit,
        )
        payload["risk_summary"] = _risk_summary(plan)
        payload["recovery"] = self._recovery_for_plan(workspace_id, plan)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="plan-detail",
            payload={"plan": payload},
        )

    def pending_approvals(
        self,
        workspace_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> FocusedCollectionReadModel:
        limit, offset = _page(limit, offset)
        self._workspace(workspace_id)
        store = self._activity_history()
        pending: list[ApprovalRequestRecord] = []
        for session in store.sessions_for_workspace(workspace_id):
            pending.extend(
                request
                for request in store.approval_requests_for_session(session.session_id)
                if store.approval_decision_for_request(request.request_id) is None
            )
        pending.sort(key=lambda value: (value.requested_at, value.request_id))
        return FocusedCollectionReadModel(
            workspace_id=workspace_id,
            kind="pending-approvals",
            limit=limit,
            offset=offset,
            total=len(pending),
            items=tuple(
                _approval_descriptor(store, request)
                for request in pending[offset : offset + limit]
            ),
        )

    def approval_detail(
        self,
        workspace_id: str,
        approval_request_id: str,
        *,
        limit: int = 50,
    ) -> FocusedDetailReadModel:
        limit = _bounded_limit(limit)
        self._workspace(workspace_id)
        store = self._activity_history()
        approval = _approval_in_workspace(store, workspace_id, approval_request_id)
        plan = _plan_in_workspace(store, workspace_id, approval.plan_id)
        if plan.session_id != approval.session_id:
            raise ReadModelError(
                f"approval {approval_request_id!r} references plan truth outside its session"
            )
        payload = _plan_descriptor(
            store,
            self._execution(),
            plan,
            workspace_id=workspace_id,
            limit=limit,
        )
        payload["risk_summary"] = _risk_summary(plan)
        payload["recovery"] = self._recovery_for_plan(workspace_id, plan)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="approval-detail",
            payload={
                "approval": _approval_descriptor(store, approval),
                "plan": payload,
            },
        )

    def observed_state(self, workspace_id: str) -> ObservedStateReadModel:
        workspace = self._workspace(workspace_id)
        as_of = self._clock()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ReadModelError("read-service clock must return a timezone-aware datetime")
        observations = tuple(
            _observation_descriptor(
                project_observation(
                    record,
                    current_graph_id=workspace.current_graph_id,
                    as_of=as_of,
                    policy=self._observation_freshness,
                )
            )
            for record in self._observed_state().latest_for_workspace(workspace_id)
        )
        return ObservedStateReadModel(workspace_id=workspace_id, observations=observations)

    def control_surface(
        self,
        workspace_id: str,
        *,
        pointer: str = "current",
    ) -> ControlSurfaceReadModel:
        workspace = self._workspace(workspace_id)
        graph_id = _graph_id_for_pointer(workspace, pointer)
        if graph_id is None:
            return ControlSurfaceReadModel(workspace_id, pointer, False)
        record = self._graph_topology_store.get(graph_id)
        descriptor = _redact_graph_descriptor(record.graph_descriptor)
        nodes = _mapping(descriptor.get("nodes", {}))
        return ControlSurfaceReadModel(
            workspace_id=workspace_id,
            pointer=pointer,
            assigned=True,
            graph_id=record.graph_id,
            graph_name=str(record.graph_descriptor.get("name", record.graph_id)),
            nodes=tuple(
                _node_control_surface(str(node_id), _mapping(node_descriptor))
                for node_id, node_descriptor in sorted(nodes.items())
            ),
        )

    def _workspace(self, workspace_id: str) -> WorkspaceRecord:
        try:
            return self._workspace_store.get(workspace_id)
        except KeyError as exc:
            raise ReadModelError(f"missing workspace {workspace_id!r}") from exc

    def _activity_history(self) -> ActivityHistoryStore:
        if self._activity_history_store is None:
            raise ReadModelError("activity history store is not configured")
        return self._activity_history_store

    def _execution(self) -> ExecutionStore:
        if self._execution_store is None:
            raise ReadModelError("execution store is not configured")
        return self._execution_store

    def _observed_state(self) -> ObservedStateStore:
        if self._observed_state_store is None:
            raise ReadModelError("observed state store is not configured")
        return self._observed_state_store

    def _graph_pointer(
        self,
        pointer: str,
        graph_id: str | None,
        *,
        include_operator_graph: bool = False,
    ) -> GraphPointerReadModel:
        if graph_id is None:
            return GraphPointerReadModel(pointer=pointer, assigned=False)
        record = self._graph_topology_store.get(graph_id)
        operator_graph: Mapping[str, object] | None = None
        if include_operator_graph:
            try:
                graph = self._graph_codec.decode(record.graph_descriptor)
            except GraphDescriptorError as exc:
                raise ReadModelError(f"invalid stored graph descriptor: {exc}") from exc
            operator_graph = _operator_graph_descriptor(graph)
        return _graph_pointer_read_model(
            pointer,
            record,
            operator_graph=operator_graph,
        )

    def _recovery_for_plan(
        self,
        workspace_id: str,
        plan: ActivityPlanRecord,
    ) -> Mapping[str, object]:
        try:
            base = self._graph_topology_store.get(plan.base_graph_id)
            desired = self._graph_topology_store.get(plan.desired_graph_id)
        except KeyError as exc:
            raise ReadModelError(
                f"plan {plan.plan_id!r} references missing graph truth"
            ) from exc
        if base.workspace_id != workspace_id or desired.workspace_id != workspace_id:
            raise ReadModelError(
                f"plan {plan.plan_id!r} references graph truth outside workspace"
            )
        try:
            target = validate_graph(self._graph_codec.decode(base.graph_descriptor))
            current = validate_graph(self._graph_codec.decode(desired.graph_descriptor))
            candidate = plan_recovery_transition(current, target)
        except (GraphDescriptorError, ValueError, TypeError) as exc:
            raise ReadModelError(
                f"plan {plan.plan_id!r} has invalid recovery graph truth"
            ) from exc
        return candidate.descriptor()


def project_observation(
    record: ObservationRecord,
    *,
    current_graph_id: str | None,
    as_of: datetime,
    policy: ObservationFreshnessPolicy,
) -> ProjectedObservation:
    """Derive usability without rewriting durable observation evidence."""

    if as_of.tzinfo is None:
        raise ValueError("observation projection clock must be timezone-aware")
    if record.freshness is ObservationFreshness.STALE:
        return _stale(record, ObservationStaleReason.RECORDED_STALE)
    if record.graph_id is None:
        return _stale(record, ObservationStaleReason.UNCORRELATED)
    if current_graph_id != record.graph_id:
        return _stale(record, ObservationStaleReason.GRAPH_CHANGED)
    try:
        observed_at = datetime.fromisoformat(record.observed_at.replace("Z", "+00:00"))
    except ValueError:
        return _stale(record, ObservationStaleReason.MALFORMED_TIMESTAMP)
    if observed_at.tzinfo is None:
        return _stale(record, ObservationStaleReason.MALFORMED_TIMESTAMP)
    normalized_as_of = as_of.astimezone(timezone.utc)
    normalized_observed_at = observed_at.astimezone(timezone.utc)
    if normalized_observed_at > normalized_as_of:
        return _stale(record, ObservationStaleReason.FUTURE_TIMESTAMP)
    if normalized_as_of - normalized_observed_at > policy.maximum_age:
        return _stale(record, ObservationStaleReason.EXPIRED)
    return ProjectedObservation(record, ObservationFreshness.FRESH, None)


def _stale(
    record: ObservationRecord,
    reason: ObservationStaleReason,
) -> ProjectedObservation:
    return ProjectedObservation(record, ObservationFreshness.STALE, reason)


def _workspace_summary(record: WorkspaceRecord) -> WorkspaceSummary:
    return WorkspaceSummary(
        workspace_id=record.workspace_id,
        name=record.name,
        lifecycle=record.lifecycle.value,
        current_graph_id=record.current_graph_id,
        desired_graph_id=record.desired_graph_id,
        metadata=record.metadata,
    )


def _graph_id_for_pointer(workspace: WorkspaceRecord, pointer: str) -> str | None:
    if pointer == "current":
        return workspace.current_graph_id
    if pointer == "desired":
        return workspace.desired_graph_id
    raise ReadModelError(f"unknown graph pointer {pointer!r}")


def _graph_pointer_read_model(
    pointer: str,
    record: GraphVersionRecord,
    *,
    operator_graph: Mapping[str, object] | None,
) -> GraphPointerReadModel:
    return GraphPointerReadModel(
        pointer=pointer,
        assigned=True,
        graph_id=record.graph_id,
        version=record.version,
        graph_name=str(record.graph_descriptor.get("name", record.graph_id)),
        graph_descriptor=_redact_graph_descriptor(record.graph_descriptor),
        operator_graph=operator_graph,
    )


def _operator_graph_descriptor(graph: object) -> dict[str, object]:
    nodes = []
    connected_requirements = {
        (edge.consumer_role, edge.requirement_socket)
        for edge in graph.edges.values()
    }
    connected_providers = {
        (edge.provider_role, edge.provider_socket)
        for edge in graph.edges.values()
    }
    for _, node in sorted(graph.nodes.items()):
        nodes.append(
            {
                "node_id": node.node_id,
                "kind": node.kind,
                "runtime_id": node.runtime_id,
                "display_name": str(node.metadata.get("display_name", node.node_id)),
                "providers": [
                    {
                        "name": socket.name,
                        "protocol": {
                            "transport": socket.protocol.transport.value,
                            "application": socket.protocol.application.value,
                        },
                        "direction": "provider",
                        "connected": (node.node_id, socket.name) in connected_providers,
                    }
                    for socket in sorted(
                        node.sockets.providers,
                        key=lambda candidate: candidate.name,
                    )
                ],
                "requirements": [
                    {
                        "name": socket.name,
                        "protocol": {
                            "transport": socket.protocol.transport.value,
                            "application": socket.protocol.application.value,
                        },
                        "direction": "requirement",
                        "binding": socket.binding.value,
                        "required": socket.required,
                        "env_bindings": list(socket.env_bindings),
                        "connected": (node.node_id, socket.name) in connected_requirements,
                    }
                    for socket in sorted(
                        node.sockets.requirements,
                        key=lambda candidate: candidate.name,
                    )
                ],
                "metadata": _redact_descriptor_value("metadata", node.metadata),
            }
        )
    return {
        "name": graph.name,
        "runtimes": [
            {
                "runtime_id": runtime.runtime_id,
                "kind": runtime.kind.value,
                "children": sorted(runtime.children),
                "metadata": _redact_descriptor_value("metadata", runtime.metadata),
            }
            for _, runtime in sorted(graph.runtimes.items())
        ],
        "nodes": nodes,
        "edges": [
            {
                "edge_id": edge.edge_id,
                "provider": {
                    "node_id": edge.provider_role,
                    "socket": edge.provider_socket,
                },
                "consumer": {
                    "node_id": edge.consumer_role,
                    "socket": edge.requirement_socket,
                },
                "protocol": {
                    "transport": edge.protocol.transport.value,
                    "application": edge.protocol.application.value,
                },
            }
            for _, edge in sorted(graph.edges.items())
        ],
    }


def _node_control_surface(node_id: str, descriptor: Mapping[str, object]) -> dict[str, object]:
    metadata = _mapping(descriptor.get("metadata", {}))
    return {
        "node_id": node_id,
        "display_name": str(metadata.get("display_name", node_id)),
        "kind": str(descriptor["kind"]),
        "runtime_id": str(descriptor["runtime_id"]),
        "capabilities": _list(metadata.get("capabilities", ())),
        "providers": dict(_mapping(descriptor.get("providers", {}))),
        "requirements": dict(_mapping(descriptor.get("requirements", {}))),
        "metadata": {
            str(key): value
            for key, value in sorted(metadata.items())
            if str(key) != "capabilities"
        },
        "warnings": [],
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReadModelError("expected mapping in graph descriptor")
    return value


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _session_summary_descriptor(session: OperationSessionRecord) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "workspace_id": session.workspace_id,
        "actor_id": session.actor_id,
        "title": session.title,
        "status": session.status.value,
        "created_at": session.created_at,
        "closed_at": session.closed_at,
        "metadata": _redact_descriptor_value("metadata", session.metadata),
    }


def _session_descriptor(
    store: ActivityHistoryStore,
    execution: ExecutionStore,
    session: OperationSessionRecord,
    *,
    limit: int,
) -> dict[str, object]:
    session_id = session.session_id
    return {
        **_session_summary_descriptor(session),
        "actions": [
            _action_descriptor(action)
            for action in store.actions_for_session(session_id)[:limit]
        ],
        "approvals": [
            _approval_descriptor(store, approval)
            for approval in store.approval_requests_for_session(session_id)[:limit]
        ],
        "plans": [
            _plan_descriptor(
                store,
                execution,
                plan,
                workspace_id=session.workspace_id,
                limit=limit,
            )
            for plan in store.plans_for_session(session_id)[:limit]
        ],
    }


def _action_descriptor(action: object) -> dict[str, object]:
    return {
        "action_id": getattr(action, "action_id"),
        "session_id": getattr(action, "session_id"),
        "ordinal": getattr(action, "ordinal"),
        "action_type": getattr(action, "action_type").value,
        "actor_id": getattr(action, "actor_id"),
        "payload": _redact_descriptor_value("payload", getattr(action, "payload")),
        "created_at": getattr(action, "created_at"),
    }


def _approval_descriptor(
    store: ActivityHistoryStore,
    approval: ApprovalRequestRecord,
) -> dict[str, object]:
    decision = store.approval_decision_for_request(approval.request_id)
    return {
        "request_id": approval.request_id,
        "session_id": approval.session_id,
        "plan_id": approval.plan_id,
        "requested_by": approval.requested_by,
        "requested_at": approval.requested_at,
        "required_scope": approval.required_scope.value,
        "max_risk": approval.max_risk.value,
        "destructive": approval.destructive,
        "comment": approval.comment,
        "state": "pending" if decision is None else getattr(decision, "decision").value,
        "decision": None if decision is None else {
            "decision_id": getattr(decision, "decision_id"),
            "actor_id": getattr(decision, "actor_id"),
            "decision": getattr(decision, "decision").value,
            "scope": getattr(decision, "scope").value,
            "decided_at": getattr(decision, "decided_at"),
            "comment": getattr(decision, "comment"),
        },
    }


def _plan_descriptor(
    store: ActivityHistoryStore,
    execution: ExecutionStore,
    plan: ActivityPlanRecord,
    *,
    workspace_id: str,
    limit: int,
) -> dict[str, object]:
    plan_id = plan.plan_id
    return {
        "plan_id": plan_id,
        "session_id": plan.session_id,
        "base_graph_id": plan.base_graph_id,
        "desired_graph_id": plan.desired_graph_id,
        "status": plan.status.value,
        "created_at": plan.created_at,
        "payload": DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan.plan),
        "runs": [
            _run_descriptor(execution, plan, run, workspace_id=workspace_id, limit=limit)
            for run in execution.runs_for_plan(plan_id)[:limit]
        ],
    }


def _run_descriptor(
    store: ExecutionStore,
    plan: ActivityPlanRecord,
    run: ActivityRunRecord,
    *,
    workspace_id: str,
    limit: int,
) -> dict[str, object]:
    try:
        request = store.get_request(run.admission.request_id)
    except KeyError as exc:
        raise ReadModelError(
            f"run {run.run_id!r} references missing execution request"
        ) from exc
    identity = getattr(request, "identity")
    if (
        identity.workspace_id != workspace_id
        or identity.session_id != plan.session_id
        or identity.plan_id != plan.plan_id
    ):
        raise ReadModelError(
            f"run {run.run_id!r} references execution truth outside its plan workspace"
        )
    return {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "request_id": run.admission.request_id,
        "attempt": run.retry.attempt,
        "prior_run_id": run.retry.prior_run_id,
        "status": run.status.value,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "settled_at": run.settled_at,
        "metadata": _redact_descriptor_value("metadata", run.metadata.descriptor()),
        "events": [
            _event_descriptor(event)
            for event in store.events_for_run(run.run_id)[:limit]
        ],
    }


def _event_descriptor(event: ActivityEventRecord) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "ordinal": event.ordinal,
        "event_type": event.kind.value,
        "occurred_at": event.occurred_at,
        "activity_id": event.activity_id,
        "payload": _redact_descriptor_value("payload", event.evidence.descriptor()),
        "failure": _failure_descriptor(event.failure),
    }


def _failure_descriptor(failure: FailureEvidence | None) -> dict[str, object] | None:
    if failure is None:
        return None
    return {
        "category": failure.category.value,
        "code": failure.code,
        "message": failure.message,
        "details": _redact_descriptor_value("details", failure.details.descriptor()),
    }


def _observation_descriptor(projected: ProjectedObservation) -> dict[str, object]:
    record = projected.record
    return {
        "observation_id": record.observation_id,
        "workspace_id": record.workspace_id,
        "subject_id": record.subject_id,
        "status": record.status.value,
        "observed_at": record.observed_at,
        "graph_id": record.graph_id,
        "probe_kind": None if record.probe_kind is None else record.probe_kind.value,
        "probe_outcome": (
            None if record.probe_outcome is None else record.probe_outcome.value
        ),
        "endpoint_context": (
            None if record.endpoint_context is None else record.endpoint_context.value
        ),
        "freshness": projected.freshness.value,
        "stale": projected.freshness is ObservationFreshness.STALE,
        "stale_reason": (
            None if projected.stale_reason is None else projected.stale_reason.value
        ),
        "payload": _redact_descriptor_value("payload", record.evidence.descriptor()),
    }


def _positive_limit(limit: int) -> int:
    if type(limit) is not int or limit < 1:
        raise ReadModelError(f"limit must be positive, got {limit}")
    return limit


def _bounded_limit(limit: int) -> int:
    limit = _positive_limit(limit)
    if limit > 100:
        raise ReadModelError(f"limit must not exceed 100, got {limit}")
    return limit


def _page(limit: int, offset: int) -> tuple[int, int]:
    limit = _bounded_limit(limit)
    if type(offset) is not int or offset < 0:
        raise ReadModelError(f"offset must be non-negative, got {offset}")
    return limit, offset


def _session_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    session_id: str,
) -> OperationSessionRecord:
    try:
        session = store.get_session(session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing session {session_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing session {session_id!r} in workspace {workspace_id!r}"
        )
    return session


def _plan_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    plan_id: str,
) -> ActivityPlanRecord:
    try:
        plan = store.get_plan(plan_id)
        session = store.get_session(plan.session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing plan {plan_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing plan {plan_id!r} in workspace {workspace_id!r}"
        )
    return plan


def _approval_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    approval_request_id: str,
) -> ApprovalRequestRecord:
    try:
        approval = store.get_approval_request(approval_request_id)
        session = store.get_session(approval.session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing approval {approval_request_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing approval {approval_request_id!r} in workspace {workspace_id!r}"
        )
    return approval


def _risk_summary(plan: ActivityPlanRecord) -> dict[str, object]:
    counts = {risk.value: 0 for risk in RiskLevel}
    for activity in plan.plan.activities:
        counts[activity.risk.value] += 1
    max_risk = max(
        (activity.risk for activity in plan.plan.activities),
        key=_risk_rank,
        default=RiskLevel.INFORMATIONAL,
    )
    return {
        "max_risk": max_risk.value,
        "counts": counts,
        "destructive_count": sum(
            activity.impact is ActivityImpact.DESTRUCTIVE
            for activity in plan.plan.activities
        ),
        "review_blocker_count": sum(
            isinstance(activity.operation, ReviewChange)
            for activity in plan.plan.activities
        ),
        "ready_for_execution": plan.plan.ready_for_execution,
    }


def _risk_rank(risk: RiskLevel) -> int:
    return tuple(RiskLevel).index(risk)


def _redact_graph_descriptor(descriptor: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _redact_descriptor_value(str(key), value)
        for key, value in sorted(descriptor.items())
    }


def _redact_descriptor_value(key: str, value: object) -> object:
    if key.lower().replace("-", "_") == "environment_bindings":
        return _redact_environment_bindings(value)
    if _looks_sensitive_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_descriptor_value(str(child_key), child_value)
            for child_key, child_value in sorted(value.items())
        }
    if isinstance(value, list):
        return [_redact_descriptor_value(key, child) for child in value]
    if isinstance(value, tuple):
        return tuple(_redact_descriptor_value(key, child) for child in value)
    return value


def _redact_environment_bindings(value: object) -> object:
    if not isinstance(value, (list, tuple)):
        return _REDACTED
    redacted: list[object] = []
    for binding in value:
        if not isinstance(binding, Mapping):
            redacted.append(_REDACTED)
            continue
        redacted.append(
            {
                str(child_key): (
                    _REDACTED
                    if str(child_key) in {"value", "reference", "reference_id"}
                    else _redact_descriptor_value(str(child_key), child_value)
                )
                for child_key, child_value in sorted(binding.items())
            }
        )
    return redacted


def _looks_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _ADDRESS_KEYS
        or ("." not in normalized and normalized.endswith("_url"))
        or any(marker in normalized for marker in _SECRET_MARKERS)
    )
