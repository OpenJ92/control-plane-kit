import unittest

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    InMemoryActivityHistoryStore,
    InMemoryGraphTopologyStore,
)
from control_plane_kit.workflows import (
    ActivityRunService,
    ApprovalWorkflowService,
    OperationActionService,
    OperationSessionService,
)


class Sequence:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0)


class WorkflowServiceTests(unittest.TestCase):
    def test_session_service_starts_and_closes_sessions(self):
        history = InMemoryActivityHistoryStore()
        clock = Sequence(["2026-07-15T00:00:00Z", "2026-07-15T00:01:00Z"])
        ids = Sequence(["session-a"])
        service = OperationSessionService(history, clock=clock, id_factory=ids)

        session = service.start(workspace_id="workspace-a", actor_id="jacob", title="Swap API")
        closed = service.close(session.session_id)

        self.assertEqual(session.status, "open")
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.closed_at, "2026-07-15T00:01:00Z")

    def test_action_service_preserves_session_action_order(self):
        history = InMemoryActivityHistoryStore()
        OperationSessionService(
            history,
            clock=Sequence(["2026-07-15T00:00:00Z"]),
            id_factory=Sequence(["session-a"]),
        ).start(workspace_id="workspace-a", actor_id="jacob", title="Swap API")
        actions = OperationActionService(
            history,
            clock=Sequence(["2026-07-15T00:01:00Z", "2026-07-15T00:02:00Z"]),
            id_factory=Sequence(["action-a", "action-b"]),
        )

        actions.record(session_id="session-a", action_type="add_block", actor_id="jacob")
        actions.record(session_id="session-a", action_type="connect_socket", actor_id="jacob")

        self.assertEqual(
            [(record.ordinal, record.action_type) for record in history.actions_for_session("session-a")],
            [(1, "add_block"), (2, "connect_socket")],
        )

    def test_approval_service_records_decision_without_execution(self):
        history = InMemoryActivityHistoryStore()
        approval = ApprovalWorkflowService(
            history,
            clock=Sequence(["2026-07-15T00:00:00Z"]),
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
        history = InMemoryActivityHistoryStore()
        service = ActivityRunService(
            history,
            clock=Sequence(["2026-07-15T00:00:00Z", "2026-07-15T00:01:00Z"]),
            id_factory=Sequence(["plan-a", "run-a"]),
        )

        plan = service.record_plan(
            session_id="session-a",
            base_graph_id="graph-current",
            desired_graph_id="graph-desired",
            payload={"activities": ["StartNode(api-v2)"]},
        )
        run = service.open_run(plan_id=plan.plan_id)

        self.assertEqual(history.get_plan("plan-a").desired_graph_id, "graph-desired")
        self.assertEqual(run.status, "open")

    def test_workflow_services_do_not_mutate_graph_truth(self):
        graph_store = InMemoryGraphTopologyStore()
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
        history = InMemoryActivityHistoryStore()
        OperationActionService(
            history,
            clock=Sequence(["2026-07-15T00:01:00Z"]),
            id_factory=Sequence(["action-a"]),
        ).record(
            session_id="session-a",
            action_type="request_graph_edit",
            actor_id="jacob",
            payload={"desired_graph_id": "graph-desired"},
        )

        self.assertEqual(graph_store.latest_for_workspace("workspace-a").graph_id, "graph-current")


if __name__ == "__main__":
    unittest.main()
