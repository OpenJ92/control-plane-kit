import concurrent.futures
import os
import threading
import unittest
import uuid
from dataclasses import replace

import psycopg

from control_plane_kit.stores import (
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    PostgresActivityHistoryStore,
    install_schema,
)
from tests.postgres_case import PostgresStoreTestCase


class OperationPostgresPrimitiveTests(PostgresStoreTestCase):
    def session(self, *, key: str | None = None, fingerprint: str | None = None):
        return OperationSessionRecord(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="jacob",
            title="Swap API",
            status=OperationSessionStatus.OPEN,
            created_at="2026-07-15T00:00:00Z",
            idempotency_key=key,
            intent_fingerprint=fingerprint,
        )

    def test_scoped_session_and_action_idempotency_lookup_round_trips(self):
        session = self.session(key="start-a", fingerprint="session-fingerprint")
        self.stores.activity_history.add_session(session)
        action = OperationActionRecord(
            action_id="action-a",
            session_id=session.session_id,
            ordinal=self.stores.activity_history.next_action_ordinal(session.session_id),
            action_type=OperationActionKind.ADD_BLOCK,
            actor_id="jacob",
            created_at="2026-07-15T00:00:01Z",
            idempotency_key="action-key",
            intent_fingerprint="action-fingerprint",
        )
        self.stores.activity_history.add_action(action)

        self.assertEqual(
            self.stores.activity_history.session_for_idempotency("workspace-a", "start-a"),
            session,
        )
        self.assertEqual(
            self.stores.activity_history.action_for_idempotency("session-a", "action-key"),
            action,
        )
        self.assertIsNone(
            self.stores.activity_history.session_for_idempotency("workspace-b", "start-a")
        )

        other_workspace = replace(
            session,
            session_id="session-b",
            workspace_id="workspace-b",
        )
        self.stores.activity_history.add_session(other_workspace)
        self.assertEqual(
            self.stores.activity_history.session_for_idempotency("workspace-b", "start-a"),
            other_workspace,
        )

    def test_idempotency_keys_are_unique_within_their_scope(self):
        self.stores.activity_history.add_session(
            self.session(key="start-a", fingerprint="fingerprint-a")
        )
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.stores.activity_history.add_session(
                replace(
                    self.session(key="start-a", fingerprint="fingerprint-b"),
                    session_id="session-b",
                )
            )

        self.stores.activity_history.add_session(
            replace(self.session(), session_id="session-c")
        )
        first = OperationActionRecord(
            action_id="action-a",
            session_id="session-c",
            ordinal=1,
            action_type=OperationActionKind.ADD_BLOCK,
            actor_id="jacob",
            idempotency_key="action-key",
            intent_fingerprint="fingerprint-a",
        )
        self.stores.activity_history.add_action(first)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.stores.activity_history.add_action(
                replace(
                    first,
                    action_id="action-b",
                    ordinal=2,
                    intent_fingerprint="fingerprint-b",
                )
            )

    def test_terminal_transition_preserves_identity_and_rejects_stale_writes(self):
        original = replace(
            self.session(key="start-a", fingerprint="session-fingerprint"),
            metadata={"purpose": "backend swap"},
        )
        self.stores.activity_history.add_session(original)

        changed = self.stores.activity_history.transition_open_session(
            "session-a",
            replacement=OperationSessionStatus.CLOSED,
            closed_at="2026-07-15T00:01:00Z",
        )
        stale = self.stores.activity_history.transition_open_session(
            "session-a",
            replacement=OperationSessionStatus.CANCELLED,
            closed_at="2026-07-15T00:02:00Z",
        )

        self.assertIsNotNone(changed)
        self.assertEqual(changed.session_id, original.session_id)
        self.assertEqual(changed.workspace_id, original.workspace_id)
        self.assertEqual(changed.actor_id, original.actor_id)
        self.assertEqual(changed.title, original.title)
        self.assertEqual(changed.created_at, original.created_at)
        self.assertEqual(changed.metadata, original.metadata)
        self.assertEqual(changed.idempotency_key, original.idempotency_key)
        self.assertEqual(changed.intent_fingerprint, original.intent_fingerprint)
        self.assertIs(changed.status, OperationSessionStatus.CLOSED)
        self.assertEqual(changed.closed_at, "2026-07-15T00:01:00Z")
        self.assertIsNone(stale)
        self.assertEqual(
            self.stores.activity_history.get_session("session-a"),
            changed,
        )

        with self.assertRaises(ValueError):
            self.stores.activity_history.transition_open_session(
                "session-a",
                replacement=OperationSessionStatus.OPEN,
                closed_at="2026-07-15T00:03:00Z",
            )
        with self.assertRaises(ValueError):
            self.stores.activity_history.transition_open_session(
                "session-a",
                replacement=OperationSessionStatus.CANCELLED,
                closed_at="",
            )

    def test_concurrent_action_writers_receive_distinct_ordinals(self):
        self.stores.activity_history.add_session(self.session())
        barrier = threading.Barrier(2)
        database_url = os.environ["CPK_TEST_DATABASE_URL"]

        def append(action_id: str) -> int:
            with psycopg.connect(database_url) as connection:
                store = PostgresActivityHistoryStore(connection)
                barrier.wait(timeout=5)
                ordinal = store.next_action_ordinal("session-a")
                store.add_action(
                    OperationActionRecord(
                        action_id=action_id,
                        session_id="session-a",
                        ordinal=ordinal,
                        action_type=OperationActionKind.ADD_BLOCK,
                        actor_id="jacob",
                        created_at=f"2026-07-15T00:00:0{ordinal}Z",
                        idempotency_key=action_id,
                        intent_fingerprint=action_id,
                    )
                )
                connection.commit()
                return ordinal

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            ordinals = tuple(executor.map(append, ("action-a", "action-b")))

        self.assertEqual(sorted(ordinals), [1, 2])
        self.assertEqual(
            [action.ordinal for action in self.stores.activity_history.actions_for_session("session-a")],
            [1, 2],
        )

    def test_schema_install_forward_migrates_legacy_operation_tables(self):
        schema = f"migration_{uuid.uuid4().hex}"
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                connection.execute(
                    """
                    CREATE TABLE cpk_operation_sessions (
                      session_id text PRIMARY KEY,
                      workspace_id text NOT NULL,
                      actor_id text NOT NULL,
                      title text NOT NULL,
                      status text NOT NULL,
                      created_at text NOT NULL,
                      closed_at text,
                      metadata jsonb NOT NULL DEFAULT '{}'::jsonb
                    );
                    CREATE TABLE cpk_operation_actions (
                      action_id text PRIMARY KEY,
                      session_id text NOT NULL REFERENCES cpk_operation_sessions(session_id),
                      ordinal integer NOT NULL,
                      action_type text NOT NULL,
                      actor_id text NOT NULL,
                      payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                      created_at text NOT NULL,
                      UNIQUE (session_id, ordinal)
                    );
                    """
                )

                install_schema(connection)

                session_columns = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = 'cpk_operation_sessions'
                        """,
                        (schema,),
                    ).fetchall()
                }
                action_columns = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = 'cpk_operation_actions'
                        """,
                        (schema,),
                    ).fetchall()
                }
                self.assertTrue({"idempotency_key", "intent_fingerprint"} <= session_columns)
                self.assertTrue({"idempotency_key", "intent_fingerprint"} <= action_columns)
                indexes = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT indexname FROM pg_indexes
                        WHERE schemaname = %s
                          AND indexname IN (
                            'cpk_operation_sessions_idempotency',
                            'cpk_operation_actions_idempotency'
                          )
                        """,
                        (schema,),
                    ).fetchall()
                }
                self.assertEqual(
                    indexes,
                    {
                        "cpk_operation_sessions_idempotency",
                        "cpk_operation_actions_idempotency",
                    },
                )
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


if __name__ == "__main__":
    unittest.main()
