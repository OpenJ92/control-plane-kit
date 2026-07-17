from __future__ import annotations

import unittest

from control_plane_kit.execution import ActivityEventKind, ActivityEventRecord
from control_plane_kit.planning import (
    ActivityId,
    ActivityPlan,
    PlannedActivity,
    RuntimeTarget,
    StartRuntime,
)
from control_plane_kit.saga import SagaStateError, SagaStepStatus
from control_plane_kit.workflows import SagaJournalError, project_activity_journal


def plan() -> ActivityPlan:
    return ActivityPlan(
        (
            PlannedActivity(
                ActivityId("start-runtime"),
                StartRuntime(RuntimeTarget("runtime")),
            ),
        )
    )


def event(
    ordinal: int,
    kind: ActivityEventKind,
    *,
    activity_id: str = "start-runtime",
) -> ActivityEventRecord:
    return ActivityEventRecord(
        event_id=f"event-{ordinal}",
        run_id="run-a",
        ordinal=ordinal,
        kind=kind,
        occurred_at=f"2026-07-16T00:00:{ordinal:02d}Z",
        activity_id=activity_id,
    )


class SagaJournalTests(unittest.TestCase):
    def test_success_reconstructs_from_canonical_activity_events(self) -> None:
        projection = project_activity_journal(
            plan(),
            (
                event(1, ActivityEventKind.STEP_STARTED),
                event(2, ActivityEventKind.STEP_SUCCEEDED),
            ),
        )

        self.assertIs(
            projection.state.steps[0].status,
            SagaStepStatus.SUCCEEDED,
        )
        self.assertEqual(projection.in_flight, ())
        self.assertEqual(projection.uncertain, ())

    def test_uncertain_attempt_remains_running_but_is_not_in_flight(self) -> None:
        uncertainty = event(2, ActivityEventKind.STEP_UNCERTAIN)
        projection = project_activity_journal(
            plan(),
            (event(1, ActivityEventKind.STEP_STARTED), uncertainty),
        )

        self.assertIs(
            projection.state.steps[0].status,
            SagaStepStatus.RUNNING,
        )
        self.assertEqual(projection.in_flight, ())
        self.assertEqual(projection.uncertain, (uncertainty,))

    def test_unsupported_is_distinct_durable_failure_evidence(self) -> None:
        projection = project_activity_journal(
            plan(),
            (event(1, ActivityEventKind.STEP_UNSUPPORTED),),
        )

        self.assertIs(
            projection.state.steps[0].status,
            SagaStepStatus.FAILED,
        )

    def test_foreign_and_impossible_histories_fail_closed(self) -> None:
        with self.assertRaises(SagaJournalError):
            project_activity_journal(
                plan(),
                (
                    event(
                        1,
                        ActivityEventKind.STEP_STARTED,
                        activity_id="foreign",
                    ),
                ),
            )
        with self.assertRaises(SagaStateError):
            project_activity_journal(
                plan(),
                (event(1, ActivityEventKind.STEP_SUCCEEDED),),
            )

    def test_mixed_runs_and_nonmonotonic_ordinals_fail_closed(self) -> None:
        first = event(1, ActivityEventKind.STEP_STARTED)
        foreign_run = ActivityEventRecord(
            event_id="event-2",
            run_id="run-b",
            ordinal=2,
            kind=ActivityEventKind.STEP_SUCCEEDED,
            occurred_at="2026-07-16T00:00:02Z",
            activity_id="start-runtime",
        )
        with self.assertRaises(SagaJournalError):
            project_activity_journal(plan(), (first, foreign_run))
        with self.assertRaises(SagaJournalError):
            project_activity_journal(
                plan(),
                (
                    event(2, ActivityEventKind.STEP_STARTED),
                    event(1, ActivityEventKind.STEP_SUCCEEDED),
                ),
            )


if __name__ == "__main__":
    unittest.main()
