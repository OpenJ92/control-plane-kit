from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from control_plane_kit.execution import (
    EndpointContext,
    ObservationFreshness,
    ObservationFreshnessPolicy,
    ObservationRecord,
    ObservationStaleReason,
    ObservationStatus,
    ProbeKind,
    ProbeOutcome,
    project_observation,
)


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
POLICY = ObservationFreshnessPolicy(timedelta(minutes=5))


class ObservationProjectionTests(unittest.TestCase):
    def test_exact_clock_boundary_is_fresh_then_expires(self) -> None:
        record = _record(observed_at="2026-07-17T11:55:00Z")

        boundary = project_observation(
            record,
            current_graph_id="graph-a",
            as_of=NOW,
            policy=POLICY,
        )
        expired = project_observation(
            record,
            current_graph_id="graph-a",
            as_of=NOW + timedelta(microseconds=1),
            policy=POLICY,
        )

        self.assertIs(boundary.freshness, ObservationFreshness.FRESH)
        self.assertIsNone(boundary.stale_reason)
        self.assertIs(expired.stale_reason, ObservationStaleReason.EXPIRED)

    def test_graph_change_and_legacy_rows_are_explicitly_stale(self) -> None:
        changed = project_observation(
            _record(),
            current_graph_id="graph-b",
            as_of=NOW,
            policy=POLICY,
        )
        legacy = project_observation(
            ObservationRecord(
                "legacy", "workspace-a", "api", ObservationStatus.UNKNOWN,
                "2026-07-17T12:00:00Z",
            ),
            current_graph_id="graph-a",
            as_of=NOW,
            policy=POLICY,
        )

        self.assertIs(changed.stale_reason, ObservationStaleReason.GRAPH_CHANGED)
        self.assertIs(legacy.stale_reason, ObservationStaleReason.UNCORRELATED)

    def test_malformed_timestamp_fails_closed_without_mutating_record(self) -> None:
        record = _record(observed_at="not-a-timestamp")

        projected = project_observation(
            record,
            current_graph_id="graph-a",
            as_of=NOW,
            policy=POLICY,
        )

        self.assertIs(
            projected.stale_reason,
            ObservationStaleReason.MALFORMED_TIMESTAMP,
        )
        self.assertEqual(record.observed_at, "not-a-timestamp")
        self.assertIs(record.freshness, ObservationFreshness.FRESH)

    def test_future_timestamp_fails_closed(self) -> None:
        projected = project_observation(
            _record(observed_at="2026-07-17T12:00:00.000001Z"),
            current_graph_id="graph-a",
            as_of=NOW,
            policy=POLICY,
        )

        self.assertIs(
            projected.stale_reason,
            ObservationStaleReason.FUTURE_TIMESTAMP,
        )

    def test_correlated_record_requires_complete_typed_probe_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires graph, probe kind, and outcome"):
            ObservationRecord(
                "partial", "workspace-a", "api", ObservationStatus.HEALTHY,
                "2026-07-17T12:00:00Z", graph_id="graph-a",
            )
        with self.assertRaisesRegex(ValueError, "not a valid process observation"):
            ObservationRecord(
                "incoherent", "workspace-a", "api", ObservationStatus.HEALTHY,
                "2026-07-17T12:00:00Z", graph_id="graph-a",
                probe_kind=ProbeKind.PROCESS,
                probe_outcome=ProbeOutcome.HEALTHY,
            )


def _record(*, observed_at: str = "2026-07-17T12:00:00Z") -> ObservationRecord:
    return ObservationRecord(
        observation_id="observation-a",
        workspace_id="workspace-a",
        subject_id="api",
        status=ObservationStatus.HEALTHY,
        observed_at=observed_at,
        graph_id="graph-a",
        probe_kind=ProbeKind.APPLICATION_HEALTH,
        probe_outcome=ProbeOutcome.HEALTHY,
        endpoint_context=EndpointContext.RUNTIME_PRIVATE,
    )


if __name__ == "__main__":
    unittest.main()
