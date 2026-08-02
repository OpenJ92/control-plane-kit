from __future__ import annotations

import concurrent.futures
import os
import threading
import unittest

import psycopg

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.activity_history import (
    PostgresActivityHistoryStore,
)
from control_plane_kit_operations.records import (
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)


class SessionSerializationPrimitiveTests(unittest.TestCase):
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
        self._create_session("session-a", "workspace-a", "action-start-a")
        self._create_session("session-b", "workspace-b", "action-start-b")

    def tearDown(self) -> None:
        self.connection.close()

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url)

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self.connect)

    def test_same_action_identity_blocks_until_transaction_finishes(self) -> None:
        first = self.connect()
        second = self.connect()
        try:
            PostgresActivityHistoryStore(first).lock_action_idempotency(
                "session-a", "command-a"
            )
            second.execute("SET LOCAL lock_timeout = '250ms'")

            with self.assertRaises(psycopg.errors.LockNotAvailable):
                PostgresActivityHistoryStore(second).lock_action_idempotency(
                    "session-a", "command-a"
                )

            second.rollback()
            first.rollback()
            PostgresActivityHistoryStore(second).lock_action_idempotency(
                "session-a", "command-a"
            )
        finally:
            first.rollback()
            second.rollback()
            first.close()
            second.close()

    def test_action_identity_locks_do_not_block_independent_sessions(self) -> None:
        first = self.connect()
        second = self.connect()
        try:
            PostgresActivityHistoryStore(first).lock_action_idempotency(
                "session-a", "shared-key"
            )
            second.execute("SET LOCAL lock_timeout = '250ms'")

            PostgresActivityHistoryStore(second).lock_action_idempotency(
                "session-b", "shared-key"
            )
        finally:
            first.rollback()
            second.rollback()
            first.close()
            second.close()

    def test_session_for_update_serializes_and_reveals_terminal_state(self) -> None:
        first = self.connect()
        second = self.connect()
        try:
            first_store = PostgresActivityHistoryStore(first)
            second_store = PostgresActivityHistoryStore(second)
            self.assertEqual(
                first_store.get_session_for_update("session-a").status,
                OperationSessionStatus.OPEN,
            )
            second.execute("SET LOCAL lock_timeout = '250ms'")
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                second_store.get_session_for_update("session-a")

            second.rollback()
            updated = first_store.transition_open_session(
                "session-a",
                replacement=OperationSessionStatus.CLOSED,
                closed_at="2026-08-02T12:01:00Z",
            )
            self.assertIsNotNone(updated)
            first.commit()

            self.assertEqual(
                second_store.get_session_for_update("session-a").status,
                OperationSessionStatus.CLOSED,
            )
        finally:
            first.rollback()
            second.rollback()
            first.close()
            second.close()

    def test_session_lock_serializes_unique_monotonic_action_ordinals(self) -> None:
        barrier = threading.Barrier(2)

        def append_action(action_id: str, key: str) -> int:
            barrier.wait(timeout=5)
            with self.unit_of_work() as unit_of_work:
                history = unit_of_work.stores.activity_history
                history.lock_action_idempotency("session-a", key)
                history.get_session_for_update("session-a")
                ordinal = history.next_action_ordinal("session-a")
                history.add_action(
                    OperationActionRecord(
                        action_id=action_id,
                        session_id="session-a",
                        ordinal=ordinal,
                        action_type=OperatorCommandKind.RECORD_OPERATION_ACTION,
                        actor_id="operator-a",
                        payload={},
                        created_at="2026-08-02T12:02:00Z",
                        idempotency_key=key,
                        intent_fingerprint=f"fingerprint-{key}",
                    )
                )
                unit_of_work.commit()
                return ordinal

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(append_action, "action-a", "command-a"),
                executor.submit(append_action, "action-b", "command-b"),
            )
            ordinals = {future.result(timeout=10) for future in futures}

        self.assertEqual(ordinals, {2, 3})
        actions = self.connection.execute(
            """
            SELECT action_id, ordinal
            FROM cpk_operation_actions
            WHERE session_id = 'session-a'
            ORDER BY ordinal
            """
        ).fetchall()
        self.assertEqual(actions[0], ("action-start-a", 1))
        self.assertEqual({row[0] for row in actions[1:]}, {"action-a", "action-b"})
        self.assertEqual({row[1] for row in actions[1:]}, {2, 3})

    def test_rollback_releases_locks_and_discards_partial_action(self) -> None:
        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.activity_history
            history.lock_action_idempotency("session-a", "rolled-back")
            history.get_session_for_update("session-a")
            history.add_action(
                OperationActionRecord(
                    action_id="action-rolled-back",
                    session_id="session-a",
                    ordinal=history.next_action_ordinal("session-a"),
                    action_type=OperatorCommandKind.RECORD_OPERATION_ACTION,
                    actor_id="operator-a",
                    payload={},
                    created_at="2026-08-02T12:03:00Z",
                    idempotency_key="rolled-back",
                    intent_fingerprint="fingerprint-rolled-back",
                )
            )

        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.activity_history
            history.lock_action_idempotency("session-a", "rolled-back")
            history.get_session_for_update("session-a")
            self.assertIsNone(
                history.action_for_idempotency("session-a", "rolled-back")
            )
            self.assertEqual(history.next_action_ordinal("session-a"), 2)

    def _create_session(
        self,
        session_id: str,
        workspace_id: str,
        action_id: str,
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id, workspace_id)
            )
            history = unit_of_work.stores.activity_history
            history.add_session(
                OperationSessionRecord(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    actor_id="operator-a",
                    title=session_id,
                    status=OperationSessionStatus.OPEN,
                    created_at="2026-08-02T12:00:00Z",
                    idempotency_key=f"start-{session_id}",
                    intent_fingerprint=f"fingerprint-{session_id}",
                )
            )
            history.add_action(
                OperationActionRecord(
                    action_id=action_id,
                    session_id=session_id,
                    ordinal=1,
                    action_type=OperatorCommandKind.START_OPERATION_SESSION,
                    actor_id="operator-a",
                    payload={"workspace_id": workspace_id},
                    created_at="2026-08-02T12:00:00Z",
                    idempotency_key=f"start-{session_id}",
                    intent_fingerprint=f"fingerprint-{session_id}",
                )
            )
            unit_of_work.commit()


if __name__ == "__main__":
    unittest.main()
