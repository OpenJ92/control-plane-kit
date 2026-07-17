import concurrent.futures
import os
import threading
import unittest

import psycopg

from control_plane_kit.stores import OperationActionKind, PostgresUnitOfWork, WorkspaceRecord
from control_plane_kit.workflows import (
    CancelOperationSession,
    CloseOperationSession,
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandService,
    OperationIdempotencyConflict,
    OperationSessionNotFound,
    OperationSessionStateConflict,
    OperationWorkspaceNotFound,
    RecordOperationAction,
    StartOperationSession,
)
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class OperationCommandServiceTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Workspace A"))

    def service(self, *ids: str, clock=None) -> OperationCommandService:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return OperationCommandService(
            lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url)),
            clock=clock or (lambda: "2026-07-15T00:00:00Z"),
            id_factory=Sequence(*ids),
        )

    def start(self, service: OperationCommandService, *, key: str = "start"):
        return service.execute(
            StartOperationSession(
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Swap API",
                idempotency_key=IdempotencyKey(key),
            )
        )

    def test_start_commits_session_and_initial_action_atomically(self):
        result = self.start(self.service("session-a", "action-start"))

        self.assertEqual(result.session.session_id, "session-a")
        self.assertEqual(result.action.action_type, OperationActionKind.SESSION_STARTED)
        self.assertEqual(result.action.ordinal, 1)
        self.assertEqual(
            self.stores.activity_history.get_session("session-a").idempotency_key,
            "start",
        )

    def test_same_intent_replays_and_conflicting_intent_fails(self):
        first = self.start(self.service("session-a", "action-start"))
        replay = self.start(self.service("unused-session", "unused-action"))

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.session.session_id, first.session.session_id)
        self.assertEqual(replay.action.action_id, first.action.action_id)

        with self.assertRaises(OperationIdempotencyConflict):
            self.service("unused-session", "unused-action").execute(
                StartOperationSession(
                    workspace_id="workspace-a",
                    actor_id="jacob",
                    title="Different intent",
                    idempotency_key=IdempotencyKey("start"),
                )
            )

    def test_start_requires_workspace_truth_and_replay_returns_original_state(self):
        service = self.service("session-a", "action-start", "action-close")
        self.start(service)
        closed = service.execute(
            CloseOperationSession("session-a", "jacob", IdempotencyKey("close"))
        )
        close_replay = self.service("unused-action").execute(
            CloseOperationSession("session-a", "jacob", IdempotencyKey("close"))
        )
        self.assertTrue(close_replay.replayed)
        self.assertEqual(close_replay.session, closed.session)

        replay = self.start(self.service("unused-session", "unused-action"))
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.session.status.value, "open")
        self.assertIsNone(replay.session.closed_at)

        with self.assertRaises(OperationWorkspaceNotFound):
            self.service("unused-session", "unused-action").execute(
                StartOperationSession(
                    "missing-workspace",
                    "jacob",
                    "Invalid",
                    IdempotencyKey("missing"),
                )
            )

    def test_concurrent_identical_starts_converge_on_one_session(self):
        barrier = threading.Barrier(2)

        def start(ids):
            service = self.service(*ids)
            barrier.wait(timeout=5)
            return self.start(service, key="same-request")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    start,
                    (("session-a", "action-a"), ("session-b", "action-b")),
                )
            )

        self.assertEqual(len({result.session.session_id for result in results}), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)
        sessions = self.stores.activity_history.sessions_for_workspace("workspace-a")
        self.assertEqual(len(sessions), 1)

    def test_action_replay_is_deterministic_and_conflict_is_explicit(self):
        service = self.service("session-a", "action-start", "action-a")
        self.start(service)
        command = RecordOperationAction(
            "session-a",
            "jacob",
            OperationActionKind.ADD_BLOCK,
            IdempotencyKey("add"),
            {"node_id": "api-v2"},
        )
        first = service.execute(command)
        replay = self.service("unused").execute(command)

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.action.action_id, replay.action.action_id)
        with self.assertRaises(OperationIdempotencyConflict):
            self.service("unused").execute(
                RecordOperationAction(
                    "session-a",
                    "jacob",
                    OperationActionKind.ADD_BLOCK,
                    IdempotencyKey("add"),
                    {"node_id": "api-v3"},
                )
            )

    def test_closed_and_cancelled_sessions_reject_new_actions(self):
        close_service = self.service("session-a", "action-start", "action-close")
        self.start(close_service)
        close_service.execute(
            CloseOperationSession("session-a", "jacob", IdempotencyKey("close"))
        )
        with self.assertRaises(OperationSessionStateConflict):
            self.service("unused").execute(
                RecordOperationAction(
                    "session-a",
                    "jacob",
                    OperationActionKind.ADD_BLOCK,
                    IdempotencyKey("after-close"),
                )
            )

        cancel_service = self.service("session-b", "action-start-b", "action-cancel")
        cancel_service.execute(
            StartOperationSession(
                "workspace-a", "jacob", "Cancel me", IdempotencyKey("start-b")
            )
        )
        cancel_service.execute(
            CancelOperationSession("session-b", "jacob", IdempotencyKey("cancel"))
        )
        with self.assertRaises(OperationSessionStateConflict):
            self.service("unused").execute(
                RecordOperationAction(
                    "session-b",
                    "jacob",
                    OperationActionKind.ADD_BLOCK,
                    IdempotencyKey("after-cancel"),
                )
            )

    def test_missing_session_and_reserved_lifecycle_actions_fail_closed(self):
        with self.assertRaises(OperationSessionNotFound):
            self.service("unused").execute(
                CloseOperationSession("missing", "jacob", IdempotencyKey("close"))
            )
        with self.assertRaises(InvalidOperationCommand):
            self.service("unused").execute(
                RecordOperationAction(
                    "missing",
                    "jacob",
                    OperationActionKind.SESSION_CLOSED,
                    IdempotencyKey("forged"),
                )
            )

    def test_late_action_failure_rolls_back_new_session(self):
        self.start(self.service("existing-session", "action-collision"))
        self.stores.workspace.create(WorkspaceRecord("workspace-b", "Workspace B"))

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service("rolled-back-session", "action-collision").execute(
                StartOperationSession(
                    "workspace-b", "jacob", "Rollback", IdempotencyKey("rollback")
                )
            )

        with self.assertRaises(KeyError):
            self.stores.activity_history.get_session("rolled-back-session")

    def test_late_action_failure_rolls_back_terminal_projection(self):
        self.start(self.service("session-a", "action-start"))
        self.start(
            self.service("session-b", "action-collision"),
            key="start-b",
        )

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service(
                "action-collision",
                clock=lambda: "2026-07-15T00:01:00Z",
            ).execute(
                CloseOperationSession(
                    "session-a",
                    "jacob",
                    IdempotencyKey("close"),
                )
            )

        persisted = self.stores.activity_history.get_session("session-a")
        self.assertEqual(persisted.status.value, "open")
        self.assertIsNone(persisted.closed_at)
        self.assertEqual(
            len(self.stores.activity_history.actions_for_session("session-a")),
            1,
        )

    def test_concurrent_terminal_transitions_cannot_both_publish(self):
        self.start(self.service("session-a", "action-start"))
        barrier = threading.Barrier(2)

        def run(command, action_id):
            service = self.service(action_id, clock=lambda: "2026-07-15T00:01:00Z")
            barrier.wait(timeout=5)
            try:
                return service.execute(command)
            except OperationSessionStateConflict as error:
                return error

        close = CloseOperationSession("session-a", "jacob", IdempotencyKey("close"))
        cancel = CancelOperationSession("session-a", "jacob", IdempotencyKey("cancel"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(
                    lambda pair: run(*pair),
                    ((close, "close-action"), (cancel, "cancel-action")),
                )
            )

        self.assertEqual(sum(isinstance(value, OperationSessionStateConflict) for value in outcomes), 1)
        persisted = self.stores.activity_history.actions_for_session("session-a")
        self.assertEqual(len(persisted), 2)


if __name__ == "__main__":
    unittest.main()
