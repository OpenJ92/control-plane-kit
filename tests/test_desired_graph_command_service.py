import os
import unittest

import psycopg

from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.stores import (
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    CloseOperationSession,
    DesiredGraphCommandService,
    DesiredGraphIdempotencyConflict,
    DesiredGraphSessionConflict,
    IdempotencyKey,
    OperationCommandService,
    SetDesiredGraph,
    StaleDesiredGraph,
    StartOperationSession,
)
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class DesiredGraphCommandServiceTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Workspace A"))
        self._start_session("workspace-a", "session-a", "start-action")

    def service(self, *ids: str) -> DesiredGraphCommandService:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return DesiredGraphCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-16T00:00:00Z",
            id_factory=Sequence(*ids),
        )

    def command(
        self,
        *,
        graph_name: str = "desired-a",
        expected: str | None = None,
        key: str = "set-desired",
    ) -> SetDesiredGraph:
        return SetDesiredGraph(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="jacob",
            graph=DeploymentGraph(graph_name),
            expected_desired_graph_id=expected,
            idempotency_key=IdempotencyKey(key),
        )

    def test_graph_pointer_and_action_commit_as_one_command(self):
        result = self.service("graph-a", "action-a").execute(self.command())

        self.assertEqual(result.graph_version.version, 1)
        self.assertEqual(result.action.action_type, OperationActionKind.SET_DESIRED_GRAPH)
        self.assertEqual(result.action.ordinal, 2)
        self.assertEqual(
            self.stores.workspace.get("workspace-a").desired_graph_id,
            "graph-a",
        )
        self.assertEqual(self.stores.graph_topology.get("graph-a").graph_descriptor["name"], "desired-a")
        self.assertEqual(
            self.stores.activity_history.actions_for_session("session-a")[-1].payload,
            {
                "workspace_id": "workspace-a",
                "previous_desired_graph_id": None,
                "desired_graph_id": "graph-a",
            },
        )

    def test_stale_expected_pointer_rejects_without_partial_writes(self):
        self.service("graph-a", "action-a").execute(self.command())

        with self.assertRaises(StaleDesiredGraph):
            self.service("graph-b", "action-b").execute(
                self.command(graph_name="desired-b", expected=None, key="stale")
            )

        self.assertEqual(self.stores.workspace.get("workspace-a").desired_graph_id, "graph-a")
        with self.assertRaises(KeyError):
            self.stores.graph_topology.get("graph-b")
        self.assertEqual(len(self.stores.activity_history.actions_for_session("session-a")), 2)

    def test_identical_request_replays_original_evidence(self):
        command = self.command()
        first = self.service("graph-a", "action-a").execute(command)
        replay = self.service("unused-graph", "unused-action").execute(command)

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.graph_version.graph_id, "graph-a")
        self.assertEqual(replay.action.action_id, "action-a")
        self.assertEqual(len(self.stores.activity_history.actions_for_session("session-a")), 2)

        with self.assertRaises(DesiredGraphIdempotencyConflict):
            self.service("unused-graph", "unused-action").execute(
                self.command(graph_name="different")
            )

    def test_session_must_be_open_and_owned_by_workspace(self):
        self.stores.workspace.create(WorkspaceRecord("workspace-b", "Workspace B"))
        self._start_session("workspace-b", "session-b", "start-action-b")
        foreign = SetDesiredGraph(
            "session-b",
            "workspace-a",
            "jacob",
            DeploymentGraph("desired"),
            None,
            IdempotencyKey("foreign"),
        )
        with self.assertRaises(DesiredGraphSessionConflict):
            self.service("graph", "action").execute(foreign)

        self._operation_service("close-action").execute(
            CloseOperationSession("session-a", "jacob", IdempotencyKey("close"))
        )
        with self.assertRaises(DesiredGraphSessionConflict):
            self.service("graph", "action").execute(self.command())

    def test_late_action_failure_rolls_back_graph_and_pointer(self):
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service("graph-a", "start-action").execute(self.command())

        self.assertIsNone(self.stores.workspace.get("workspace-a").desired_graph_id)
        with self.assertRaises(KeyError):
            self.stores.graph_topology.get("graph-a")
        self.assertEqual(len(self.stores.activity_history.actions_for_session("session-a")), 1)

    def _start_session(self, workspace_id: str, session_id: str, action_id: str) -> None:
        self._operation_service(session_id, action_id).execute(
            StartOperationSession(
                workspace_id,
                "jacob",
                "Edit desired graph",
                IdempotencyKey(f"start-{session_id}"),
            )
        )

    def _operation_service(self, *ids: str) -> OperationCommandService:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return OperationCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=lambda: "2026-07-16T00:00:00Z",
            id_factory=Sequence(*ids),
        )


if __name__ == "__main__":
    unittest.main()
