import os
import unittest

import psycopg

from control_plane_kit import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, StartNode
from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ActivityRunService,
    ApprovalWorkflowService,
    CloseOperationSession,
    IdempotencyKey,
    OperationCommandService,
    RecordOperationAction,
    StartOperationSession,
)
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


class WorkflowServiceTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Demo"))

    def operation_service(self, ids):
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return OperationCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-15T00:00:00Z",
            id_factory=Sequence(ids),
        )

    def test_session_service_starts_and_closes_sessions(self):
        service = self.operation_service(["session-a", "action-start", "action-close"])

        session = service.execute(
            StartOperationSession(
                "workspace-a", "jacob", "Swap API", IdempotencyKey("start")
            )
        ).session
        closed = service.execute(
            CloseOperationSession("session-a", "jacob", IdempotencyKey("close"))
        ).session

        self.assertEqual(session.status.value, "open")
        self.assertEqual(closed.status.value, "closed")
        self.assertEqual(closed.closed_at, "2026-07-15T00:00:00Z")

    def test_action_service_preserves_session_action_order(self):
        service = self.operation_service(
            ["session-a", "action-start", "action-a", "action-b"]
        )
        service.execute(
            StartOperationSession(
                "workspace-a", "jacob", "Swap API", IdempotencyKey("start")
            )
        )

        service.execute(
            RecordOperationAction(
                "session-a", "jacob", OperationActionKind.ADD_BLOCK, IdempotencyKey("add")
            )
        )
        service.execute(
            RecordOperationAction(
                "session-a",
                "jacob",
                OperationActionKind.CONNECT_SOCKET,
                IdempotencyKey("connect"),
            )
        )

        self.assertEqual(
            [
                (record.ordinal, record.action_type.value)
                for record in self.stores.activity_history.actions_for_session("session-a")
            ],
            [(1, "session_started"), (2, "add_block"), (3, "connect_socket")],
        )

    def test_approval_service_records_decision_without_execution(self):
        history = self.stores.activity_history
        self.operation_service(["session-a", "action-start"]).execute(
            StartOperationSession(
                "workspace-a", "jacob", "Approve plan", IdempotencyKey("start")
            )
        )
        approval = ApprovalWorkflowService(
            history,
            clock=Sequence(["2026-07-15T00:01:00Z"]),
            id_factory=Sequence(["approval-a"]),
        ).decide(
            session_id="session-a",
            target_id="plan-a",
            actor_id="manager",
            decision="approved",
            scope="plan:approve",
            comment="Looks safe.",
        )

        self.assertEqual(approval.decision, "approved")
        self.assertEqual(history.approvals_for_session("session-a")[0].target_id, "plan-a")

    def test_activity_run_service_records_plan_and_run_without_effects(self):
        history = self.stores.activity_history
        self.operation_service(["session-a", "action-start"]).execute(
            StartOperationSession(
                "workspace-a", "jacob", "Plan run", IdempotencyKey("start")
            )
        )
        service = ActivityRunService(
            history,
            clock=Sequence(["2026-07-15T00:01:00Z", "2026-07-15T00:02:00Z"]),
            id_factory=Sequence(["plan-a", "run-a"]),
        )

        plan = service.record_plan(
            session_id="session-a",
            base_graph_id="graph-current",
            desired_graph_id="graph-desired",
            plan=ActivityPlan(
                (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api-v2"))),)
            ),
        )
        run = service.open_run(plan_id=plan.plan_id)

        self.assertEqual(history.get_plan("plan-a").desired_graph_id, "graph-desired")
        self.assertEqual(history.get_plan("plan-a").plan, plan.plan)
        self.assertEqual(run.status, "open")

    def test_workflow_services_do_not_mutate_graph_truth(self):
        graph_store = self.stores.graph_topology
        graph_store.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=DeploymentGraph(name="current"),
                created_by="jacob",
                created_at="2026-07-15T00:00:00Z",
            )
        )
        history = self.stores.activity_history
        service = self.operation_service(["session-a", "action-start", "action-a"])
        service.execute(
            StartOperationSession(
                "workspace-a", "jacob", "Prepare graph edit", IdempotencyKey("start")
            )
        )
        service.execute(
            RecordOperationAction(
                session_id="session-a",
                action_type=OperationActionKind.REQUEST_GRAPH_EDIT,
                actor_id="jacob",
                idempotency_key=IdempotencyKey("edit"),
                payload={"desired_graph_id": "graph-desired"},
            )
        )

        self.assertEqual(graph_store.latest_for_workspace("workspace-a").graph_id, "graph-current")


if __name__ == "__main__":
    unittest.main()
