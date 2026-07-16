import os
import unittest

import psycopg

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
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
