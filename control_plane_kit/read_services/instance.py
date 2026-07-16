"""Read-only service for one control-plane instance workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.planning.activity_plan import ActivityImpact, ReviewChange, RiskLevel
from control_plane_kit.planning.codec import DEFAULT_ACTIVITY_PLAN_CODEC
from control_plane_kit.control_routes import route_set_named
from control_plane_kit.topology.codec import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorCodec,
    GraphDescriptorError,
)
from control_plane_kit.projections import project_operator_graph
from control_plane_kit.planning.recovery import plan_recovery_transition
from control_plane_kit.stores.protocols import (
    ActivityHistoryStore,
    GraphTopologyStore,
    ObservedStateStore,
    WorkspaceStore,
)
from control_plane_kit.execution import ObservationRecord
from control_plane_kit.stores.records import (
    ActivityPlanRecord,
    GraphVersionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from control_plane_kit.topology.validation import GraphValidationError, validate_graph
from control_plane_kit.types import EndpointScope, Protocol, RuntimeKind

_REDACTED = "<redacted>"
_SECRET_MARKERS = ("secret", "token", "password", "private_key", "credential", "api_key")
_ADDRESS_KEYS = ("address", "url", "environment", "env_assignments")


class ReadModelError(ValueError):
    """Raised when durable truth cannot support a requested read model."""


@dataclass(frozen=True)
class WorkspaceSummary:
    """Small workspace identity and lifecycle summary."""

    workspace_id: str
    name: str
    lifecycle: str
    current_graph_id: str | None
    desired_graph_id: str | None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "lifecycle": self.lifecycle,
            "current_graph_id": self.current_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "metadata": dict(sorted(self.metadata.items())),
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
    """Top-level workspace read model for one control-plane instance."""

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
    """Bounded activity-history summary for a workspace."""

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
    """Bounded, offset-addressable focused workflow collection."""

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
    """Named focused workflow detail with one canonical payload."""

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
    """Latest observed state by subject for a workspace."""

    workspace_id: str
    observations: tuple[Mapping[str, object], ...]

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "observations": [dict(observation) for observation in self.observations],
        }


@dataclass(frozen=True)
class NodeControlSurfaceReadModel:
    """Operator-facing declared control surface for one graph node."""

    node_id: str
    display_name: str
    kind: str
    runtime_id: str
    capabilities: tuple[Mapping[str, object], ...]
    control_route_sets: tuple[Mapping[str, object], ...]
    providers: Mapping[str, object]
    requirements: Mapping[str, object]
    metadata: Mapping[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "runtime_id": self.runtime_id,
            "capabilities": [dict(capability) for capability in self.capabilities],
            "control_route_sets": [dict(route_set) for route_set in self.control_route_sets],
            "providers": dict(self.providers),
            "requirements": dict(self.requirements),
            "metadata": dict(self.metadata),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ControlSurfaceReadModel:
    """Declared capability, control-route, and socket view for a graph pointer."""

    workspace_id: str
    pointer: str
    assigned: bool
    graph_id: str | None = None
    graph_name: str | None = None
    nodes: tuple[NodeControlSurfaceReadModel, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "pointer": self.pointer,
            "assigned": self.assigned,
            "graph_id": self.graph_id,
            "graph_name": self.graph_name,
            "nodes": [node.descriptor() for node in self.nodes],
        }


class InstanceReadService:
    """Composes source-of-truth stores into read-only instance views."""

    def __init__(
        self,
        *,
        workspace_store: WorkspaceStore,
        graph_topology_store: GraphTopologyStore,
        activity_history_store: ActivityHistoryStore | None = None,
        observed_state_store: ObservedStateStore | None = None,
        graph_codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
    ) -> None:
        self._workspace_store = workspace_store
        self._graph_topology_store = graph_topology_store
        self._activity_history_store = activity_history_store
        self._observed_state_store = observed_state_store
        self._graph_codec = graph_codec

    def workspace(self, workspace_id: str) -> WorkspaceReadModel:
        """Return the workspace summary and graph pointer read models."""

        workspace = self._workspace(workspace_id)
        return WorkspaceReadModel(
            workspace=_workspace_summary(workspace),
            current_graph=self.current_graph(workspace_id),
            desired_graph=self.desired_graph(workspace_id),
        )

    def current_graph(self, workspace_id: str) -> GraphPointerReadModel:
        """Return the current graph pointer read model."""

        workspace = self._workspace(workspace_id)
        return self._graph_pointer("current", workspace.current_graph_id)

    def desired_graph(self, workspace_id: str) -> GraphPointerReadModel:
        """Return the desired graph pointer read model."""

        workspace = self._workspace(workspace_id)
        return self._graph_pointer("desired", workspace.desired_graph_id)

    def operator_graph(self, workspace_id: str, *, pointer: str = "current") -> GraphPointerReadModel:
        """Return a graph pointer read model with operator projection included."""

        if pointer == "current":
            workspace = self._workspace(workspace_id)
            return self._graph_pointer("current", workspace.current_graph_id, include_operator_graph=True)
        if pointer == "desired":
            workspace = self._workspace(workspace_id)
            return self._graph_pointer("desired", workspace.desired_graph_id, include_operator_graph=True)
        raise ReadModelError(f"unknown graph pointer {pointer!r}")

    def activity_timeline(self, workspace_id: str, *, limit: int = 50) -> ActivityTimelineReadModel:
        """Return a bounded activity timeline for one workspace."""

        limit = _positive_limit(limit)
        self._workspace(workspace_id)
        store = self._activity_history()
        sessions = store.sessions_for_workspace(workspace_id)[:limit]
        return ActivityTimelineReadModel(
            workspace_id=workspace_id,
            limit=limit,
            sessions=tuple(_session_descriptor(store, session, limit=limit) for session in sessions),
        )

    def open_sessions(
        self,
        workspace_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> FocusedCollectionReadModel:
        """Return deterministic summaries for open operation sessions."""

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
        """Return one bounded session projection inside a workspace boundary."""

        limit = _bounded_limit(limit)
        self._workspace(workspace_id)
        store = self._activity_history()
        session = _session_in_workspace(store, workspace_id, session_id)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="session-detail",
            payload={"session": _session_descriptor(store, session, limit=limit)},
        )

    def plan_detail(
        self,
        workspace_id: str,
        plan_id: str,
        *,
        limit: int = 50,
    ) -> FocusedDetailReadModel:
        """Return one canonical plan with risk and recovery projections."""

        limit = _bounded_limit(limit)
        self._workspace(workspace_id)
        store = self._activity_history()
        plan = _plan_in_workspace(store, workspace_id, plan_id)
        payload = _plan_descriptor(store, plan, limit=limit)
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
        """Return pending approval requests without exposing decision commands."""

        limit, offset = _page(limit, offset)
        self._workspace(workspace_id)
        store = self._activity_history()
        pending = []
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

    def observed_state(self, workspace_id: str) -> ObservedStateReadModel:
        """Return latest observed state per subject for one workspace."""

        self._workspace(workspace_id)
        observations = tuple(
            _observation_descriptor(record)
            for record in self._observed_state().latest_for_workspace(workspace_id)
        )
        return ObservedStateReadModel(workspace_id=workspace_id, observations=observations)

    def control_surface(self, workspace_id: str, *, pointer: str = "current") -> ControlSurfaceReadModel:
        """Return declared capabilities, control routes, and socket contracts."""

        workspace = self._workspace(workspace_id)
        graph_id = _graph_id_for_pointer(workspace, pointer)
        if graph_id is None:
            return ControlSurfaceReadModel(workspace_id=workspace_id, pointer=pointer, assigned=False)
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
            operator_graph = project_operator_graph(graph).descriptor()
        return _graph_pointer_read_model(pointer, record, operator_graph=operator_graph)

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
            target = validate_graph(
                self._graph_codec.decode(base.graph_descriptor),
                codec=self._graph_codec,
            )
            current = validate_graph(
                self._graph_codec.decode(desired.graph_descriptor),
                codec=self._graph_codec,
            )
            candidate = plan_recovery_transition(current, target)
        except (GraphDescriptorError, GraphValidationError) as exc:
            raise ReadModelError(
                f"plan {plan.plan_id!r} has invalid recovery graph truth"
            ) from exc
        return candidate.descriptor()


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


def _node_control_surface(node_id: str, descriptor: Mapping[str, object]) -> NodeControlSurfaceReadModel:
    metadata = _mapping(descriptor.get("metadata", {}))
    capabilities = tuple(_capability_descriptor(value) for value in _list(metadata.get("capabilities", ())))
    route_sets, warnings = _route_sets_for_capabilities(capabilities)
    return NodeControlSurfaceReadModel(
        node_id=node_id,
        display_name=str(metadata.get("display_name", node_id)),
        kind=str(descriptor["kind"]),
        runtime_id=str(descriptor["runtime_id"]),
        capabilities=capabilities,
        control_route_sets=route_sets,
        providers=_mapping(descriptor.get("providers", {})),
        requirements=_mapping(descriptor.get("requirements", {})),
        metadata=_control_metadata(metadata),
        warnings=warnings,
    )


def _capability_descriptor(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): child for key, child in sorted(value.items())}


def _route_sets_for_capabilities(
    capabilities: tuple[Mapping[str, object], ...]
) -> tuple[tuple[Mapping[str, object], ...], tuple[str, ...]]:
    descriptors: dict[str, Mapping[str, object]] = {}
    warnings: list[str] = []
    for capability in capabilities:
        route_set_name = capability.get("route_set")
        if route_set_name is None:
            continue
        try:
            descriptors[str(route_set_name)] = route_set_named(str(route_set_name)).as_descriptor()
        except KeyError:
            warnings.append(f"unknown control route set {route_set_name!r}")
    return tuple(descriptors[name] for name in sorted(descriptors)), tuple(sorted(warnings))


def _control_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    omitted = {"capabilities"}
    return {
        str(key): _redact_descriptor_value(str(key), value)
        for key, value in sorted(metadata.items())
        if str(key) not in omitted
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
    session: OperationSessionRecord,
    *,
    limit: int,
) -> dict[str, object]:
    session_id = session.session_id
    plans = store.plans_for_session(session_id)[:limit]
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
            _plan_descriptor(store, plan, limit=limit)
            for plan in plans
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
    approval: object,
) -> dict[str, object]:
    request_id = getattr(approval, "request_id")
    decision = store.approval_decision_for_request(request_id)
    return {
        "request_id": request_id,
        "session_id": getattr(approval, "session_id"),
        "plan_id": getattr(approval, "plan_id"),
        "requested_by": getattr(approval, "requested_by"),
        "requested_at": getattr(approval, "requested_at"),
        "required_scope": getattr(approval, "required_scope"),
        "max_risk": getattr(approval, "max_risk").value,
        "destructive": getattr(approval, "destructive"),
        "comment": getattr(approval, "comment"),
        "state": "pending" if decision is None else decision.decision.value,
        "decision": None if decision is None else {
            "decision_id": decision.decision_id,
            "actor_id": decision.actor_id,
            "decision": decision.decision.value,
            "scope": decision.scope,
            "decided_at": decision.decided_at,
            "comment": decision.comment,
        },
    }


def _plan_descriptor(
    store: ActivityHistoryStore,
    plan: ActivityPlanRecord,
    *,
    limit: int,
) -> dict[str, object]:
    plan_id = plan.plan_id
    return {
        "plan_id": plan_id,
        "session_id": plan.session_id,
        "base_graph_id": plan.base_graph_id,
        "desired_graph_id": plan.desired_graph_id,
        "status": plan.status,
        "created_at": plan.created_at,
        "payload": DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan.plan),
        "runs": [
            _run_descriptor(store, run, limit=limit)
            for run in store.runs_for_plan(plan_id)[:limit]
        ],
    }


def _run_descriptor(store: ActivityHistoryStore, run: object, *, limit: int) -> dict[str, object]:
    run_id = getattr(run, "run_id")
    return {
        "run_id": run_id,
        "plan_id": getattr(run, "plan_id"),
        "status": getattr(run, "status").value,
        "started_at": getattr(run, "started_at"),
        "finished_at": getattr(run, "finished_at"),
        "metadata": _redact_descriptor_value("metadata", getattr(run, "metadata").descriptor()),
        "events": [
            _event_descriptor(event)
            for event in store.events_for_run(run_id)[:limit]
        ],
    }


def _event_descriptor(event: object) -> dict[str, object]:
    return {
        "event_id": getattr(event, "event_id"),
        "run_id": getattr(event, "run_id"),
        "ordinal": getattr(event, "ordinal"),
        "event_type": getattr(event, "kind").value,
        "occurred_at": getattr(event, "occurred_at"),
        "activity_id": getattr(event, "activity_id"),
        "payload": _redact_descriptor_value("payload", getattr(event, "evidence").descriptor()),
    }


def _observation_descriptor(record: ObservationRecord) -> dict[str, object]:
    return {
        "observation_id": record.observation_id,
        "workspace_id": record.workspace_id,
        "subject_id": record.subject_id,
        "status": record.status.value,
        "observed_at": record.observed_at,
        "stale": record.freshness.value == "stale",
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
    except KeyError as exc:
        raise ReadModelError(
            f"missing plan {plan_id!r} in workspace {workspace_id!r}"
        ) from exc
    try:
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


def _looks_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _ADDRESS_KEYS
        or ("." not in normalized and normalized.endswith("_url"))
        or any(marker in normalized for marker in _SECRET_MARKERS)
    )
