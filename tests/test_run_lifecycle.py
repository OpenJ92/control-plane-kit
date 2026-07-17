from __future__ import annotations

import concurrent.futures
import inspect
import os
from dataclasses import replace

import psycopg

from control_plane_kit.execution import (
    AcceptUncompensatedFailure,
    BeginCompensation,
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunStatus,
    BoundedEvidence,
    ConfirmEffectSucceeded,
    ExecutionRequestStatus,
    FailureCategory,
    FailureEvidence,
    RecoveryAuthority,
    RecoveryDecisionRecord,
    RecoveryScope,
    RemainPaused,
    ResumeSameIntent,
    RetryAsNewRun,
)
from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import (
    OperationActionKind,
    OperationActionRecord,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows import (
    CancelActivityRun,
    ClaimAndOpenActivityRun,
    CompleteActivityRun,
    CompleteActivityRunCompensation,
    DecideActivityRunRecovery,
    ExecutionWorkerAuthority,
    FailActivityRun,
    FailActivityRunCompensation,
    IdempotencyKey,
    PauseActivityRun,
    RunLifecycleCommandService,
    RunLifecycleConflict,
    RunLifecycleDenied,
    RunLifecycleIdempotencyConflict,
    StartActivityRun,
    decide_run_transition,
)
from tests.postgres_case import PostgresStoreTestCase
from tests.test_execution_store import ExecutionStoreTests


class Sequence:
    def __init__(self, *values: str) -> None:
        self.values = list(values)

    def __call__(self) -> str:
        return self.values.pop(0)


class RunLifecycleTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        ExecutionStoreTests._seed_admission_truth(self.stores)
        self.stores.execution.add_request(
            replace(
                ExecutionStoreTests._request(),
                status=ExecutionRequestStatus.QUEUED,
                claim=None,
            )
        )

    def _unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def _service(
        self,
        *ids: str,
        now: str = "2026-07-16T00:04:00Z",
    ) -> RunLifecycleCommandService:
        return RunLifecycleCommandService(
            self._unit_of_work,
            clock=lambda: now,
            id_factory=Sequence(*ids),
        )

    def _authority(
        self,
        worker_id: str = "worker-a",
        scopes: tuple[str, ...] = ("execution:operate",),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def _claim(
        self,
        *,
        key: str = "claim-a",
        ids: tuple[str, str, str] = ("run-a", "event-open", "action-open"),
    ):
        return self._service(*ids).execute(
            ClaimAndOpenActivityRun(
                "execution-request-a",
                self._authority(),
                "2026-07-16T00:05:00Z",
                IdempotencyKey(key),
            )
        )

    def _transition(
        self,
        command,
        event_id: str,
        action_id: str,
        *,
        now: str = "2026-07-16T00:04:00Z",
    ):
        return self._service(event_id, action_id, now=now).execute(command)

    def _failure(self, code: str = "runtime.failed") -> FailureEvidence:
        return FailureEvidence(
            FailureCategory.RETRYABLE,
            code,
            "The runtime operation failed.",
            BoundedEvidence.from_mapping({"reference": "failure/a"}),
        )

    def _recovery(
        self,
        decision,
        *,
        scope: RecoveryScope,
        key: str,
        ids: tuple[str, ...],
        now: str = "2026-07-16T00:04:00Z",
    ):
        return self._service(*ids, now=now).execute(
            DecideActivityRunRecovery(
                "run-a",
                "worker-a",
                RecoveryDecisionRecord(
                    f"decision-{key}",
                    decision,
                    RecoveryAuthority("operator-a", "grant-a", (scope,)),
                    f"Authorize {key} for the admitted run.",
                ),
                IdempotencyKey(key),
            )
        )

    def _succeed_plan_step(self) -> None:
        for event_id, kind in (
            ("event-step-start", ActivityEventKind.STEP_STARTED),
            ("event-step-success", ActivityEventKind.STEP_SUCCEEDED),
        ):
            self.stores.execution.add_event(
                ActivityEventRecord(
                    event_id,
                    "run-a",
                    self.stores.execution.next_event_ordinal("run-a"),
                    kind,
                    "2026-07-16T00:04:00Z",
                    activity_id="start-runtime-a",
                )
            )

    def _pause_with_uncertain_step(self) -> None:
        self._transition(
            StartActivityRun("run-a", self._authority(), IdempotencyKey("start-a")),
            "event-start",
            "action-start",
        )
        for event_id, kind in (
            ("event-step-start", ActivityEventKind.STEP_STARTED),
            ("event-step-uncertain", ActivityEventKind.STEP_UNCERTAIN),
        ):
            self.stores.execution.add_event(
                ActivityEventRecord(
                    event_id,
                    "run-a",
                    self.stores.execution.next_event_ordinal("run-a"),
                    kind,
                    "2026-07-16T00:04:00Z",
                    activity_id="start-runtime-a",
                )
            )
        self._transition(
            PauseActivityRun("run-a", self._authority(), IdempotencyKey("pause-a")),
            "event-pause",
            "action-pause",
        )

    def test_claim_opens_one_run_and_event_atomically_without_effect_dependency(self):
        result = self._claim()

        self.assertIs(result.request.status, ExecutionRequestStatus.CLAIMED)
        self.assertEqual(result.request.claim.worker_id, "worker-a")
        self.assertIs(result.run.status, ActivityRunStatus.CLAIMED)
        self.assertEqual(result.run.retry.attempt, 1)
        self.assertIs(result.event.kind, ActivityEventKind.RUN_OPENED)
        self.assertEqual(result.event.evidence.descriptor(), {"attempt": 1})
        self.assertIs(
            result.action.action_type,
            OperationActionKind.EXECUTION_RUN_TRANSITIONED,
        )
        self.assertNotIn(
            "effects",
            inspect.signature(RunLifecycleCommandService.__init__).parameters,
        )

    def test_claim_scope_idempotency_conflict_and_competing_workers_fail_closed(self):
        with self.assertRaises(RunLifecycleDenied):
            self._service("unused", "unused", "unused").execute(
                ClaimAndOpenActivityRun(
                    "execution-request-a",
                    self._authority(scopes=()),
                    "2026-07-16T00:05:00Z",
                    IdempotencyKey("claim-a"),
                )
            )

        first = self._claim()
        replay = self._claim(ids=("unused", "unused", "unused"))
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.event, first.event)
        with self.assertRaises(RunLifecycleIdempotencyConflict):
            self._service("unused", "unused", "unused").execute(
                ClaimAndOpenActivityRun(
                    "execution-request-a",
                    self._authority(),
                    "2026-07-16T00:06:00Z",
                    IdempotencyKey("claim-a"),
                )
            )

    def test_concurrent_claims_have_exactly_one_worker_winner(self):
        def claim(worker_id: str):
            try:
                return self._service(
                    f"run-{worker_id}",
                    f"event-{worker_id}",
                    f"action-{worker_id}",
                ).execute(
                    ClaimAndOpenActivityRun(
                        "execution-request-a",
                        self._authority(worker_id),
                        "2026-07-16T00:05:00Z",
                        IdempotencyKey(f"claim-{worker_id}"),
                    )
                )
            except RunLifecycleConflict:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(claim, ("worker-a", "worker-b")))

        winners = tuple(result for result in results if result is not None)
        self.assertEqual(len(winners), 1)
        self.assertEqual(
            self.stores.execution.get_request("execution-request-a").claim.worker_id,
            winners[0].request.claim.worker_id,
        )
        self.assertEqual(
            len(self.stores.execution.runs_for_request("execution-request-a")),
            1,
        )

    def test_start_pause_resume_complete_are_atomic_and_visible_to_reads(self):
        self._claim()
        started = self._transition(
            StartActivityRun("run-a", self._authority(), IdempotencyKey("start-a")),
            "event-start",
            "action-start",
        )
        paused = self._transition(
            PauseActivityRun("run-a", self._authority(), IdempotencyKey("pause-a")),
            "event-pause",
            "action-pause",
        )
        resumed = self._recovery(
            ResumeSameIntent(),
            scope=RecoveryScope.OPERATE,
            key="resume-a",
            ids=("event-resume-decision", "event-resume", "action-resume"),
        )
        completed = self._transition(
            CompleteActivityRun(
                "run-a", self._authority(), IdempotencyKey("complete-a")
            ),
            "event-complete",
            "action-complete",
        )
        self.assertIs(started.run.status, ActivityRunStatus.RUNNING)
        self.assertIs(paused.run.status, ActivityRunStatus.PAUSED)
        self.assertIs(resumed.run.status, ActivityRunStatus.RUNNING)
        self.assertIs(
            resumed.consequence_event.kind,
            ActivityEventKind.RUN_RESUMED,
        )
        self.assertIs(completed.run.status, ActivityRunStatus.SUCCEEDED)

        run = self.stores.execution.get_run("run-a")
        self.assertEqual(run.started_at, "2026-07-16T00:04:00Z")
        self.assertEqual(run.settled_at, "2026-07-16T00:04:00Z")
        model = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
        ).session_detail("workspace-a", "session-a")
        run_view = model.payload["session"]["plans"][0]["runs"][0]
        self.assertEqual(run_view["status"], "succeeded")
        self.assertEqual(run_view["settled_at"], "2026-07-16T00:04:00Z")
        self.assertNotIn("finished_at", run_view)
        self.assertEqual(
            [event["event_type"] for event in run_view["events"]],
            [
                "run_opened",
                "run_started",
                "run_paused",
                "recovery_decision_recorded",
                "run_resumed",
                "run_succeeded",
            ],
        )

    def test_uncertainty_resolution_records_choice_and_consequence_atomically(self):
        self._claim()
        self._pause_with_uncertain_step()

        result = self._recovery(
            ConfirmEffectSucceeded("start-runtime-a"),
            scope=RecoveryScope.RESOLVE_UNCERTAINTY,
            key="resolve-a",
            ids=("event-decision", "event-resolution", "action-recovery"),
        )

        self.assertIs(result.run.status, ActivityRunStatus.PAUSED)
        self.assertIs(
            result.decision_event.kind,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
        )
        self.assertEqual(result.decision_event.recovery.decision_id, "decision-resolve-a")
        self.assertIs(
            result.consequence_event.kind,
            ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
        )
        self.assertEqual(
            result.consequence_event.activity_id,
            "start-runtime-a",
        )
        replay = self._recovery(
            ConfirmEffectSucceeded("start-runtime-a"),
            scope=RecoveryScope.RESOLVE_UNCERTAINTY,
            key="resolve-a",
            ids=("unused", "unused", "unused"),
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.decision_event, result.decision_event)
        self.assertEqual(replay.consequence_event, result.consequence_event)

    def test_recovery_authority_and_late_action_failure_fail_closed(self):
        self._claim()
        self._pause_with_uncertain_step()
        with self.assertRaises(RunLifecycleDenied):
            self._recovery(
                ConfirmEffectSucceeded("start-runtime-a"),
                scope=RecoveryScope.OPERATE,
                key="wrong-scope",
                ids=("unused", "unused", "unused"),
            )
        self.assertNotIn(
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            tuple(
                event.kind
                for event in self.stores.execution.events_for_run("run-a")
            ),
        )

        self.stores.activity_history.add_action(
            OperationActionRecord(
                "collision-action",
                "session-a",
                self.stores.activity_history.next_action_ordinal("session-a"),
                OperationActionKind.SESSION_STARTED,
                "operator",
                created_at="2026-07-16T00:04:00Z",
            )
        )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._recovery(
                ConfirmEffectSucceeded("start-runtime-a"),
                scope=RecoveryScope.RESOLVE_UNCERTAINTY,
                key="resolve-collision",
                ids=("event-decision", "event-resolution", "collision-action"),
            )
        events = self.stores.execution.events_for_run("run-a")
        self.assertNotIn("event-decision", {event.event_id for event in events})
        self.assertNotIn("event-resolution", {event.event_id for event in events})

    def test_acceptance_and_remain_paused_preserve_distinct_projection_meaning(self):
        self._claim()
        self._transition(
            StartActivityRun("run-a", self._authority(), IdempotencyKey("start-a")),
            "event-start",
            "action-start",
        )
        self._transition(
            FailActivityRun(
                "run-a",
                self._authority(),
                self._failure(),
                IdempotencyKey("fail-a"),
            ),
            "event-fail",
            "action-fail",
        )
        paused = self._recovery(
            RemainPaused(),
            scope=RecoveryScope.OPERATE,
            key="remain-paused",
            ids=("event-remain-paused", "action-remain-paused"),
        )
        self.assertIs(paused.run.status, ActivityRunStatus.FAILED)
        self.assertIsNone(paused.consequence_event)

        accepted = self._recovery(
            AcceptUncompensatedFailure(),
            scope=RecoveryScope.ACCEPT_LOSS,
            key="accept-loss",
            ids=("event-accept-decision", "event-accept", "action-accept"),
            now="2026-07-16T00:05:00Z",
        )
        self.assertIs(
            accepted.run.status,
            ActivityRunStatus.UNCOMPENSATED_FAILURE,
        )
        self.assertEqual(accepted.run.settled_at, "2026-07-16T00:05:00Z")
        self.assertIs(
            accepted.consequence_event.kind,
            ActivityEventKind.RUN_UNCOMPENSATED_FAILURE_ACCEPTED,
        )

    def test_transition_algebra_exhaustively_accepts_only_declared_domains(self):
        failure = self._failure()
        commands = (
            (
                StartActivityRun("run", self._authority(), IdempotencyKey("a")),
                {ActivityRunStatus.CLAIMED},
            ),
            (
                PauseActivityRun("run", self._authority(), IdempotencyKey("b")),
                {ActivityRunStatus.RUNNING},
            ),
            (
                CompleteActivityRun("run", self._authority(), IdempotencyKey("d")),
                {ActivityRunStatus.RUNNING},
            ),
            (
                FailActivityRun(
                    "run", self._authority(), failure, IdempotencyKey("e")
                ),
                {ActivityRunStatus.RUNNING, ActivityRunStatus.PAUSED},
            ),
            (
                CompleteActivityRunCompensation(
                    "run", self._authority(), IdempotencyKey("g")
                ),
                {ActivityRunStatus.COMPENSATING},
            ),
            (
                FailActivityRunCompensation(
                    "run", self._authority(), failure, IdempotencyKey("h")
                ),
                {ActivityRunStatus.COMPENSATING},
            ),
            (
                CancelActivityRun("run", self._authority(), IdempotencyKey("i")),
                {
                    ActivityRunStatus.CLAIMED,
                    ActivityRunStatus.RUNNING,
                    ActivityRunStatus.PAUSED,
                },
            ),
        )
        for command, accepted in commands:
            for status in ActivityRunStatus:
                with self.subTest(command=type(command).__name__, status=status.value):
                    if status in accepted:
                        self.assertEqual(
                            decide_run_transition(command, status).expected,
                            frozenset(accepted),
                        )
                    else:
                        with self.assertRaises(RunLifecycleConflict):
                            decide_run_transition(command, status)

    def test_failure_compensation_and_retry_preserve_truthful_lineage(self):
        self._claim()
        self._transition(
            StartActivityRun("run-a", self._authority(), IdempotencyKey("start-a")),
            "event-start",
            "action-start",
        )
        failed = self._transition(
            FailActivityRun(
                "run-a",
                self._authority(),
                self._failure(),
                IdempotencyKey("fail-a"),
            ),
            "event-fail",
            "action-fail",
        )
        self.assertEqual(failed.event.failure, self._failure())
        self.assertIsNone(failed.run.settled_at)
        self.assertEqual(failed.event.occurred_at, "2026-07-16T00:04:00Z")

        retry = self._recovery(
            RetryAsNewRun(),
            scope=RecoveryScope.OPERATE,
            key="retry-a",
            ids=(
                "event-retry-decision",
                "run-b",
                "event-retry",
                "action-retry",
            ),
        )
        self.assertEqual(retry.run.retry.attempt, 2)
        self.assertEqual(retry.run.retry.prior_run_id, "run-a")
        self.assertEqual(
            retry.consequence_event.evidence.descriptor(),
            {"attempt": 2, "prior_run_id": "run-a"},
        )
        prior = self.stores.execution.get_run("run-a")
        self.assertIs(prior.status, ActivityRunStatus.FAILED)
        self.assertIsNone(prior.settled_at)
        self.assertEqual(
            [event.kind for event in self.stores.execution.events_for_run("run-a")],
            [
                ActivityEventKind.RUN_OPENED,
                ActivityEventKind.RUN_STARTED,
                ActivityEventKind.RUN_FAILED,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
            ],
        )
        with self.assertRaises(RunLifecycleConflict):
            self._recovery(
                RetryAsNewRun(),
                scope=RecoveryScope.OPERATE,
                key="retry-branch",
                ids=("unused", "unused", "unused", "unused"),
            )

    def test_compensation_uses_events_and_settles_without_erasing_failure(self):
        self._claim()
        self._transition(
            StartActivityRun("run-a", self._authority(), IdempotencyKey("start-a")),
            "event-start",
            "action-start",
        )
        self._succeed_plan_step()
        self._transition(
            FailActivityRun(
                "run-a",
                self._authority(),
                self._failure(),
                IdempotencyKey("fail-a"),
            ),
            "event-fail",
            "action-fail",
        )
        compensating = self._recovery(
            BeginCompensation(),
            scope=RecoveryScope.COMPENSATE,
            key="compensate-a",
            ids=(
                "event-compensate-decision",
                "event-compensate",
                "action-compensate",
            ),
            now="2026-07-16T00:05:00Z",
        )
        self.assertIs(compensating.run.status, ActivityRunStatus.COMPENSATING)
        self.assertIsNone(compensating.run.settled_at)
        compensated = self._transition(
            CompleteActivityRunCompensation(
                "run-a", self._authority(), IdempotencyKey("compensated-a")
            ),
            "event-compensated",
            "action-compensated",
            now="2026-07-16T00:06:00Z",
        )
        self.assertIs(compensated.run.status, ActivityRunStatus.COMPENSATED)
        self.assertEqual(compensated.run.settled_at, "2026-07-16T00:06:00Z")
        events = self.stores.execution.events_for_run("run-a")
        self.assertEqual(
            [(event.kind, event.occurred_at) for event in events[-4:]],
            [
                (ActivityEventKind.RUN_FAILED, "2026-07-16T00:04:00Z"),
                (
                    ActivityEventKind.RECOVERY_DECISION_RECORDED,
                    "2026-07-16T00:05:00Z",
                ),
                (ActivityEventKind.RUN_COMPENSATION_STARTED, "2026-07-16T00:05:00Z"),
                (ActivityEventKind.RUN_COMPENSATION_SUCCEEDED, "2026-07-16T00:06:00Z"),
            ],
        )

    def test_compensation_failure_records_partial_failure(self):
        self._claim()
        self._transition(
            StartActivityRun("run-a", self._authority(), IdempotencyKey("start-a")),
            "event-start",
            "action-start",
        )
        self._succeed_plan_step()
        self._transition(
            FailActivityRun(
                "run-a",
                self._authority(),
                self._failure(),
                IdempotencyKey("fail-a"),
            ),
            "event-fail",
            "action-fail",
        )
        self._recovery(
            BeginCompensation(),
            scope=RecoveryScope.COMPENSATE,
            key="compensate-a",
            ids=(
                "event-compensate-decision",
                "event-compensate",
                "action-compensate",
            ),
        )
        result = self._transition(
            FailActivityRunCompensation(
                "run-a",
                self._authority(),
                self._failure("compensation.failed"),
                IdempotencyKey("compensation-failed-a"),
            ),
            "event-compensation-failed",
            "action-compensation-failed",
        )
        self.assertIs(result.run.status, ActivityRunStatus.PARTIALLY_FAILED)
        self.assertEqual(result.event.failure.code, "compensation.failed")

    def test_cancel_clears_request_claim_and_still_replays_exact_command(self):
        self._claim()
        cancelled = self._transition(
            CancelActivityRun(
                "run-a", self._authority(), IdempotencyKey("cancel-a")
            ),
            "event-cancel",
            "action-cancel",
        )
        self.assertIs(cancelled.run.status, ActivityRunStatus.CANCELLED)
        self.assertEqual(cancelled.run.settled_at, "2026-07-16T00:04:00Z")
        self.assertIs(cancelled.request.status, ExecutionRequestStatus.CANCELLED)
        replay = self._transition(
            CancelActivityRun(
                "run-a", self._authority(), IdempotencyKey("cancel-a")
            ),
            "unused-event",
            "unused-action",
        )
        self.assertTrue(replay.replayed)

    def test_late_operation_evidence_failure_rolls_back_claim_run_and_event(self):
        self.stores.activity_history.add_action(
            OperationActionRecord(
                "collision-action",
                "session-a",
                1,
                OperationActionKind.SESSION_STARTED,
                "operator",
                created_at="2026-07-16T00:00:00Z",
            )
        )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._claim(ids=("run-a", "event-open", "collision-action"))

        request = self.stores.execution.get_request("execution-request-a")
        self.assertIs(request.status, ExecutionRequestStatus.QUEUED)
        self.assertEqual(
            self.stores.execution.runs_for_request("execution-request-a"),
            (),
        )

    def test_worker_ownership_and_transition_rollback_fail_closed(self):
        self._claim()
        with self.assertRaises(RunLifecycleDenied):
            self._transition(
                StartActivityRun(
                    "run-a",
                    self._authority("worker-b"),
                    IdempotencyKey("start-foreign"),
                ),
                "unused-event",
                "unused-action",
            )

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._transition(
                StartActivityRun(
                    "run-a", self._authority(), IdempotencyKey("start-a")
                ),
                "event-start",
                "action-open",
            )

        self.assertIs(
            self.stores.execution.get_run("run-a").status,
            ActivityRunStatus.CLAIMED,
        )
        self.assertEqual(
            [
                event.kind
                for event in self.stores.execution.events_for_run("run-a")
            ],
            [ActivityEventKind.RUN_OPENED],
        )
