"""Services that record workflow intent without owning topology truth."""

from __future__ import annotations

from typing import Callable, Mapping
from uuid import uuid4

from control_plane_kit.stores import ActivityHistoryStore, ActivityRunRecord


Clock = Callable[[], str]
IdFactory = Callable[[], str]


def _uuid() -> str:
    return uuid4().hex


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
