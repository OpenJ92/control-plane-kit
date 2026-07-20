from __future__ import annotations

import concurrent.futures
import os

import psycopg

from control_plane_kit.execution import (
    AbandonExpiredClaim,
    AcceptUncompensatedFailure,
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    BeginCompensation,
    BoundedEvidence,
    RecoveryAuthority,
    RecoveryDecisionRecord,
    RecoveryScope,
    RemainPaused,
    RenewExpiredClaim,
    RetryAsNewRun,
    RetryIdentity,
    TakeOverExpiredClaim,
)
from control_plane_kit.stores import (
    OperationActionKind,
    OperationActionRecord,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows import (
    DecideActivityRunRecovery,
    IdempotencyKey,
    RunLifecycleCommandService,
    RunLifecycleConflict,
    RunLifecycleDenied,
)
from tests.postgres_case import PostgresStoreTestCase
from tests.test_execution_store import ExecutionStoreTests


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class RecoveryConcurrencyTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        ExecutionStoreTests._seed_admission_truth(self.stores)
        self.stores.execution.add_request(ExecutionStoreTests._request())
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.FAILED,
                created_at="2026-07-16T00:04:00Z",
                started_at="2026-07-16T00:04:05Z",
            )
        )
        for event_id, kind, activity_id in (
            ("event-open", ActivityEventKind.RUN_OPENED, None),
            ("event-start", ActivityEventKind.RUN_STARTED, None),
            ("event-step-start", ActivityEventKind.STEP_STARTED, "start-runtime-a"),
            ("event-step-success", ActivityEventKind.STEP_SUCCEEDED, "start-runtime-a"),
            ("event-fail", ActivityEventKind.RUN_FAILED, None),
        ):
            self.stores.execution.add_event(
                ActivityEventRecord(
                    event_id,
                    "run-a",
                    self.stores.execution.next_event_ordinal("run-a"),
                    kind,
                    "2026-07-16T00:04:10Z",
                    activity_id=activity_id,
                )
            )

    def _unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def _execute(
        self,
        *,
        name: str,
        decision,
        scope: RecoveryScope,
        now: str,
        ids: tuple[str, ...],
        expected_event_ordinal: int = 5,
    ):
        service = RunLifecycleCommandService(
            self._unit_of_work,
            clock=lambda: now,
            id_factory=Sequence(*ids),
        )
        return service.execute(
            DecideActivityRunRecovery(
                "run-a",
                "worker-a",
                expected_event_ordinal,
                RecoveryDecisionRecord(
                    f"decision-{name}",
                    decision,
                    RecoveryAuthority("operator-a", f"grant-{name}", (scope,)),
                    f"Authorize the {name} recovery decision.",
                ),
                IdempotencyKey(f"recovery-{name}"),
            )
        )

    def test_incompatible_recovery_decisions_have_one_postgres_winner(self):
        candidates = (
            (
                "remain",
                RemainPaused(),
                RecoveryScope.OPERATE,
                ("remain-decision", "remain-action"),
            ),
            (
                "retry",
                RetryAsNewRun(),
                RecoveryScope.OPERATE,
                ("retry-decision", "run-b", "retry-event", "retry-action"),
            ),
            (
                "compensate",
                BeginCompensation(),
                RecoveryScope.COMPENSATE,
                ("compensate-decision", "compensate-event", "compensate-action"),
            ),
            (
                "accept",
                AcceptUncompensatedFailure(),
                RecoveryScope.ACCEPT_LOSS,
                ("accept-decision", "accept-event", "accept-action"),
            ),
        )

        def attempt(candidate) -> str:
            name, decision, scope, ids = candidate
            try:
                self._execute(
                    name=name,
                    decision=decision,
                    scope=scope,
                    now="2026-07-16T00:04:30Z",
                    ids=ids,
                )
                return "committed"
            except (RunLifecycleConflict, RunLifecycleDenied):
                return "rejected"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            outcomes = tuple(executor.map(attempt, candidates))

        self.assertEqual(outcomes.count("committed"), 1)
        decision_events = tuple(
            event
            for event in self.stores.execution.events_for_run("run-a")
            if event.kind is ActivityEventKind.RECOVERY_DECISION_RECORDED
        )
        self.assertEqual(len(decision_events), 1)

    def test_expired_renew_takeover_and_abandon_have_one_postgres_winner(self):
        candidates = (
            (
                "renew",
                RenewExpiredClaim("2026-07-16T00:07:00Z"),
                RecoveryScope.RENEW_CLAIM,
                ("renew-decision", "renew-event", "renew-action"),
            ),
            (
                "takeover",
                TakeOverExpiredClaim("worker-b", "2026-07-16T00:07:00Z"),
                RecoveryScope.TAKE_OVER_CLAIM,
                ("takeover-decision", "takeover-event", "takeover-action"),
            ),
            (
                "abandon",
                AbandonExpiredClaim(),
                RecoveryScope.ABANDON_CLAIM,
                ("abandon-decision", "abandon-event", "abandon-action"),
            ),
        )

        def attempt(candidate) -> str:
            name, decision, scope, ids = candidate
            try:
                self._execute(
                    name=name,
                    decision=decision,
                    scope=scope,
                    now="2026-07-16T00:06:00Z",
                    ids=ids,
                )
                return "committed"
            except (RunLifecycleConflict, RunLifecycleDenied):
                return "rejected"

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            outcomes = tuple(executor.map(attempt, candidates))

        self.assertEqual(outcomes.count("committed"), 1)
        events = self.stores.execution.events_for_run("run-a")
        self.assertEqual(
            sum(
                event.kind
                in {
                    ActivityEventKind.REQUEST_CLAIM_RENEWED,
                    ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER,
                    ActivityEventKind.REQUEST_CLAIM_ABANDONED,
                }
                for event in events
            ),
            1,
        )

    def test_late_action_failure_rolls_back_expired_claim_recovery(self):
        original = self.stores.execution.get_request("execution-request-a")
        self.stores.activity_history.add_action(
            OperationActionRecord(
                "collision-action",
                "session-a",
                self.stores.activity_history.next_action_ordinal("session-a"),
                OperationActionKind.SESSION_STARTED,
                "operator-a",
                created_at="2026-07-16T00:05:00Z",
            )
        )

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._execute(
                name="renew-collision",
                decision=RenewExpiredClaim("2026-07-16T00:07:00Z"),
                scope=RecoveryScope.RENEW_CLAIM,
                now="2026-07-16T00:06:00Z",
                ids=("collision-decision", "collision-event", "collision-action"),
            )

        self.assertEqual(
            self.stores.execution.get_request("execution-request-a"),
            original,
        )
        self.assertEqual(
            tuple(event.event_id for event in self.stores.execution.events_for_run("run-a")),
            (
                "event-open",
                "event-start",
                "event-step-start",
                "event-step-success",
                "event-fail",
            ),
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
