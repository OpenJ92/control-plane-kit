from __future__ import annotations

import unittest

from control_plane_kit.execution import (
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureCategory,
    FailureEvidence,
    BoundedEvidence,
    RecoveryAuthority,
    RecoveryDecisionRecord,
    RecoveryScope,
    RemainPaused,
    RetryIdentity,
)
from control_plane_kit.planning import (
    ActivityDependency,
    ActivityId,
    ActivityPlan,
    PlannedActivity,
    RuntimeTarget,
    StartRuntime,
)
from control_plane_kit.projections import (
    ClaimObservation,
    OperatorClaimStatus,
    OperatorRecoveryOptionKind,
    OperatorRecoveryProjectionError,
    project_operator_recovery,
)
from control_plane_kit.saga import SagaStatus


class OperatorRecoveryProjectionTests(unittest.TestCase):
    def test_empty_and_active_runs_preserve_distinct_run_and_saga_status(self) -> None:
        empty = self._project(ActivityRunStatus.CLAIMED, ())
        self.assertIs(empty.run_status, ActivityRunStatus.CLAIMED)
        self.assertIs(empty.saga_status, SagaStatus.ACTIVE)
        self.assertEqual(empty.schedule.ready, ("runtime-a",))
        self.assertEqual(empty.allowed_decisions, ())

        active = self._project(
            ActivityRunStatus.RUNNING,
            (self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),),
        )
        self.assertEqual(active.forward_in_flight, ("runtime-a",))
        self.assertEqual(active.schedule.running, ("runtime-a",))

    def test_paused_uncertainty_exposes_only_evidence_guarded_choices(self) -> None:
        view = self._project(
            ActivityRunStatus.PAUSED,
            (
                self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),
                self._step_event(2, ActivityEventKind.STEP_UNCERTAIN, "runtime-a"),
            ),
        )

        self.assertEqual(view.forward_uncertain, ("runtime-a",))
        self.assertEqual(
            tuple(option.kind for option in view.allowed_decisions),
            (
                OperatorRecoveryOptionKind.CONFIRM_EFFECT_SUCCEEDED,
                OperatorRecoveryOptionKind.CONFIRM_EFFECT_FAILED,
                OperatorRecoveryOptionKind.REMAIN_PAUSED,
            ),
        )
        self.assertTrue(
            all(
                option.required_scope in {
                    RecoveryScope.RESOLVE_UNCERTAINTY,
                    RecoveryScope.OPERATE,
                }
                for option in view.allowed_decisions
            )
        )

    def test_failed_run_preserves_failure_and_offers_closed_recovery(self) -> None:
        failure = FailureEvidence(
            FailureCategory.TERMINAL,
            "runtime.failed",
            "Runtime failed to start.",
        )
        view = self._project(
            ActivityRunStatus.FAILED,
            (
                self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),
                self._step_event(
                    2,
                    ActivityEventKind.STEP_FAILED,
                    "runtime-a",
                    failure=failure,
                ),
                self._run_event(3, ActivityEventKind.RUN_FAILED, failure=failure),
            ),
        )

        self.assertEqual(tuple(event.ordinal for event in view.original_failures), (2, 3))
        self.assertEqual(view.compensation_failures, ())
        self.assertEqual(
            tuple(option.kind for option in view.allowed_decisions),
            (
                OperatorRecoveryOptionKind.RETRY_AS_NEW_RUN,
                OperatorRecoveryOptionKind.ACCEPT_UNCOMPENSATED_FAILURE,
                OperatorRecoveryOptionKind.REMAIN_PAUSED,
            ),
        )

    def test_compensation_progress_is_derived_from_the_canonical_journal(self) -> None:
        admitted = (
            self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),
            self._step_event(2, ActivityEventKind.STEP_SUCCEEDED, "runtime-a"),
            self._run_event(3, ActivityEventKind.RUN_COMPENSATION_STARTED),
        )
        compensating = self._project(ActivityRunStatus.COMPENSATING, admitted)
        self.assertIs(compensating.saga_status, SagaStatus.COMPENSATING)
        self.assertEqual(compensating.schedule.compensation_ready, ("runtime-a",))

        in_flight = self._project(
            ActivityRunStatus.COMPENSATING,
            (*admitted, self._step_event(4, ActivityEventKind.STEP_COMPENSATION_STARTED, "runtime-a")),
        )
        self.assertEqual(in_flight.compensation_in_flight, ("runtime-a",))
        self.assertEqual(in_flight.schedule.compensating, ("runtime-a",))

        compensated = self._project(
            ActivityRunStatus.COMPENSATED,
            (
                *admitted,
                self._step_event(4, ActivityEventKind.STEP_COMPENSATION_STARTED, "runtime-a"),
                self._step_event(5, ActivityEventKind.STEP_COMPENSATION_SUCCEEDED, "runtime-a"),
                self._run_event(6, ActivityEventKind.RUN_COMPENSATION_SUCCEEDED),
            ),
        )
        self.assertIs(compensated.saga_status, SagaStatus.COMPENSATED)
        self.assertEqual(compensated.schedule.compensated, ("runtime-a",))

    def test_compensation_failure_never_replaces_original_failure(self) -> None:
        original = FailureEvidence(
            FailureCategory.TERMINAL,
            "forward.failed",
            "Forward work failed.",
        )
        compensation = FailureEvidence(
            FailureCategory.TERMINAL,
            "compensation.failed",
            "Compensation failed.",
        )
        events = (
            self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),
            self._step_event(2, ActivityEventKind.STEP_SUCCEEDED, "runtime-a"),
            self._step_event(3, ActivityEventKind.STEP_STARTED, "runtime-b"),
            self._step_event(4, ActivityEventKind.STEP_FAILED, "runtime-b", failure=original),
            self._run_event(5, ActivityEventKind.RUN_FAILED, failure=original),
            self._run_event(6, ActivityEventKind.RUN_COMPENSATION_STARTED),
            self._step_event(7, ActivityEventKind.STEP_COMPENSATION_STARTED, "runtime-a"),
            self._step_event(
                8,
                ActivityEventKind.STEP_COMPENSATION_FAILED,
                "runtime-a",
                failure=compensation,
            ),
            self._run_event(9, ActivityEventKind.RUN_COMPENSATION_FAILED, failure=compensation),
        )

        view = self._project(ActivityRunStatus.PARTIALLY_FAILED, events)

        self.assertEqual(
            tuple(event.failure.code for event in view.original_failures),
            ("forward.failed", "forward.failed"),
        )
        self.assertEqual(
            tuple(event.failure.code for event in view.compensation_failures),
            ("compensation.failed", "compensation.failed"),
        )
        self.assertEqual(view.schedule.compensation_failed, ("runtime-a",))

    def test_accepted_uncompensated_and_recovery_history_remain_visible(self) -> None:
        decision = RecoveryDecisionRecord(
            "decision-a",
            RemainPaused(),
            RecoveryAuthority("operator-a", "grant-a", (RecoveryScope.OPERATE,)),
            "Keep the failed run paused.",
        )
        events = (
            self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),
            self._step_event(2, ActivityEventKind.STEP_FAILED, "runtime-a"),
            ActivityEventRecord(
                "event-3",
                "run-a",
                3,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                "2026-07-16T00:00:03Z",
                recovery=decision,
            ),
            self._run_event(4, ActivityEventKind.RUN_UNCOMPENSATED_FAILURE_ACCEPTED),
        )
        view = self._project(ActivityRunStatus.UNCOMPENSATED_FAILURE, events)

        self.assertEqual(view.decisions, (decision,))
        self.assertEqual(view.allowed_decisions, ())

    def test_non_compensatable_failure_projects_bounded_activity_ids(self) -> None:
        failure = FailureEvidence(
            FailureCategory.TERMINAL,
            "compensation.non-compensatable-work",
            "Completed work cannot be compensated automatically.",
            BoundedEvidence.from_mapping({"activity_ids": ["runtime-b"]}),
        )
        view = self._project(
            ActivityRunStatus.PARTIALLY_FAILED,
            (
                self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),
                self._step_event(2, ActivityEventKind.STEP_SUCCEEDED, "runtime-a"),
                self._run_event(3, ActivityEventKind.RUN_COMPENSATION_STARTED),
                self._step_event(4, ActivityEventKind.STEP_COMPENSATION_STARTED, "runtime-a"),
                self._step_event(5, ActivityEventKind.STEP_COMPENSATION_FAILED, "runtime-a"),
                self._run_event(
                    6,
                    ActivityEventKind.RUN_COMPENSATION_FAILED,
                    failure=failure,
                ),
            ),
        )

        self.assertEqual(view.non_compensatable_activity_ids, ("runtime-b",))
        self.assertIs(view.compensation_failures[-1].failure, failure)

    def test_expired_claim_replaces_effect_recovery_with_claim_recovery(self) -> None:
        view = self._project(
            ActivityRunStatus.FAILED,
            (
                self._step_event(1, ActivityEventKind.STEP_STARTED, "runtime-a"),
                self._step_event(2, ActivityEventKind.STEP_FAILED, "runtime-a"),
            ),
            observed_at="2026-07-16T00:05:00Z",
        )

        self.assertIs(view.claim_status, OperatorClaimStatus.EXPIRED)
        self.assertEqual(
            tuple(option.kind for option in view.allowed_decisions),
            (
                OperatorRecoveryOptionKind.RENEW_EXPIRED_CLAIM,
                OperatorRecoveryOptionKind.TAKE_OVER_EXPIRED_CLAIM,
                OperatorRecoveryOptionKind.ABANDON_EXPIRED_CLAIM,
            ),
        )
        self.assertEqual(
            view.allowed_decisions[1].required_parameters,
            ("replacement_worker_id", "lease_expires_at"),
        )

    def test_foreign_identity_and_malformed_observation_fail_closed(self) -> None:
        request = self._request()
        foreign = ActivityRunRecord(
            "run-a",
            "plan-a",
            AdmittedRun("foreign-request"),
            RetryIdentity(1),
            ActivityRunStatus.CLAIMED,
            "2026-07-16T00:00:00Z",
        )
        with self.assertRaisesRegex(OperatorRecoveryProjectionError, "ownership"):
            project_operator_recovery(
                self._plan(),
                request,
                foreign,
                (),
                ClaimObservation("2026-07-16T00:01:00Z"),
            )
        with self.assertRaisesRegex(OperatorRecoveryProjectionError, "ISO-8601"):
            ClaimObservation("not-a-time")

    def _project(
        self,
        status: ActivityRunStatus,
        events: tuple[ActivityEventRecord, ...],
        *,
        observed_at: str = "2026-07-16T00:04:00Z",
    ):
        started_at = (
            None
            if status in {ActivityRunStatus.CLAIMED, ActivityRunStatus.CANCELLED}
            else "2026-07-16T00:00:01Z"
        )
        settled_at = (
            "2026-07-16T00:01:00Z"
            if status
            in {
                ActivityRunStatus.SUCCEEDED,
                ActivityRunStatus.COMPENSATED,
                ActivityRunStatus.PARTIALLY_FAILED,
                ActivityRunStatus.UNCOMPENSATED_FAILURE,
                ActivityRunStatus.CANCELLED,
            }
            else None
        )
        return project_operator_recovery(
            self._plan(),
            self._request(),
            ActivityRunRecord(
                "run-a",
                "plan-a",
                AdmittedRun("request-a"),
                RetryIdentity(1),
                status,
                "2026-07-16T00:00:00Z",
                started_at=started_at,
                settled_at=settled_at,
            ),
            events,
            ClaimObservation(observed_at),
        )

    @staticmethod
    def _plan() -> ActivityPlan:
        return ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("runtime-a"),
                    StartRuntime(RuntimeTarget("runtime-a")),
                ),
                PlannedActivity(
                    ActivityId("runtime-b"),
                    StartRuntime(RuntimeTarget("runtime-b")),
                    (ActivityDependency(ActivityId("runtime-a")),),
                ),
            )
        )

    @staticmethod
    def _request() -> ExecutionRequestRecord:
        return ExecutionRequestRecord(
            ExecutionRequestIdentity("request-a", "workspace-a", "session-a", "plan-a"),
            ExecutionRequestStatus.CLAIMED,
            "operator-a",
            "2026-07-16T00:00:00Z",
            "approval-request-a",
            "approval-decision-a",
            ExecutionIdempotency("request-key", "fingerprint-a"),
            ClaimIdentity(
                "worker-a",
                "2026-07-16T00:00:00Z",
                "2026-07-16T00:05:00Z",
            ),
        )

    @staticmethod
    def _step_event(
        ordinal: int,
        kind: ActivityEventKind,
        activity_id: str,
        *,
        failure: FailureEvidence | None = None,
    ) -> ActivityEventRecord:
        return ActivityEventRecord(
            f"event-{ordinal}",
            "run-a",
            ordinal,
            kind,
            f"2026-07-16T00:00:{ordinal:02d}Z",
            activity_id=activity_id,
            failure=failure,
        )

    @staticmethod
    def _run_event(
        ordinal: int,
        kind: ActivityEventKind,
        *,
        failure: FailureEvidence | None = None,
    ) -> ActivityEventRecord:
        return ActivityEventRecord(
            f"event-{ordinal}",
            "run-a",
            ordinal,
            kind,
            f"2026-07-16T00:00:{ordinal:02d}Z",
            failure=failure,
        )


if __name__ == "__main__":
    unittest.main()
