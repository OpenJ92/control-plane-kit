"""Pure read-time interpretation of immutable runtime observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from control_plane_kit.execution.values import (
    ObservationFreshness,
    ObservationRecord,
    ObservationStaleReason,
)


@dataclass(frozen=True)
class ObservationFreshnessPolicy:
    """Maximum age for evidence to describe the current graph."""

    maximum_age: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.maximum_age <= timedelta(0):
            raise ValueError("observation maximum age must be positive")


@dataclass(frozen=True)
class ProjectedObservation:
    """An immutable observation interpreted at one explicit instant."""

    record: ObservationRecord
    freshness: ObservationFreshness
    stale_reason: ObservationStaleReason | None


def project_observation(
    record: ObservationRecord,
    *,
    current_graph_id: str | None,
    as_of: datetime,
    policy: ObservationFreshnessPolicy,
) -> ProjectedObservation:
    """Derive usability without rewriting the durable observation."""

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
