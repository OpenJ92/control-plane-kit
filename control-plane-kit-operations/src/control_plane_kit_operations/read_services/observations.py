"""Clocked freshness projections over immutable observation evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from control_plane_kit_operations.read_pages import ReadPage, ReadPageRequest
from control_plane_kit_operations.records import (
    ObservationFreshness,
    ObservationRecord,
    ObservationStaleReason,
    WorkspaceRecord,
)

from ._redaction import _redact_descriptor_value
from .errors import ReadModelError
from .protocols import ObservedStateStore


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


class _ObservationReadProjection:
    def __init__(
        self,
        require_workspace: Callable[[str], WorkspaceRecord],
        observed_state_store: ObservedStateStore | None,
        *,
        clock: Callable[[], datetime],
        freshness: ObservationFreshnessPolicy,
    ) -> None:
        self._require_workspace = require_workspace
        self._observed_state_store = observed_state_store
        self._clock = clock
        self._freshness = freshness

    def observed_state(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace = self._require_workspace(request.scope.workspace_id)
        as_of = self._clock()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ReadModelError(
                "read-service clock must return a timezone-aware datetime"
            )
        return self._observed_state().latest_page(request).map(
            lambda record: _observation_descriptor(
                project_observation(
                    record,
                    current_graph_id=workspace.current_graph_id,
                    as_of=as_of,
                    policy=self._freshness,
                )
            )
        )

    def _observed_state(self) -> ObservedStateStore:
        if self._observed_state_store is None:
            raise ReadModelError("observed state store is not configured")
        return self._observed_state_store


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
