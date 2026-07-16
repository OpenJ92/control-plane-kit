from __future__ import annotations

import concurrent.futures
import inspect
import os
from dataclasses import replace

import psycopg

from control_plane_kit.execution import (
    ActivityEventKind,
    ActivityRunStatus,
    BoundedEvidence,
    ExecutionRequestStatus,
    FailureCategory,
    FailureEvidence,
)
from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import (
    OperationActionKind,
    OperationActionRecord,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows import (
    BeginActivityRunCompensation,
    CancelActivityRun,
    ClaimAndOpenActivityRun,
    CompleteActivityRun,
    CompleteActivityRunCompensation,
    ExecutionWorkerAuthority,
    FailActivityRun,
    FailActivityRunCompensation,
    IdempotencyKey,
    PauseActivityRun,
    ResumeActivityRun,
    RetryActivityRun,
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
        commands = (
            (
                StartActivityRun(
                    "run-a", self._authority(), IdempotencyKey("start-a")
                ),
                "event-start",
                "action-start",
                ActivityRunStatus.RUNNING,
                ActivityEventKind.RUN_STARTED,
            ),
            (
                PauseActivityRun(
                    "run-a", self._authority(), IdempotencyKey("pause-a")
                ),
                "event-pause",
                "action-pause",
                ActivityRunStatus.PAUSED,
                ActivityEventKind.RUN_PAUSED,
            ),
            (
                ResumeActivityRun(
                    "run-a", self._authority(), IdempotencyKey("resume-a")
                ),
                "event-resume",
                "action-resume",
                ActivityRunStatus.RUNNING,
                ActivityEventKind.RUN_RESUMED,
            ),
            (
                CompleteActivityRun(
                    "run-a", self._authority(), IdempotencyKey("complete-a")
                ),
                "event-complete",
                "action-complete",
                ActivityRunStatus.SUCCEEDED,
                ActivityEventKind.RUN_SUCCEEDED,
            ),
        )
        for command, event_id, action_id, status, event_kind in commands:
            with self.subTest(command=type(command).__name__):
                result = self._transition(command, event_id, action_id)
                self.assertIs(result.run.status, status)
                self.assertIs(result.event.kind, event_kind)

        run = self.stores.execution.get_run("run-a")
        self.assertEqual(run.started_at, "2026-07-16T00:04:00Z")
        self.assertEqual(run.finished_at, "2026-07-16T00:04:00Z")
        model = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
        ).session_detail("workspace-a", "session-a")
        run_view = model.payload["session"]["plans"][0]["runs"][0]
        self.assertEqual(run_view["status"], "succeeded")
        self.assertEqual(
            [event["event_type"] for event in run_view["events"]],
            [
                "run_opened",
                "run_started",
                "run_paused",
                "run_resumed",
                "run_succeeded",
            ],
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
                ResumeActivityRun("run", self._authority(), IdempotencyKey("c")),
                {ActivityRunStatus.PAUSED},
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
                BeginActivityRunCompensation(
                    "run", self._authority(), IdempotencyKey("f")
                ),
                {ActivityRunStatus.FAILED},
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

        retry = self._service("run-b", "event-retry", "action-retry").execute(
            RetryActivityRun(
                "run-a", self._authority(), IdempotencyKey("retry-a")
            )
        )
        self.assertEqual(retry.run.retry.attempt, 2)
        self.assertEqual(retry.run.retry.prior_run_id, "run-a")
        self.assertEqual(
            retry.event.evidence.descriptor(),
            {"attempt": 2, "prior_run_id": "run-a"},
        )
        with self.assertRaises(RunLifecycleConflict):
            self._service("unused", "unused", "unused").execute(
                RetryActivityRun(
                    "run-a", self._authority(), IdempotencyKey("retry-branch")
                )
            )

    def test_compensation_success_is_explicit_and_replaces_failure_finish_time(self):
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
        compensating = self._transition(
            BeginActivityRunCompensation(
                "run-a", self._authority(), IdempotencyKey("compensate-a")
            ),
            "event-compensate",
            "action-compensate",
            now="2026-07-16T00:05:00Z",
        )
        self.assertIs(compensating.run.status, ActivityRunStatus.COMPENSATING)
        self.assertIsNone(compensating.run.finished_at)
        compensated = self._transition(
            CompleteActivityRunCompensation(
                "run-a", self._authority(), IdempotencyKey("compensated-a")
            ),
            "event-compensated",
            "action-compensated",
            now="2026-07-16T00:06:00Z",
        )
        self.assertIs(compensated.run.status, ActivityRunStatus.COMPENSATED)
        self.assertEqual(compensated.run.finished_at, "2026-07-16T00:06:00Z")

    def test_compensation_failure_records_partial_failure(self):
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
        self._transition(
            BeginActivityRunCompensation(
                "run-a", self._authority(), IdempotencyKey("compensate-a")
            ),
            "event-compensate",
            "action-compensate",
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
