from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
)
from control_plane_kit_core.planning import (
    ActivityDependency,
    ActivityId,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    StartNode,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    CoordinatorStatus,
    ExecuteActivityRun,
    ExecutionCoordinator,
    ExecutionCoordinatorConflict,
    ExecutionCoordinatorDenied,
)
from control_plane_kit_operations.lifecycle import (
    ClaimAndOpenActivityRun,
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
    StartActivityRun,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    BoundedEvidence,
    FailureEvidence,
)
from control_plane_kit_operations.workflows import IdempotencyKey


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)
        self._next = 0

    def __call__(self) -> str:
        if self._values:
            return self._values.pop(0)
        self._next += 1
        return f"generated-{self._next}"


class TrackingUnitOfWorkFactory:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0
        self.entered = 0
        self.committed = 0

    def __call__(self) -> "TrackingUnitOfWork":
        return TrackingUnitOfWork(self, PostgresUnitOfWork(self._connect))

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url)


class TrackingUnitOfWork:
    def __init__(
        self,
        factory: TrackingUnitOfWorkFactory,
        inner: PostgresUnitOfWork,
    ) -> None:
        self._factory = factory
        self._inner = inner

    @property
    def stores(self):
        return self._inner.stores

    def __enter__(self) -> "TrackingUnitOfWork":
        self._factory.entered += 1
        self._factory.active += 1
        self._inner.__enter__()
        return self

    def commit(self) -> None:
        self._factory.committed += 1
        self._inner.commit()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self._inner.__exit__(exc_type, exc, traceback)
        finally:
            self._factory.active -= 1


class RecordingAdapter:
    def __init__(self, tracker: TrackingUnitOfWorkFactory, *outcomes: object) -> None:
        self.tracker = tracker
        self.outcomes = list(outcomes)
        self.calls: list[str] = []
        self.active_during_calls: list[int] = []

    def execute(self, activity: PlannedActivity) -> ActivityExecutionOutcome:
        self.calls.append(activity.activity_id.value)
        self.active_during_calls.append(self.tracker.active)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, ActivityExecutionOutcome):
            raise AssertionError("test adapter outcome must be ActivityExecutionOutcome")
        return outcome


class ExecutionCoordinatorTests(unittest.TestCase):
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
        self.ids = Sequence()
        self.tracker = TrackingUnitOfWorkFactory(database_url)
        self.seed_execution_request(plan=single_activity_plan())

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> TrackingUnitOfWork:
        return self.tracker()

    def lifecycle(self) -> RunLifecycleCommandService:
        return RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:00:00Z",
            id_factory=self.ids,
        )

    def lifecycle_with_ids(self, *ids: str) -> RunLifecycleCommandService:
        return RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:00:00Z",
            id_factory=Sequence(*ids),
        )

    def coordinator(self, adapter: RecordingAdapter) -> ExecutionCoordinator:
        return ExecutionCoordinator(
            self.unit_of_work,
            lifecycle=self.lifecycle(),
            adapter=adapter,
            clock=lambda: "2026-07-22T13:01:00Z",
            id_factory=self.ids,
        )

    def authority(
        self,
        worker_id: str = "worker-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def command(
        self,
        *,
        worker_id: str = "worker-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
        max_effects: int = 1,
    ) -> ExecuteActivityRun:
        return ExecuteActivityRun(
            "run-a",
            self.authority(worker_id, scopes),
            IdempotencyKey("execute-a"),
            max_effects=max_effects,
        )

    def test_success_records_intent_and_result_without_transaction_spanning_adapter(self) -> None:
        self.claim_and_start()
        adapter = RecordingAdapter(
            self.tracker,
            ActivityExecutionOutcome.succeeded(
                BoundedEvidence.from_mapping({"adapter": "fake"})
            ),
        )

        result = self.coordinator(adapter).execute(self.command())

        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(adapter.calls, ["start-api"])
        self.assertEqual(adapter.active_during_calls, [0])
        with self.unit_of_work() as unit_of_work:
            run = unit_of_work.stores.execution.get_run("run-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertIs(run.status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(
            [event.kind for event in events],
            [
                ActivityEventKind.RUN_OPENED,
                ActivityEventKind.RUN_STARTED,
                ActivityEventKind.STEP_STARTED,
                ActivityEventKind.STEP_SUCCEEDED,
                ActivityEventKind.RUN_SUCCEEDED,
            ],
        )
        self.assertEqual(
            [event.activity_id for event in events if event.activity_id is not None],
            ["start-api", "start-api"],
        )

    def test_adapter_exception_records_uncertainty_and_does_not_blind_replay(self) -> None:
        self.claim_and_start()
        adapter = RecordingAdapter(self.tracker, RuntimeError("lost result"))

        uncertain = self.coordinator(adapter).execute(self.command())
        replay = self.coordinator(adapter).execute(self.command())

        self.assertIs(uncertain.status, CoordinatorStatus.UNCERTAIN)
        self.assertIs(replay.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(adapter.calls, ["start-api"])
        with self.unit_of_work() as unit_of_work:
            run = unit_of_work.stores.execution.get_run("run-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertIs(run.status, ActivityRunStatus.RUNNING)
        self.assertEqual(events[-1].kind, ActivityEventKind.STEP_UNCERTAIN)
        self.assertEqual(events[-1].failure.category, FailureCategory.UNCERTAIN)

    def test_started_intent_without_result_is_in_flight_and_not_replayed(self) -> None:
        self.claim_and_start()
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(
                ActivityEventRecord(
                    "manual-step-intent",
                    "run-a",
                    stores.execution.next_event_ordinal("run-a"),
                    ActivityEventKind.STEP_STARTED,
                    "2026-07-22T13:00:30Z",
                    activity_id="start-api",
                )
            )
            unit_of_work.commit()
        adapter = RecordingAdapter(
            self.tracker,
            ActivityExecutionOutcome.succeeded(),
        )

        result = self.coordinator(adapter).execute(self.command())

        self.assertIs(result.status, CoordinatorStatus.IN_FLIGHT)
        self.assertEqual(adapter.calls, [])

    def test_failed_and_unsupported_outcomes_fail_run_but_remain_distinct(self) -> None:
        self.claim_and_start()
        failed = self.coordinator(
            RecordingAdapter(
                self.tracker,
                ActivityExecutionOutcome.failed(
                    FailureEvidence(
                        FailureCategory.TERMINAL,
                        "adapter-failed",
                        "adapter reported failure",
                    )
                ),
            )
        ).execute(self.command())

        self.assertIs(failed.status, CoordinatorStatus.FAILED)
        self.assertIs(failed.run.status, ActivityRunStatus.FAILED)

        self.reset_execution_request(plan=single_activity_plan())
        self.claim_and_start()
        unsupported = self.coordinator(
            RecordingAdapter(
                self.tracker,
                ActivityExecutionOutcome.unsupported(
                    FailureEvidence(
                        FailureCategory.OPERATOR_REVIEW,
                        "unsupported-capability",
                        "adapter does not support this operation",
                    )
                ),
            )
        ).execute(self.command())

        self.assertIs(unsupported.status, CoordinatorStatus.UNSUPPORTED)
        self.assertIs(unsupported.run.status, ActivityRunStatus.FAILED)
        with self.unit_of_work() as unit_of_work:
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(events[-2].kind, ActivityEventKind.STEP_UNSUPPORTED)
        self.assertEqual(events[-2].failure.code, "unsupported-capability")

    def test_worker_scope_and_claim_ownership_are_checked_before_adapter(self) -> None:
        self.claim_and_start()
        adapter = RecordingAdapter(
            self.tracker,
            ActivityExecutionOutcome.succeeded(),
        )
        with self.assertRaises(ExecutionCoordinatorDenied):
            self.coordinator(adapter).execute(self.command(scopes=()))
        with self.assertRaises(ExecutionCoordinatorDenied):
            self.coordinator(adapter).execute(self.command(worker_id="worker-b"))

        self.assertEqual(adapter.calls, [])

    def test_claimed_run_must_be_started_before_coordinator_execution(self) -> None:
        self.claim()
        adapter = RecordingAdapter(
            self.tracker,
            ActivityExecutionOutcome.succeeded(),
        )

        with self.assertRaisesRegex(ExecutionCoordinatorConflict, "must be started"):
            self.coordinator(adapter).execute(self.command())

        self.assertEqual(adapter.calls, [])

    def test_max_effects_limits_progress_without_losing_schedule(self) -> None:
        self.reset_execution_request(plan=two_step_plan())
        self.claim_and_start()
        adapter = RecordingAdapter(
            self.tracker,
            ActivityExecutionOutcome.succeeded(),
            ActivityExecutionOutcome.succeeded(),
        )

        progressed = self.coordinator(adapter).execute(self.command(max_effects=1))
        completed = self.coordinator(adapter).execute(self.command(max_effects=2))

        self.assertIs(progressed.status, CoordinatorStatus.PROGRESSED)
        self.assertEqual(progressed.effects_attempted, 1)
        self.assertEqual(progressed.activity_id, "wait-api")
        self.assertIs(completed.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, ["start-api", "wait-api"])

    def claim(self) -> None:
        self.lifecycle_with_ids("run-a", "event-open", "action-claim").execute(
            ClaimAndOpenActivityRun(
                "request-a",
                self.authority(),
                "2026-07-22T13:10:00Z",
                IdempotencyKey("claim-a"),
            )
        )

    def claim_and_start(self) -> None:
        self.claim()
        self.lifecycle_with_ids("event-start", "action-start").execute(
            StartActivityRun(
                "run-a",
                self.authority(),
                IdempotencyKey("start-a"),
            )
        )

    def seed_execution_request(self, *, plan: ActivityPlan) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created');
            INSERT INTO cpk_graph_versions
              (graph_id, workspace_id, version, graph_descriptor, created_by,
               created_at)
            VALUES
              ('graph-current', 'workspace-a', 1, '{}'::jsonb, 'operator-a',
               '2026-07-22T12:00:00Z'),
              ('graph-desired', 'workspace-a', 2, '{}'::jsonb, 'operator-a',
               '2026-07-22T12:00:30Z');
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open',
                    '2026-07-22T12:01:00Z');
            """
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_plan(
                ActivityPlanRecord(
                    "plan-a",
                    "session-a",
                    "graph-current",
                    "graph-desired",
                    ActivityPlanStatus.PLANNED,
                    "2026-07-22T12:02:00Z",
                    plan,
                )
            )
            unit_of_work.commit()
        self.connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'operator-a',
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

    def reset_execution_request(self, *, plan: ActivityPlan) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.ids = Sequence()
        self.seed_execution_request(plan=plan)


def single_activity_plan() -> ActivityPlan:
    return ActivityPlan(
        (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),)
    )


def two_step_plan() -> ActivityPlan:
    return ActivityPlan(
        (
            PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),
            PlannedActivity(
                ActivityId("wait-api"),
                StartNode(NodeTarget("api-ready")),
                (ActivityDependency(ActivityId("start-api")),),
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
