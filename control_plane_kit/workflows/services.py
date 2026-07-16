"""Services that record workflow intent without owning topology truth."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Mapping
from uuid import uuid4

from control_plane_kit.stores import (
    ActivityHistoryStore,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
)


Clock = Callable[[], str]
IdFactory = Callable[[], str]


def _uuid() -> str:
    return uuid4().hex


class OperationSessionService:
    """Records the lifecycle of one grouped operator task."""

    def __init__(
        self,
        history: ActivityHistoryStore,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
    ) -> None:
        self._history = history
        self._clock = clock
        self._id_factory = id_factory

    def start(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        title: str,
        metadata: Mapping[str, str] | None = None,
    ) -> OperationSessionRecord:
        record = OperationSessionRecord(
            session_id=self._id_factory(),
            workspace_id=workspace_id,
            actor_id=actor_id,
            title=title,
            status=OperationSessionStatus.OPEN,
            created_at=self._clock(),
            metadata=metadata or {},
        )
        return self._history.add_session(record)

    def close(
        self,
        session_id: str,
        *,
        status: OperationSessionStatus = OperationSessionStatus.CLOSED,
    ) -> OperationSessionRecord:
        existing = self._history.get_session(session_id)
        closed = replace(existing, status=status, closed_at=self._clock())
        return self._history.update_session(closed)


class OperationActionService:
    """Records ordered operator actions inside an operation session."""

    def __init__(
        self,
        history: ActivityHistoryStore,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
    ) -> None:
        self._history = history
        self._clock = clock
        self._id_factory = id_factory

    def record(
        self,
        *,
        session_id: str,
        action_type: OperationActionKind,
        actor_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> OperationActionRecord:
        ordinal = len(self._history.actions_for_session(session_id)) + 1
        record = OperationActionRecord(
            action_id=self._id_factory(),
            session_id=session_id,
            ordinal=ordinal,
            action_type=action_type,
            actor_id=actor_id,
            payload=payload or {},
            created_at=self._clock(),
        )
        return self._history.add_action(record)


class ApprovalWorkflowService:
    """Records approval decisions without executing the approved target."""

    def __init__(
        self,
        history: ActivityHistoryStore,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
    ) -> None:
        self._history = history
        self._clock = clock
        self._id_factory = id_factory

    def decide(
        self,
        *,
        session_id: str,
        target_id: str,
        actor_id: str,
        decision: str,
        scope: str,
        comment: str | None = None,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=self._id_factory(),
            session_id=session_id,
            target_id=target_id,
            actor_id=actor_id,
            decision=decision,
            scope=scope,
            decided_at=self._clock(),
            comment=comment,
        )
        return self._history.add_approval(record)


class ActivityRunService:
    """Records plan and run state; it does not execute runtime effects."""

    def __init__(
        self,
        history: ActivityHistoryStore,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
    ) -> None:
        self._history = history
        self._clock = clock
        self._id_factory = id_factory

    def record_plan(
        self,
        *,
        session_id: str,
        base_graph_id: str,
        desired_graph_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> ActivityPlanRecord:
        record = ActivityPlanRecord(
            plan_id=self._id_factory(),
            session_id=session_id,
            base_graph_id=base_graph_id,
            desired_graph_id=desired_graph_id,
            status="planned",
            created_at=self._clock(),
            payload=payload or {},
        )
        return self._history.add_plan(record)

    def open_run(
        self,
        *,
        plan_id: str,
        metadata: Mapping[str, str] | None = None,
    ) -> ActivityRunRecord:
        record = ActivityRunRecord(
            run_id=self._id_factory(),
            plan_id=plan_id,
            status="open",
            started_at=self._clock(),
            metadata=metadata or {},
        )
        return self._history.add_run(record)
