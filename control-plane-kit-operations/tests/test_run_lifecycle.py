from __future__ import annotations

import concurrent.futures
import os
import unittest

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    LifecycleOperationKind,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.lifecycle import (
    ClaimAndOpenActivityRun,
    CompleteActivityRun,
    ExecutionWorkerAuthority,
    FailActivityRun,
    PauseActivityRun,
    ResumeActivityRun,
    RunLifecycleCommandService,
    RunLifecycleConflict,
    RunLifecycleDenied,
    RunLifecycleError,
    RunLifecycleIdempotencyConflict,
    StartActivityRun,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    FailureEvidence,
    OperationsRecordError,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workflows import (
    CloseOperationSession,
    OperationCommandService,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class RunRecordLawTests(unittest.TestCase):
    def test_run_timing_and_event_scope_fail_closed(self) -> None:
        with self.assertRaisesRegex(OperationsRecordError, "claimed runs must not"):
            self.run_record(ActivityRunStatus.CLAIMED, started_at="started")
        with self.assertRaisesRegex(OperationsRecordError, "running runs require"):
            self.run_record(ActivityRunStatus.RUNNING)
        with self.assertRaisesRegex(OperationsRecordError, "succeeded runs require"):
            self.run_record(ActivityRunStatus.SUCCEEDED, started_at="started")
        with self.assertRaisesRegex(OperationsRecordError, "run event must not"):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.RUN_STARTED,
                "occurred",
                activity_id="start-api",
            )
        with self.assertRaisesRegex(OperationsRecordError, "step event requires"):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.STEP_STARTED,
                "occurred",
            )

    def test_bounded_evidence_rejects_secret_shapes_and_non_json_values(self) -> None:
        with self.assertRaisesRegex(OperationsRecordError, "secret-shaped"):
            BoundedEvidence.from_mapping({"api_token": "do-not-store"})
        with self.assertRaisesRegex(OperationsRecordError, "unsupported"):
            BoundedEvidence.from_mapping({"activity_ids": ("start-api",)})
        with self.assertRaisesRegex(OperationsRecordError, "finite"):
            BoundedEvidence.from_mapping({"latency": float("inf")})

    @staticmethod
    def run_record(
        status: ActivityRunStatus,
        *,
        started_at: str | None = None,
        settled_at: str | None = None,
    ) -> ActivityRunRecord:
        return ActivityRunRecord(
            run_id="run-a",
            plan_id="plan-a",
            admission=AdmittedRun("request-a"),
            retry=RetryIdentity(1),
            status=status,
            created_at="created",
            started_at=started_at,
            settled_at=settled_at,
        )


class RunLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_execution_request()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self, *ids: str, now: str = "2026-07-22T13:00:00Z") -> RunLifecycleCommandService:
        return RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: now,
            id_factory=Sequence(*ids),
        )

    def authority(
        self,
        worker_id: str = "worker-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def claim_command(
        self,
        *,
        worker_id: str = "worker-a",
        key: str = "claim-a",
        lease: str = "2026-07-22T13:10:00Z",
    ) -> ClaimAndOpenActivityRun:
        return ClaimAndOpenActivityRun(
            "request-a",
            self.authority(worker_id),
            lease,
            IdempotencyKey(key),
        )

    def test_claim_opens_one_run_and_event_atomically_without_effect_dependency(self) -> None:
        result = self.service("run-a", "event-open", "action-claim").execute(
            self.claim_command()
        )

        self.assertIs(result.request.status, ExecutionRequestStatus.CLAIMED)
        self.assertEqual(result.request.claim.worker_id, "worker-a")
        self.assertIs(result.run.status, ActivityRunStatus.CLAIMED)
        self.assertEqual(result.run.admission.request_id, "request-a")
        self.assertIs(result.event.kind, ActivityEventKind.RUN_OPENED)
        self.assertEqual(result.event.ordinal, 1)
        self.assertIs(result.action.action_type, LifecycleOperationKind.CLAIM_RUN)
        self.assertEqual(
            result.action.payload["execution_request_id"],
            "request-a",
        )

    def test_claim_replay_conflict_scope_and_competing_worker_fail_closed(self) -> None:
        first = self.service("run-a", "event-open", "action-claim").execute(
            self.claim_command()
        )
        replay = self.service("unused-run", "unused-event", "unused-action").execute(
            self.claim_command()
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.run, first.run)
        with self.assertRaises(RunLifecycleIdempotencyConflict):
            self.service("unused-run", "unused-event", "unused-action").execute(
                self.claim_command(lease="2026-07-22T13:11:00Z")
            )
        with self.assertRaises(RunLifecycleDenied):
            self.service("unused-run", "unused-event", "unused-action").execute(
                ClaimAndOpenActivityRun(
                    "request-a",
                    self.authority(scopes=()),
                    "2026-07-22T13:10:00Z",
                    IdempotencyKey("claim-denied"),
                )
            )
        with self.assertRaises(RunLifecycleConflict):
            self.service("unused-run", "unused-event", "unused-action").execute(
                self.claim_command(worker_id="worker-b", key="claim-b")
            )

    def test_concurrent_claims_have_exactly_one_worker_winner(self) -> None:
        def submit(worker_id: str) -> str:
            try:
                result = self.service(
                    f"run-{worker_id}",
                    f"event-{worker_id}",
                    f"action-{worker_id}",
                ).execute(self.claim_command(worker_id=worker_id, key=f"claim-{worker_id}"))
                return result.request.claim.worker_id
            except RunLifecycleConflict:
                return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(submit, ("worker-a", "worker-b")))

        self.assertEqual(sum(value != "conflict" for value in results), 1)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                len(unit_of_work.stores.execution.runs_for_request("request-a")),
                1,
            )

    def test_start_pause_resume_complete_are_atomic_and_visible(self) -> None:
        self.claim()

        started = self.service("event-start", "action-start").execute(
            StartActivityRun("run-a", self.authority(), IdempotencyKey("start-a"))
        )
        paused = self.service("event-pause", "action-pause").execute(
            PauseActivityRun(
                "run-a",
                self.authority(),
                IdempotencyKey("pause-a"),
                BoundedEvidence.from_mapping({"reason": "operator-review"}),
            )
        )
        resumed = self.service("event-resume", "action-resume").execute(
            ResumeActivityRun("run-a", self.authority(), IdempotencyKey("resume-a"))
        )
        completed = self.service("event-complete", "action-complete").execute(
            CompleteActivityRun(
                "run-a",
                self.authority(),
                IdempotencyKey("complete-a"),
                BoundedEvidence.from_mapping({"result": "ok"}),
            )
        )

        self.assertIs(started.run.status, ActivityRunStatus.RUNNING)
        self.assertIs(paused.run.status, ActivityRunStatus.PAUSED)
        self.assertIs(resumed.run.status, ActivityRunStatus.RUNNING)
        self.assertIs(completed.run.status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(
            [event.kind for event in self.events()],
            [
                ActivityEventKind.RUN_OPENED,
                ActivityEventKind.RUN_STARTED,
                ActivityEventKind.RUN_PAUSED,
                ActivityEventKind.RUN_RESUMED,
                ActivityEventKind.RUN_SUCCEEDED,
            ],
        )
        self.assertEqual([event.ordinal for event in self.events()], [1, 2, 3, 4, 5])

    def test_worker_ownership_and_late_action_failure_roll_back_transition(self) -> None:
        self.claim()
        self.service("event-start", "action-start").execute(
            StartActivityRun("run-a", self.authority(), IdempotencyKey("start-a"))
        )
        with self.assertRaises(RunLifecycleDenied):
            self.service("event-foreign", "action-foreign").execute(
                PauseActivityRun(
                    "run-a",
                    self.authority("worker-b"),
                    IdempotencyKey("pause-foreign"),
                )
            )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service("event-paused", "action-start").execute(
                PauseActivityRun(
                    "run-a",
                    self.authority(),
                    IdempotencyKey("pause-rollback"),
                )
            )

        with self.unit_of_work() as unit_of_work:
            run = unit_of_work.stores.execution.get_run("run-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertIs(run.status, ActivityRunStatus.RUNNING)
        self.assertEqual(
            [event.kind for event in events],
            [ActivityEventKind.RUN_OPENED, ActivityEventKind.RUN_STARTED],
        )

    def test_fail_records_bounded_failure_and_terminal_settlement_is_write_once(self) -> None:
        self.claim()
        self.service("event-start", "action-start").execute(
            StartActivityRun("run-a", self.authority(), IdempotencyKey("start-a"))
        )
        failed = self.service("event-fail", "action-fail").execute(
            FailActivityRun(
                "run-a",
                self.authority(),
                IdempotencyKey("fail-a"),
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "adapter-error",
                    "adapter returned a terminal failure",
                    BoundedEvidence.from_mapping({"adapter": "fake"}),
                ),
            )
        )

        self.assertIs(failed.run.status, ActivityRunStatus.FAILED)
        self.assertEqual(failed.event.failure.code, "adapter-error")
        with self.assertRaises(RunLifecycleConflict):
            self.service("event-complete", "action-complete").execute(
                CompleteActivityRun(
                    "run-a",
                    self.authority(),
                    IdempotencyKey("complete-after-fail"),
                )
            )

    def test_lifecycle_replay_survives_close_but_new_transition_is_fenced(self) -> None:
        claim_command = self.claim_command()
        claimed = self.service("run-a", "event-open", "action-claim").execute(
            claim_command
        )
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:01:00Z",
            id_factory=Sequence("action-close"),
        ).execute(
            CloseOperationSession(
                "session-a",
                "operator-a",
                IdempotencyKey("close"),
            )
        )

        replay = self.service("unused", "unused", "unused").execute(claim_command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.run, claimed.run)
        with self.assertRaisesRegex(RunLifecycleConflict, "open session"):
            self.service("event-start", "action-start").execute(
                StartActivityRun(
                    "run-a",
                    self.authority(),
                    IdempotencyKey("start-after-close"),
                )
            )

        self.assertEqual(
            tuple(event.kind for event in self.events()),
            (ActivityEventKind.RUN_OPENED,),
        )

    def test_canonical_current_prior_and_event_run_identity_round_trip(self) -> None:
        long_run_id = "r" * 200
        with self.unit_of_work() as unit_of_work:
            store = unit_of_work.stores.execution
            store.add_run(self._run_record("a"))
            store.add_run(
                self._run_record(long_run_id, attempt=2, prior_run_id="a")
            )
            store.add_event(
                ActivityEventRecord(
                    "event-long",
                    long_run_id,
                    1,
                    ActivityEventKind.RUN_OPENED,
                    "2026-07-22T13:00:00Z",
                )
            )
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            run = unit_of_work.stores.execution.get_run(long_run_id)
            event = unit_of_work.stores.execution.get_event("event-long")

        self.assertEqual(run.run_id, long_run_id)
        self.assertEqual(run.retry.prior_run_id, "a")
        self.assertEqual(event.run_id, long_run_id)

    def test_restart_rejects_corrupted_current_run_identity(self) -> None:
        self._insert_run("run/current-canary")

        with self.assertRaises(OperationsRecordError) as captured:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.execution.runs_for_request("request-a")

        self._assert_safe_error(captured.exception, "current-canary")

    def test_restart_rejects_corrupted_prior_run_identity(self) -> None:
        self._insert_run("run/prior-canary")
        self._insert_run(
            "run-current",
            attempt=2,
            prior_run_id="run/prior-canary",
        )

        with self.assertRaises(OperationsRecordError) as captured:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.execution.get_run("run-current")

        self._assert_safe_error(captured.exception, "prior-canary")

    def test_restart_rejects_corrupted_event_run_identity(self) -> None:
        self._insert_run("run/event-canary")
        self.connection.execute(
            """
            INSERT INTO cpk_activity_events
              (event_id, run_id, ordinal, event_type, occurred_at, payload)
            VALUES ('event-corrupt', 'run/event-canary', 1, 'run_opened',
                    '2026-07-22T13:00:00Z', '{"evidence":{}}'::jsonb)
            """
        )

        with self.assertRaises(OperationsRecordError) as captured:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.execution.get_event("event-corrupt")

        self._assert_safe_error(captured.exception, "event-canary")

    def test_invalid_run_factory_leaves_all_claim_truth_unchanged(self) -> None:
        error = None
        try:
            self.service(
                "run/factory-canary",
                "event-invalid",
                "action-invalid",
            ).execute(self.claim_command())
        except RunLifecycleError as caught:
            error = caught

        self.assertEqual(self._request_status(), ExecutionRequestStatus.QUEUED.value)
        self.assertEqual(self._count("cpk_activity_runs"), 0)
        self.assertEqual(self._count("cpk_activity_events"), 0)
        self.assertEqual(self._count("cpk_operation_actions"), 0)
        self.assertIsNotNone(error)
        self._assert_safe_error(error, "factory-canary")

    def test_reconstructed_claim_replay_consumes_no_factory(self) -> None:
        command = self.claim_command()
        claimed = self.service("r" * 200, "event-open", "action-claim").execute(
            command
        )
        factory_calls = 0

        def fail_factory() -> str:
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("replay consumed a new identity")

        replay = self._service_with_factory(fail_factory).execute(command)

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.run, claimed.run)
        self.assertEqual(factory_calls, 0)

    def test_replay_rejects_persisted_foreign_run_and_event_evidence(self) -> None:
        command = self.claim_command()
        self.service("run-a", "event-a", "action-a").execute(command)
        self._seed_second_request()
        self._insert_run("run-b", request_id="request-b")
        self.connection.execute(
            """
            INSERT INTO cpk_activity_events
              (event_id, run_id, ordinal, event_type, occurred_at, payload)
            VALUES ('event-b', 'run-b', 1, 'run_opened',
                    '2026-07-22T13:01:00Z', '{"evidence":{}}'::jsonb);
            UPDATE cpk_operation_actions
            SET payload = jsonb_set(
                jsonb_set(payload, '{run_id}', '"run-b"'::jsonb),
                '{event_id}', '"event-b"'::jsonb
            )
            WHERE action_id = 'action-a'
            """
        )

        with self.assertRaises(RunLifecycleError) as captured:
            self._service_with_factory(self._fail_factory).execute(command)

        self._assert_safe_error(captured.exception)

    def test_missing_replay_event_clears_candidate_bearing_store_error(self) -> None:
        command = self.claim_command()
        self.service("run-a", "event-secret-canary", "action-a").execute(command)
        self.connection.execute(
            "DELETE FROM cpk_activity_events WHERE event_id = 'event-secret-canary'"
        )

        with self.assertRaises(RunLifecycleError) as captured:
            self._service_with_factory(self._fail_factory).execute(command)

        self._assert_safe_error(captured.exception, "event-secret-canary")

    def test_valid_factory_collision_rolls_back_claim_and_stays_raw(self) -> None:
        self._seed_second_request()
        self._insert_run("run-collision", request_id="request-b")

        with self.assertRaises(psycopg.errors.UniqueViolation) as captured:
            self.service("run-collision", "event-a", "action-a").execute(
                self.claim_command()
            )

        self.assertIs(type(captured.exception), psycopg.errors.UniqueViolation)
        self.assertEqual(self._request_status(), ExecutionRequestStatus.QUEUED.value)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_activity_runs WHERE request_id = 'request-a'"
            ).fetchone()[0],
            0,
        )

    def claim(self) -> None:
        self.service("run-a", "event-open", "action-claim").execute(
            self.claim_command()
        )

    def events(self) -> tuple[ActivityEventRecord, ...]:
        with self.unit_of_work() as unit_of_work:
            return unit_of_work.stores.execution.events_for_run("run-a")

    def _service_with_factory(self, factory) -> RunLifecycleCommandService:
        return RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:00:00Z",
            id_factory=factory,
        )

    @staticmethod
    def _fail_factory() -> str:
        raise AssertionError("replay consumed a new identity")

    @staticmethod
    def _assert_safe_error(error, *canaries: str) -> None:
        assert error is not None
        if error.__cause__ is not None or error.__context__ is not None:
            raise AssertionError("public run identity error retained exception context")
        rendered = f"{error!s} {error!r}"
        if len(rendered) > 512:
            raise AssertionError("public run identity error is unbounded")
        for canary in canaries:
            if canary in rendered:
                raise AssertionError("public run identity error exposed candidate text")

    def _run_record(
        self,
        run_id: str,
        *,
        attempt: int = 1,
        prior_run_id: str | None = None,
    ) -> ActivityRunRecord:
        return ActivityRunRecord(
            run_id,
            "plan-a",
            AdmittedRun("request-a"),
            RetryIdentity(attempt, prior_run_id),
            ActivityRunStatus.CLAIMED,
            "2026-07-22T13:00:00Z",
        )

    def _insert_run(
        self,
        run_id: str,
        *,
        request_id: str = "request-a",
        attempt: int = 1,
        prior_run_id: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, prior_run_id, status,
               created_at, metadata)
            VALUES (%s, 'plan-a', %s, %s, %s, 'claimed',
                    '2026-07-22T13:00:00Z', '{}'::jsonb)
            """,
            (run_id, request_id, attempt, prior_run_id),
        )

    def _seed_second_request(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint)
            VALUES ('request-b', 'workspace-a', 'session-a', 'plan-a', 'queued',
                    'operator-a', '2026-07-22T12:05:00Z', 'approval-request-a',
                    'approval-decision-a', 'execute-b', 'fingerprint-b')
            """
        )

    def _request_status(self) -> str:
        return self.connection.execute(
            "SELECT status FROM cpk_execution_requests WHERE request_id = 'request-a'"
        ).fetchone()[0]

    def _count(self, table: str) -> int:
        if table not in {
            "cpk_activity_runs",
            "cpk_activity_events",
            "cpk_operation_actions",
        }:
            raise AssertionError("unexpected test table")
        return self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def seed_execution_request(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            """
        )
        with self.unit_of_work() as unit_of_work:
            lineage = seed_identity_graphs(
                unit_of_work.stores,
                workspace_id="workspace-a",
                graph_ids=("graph-current", "graph-desired"),
            )
            unit_of_work.commit()
        self.connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open',
                    '2026-07-22T12:01:00Z');
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               status, created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-current', 'graph-desired',
                    %s, %s, 'planned', '2026-07-22T12:02:00Z', '{}'::jsonb);
            """,
            (lineage["graph-current"], lineage["graph-desired"]),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'activity-plan',
                    '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                    encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                    'operator-a',
                    '2026-07-22T12:03:00Z', 'plan:approve', 'low', false);
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('approval-decision-a', 'approval-request-a', 'manager-a',
                    'approved', 'plan:approve', '2026-07-22T12:03:30Z');
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint)
            VALUES ('request-a', 'workspace-a', 'session-a', 'plan-a', 'queued',
                    'operator-a', '2026-07-22T12:04:00Z', 'approval-request-a',
                    'approval-decision-a', 'execute-a', 'fingerprint-a');
            """
        )
