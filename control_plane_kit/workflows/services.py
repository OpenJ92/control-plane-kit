"""Services that record workflow intent without owning topology truth."""

from __future__ import annotations

from typing import Callable, Mapping
from uuid import uuid4

from control_plane_kit.stores import (
    ActivityHistoryStore,
    ActivityRunRecord,
    ApprovalRecord,
)


Clock = Callable[[], str]
IdFactory = Callable[[], str]


def _uuid() -> str:
    return uuid4().hex


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
    """Records run state for an already persisted canonical plan."""

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
