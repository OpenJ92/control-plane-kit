from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import threading
import unittest

import psycopg

from control_plane_kit import (
    SecretReference,
)
from control_plane_kit.webhook import (
    PostgresWebhookUnitOfWork,
    install_webhook_schema,
)
from control_plane_kit.domains.webhook import (
    WebhookClaim,
    WebhookClaimed,
    WebhookContentType,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookEnqueued,
    WebhookPayload,
    WebhookRetryPolicy,
    WebhookSigning,
    evolve_webhook_delivery,
    replay_webhook_events,
)


NOW = datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc)


class WebhookPostgresTests(unittest.TestCase):
    connection: psycopg.Connection

    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = os.environ.get("CPK_TEST_DATABASE_URL")
        if not cls.database_url:
            raise RuntimeError(
                "CPK_TEST_DATABASE_URL is required. Run ./test.sh so Docker starts Postgres."
            )
        with psycopg.connect(cls.database_url) as connection:
            install_webhook_schema(connection)
        cls.connection = psycopg.connect(cls.database_url, autocommit=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def setUp(self) -> None:
        self.connection.execute(
            """
            TRUNCATE TABLE
              cpk_webhook_events,
              cpk_webhook_projections,
              cpk_webhook_commands,
              cpk_webhook_intents
            CASCADE
            """
        )

    def factory(self) -> PostgresWebhookUnitOfWork:
        return PostgresWebhookUnitOfWork(
            lambda: psycopg.connect(self.database_url)
        )

    def test_intent_journal_and_projection_reconstruct_exact_canonical_state(self) -> None:
        intent = _intent()
        initial = self._persist_initial(intent)

        with self.factory() as work:
            stored_intent = work.intents.get(intent.identity)
            events = work.journal.events_for(intent.identity)
            projection = work.projections.get(intent.identity)

        self.assertEqual(stored_intent, intent)
        self.assertEqual(events, (WebhookEnqueued(intent),))
        self.assertEqual(projection.state, initial)
        self.assertEqual(projection.state, replay_webhook_events(events))
        self.assertEqual(projection.journal_version, 1)
        self.assertNotIn('{"order_id":42}', repr(stored_intent))
        self.assertEqual(stored_intent.signing.secret_reference.reference_id, "secret://hooks/a")

    def test_one_unit_of_work_rolls_back_all_webhook_relations_together(self) -> None:
        intent = _intent()
        initial = evolve_webhook_delivery(None, WebhookEnqueued(intent))

        with self.factory() as work:
            work.intents.add(intent)
            self.assertTrue(
                work.journal.append(intent.identity, 1, WebhookEnqueued(intent))
            )
            work.projections.add(initial, 1)
            work.commands.add(
                intent.command_id,
                intent.identity.workspace_id,
                "enqueue",
                intent.intent_fingerprint,
                "operator-a",
                {"delivery_id": intent.identity.delivery_id},
                NOW,
            )

        counts = self.connection.execute(
            """
            SELECT
              (SELECT count(*) FROM cpk_webhook_intents),
              (SELECT count(*) FROM cpk_webhook_events),
              (SELECT count(*) FROM cpk_webhook_projections),
              (SELECT count(*) FROM cpk_webhook_commands)
            """
        ).fetchone()
        self.assertEqual(counts, (0, 0, 0, 0))

    def test_versioned_append_and_projection_reject_stale_writers(self) -> None:
        intent = _intent()
        self._persist_initial(intent)
        claim = _claim(intent)

        with self.factory() as work:
            work.journal.lock_delivery(intent.identity)
            projection = work.projections.get(intent.identity)
            claimed = evolve_webhook_delivery(
                projection.state,
                WebhookClaimed(claim),
            )
            self.assertTrue(
                work.journal.append(intent.identity, 2, WebhookClaimed(claim))
            )
            self.assertTrue(work.projections.replace(claimed, 1, 2))
            self.assertFalse(
                work.journal.append(intent.identity, 2, WebhookClaimed(claim))
            )
            self.assertFalse(work.projections.replace(claimed, 1, 2))
            work.commit()

        with self.factory() as work:
            events = work.journal.events_for(intent.identity)
            projection = work.projections.get(intent.identity)
        self.assertEqual(projection.journal_version, 2)
        self.assertEqual(projection.state, replay_webhook_events(events))
        self.assertEqual(projection.state.active_claim, claim)

    def test_concurrent_enqueue_has_one_winner_on_independent_connections(self) -> None:
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        outcome_lock = threading.Lock()

        def enqueue(command_id: str) -> None:
            intent = _intent(command_id=command_id)
            barrier.wait(timeout=5)
            with self.factory() as work:
                work.journal.lock_delivery(intent.identity)
                if work.intents.get(intent.identity) is not None:
                    result = "conflict"
                else:
                    state = evolve_webhook_delivery(None, WebhookEnqueued(intent))
                    work.intents.add(intent)
                    self.assertTrue(
                        work.journal.append(
                            intent.identity,
                            1,
                            WebhookEnqueued(intent),
                        )
                    )
                    work.projections.add(state, 1)
                    work.commit()
                    result = "enqueued"
            with outcome_lock:
                outcomes.append(result)

        threads = [
            threading.Thread(target=enqueue, args=("enqueue-a",)),
            threading.Thread(target=enqueue, args=("enqueue-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["conflict", "enqueued"])
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_webhook_intents"
            ).fetchone()[0],
            1,
        )

    def test_concurrent_claim_has_one_winner_and_one_canonical_event(self) -> None:
        intent = _intent()
        self._persist_initial(intent)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        outcome_lock = threading.Lock()

        def claim(claim_id: str, worker_id: str) -> None:
            barrier.wait(timeout=5)
            with self.factory() as work:
                work.journal.lock_delivery(intent.identity)
                projection = work.projections.get(intent.identity)
                if projection.state.status is not WebhookDeliveryStatus.QUEUED:
                    result = "conflict"
                else:
                    event = WebhookClaimed(
                        _claim(intent, claim_id=claim_id, worker_id=worker_id)
                    )
                    replacement = evolve_webhook_delivery(projection.state, event)
                    next_version = projection.journal_version + 1
                    self.assertTrue(
                        work.journal.append(intent.identity, next_version, event)
                    )
                    self.assertTrue(
                        work.projections.replace(
                            replacement,
                            projection.journal_version,
                            next_version,
                        )
                    )
                    work.commit()
                    result = "claimed"
            with outcome_lock:
                outcomes.append(result)

        threads = [
            threading.Thread(target=claim, args=("claim-a", "worker-a")),
            threading.Thread(target=claim, args=("claim-b", "worker-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["claimed", "conflict"])
        with self.factory() as work:
            events = work.journal.events_for(intent.identity)
            projection = work.projections.get(intent.identity)
        self.assertEqual(len(events), 2)
        self.assertEqual(projection.journal_version, 2)
        self.assertEqual(projection.state, replay_webhook_events(events))
        self.assertIn(projection.state.active_claim.worker_id, {"worker-a", "worker-b"})

    def test_command_ledger_preserves_exact_replay_identity(self) -> None:
        intent = _intent()
        self._persist_initial(intent)

        with self.factory() as work:
            work.commands.lock_command(intent.command_id)
            record = work.commands.get(intent.command_id)

        self.assertEqual(record.command_id, intent.command_id)
        self.assertEqual(record.intent_fingerprint, intent.intent_fingerprint)
        self.assertEqual(record.result_descriptor, {"delivery_id": "delivery-a"})
        self.assertNotIn("order_id", repr(record))

    def test_unknown_closed_values_and_malformed_shapes_fail_in_postgres(self) -> None:
        intent = _intent()
        self._persist_initial(intent)

        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute(
                """
                UPDATE cpk_webhook_projections SET status = 'maybe'
                WHERE workspace_id = %s AND delivery_id = %s
                """,
                (intent.identity.workspace_id, intent.identity.delivery_id),
            )
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute(
                """
                INSERT INTO cpk_webhook_events
                  (workspace_id, delivery_id, ordinal, variant, descriptor, recorded_at)
                VALUES (%s, %s, 2, 'unknown', '{"variant":"unknown"}'::jsonb, %s)
                """,
                (intent.identity.workspace_id, intent.identity.delivery_id, NOW),
            )
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute(
                """
                UPDATE cpk_webhook_projections
                SET status = 'claimed', active_claim_id = 'partial'
                WHERE workspace_id = %s AND delivery_id = %s
                """,
                (intent.identity.workspace_id, intent.identity.delivery_id),
            )

    def test_schema_reinstall_preserves_rows_and_constraint_identity(self) -> None:
        self._persist_initial(_intent())
        before = self.connection.execute(
            """
            SELECT oid FROM pg_constraint
            WHERE conname = 'cpk_webhook_projection_state_shape_check'
            """
        ).fetchone()[0]

        with psycopg.connect(self.database_url) as connection:
            install_webhook_schema(connection)

        after = self.connection.execute(
            """
            SELECT oid FROM pg_constraint
            WHERE conname = 'cpk_webhook_projection_state_shape_check'
            """
        ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_webhook_intents"
            ).fetchone()[0],
            1,
        )

    def test_schema_installer_leaves_commit_and_rollback_to_caller(self) -> None:
        class TrackingConnection:
            def __init__(self) -> None:
                self.executed: list[str] = []
                self.commit_calls = 0
                self.rollback_calls = 0

            def execute(self, query, params=()):
                self.executed.append(query)

            def commit(self):
                self.commit_calls += 1

            def rollback(self):
                self.rollback_calls += 1

        connection = TrackingConnection()

        install_webhook_schema(connection)

        self.assertEqual(len(connection.executed), 1)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)

    def _persist_initial(self, intent: WebhookDeliveryIntent):
        state = evolve_webhook_delivery(None, WebhookEnqueued(intent))
        with self.factory() as work:
            work.journal.lock_delivery(intent.identity)
            work.commands.lock_command(intent.command_id)
            work.intents.add(intent)
            self.assertTrue(
                work.journal.append(intent.identity, 1, WebhookEnqueued(intent))
            )
            work.projections.add(state, 1)
            work.commands.add(
                intent.command_id,
                intent.identity.workspace_id,
                "enqueue",
                intent.intent_fingerprint,
                "operator-a",
                {"delivery_id": intent.identity.delivery_id},
                NOW,
            )
            work.commit()
        return state


def _intent(*, command_id: str = "enqueue-a") -> WebhookDeliveryIntent:
    return WebhookDeliveryIntent(
        command_id,
        WebhookDeliveryIdentity("workspace-a", "delivery-a"),
        WebhookEndpoint("orders", "https://hooks.example.test/orders"),
        WebhookPayload(WebhookContentType.JSON, b'{"order_id":42}'),
        WebhookRetryPolicy(3, 1_000, 10_000, 3_600),
        NOW,
        WebhookSigning(SecretReference("secret://hooks/a")),
    )


def _claim(
    intent: WebhookDeliveryIntent,
    *,
    claim_id: str = "claim-a",
    worker_id: str = "worker-a",
) -> WebhookClaim:
    return WebhookClaim(
        intent.identity,
        claim_id,
        worker_id,
        1,
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=31),
    )


if __name__ == "__main__":
    unittest.main()
