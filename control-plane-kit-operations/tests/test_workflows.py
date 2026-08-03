from __future__ import annotations

import concurrent.futures
import os
import threading
import unittest

import psycopg

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    CancelOperationSession,
    CloseOperationSession,
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandService,
    OperationIdempotencyConflict,
    OperationSessionStateConflict,
    OperationWorkspaceNotFound,
    RecordOperationAction,
    StartOperationSession,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class BlockingClock:
    def __init__(self, value: str) -> None:
        self.value = value
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> str:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test clock was not released")
        return self.value


class OperationWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def service(self, *ids: str, clock=None) -> OperationCommandService:
        return OperationCommandService(
            self.unit_of_work,
            clock=clock or (lambda: "2026-07-22T10:00:00Z"),
            id_factory=Sequence(*ids),
        )

    def start(self, service: OperationCommandService, *, key: str = "start"):
        return service.execute(
            StartOperationSession(
                workspace_id="workspace-a",
                actor_id="operator-a",
                title="Swap API",
                idempotency_key=IdempotencyKey(key),
            )
        )

    def test_start_commits_session_and_initial_action_atomically(self) -> None:
        result = self.start(self.service("session-a", "action-start"))

        self.assertFalse(result.replayed)
        self.assertEqual(result.session.session_id, "session-a")
        self.assertEqual(result.session.status, OperationSessionStatus.OPEN)
        self.assertEqual(
            result.action.action_type,
            OperatorCommandKind.START_OPERATION_SESSION,
        )
        self.assertEqual(result.action.ordinal, 1)

        with self.unit_of_work() as unit_of_work:
            stored = unit_of_work.stores.activity_history.get_session("session-a")
            actions = unit_of_work.stores.activity_history.actions_for_session(
                "session-a"
            )
            self.assertEqual(stored.idempotency_key, "start")
            self.assertEqual(tuple(action.action_id for action in actions), ("action-start",))

    def test_start_replays_same_intent_and_conflicts_on_changed_intent(self) -> None:
        first = self.start(self.service("session-a", "action-start"))
        replay = self.start(self.service("unused-session", "unused-action"))

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.session, first.session)
        self.assertEqual(replay.action, first.action)

        with self.assertRaises(OperationIdempotencyConflict):
            self.service("unused-session", "unused-action").execute(
                StartOperationSession(
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    title="Different",
                    idempotency_key=IdempotencyKey("start"),
                )
            )

    def test_concurrent_identical_starts_converge_on_one_session(self) -> None:
        barrier = threading.Barrier(2)

        def submit(ids: tuple[str, str]):
            barrier.wait(timeout=5)
            return self.start(self.service(*ids))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    submit,
                    (("session-a", "action-a"), ("session-b", "action-b")),
                )
            )

        self.assertEqual(len({result.session.session_id for result in results}), 1)
        self.assertEqual(len({result.action.action_id for result in results}), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)

    def test_start_requires_workspace_truth(self) -> None:
        with self.assertRaises(OperationWorkspaceNotFound):
            self.service("session-a", "action-start").execute(
                StartOperationSession(
                    workspace_id="missing-workspace",
                    actor_id="operator-a",
                    title="Invalid",
                    idempotency_key=IdempotencyKey("missing"),
                )
            )

    def test_action_records_ordinals_replay_and_conflict(self) -> None:
        service = self.service("session-a", "action-start", "action-a", "action-b")
        self.start(service)
        command = RecordOperationAction(
            session_id="session-a",
            actor_id="operator-a",
            action_type=OperatorCommandKind.SET_DESIRED_GRAPH,
            idempotency_key=IdempotencyKey("desired"),
            payload={"graph_id": "graph-a"},
        )
        first = service.execute(command)
        replay = self.service("unused").execute(command)
        second = service.execute(
            RecordOperationAction(
                session_id="session-a",
                actor_id="operator-a",
                action_type=OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
                idempotency_key=IdempotencyKey("plan"),
            )
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.action, replay.action)
        self.assertEqual(first.action.ordinal, 2)
        self.assertEqual(second.action.ordinal, 3)

        with self.assertRaises(OperationIdempotencyConflict):
            self.service("unused").execute(
                RecordOperationAction(
                    session_id="session-a",
                    actor_id="operator-a",
                    action_type=OperatorCommandKind.SET_DESIRED_GRAPH,
                    idempotency_key=IdempotencyKey("desired"),
                    payload={"graph_id": "graph-b"},
                )
            )

    def test_closed_session_rejects_new_actions(self) -> None:
        service = self.service("session-a", "action-start", "action-close")
        self.start(service)
        closed = service.execute(
            CloseOperationSession("session-a", "operator-a", IdempotencyKey("close"))
        )

        self.assertEqual(closed.session.status, OperationSessionStatus.CLOSED)
        self.assertEqual(closed.action.ordinal, 2)

        with self.assertRaises(OperationSessionStateConflict):
            self.service("unused").execute(
                RecordOperationAction(
                    "session-a",
                    "operator-a",
                    OperatorCommandKind.SET_DESIRED_GRAPH,
                    IdempotencyKey("after-close"),
                )
            )

    def test_cancelled_session_replays_cancel_and_rejects_new_actions(self) -> None:
        self.start(self.service("session-a", "action-start"))
        command = CancelOperationSession(
            "session-a",
            "operator-a",
            IdempotencyKey("cancel"),
        )
        cancelled = self.service("action-cancel").execute(command)
        replay = self.service("unused").execute(command)

        self.assertEqual(cancelled.session.status, OperationSessionStatus.CANCELLED)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.session, cancelled.session)
        self.assertEqual(replay.action, cancelled.action)
        with self.assertRaises(OperationSessionStateConflict):
            self.service("unused").execute(
                RecordOperationAction(
                    "session-a",
                    "operator-a",
                    OperatorCommandKind.SET_DESIRED_GRAPH,
                    IdempotencyKey("after-cancel"),
                )
            )

    def test_manual_action_and_close_follow_the_session_lock_order(self) -> None:
        self.start(self.service("session-a", "action-start"))
        manual_clock = BlockingClock("2026-07-22T10:01:00Z")
        manual = self.service("action-manual", clock=manual_clock)
        close = self.service("action-close")
        command = RecordOperationAction(
            "session-a",
            "operator-a",
            OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
            IdempotencyKey("manual"),
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            manual_future = executor.submit(manual.execute, command)
            self.assertTrue(manual_clock.entered.wait(timeout=5))
            close_future = executor.submit(
                close.execute,
                CloseOperationSession(
                    "session-a",
                    "operator-a",
                    IdempotencyKey("close"),
                ),
            )
            with self.assertRaises(concurrent.futures.TimeoutError):
                close_future.result(timeout=0.1)
            manual_clock.release.set()
            manual_result = manual_future.result(timeout=5)
            close_result = close_future.result(timeout=5)

        self.assertEqual(manual_result.action.ordinal, 2)
        self.assertEqual(close_result.action.ordinal, 3)

    def test_close_fences_a_later_manual_action_without_partial_history(self) -> None:
        self.start(self.service("session-a", "action-start"))
        close_clock = BlockingClock("2026-07-22T10:01:00Z")
        close = self.service("action-close", clock=close_clock)
        manual = self.service("action-manual")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            close_future = executor.submit(
                close.execute,
                CloseOperationSession(
                    "session-a",
                    "operator-a",
                    IdempotencyKey("close"),
                ),
            )
            self.assertTrue(close_clock.entered.wait(timeout=5))
            manual_future = executor.submit(
                manual.execute,
                RecordOperationAction(
                    "session-a",
                    "operator-a",
                    OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
                    IdempotencyKey("manual"),
                ),
            )
            with self.assertRaises(concurrent.futures.TimeoutError):
                manual_future.result(timeout=0.1)
            close_clock.release.set()
            close_future.result(timeout=5)
            with self.assertRaises(OperationSessionStateConflict):
                manual_future.result(timeout=5)

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                tuple(
                    action.action_type.value
                    for action in unit_of_work.stores.activity_history.actions_for_session(
                        "session-a"
                    )
                ),
                ("start-operation-session", "close-operation-session"),
            )
            unit_of_work.commit()

    def test_concurrent_close_and_cancel_are_write_once_without_deadlock(self) -> None:
        for attempt in range(8):
            session_id = f"terminal-session-{attempt}"
            self.service(session_id, f"start-{attempt}").execute(
                StartOperationSession(
                    "workspace-a",
                    "operator-a",
                    f"Terminal race {attempt}",
                    IdempotencyKey(f"start-{attempt}"),
                )
            )
            barrier = threading.Barrier(2)

            def terminal(command, action_id):
                barrier.wait(timeout=5)
                try:
                    return self.service(action_id).execute(command)
                except OperationSessionStateConflict as error:
                    return error

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                close_future = executor.submit(
                    terminal,
                    CloseOperationSession(
                        session_id,
                        "operator-a",
                        IdempotencyKey(f"close-{attempt}"),
                    ),
                    f"close-action-{attempt}",
                )
                cancel_future = executor.submit(
                    terminal,
                    CancelOperationSession(
                        session_id,
                        "operator-a",
                        IdempotencyKey(f"cancel-{attempt}"),
                    ),
                    f"cancel-action-{attempt}",
                )
                outcomes = (
                    close_future.result(timeout=5),
                    cancel_future.result(timeout=5),
                )

            self.assertEqual(
                sum(isinstance(item, OperationSessionStateConflict) for item in outcomes),
                1,
            )
            with self.unit_of_work() as unit_of_work:
                actions = unit_of_work.stores.activity_history.actions_for_session(
                    session_id
                )
                self.assertEqual(tuple(action.ordinal for action in actions), (1, 2))
                self.assertIn(
                    actions[-1].action_type,
                    (
                        OperatorCommandKind.CLOSE_OPERATION_SESSION,
                        OperatorCommandKind.CANCEL_OPERATION_SESSION,
                    ),
                )
                unit_of_work.commit()

    def test_independent_session_commands_do_not_share_a_lifecycle_lock(self) -> None:
        self.start(self.service("session-a", "action-start"))
        self.service("session-b", "action-start-b").execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Independent",
                IdempotencyKey("start-b"),
            )
        )
        blocked_clock = BlockingClock("2026-07-22T10:01:00Z")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            blocked = executor.submit(
                self.service("action-a", clock=blocked_clock).execute,
                RecordOperationAction(
                    "session-a",
                    "operator-a",
                    OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
                    IdempotencyKey("manual-a"),
                ),
            )
            self.assertTrue(blocked_clock.entered.wait(timeout=5))
            independent = executor.submit(
                self.service("action-b").execute,
                RecordOperationAction(
                    "session-b",
                    "operator-a",
                    OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
                    IdempotencyKey("manual-b"),
                ),
            )
            self.assertEqual(independent.result(timeout=2).action.ordinal, 2)
            blocked_clock.release.set()
            self.assertEqual(blocked.result(timeout=5).action.ordinal, 2)

    def test_reserved_lifecycle_actions_cannot_be_forged(self) -> None:
        with self.assertRaisesRegex(InvalidOperationCommand, "reserved"):
            RecordOperationAction(
                "session-a",
                "operator-a",
                OperatorCommandKind.CLOSE_OPERATION_SESSION,
                IdempotencyKey("forged"),
            )

    def test_late_action_failure_rolls_back_session_start(self) -> None:
        self.start(self.service("existing-session", "action-collision"))
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-b", "Workspace B")
            )
            unit_of_work.commit()

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service("rolled-back-session", "action-collision").execute(
                StartOperationSession(
                    "workspace-b",
                    "operator-a",
                    "Rollback",
                    IdempotencyKey("rollback"),
                )
            )

        with self.assertRaises(KeyError):
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.activity_history.get_session("rolled-back-session")

    def test_session_and_action_queries_are_deterministic(self) -> None:
        service = self.service("session-b", "action-b", "session-a", "action-a")
        service.execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Second",
                IdempotencyKey("second"),
            )
        )
        earlier = self.service(
            "session-a",
            "action-a",
            clock=lambda: "2026-07-22T09:00:00Z",
        )
        earlier.execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "First",
                IdempotencyKey("first"),
            )
        )

        with self.unit_of_work() as unit_of_work:
            sessions = unit_of_work.stores.activity_history.sessions_for_workspace(
                "workspace-a"
            )
            self.assertEqual(
                tuple(session.session_id for session in sessions),
                ("session-a", "session-b"),
            )

    def test_records_reject_untyped_status_and_action_kind(self) -> None:
        with self.assertRaisesRegex(Exception, "OperationSessionStatus"):
            OperationSessionRecord(
                "session-a",
                "workspace-a",
                "operator-a",
                "Bad",
                "open",  # type: ignore[arg-type]
                "2026-07-22T10:00:00Z",
            )
        with self.assertRaisesRegex(Exception, "closed command or lifecycle kind"):
            OperationActionRecord(
                "action-a",
                "session-a",
                1,
                "set-desired-graph",  # type: ignore[arg-type]
                "operator-a",
                created_at="2026-07-22T10:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
