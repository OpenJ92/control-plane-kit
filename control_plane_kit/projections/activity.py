"""Activity timeline and observed-state read models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.stores.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRecord,
    ObservationRecord,
    OperationActionRecord,
    OperationSessionRecord,
)


@dataclass(frozen=True)
class OperationActionReadModel:
    """One bounded operation action payload."""

    action_id: str
    ordinal: int
    action_type: str
    actor_id: str
    created_at: str
    payload: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: OperationActionRecord) -> "OperationActionReadModel":
        return cls(
            action_id=record.action_id,
            ordinal=record.ordinal,
            action_type=record.action_type,
            actor_id=record.actor_id,
            created_at=record.created_at,
            payload=record.payload,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "ordinal": self.ordinal,
            "action_type": self.action_type,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ActivityEventReadModel:
    """One bounded activity event payload."""

    event_id: str
    ordinal: int
    event_type: str
    occurred_at: str
    payload: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: ActivityEventRecord) -> "ActivityEventReadModel":
        return cls(
            event_id=record.event_id,
            ordinal=record.ordinal,
            event_type=record.event_type,
            occurred_at=record.occurred_at,
            payload=record.payload,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "ordinal": self.ordinal,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ActivityRunTimelineReadModel:
    """A run with bounded events."""

    run_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    events: tuple[ActivityEventReadModel, ...] = ()

    @classmethod
    def from_record(
        cls,
        record: ActivityRunRecord,
        *,
        events: tuple[ActivityEventReadModel, ...] = (),
    ) -> "ActivityRunTimelineReadModel":
        return cls(
            run_id=record.run_id,
            status=record.status,
            started_at=record.started_at,
            finished_at=record.finished_at,
            metadata=record.metadata,
            events=events,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": dict(self.metadata),
            "events": [event.descriptor() for event in self.events],
        }


@dataclass(frozen=True)
class ActivityPlanTimelineReadModel:
    """A plan with any known runs."""

    plan_id: str
    base_graph_id: str
    desired_graph_id: str
    status: str
    created_at: str
    payload: Mapping[str, object] = field(default_factory=dict)
    runs: tuple[ActivityRunTimelineReadModel, ...] = ()

    @classmethod
    def from_record(
        cls,
        record: ActivityPlanRecord,
        *,
        runs: tuple[ActivityRunTimelineReadModel, ...] = (),
    ) -> "ActivityPlanTimelineReadModel":
        return cls(
            plan_id=record.plan_id,
            base_graph_id=record.base_graph_id,
            desired_graph_id=record.desired_graph_id,
            status=record.status,
            created_at=record.created_at,
            payload=record.payload,
            runs=runs,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "base_graph_id": self.base_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "status": self.status,
            "created_at": self.created_at,
            "payload": dict(self.payload),
            "runs": [run.descriptor() for run in self.runs],
        }


@dataclass(frozen=True)
class OperationSessionTimelineReadModel:
    """One session timeline entry."""

    session_id: str
    workspace_id: str
    actor_id: str
    title: str
    status: str
    created_at: str
    closed_at: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    actions: tuple[OperationActionReadModel, ...] = ()
    approvals: tuple[Mapping[str, object], ...] = ()
    plans: tuple[ActivityPlanTimelineReadModel, ...] = ()

    @classmethod
    def from_record(
        cls,
        record: OperationSessionRecord,
        *,
        actions: tuple[OperationActionReadModel, ...] = (),
        approvals: tuple[Mapping[str, object], ...] = (),
        plans: tuple[ActivityPlanTimelineReadModel, ...] = (),
    ) -> "OperationSessionTimelineReadModel":
        return cls(
            session_id=record.session_id,
            workspace_id=record.workspace_id,
            actor_id=record.actor_id,
            title=record.title,
            status=record.status,
            created_at=record.created_at,
            closed_at=record.closed_at,
            metadata=record.metadata,
            actions=actions,
            approvals=approvals,
            plans=plans,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "metadata": dict(self.metadata),
            "actions": [action.descriptor() for action in self.actions],
            "approvals": [dict(approval) for approval in self.approvals],
            "plans": [plan.descriptor() for plan in self.plans],
        }


@dataclass(frozen=True)
class ActivityTimelineReadModel:
    """Bounded activity timeline for one workspace."""

    workspace_id: str
    limit: int
    sessions: tuple[OperationSessionTimelineReadModel, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "limit": self.limit,
            "sessions": [session.descriptor() for session in self.sessions],
        }


@dataclass(frozen=True)
class ObservationReadModel:
    """Latest observation for one subject."""

    subject_id: str
    status: str
    observed_at: str
    stale: bool = False
    payload: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: ObservationRecord) -> "ObservationReadModel":
        return cls(
            subject_id=record.subject_id,
            status=record.status,
            observed_at=record.observed_at,
            stale=record.stale,
            payload=record.payload,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "subject_id": self.subject_id,
            "status": self.status,
            "observed_at": self.observed_at,
            "stale": self.stale,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ObservedStateReadModel:
    """Latest observed state for one workspace."""

    workspace_id: str
    limit: int
    observations: tuple[ObservationReadModel, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "limit": self.limit,
            "observations": [observation.descriptor() for observation in self.observations],
        }


def approval_descriptor(record: ApprovalRecord) -> dict[str, object]:
    """Return a bounded approval descriptor."""

    return {
        "approval_id": record.approval_id,
        "target_id": record.target_id,
        "actor_id": record.actor_id,
        "decision": record.decision,
        "scope": record.scope,
        "decided_at": record.decided_at,
        "comment": record.comment,
    }
