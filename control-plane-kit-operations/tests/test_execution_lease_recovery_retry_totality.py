from __future__ import annotations

import unittest

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_core.planning import ActivityPlan
import control_plane_kit_operations.execution_lease_recovery_interpreter as interpreter
from control_plane_kit_operations.execution_lease_recovery import (
    RecoveryAuthority,
    RenewExpiredExecutionClaim,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    RunLifecycleConflict,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    ExecutionLeaseRecoveryEvidence,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey


class ExecutionLeaseRecoveryRetryTotalityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fence = ExecutionLeaseFence("worker-a", 7)
        self.command = RenewExpiredExecutionClaim(
            "request-a",
            RunId("run-a"),
            self.fence,
            RecoveryAuthority(
                "operator-a",
                "authority-reference-a",
                (RecoveryScope.RENEW_CLAIM,),
            ),
            ExecutionLeaseDuration(600),
            IdempotencyKey("recover-a"),
        )
        self.run = ActivityRunRecord(
            "run-a",
            "plan-a",
            AdmittedRun("request-a"),
            RetryIdentity(1),
            ActivityRunStatus.FAILED,
            "2026-08-15T03:59:10Z",
            started_at="2026-08-15T03:59:20Z",
        )
        self.plan = ActivityPlanRecord(
            "plan-a",
            "session-a",
            "graph-current",
            "graph-desired",
            ActivityPlanStatus.PLANNED,
            "2026-08-15T03:58:00Z",
            ActivityPlan(()),
        )

    def history(
        self,
        *,
        followed: bool,
    ) -> tuple[ActivityEventRecord, ...]:
        events = (
            ActivityEventRecord(
                "opened-a",
                "run-a",
                1,
                ActivityEventKind.RUN_OPENED,
                "2026-08-15T03:59:10Z",
            ),
            ActivityEventRecord(
                "started-a",
                "run-a",
                2,
                ActivityEventKind.RUN_STARTED,
                "2026-08-15T03:59:20Z",
            ),
            ActivityEventRecord(
                "failed-a",
                "run-a",
                3,
                ActivityEventKind.RUN_FAILED,
                "2026-08-15T04:00:00Z",
            ),
            ActivityEventRecord(
                "retry-a",
                "run-a",
                4,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                "2026-08-15T04:01:00Z",
                recovery=ExecutionLeaseRecoveryEvidence(
                    RecoveryDecisionKind.RETRY_AS_NEW_RUN,
                    RunId("run-a"),
                    self.fence,
                    self.fence,
                ),
            ),
        )
        if not followed:
            return events
        return events + (
            ActivityEventRecord(
                "ordinary-after-retry-a",
                "run-a",
                5,
                ActivityEventKind.RUN_CANCELLED,
                "2026-08-15T04:02:00Z",
            ),
        )

    def assert_retry_history_is_categorically_ineligible(
        self,
        events: tuple[ActivityEventRecord, ...],
    ) -> None:
        snapshot = repr(events)
        identities = tuple(id(event) for event in events)

        with self.assertRaises(RunLifecycleConflict) as captured:
            interpreter._require_journal(
                self.command,
                self.run,
                self.plan,
                events,
            )

        self.assertEqual(
            str(captured.exception),
            "retained run journal is invalid",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertLessEqual(
            len(f"{captured.exception!s} {captured.exception!r}"),
            512,
        )
        self.assertEqual(repr(events), snapshot)
        self.assertEqual(tuple(id(event) for event in events), identities)

    def test_terminal_retry_marker_is_categorically_ineligible(self) -> None:
        self.assert_retry_history_is_categorically_ineligible(
            self.history(followed=False)
        )

    def test_followed_retry_marker_is_categorically_ineligible(self) -> None:
        self.assert_retry_history_is_categorically_ineligible(
            self.history(followed=True)
        )


if __name__ == "__main__":
    unittest.main()
