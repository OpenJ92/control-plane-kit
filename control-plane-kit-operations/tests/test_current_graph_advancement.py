from __future__ import annotations

import concurrent.futures
import os
import unittest

import psycopg

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget
from control_plane_kit_core.planning import PlannedActivity, StartNode
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph,
    CurrentGraphAdvancementCommandService,
    CurrentGraphAdvancementConflict,
    CurrentGraphAdvancementDenied,
    CurrentGraphAdvancementIdempotencyConflict,
    CurrentGraphAdvancementIncomplete,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    GraphVersionRecord,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    RetryIdentity,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import IdempotencyKey


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class CurrentGraphAdvancementTests(unittest.TestCase):
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
        self.seed_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self, *ids: str) -> CurrentGraphAdvancementCommandService:
        return CurrentGraphAdvancementCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:05:00Z",
            id_factory=Sequence(*ids),
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
        key: str = "advance-a",
        worker_id: str = "worker-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
        expected_current_graph_id: str = "graph-current",
        desired_graph_id: str = "graph-desired",
    ) -> AdvanceCurrentGraph:
        return AdvanceCurrentGraph(
            workspace_id="workspace-a",
            run_id="run-a",
            plan_id="plan-a",
            expected_current_graph_id=expected_current_graph_id,
            desired_graph_id=desired_graph_id,
            authority=self.authority(worker_id, scopes),
            idempotency_key=IdempotencyKey(key),
        )

    def test_complete_durable_success_advances_current_graph_once(self) -> None:
        self.seed_succeeded_run()

        result = self.service("event-advance", "action-advance").execute(
            self.command()
        )
        replay = self.service("unused-event", "unused-action").execute(self.command())

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(workspace.current_graph_id, "graph-desired")
        self.assertIs(result.event.kind, ActivityEventKind.CURRENT_GRAPH_ADVANCED)
        self.assertIs(result.action.action_type, LifecycleOperationKind.ADVANCE_CURRENT_GRAPH)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.event, result.event)
        self.assertEqual(replay.action, result.action)
        self.assertEqual(
            [event.kind for event in events],
            [
                ActivityEventKind.RUN_OPENED,
                ActivityEventKind.RUN_STARTED,
                ActivityEventKind.STEP_STARTED,
                ActivityEventKind.STEP_SUCCEEDED,
                ActivityEventKind.RUN_SUCCEEDED,
                ActivityEventKind.CURRENT_GRAPH_ADVANCED,
            ],
        )

    def test_incomplete_uncertain_or_failed_evidence_cannot_advance(self) -> None:
        for step_kind in (
            ActivityEventKind.STEP_UNCERTAIN,
            ActivityEventKind.STEP_UNSUPPORTED,
            ActivityEventKind.STEP_FAILED,
        ):
            with self.subTest(step_kind=step_kind):
                self.reset_truth()
                self.seed_succeeded_run(step_kind=step_kind)

                with self.assertRaises(CurrentGraphAdvancementIncomplete):
                    self.service("unused-event", "unused-action").execute(
                        self.command()
                    )

                with self.unit_of_work() as unit_of_work:
                    workspace = unit_of_work.stores.workspaces.get("workspace-a")
                    actions = unit_of_work.stores.activity_history.actions_for_session(
                        "session-a"
                    )
                self.assertEqual(workspace.current_graph_id, "graph-current")
                self.assertEqual(actions, ())

    def test_scope_worker_and_stale_graph_fail_closed(self) -> None:
        self.seed_succeeded_run()

        with self.assertRaises(CurrentGraphAdvancementDenied):
            self.service("unused-event", "unused-action").execute(
                self.command(scopes=())
            )
        with self.assertRaises(CurrentGraphAdvancementDenied):
            self.service("unused-event", "unused-action").execute(
                self.command(worker_id="worker-b", key="advance-worker-b")
            )
        with self.assertRaises(CurrentGraphAdvancementConflict):
            self.service("unused-event", "unused-action").execute(
                self.command(
                    key="advance-stale",
                    expected_current_graph_id="graph-stale",
                )
            )

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        self.assertEqual(workspace.current_graph_id, "graph-current")

    def test_changed_idempotent_intent_conflicts_without_second_event(self) -> None:
        self.seed_succeeded_run()
        self.service("event-advance", "action-advance").execute(self.command())

        with self.assertRaises(CurrentGraphAdvancementIdempotencyConflict):
            self.service("unused-event", "unused-action").execute(
                self.command(worker_id="worker-b")
            )

        with self.unit_of_work() as unit_of_work:
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(
            sum(event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED for event in events),
            1,
        )

    def test_late_action_failure_rolls_back_pointer_and_event(self) -> None:
        self.seed_succeeded_run()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_action(
                OperationActionRecord(
                    "action-duplicate",
                    "session-a",
                    1,
                    LifecycleOperationKind.START_RUN,
                    "worker-a",
                    created_at="2026-07-22T13:04:00Z",
                    idempotency_key="existing",
                    intent_fingerprint="existing",
                )
            )
            unit_of_work.commit()

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service("event-advance", "action-duplicate").execute(self.command())

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(workspace.current_graph_id, "graph-current")
        self.assertEqual(
            sum(event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED for event in events),
            0,
        )

    def test_concurrent_advancement_has_one_winner(self) -> None:
        self.seed_succeeded_run()

        def advance(label: str) -> str:
            try:
                result = self.service(
                    f"event-{label}",
                    f"action-{label}",
                ).execute(self.command(key=f"advance-{label}"))
                return result.action.action_id
            except CurrentGraphAdvancementConflict:
                return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(advance, ("one", "two")))

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(sum(result != "conflict" for result in results), 1)
        self.assertEqual(workspace.current_graph_id, "graph-desired")
        self.assertEqual(
            sum(event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED for event in events),
            1,
        )

    def seed_truth(self) -> None:
        plan = ActivityPlan(
            (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),)
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph=DeploymentGraph("current"),
                    created_by="operator-a",
                    created_at="2026-07-22T12:00:00Z",
                )
            )
            stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=DeploymentGraph("desired"),
                    created_by="operator-a",
                    created_at="2026-07-22T12:00:30Z",
                )
            )
            stores.workspaces.set_current_graph("workspace-a", "graph-current")
            stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
            stores.activity_history.add_session(
                OperationSessionRecord(
                    "session-a",
                    "workspace-a",
                    "operator-a",
                    "Deploy",
                    OperationSessionStatus.OPEN,
                    "2026-07-22T12:01:00Z",
                )
            )
            stores.activity_history.add_plan(
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
            stores.activity_history.add_approval_request(
                ApprovalRequestRecord(
                    "approval-request-a",
                    "session-a",
                    "plan-a",
                    "operator-a",
                    "2026-07-22T12:03:00Z",
                    PolicyScope.PLAN_APPROVE,
                    RiskLevel.LOW,
                    False,
                )
            )
            stores.activity_history.add_approval_decision(
                ApprovalDecisionRecord(
                    "approval-decision-a",
                    "approval-request-a",
                    "manager-a",
                    ApprovalDecisionKind.APPROVED,
                    PolicyScope.PLAN_APPROVE,
                    "2026-07-22T12:03:30Z",
                )
            )
            stores.execution.add_request(
                ExecutionRequestRecord(
                    ExecutionRequestIdentity(
                        "request-a",
                        "workspace-a",
                        "session-a",
                        "plan-a",
                    ),
                    ExecutionRequestStatus.CLAIMED,
                    "operator-a",
                    "2026-07-22T12:04:00Z",
                    "approval-request-a",
                    "approval-decision-a",
                    ExecutionIdempotency("execute-a", "fingerprint-a"),
                    ClaimIdentity(
                        "worker-a",
                        "2026-07-22T12:04:30Z",
                        "2026-07-22T12:14:30Z",
                    ),
                )
            )
            unit_of_work.commit()

    def reset_truth(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_truth()

    def seed_succeeded_run(
        self,
        *,
        step_kind: ActivityEventKind = ActivityEventKind.STEP_SUCCEEDED,
        activity_id: str = "start-api",
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_run(
                ActivityRunRecord(
                    "run-a",
                    "plan-a",
                    AdmittedRun("request-a"),
                    RetryIdentity(1),
                    ActivityRunStatus.SUCCEEDED,
                    "2026-07-22T13:00:00Z",
                    started_at="2026-07-22T13:00:30Z",
                    settled_at="2026-07-22T13:04:00Z",
                )
            )
            for ordinal, (kind, event_activity_id) in enumerate(
                (
                    (ActivityEventKind.RUN_OPENED, None),
                    (ActivityEventKind.RUN_STARTED, None),
                    (ActivityEventKind.STEP_STARTED, activity_id),
                    (step_kind, activity_id),
                    (ActivityEventKind.RUN_SUCCEEDED, None),
                ),
                start=1,
            ):
                stores.execution.add_event(
                    ActivityEventRecord(
                        f"event-{ordinal}",
                        "run-a",
                        ordinal,
                        kind,
                        f"2026-07-22T13:00:{ordinal:02d}Z",
                        activity_id=event_activity_id,
                        evidence=BoundedEvidence.from_mapping({"seed": "test"}),
                    )
                )
            unit_of_work.commit()


if __name__ == "__main__":
    unittest.main()
