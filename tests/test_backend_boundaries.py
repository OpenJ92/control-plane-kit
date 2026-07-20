import os
import unittest

import psycopg

from control_plane_kit.core.topology.graph import DeploymentGraph
from control_plane_kit.policies import ApprovalPolicy, DestructiveActivityPolicy
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
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


class BackendBoundaryTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Demo"))

    def operation_service(self, ids):
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return OperationCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-15T00:01:00Z",
            id_factory=Sequence(ids),
        )

    def test_workspace_and_graph_truth_are_owned_by_stores(self):
        workspaces = self.stores.workspace
        graphs = self.stores.graph_topology
        graphs.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-a",
                workspace_id="workspace-a",
                version=1,
                graph=DeploymentGraph(name="current"),
                created_by="jacob",
                created_at="2026-07-15T00:00:00Z",
            )
        )

        workspaces.set_current_graph("workspace-a", "graph-a")

        self.assertEqual(workspaces.get("workspace-a").current_graph_id, "graph-a")
        self.assertEqual(graphs.latest_for_workspace("workspace-a").graph_descriptor["name"], "current")

    def test_workflow_records_intent_without_writing_graph_truth(self):
        graphs = self.stores.graph_topology
        graphs.save(
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
        session = service.execute(
            StartOperationSession(
                "workspace-a", "jacob", "Prepare graph edit", IdempotencyKey("start")
            )
        ).session

        service.execute(
            RecordOperationAction(
                session_id=session.session_id,
                action_type=OperationActionKind.PROPOSE_DESIRED_GRAPH,
                actor_id="jacob",
                idempotency_key=IdempotencyKey("propose"),
                payload={"desired_graph_id": "graph-desired"},
            )
        )

        self.assertEqual(graphs.latest_for_workspace("workspace-a").graph_id, "graph-current")
        self.assertEqual(
            history.actions_for_session("session-a")[-1].action_type.value,
            "propose_desired_graph",
        )

    def test_policy_decisions_do_not_create_workflow_records(self):
        history = self.stores.activity_history
        decision = ApprovalPolicy().can_approve_plan(["plan:approve"])

        self.assertTrue(decision.allowed)
        self.assertEqual(history.actions_for_session("session-a"), ())

    def test_destructive_classification_is_data_before_execution(self):
        decision = DestructiveActivityPolicy().classify("delete_history")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.required_scope, "plan:approve-destructive")


if __name__ == "__main__":
    unittest.main()
