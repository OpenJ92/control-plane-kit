from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import threading
import unittest

import psycopg

from control_plane_kit import (
    ClaimWebhook,
    DispatchWebhook,
    EnqueueWebhook,
    PostgresWebhookUnitOfWork,
    RecoverWebhook,
    SecretReference,
    WebhookAttemptOutcome,
    WebhookAuthority,
    WebhookContentType,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookDeliveryService,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookOutboundRequest,
    WebhookOutboundResult,
    WebhookPayload,
    WebhookRetryPolicy,
    WebhookScope,
    WebhookSigning,
    WebhookStateConflict,
    install_webhook_schema,
    replay_webhook_events,
)


NOW = datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc)
AUTHORITY = WebhookAuthority(
    "operator-a",
    "workspace-a",
    frozenset(
        {
            WebhookScope.ENQUEUE,
            WebhookScope.DISPATCH,
            WebhookScope.RECOVER,
        }
    ),
)


class FixedClock:
    def __init__(self, *values: datetime) -> None:
        self._values = list(values)

    def __call__(self) -> datetime:
        if not self._values:
            raise AssertionError("webhook hardening clock was exhausted")
        return self._values.pop(0)


class RecordingOutbound:
    def __init__(
        self,
        result: WebhookOutboundResult,
        connections: list[psycopg.Connection],
    ) -> None:
        self.result = result
        self.connections = connections
        self.requests: list[WebhookOutboundRequest] = []

    def deliver(self, request: WebhookOutboundRequest) -> WebhookOutboundResult:
        if any(not connection.closed for connection in self.connections):
            raise AssertionError("webhook outbound effect ran inside a transaction")
        self.requests.append(request)
        return self.result


class BlockingOutbound(RecordingOutbound):
    def __init__(
        self,
        result: WebhookOutboundResult,
        connections: list[psycopg.Connection],
    ) -> None:
        super().__init__(result, connections)
        self.entered = threading.Event()
        self.release = threading.Event()

    def deliver(self, request: WebhookOutboundRequest) -> WebhookOutboundResult:
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("webhook hardening effect was not released")
        return super().deliver(request)


class WebhookServiceHardeningTests(unittest.TestCase):
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
        self._remove_failure_triggers()
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
        self.connections: list[psycopg.Connection] = []

    def tearDown(self) -> None:
        self._remove_failure_triggers()

    def factory(self) -> PostgresWebhookUnitOfWork:
        def connect() -> psycopg.Connection:
            connection = psycopg.connect(self.database_url)
            self.connections.append(connection)
            return connection

        return PostgresWebhookUnitOfWork(connect)

    def service(
        self,
        outbound: RecordingOutbound,
        *,
        clock: FixedClock,
        claim_ids: list[str],
    ) -> WebhookDeliveryService:
        ids = iter(claim_ids)
        return WebhookDeliveryService(
            self.factory,
            outbound,
            clock=clock,
            id_factory=lambda: next(ids),
        )

    def test_effect_success_with_result_transaction_failure_remains_uncertain(self) -> None:
        intent = _intent()
        outbound = RecordingOutbound(
            WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 204),
            self.connections,
        )
        service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=3),
            ),
            claim_ids=["claim-a"],
        )
        service.execute(EnqueueWebhook(intent, AUTHORITY))
        claim = service.execute(_claim(intent)).state.active_claim
        self._install_projection_failure()

        with self.assertRaises(psycopg.errors.RaiseException):
            service.execute(_dispatch(intent, claim.claim_id))

        state = self._state(intent.identity)
        self.assertIs(state.status, WebhookDeliveryStatus.IN_FLIGHT)
        self.assertEqual(
            self._event_variants(intent.identity),
            ["enqueued", "claimed", "attempt-started"],
        )
        self.assertEqual(len(outbound.requests), 1)
        self._remove_failure_triggers()
        replay = service.execute(_dispatch(intent, claim.claim_id))
        self.assertTrue(replay.replayed)
        self.assertEqual(len(outbound.requests), 1)
        recovered = self.service(
            RecordingOutbound(
                WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 200),
                self.connections,
            ),
            clock=FixedClock(NOW + timedelta(seconds=32)),
            claim_ids=[],
        ).execute(RecoverWebhook("recover-a", intent.identity, AUTHORITY))
        self.assertIs(recovered.state.status, WebhookDeliveryStatus.OPERATOR_REQUIRED)

    def test_recovery_wins_race_against_late_effect_result(self) -> None:
        intent = _intent()
        outbound = BlockingOutbound(
            WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 200),
            self.connections,
        )
        dispatch_service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=12),
            ),
            claim_ids=["claim-a"],
        )
        dispatch_service.execute(EnqueueWebhook(intent, AUTHORITY))
        claim = dispatch_service.execute(
            _claim(intent, lease_seconds=10)
        ).state.active_claim
        errors: list[BaseException] = []

        def dispatch() -> None:
            try:
                dispatch_service.execute(_dispatch(intent, claim.claim_id))
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=dispatch)
        thread.start()
        self.assertTrue(outbound.entered.wait(timeout=10))
        recovered = self.service(
            RecordingOutbound(
                WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 200),
                self.connections,
            ),
            clock=FixedClock(NOW + timedelta(seconds=12)),
            claim_ids=[],
        ).execute(RecoverWebhook("recover-a", intent.identity, AUTHORITY))
        outbound.release.set()
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], WebhookStateConflict)
        self.assertIs(recovered.state.status, WebhookDeliveryStatus.OPERATOR_REQUIRED)
        self.assertIs(self._state(intent.identity).status, WebhookDeliveryStatus.OPERATOR_REQUIRED)
        self.assertEqual(
            self._event_variants(intent.identity),
            [
                "enqueued",
                "claimed",
                "attempt-started",
                "attempt-finished",
                "operator-required",
            ],
        )

    def test_committed_result_wins_before_late_recovery(self) -> None:
        intent = _intent()
        outbound = RecordingOutbound(
            WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 200),
            self.connections,
        )
        service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=3),
                NOW + timedelta(seconds=32),
            ),
            claim_ids=["claim-a"],
        )
        service.execute(EnqueueWebhook(intent, AUTHORITY))
        claim = service.execute(_claim(intent)).state.active_claim
        completed = service.execute(_dispatch(intent, claim.claim_id))

        with self.assertRaises(WebhookStateConflict):
            service.execute(RecoverWebhook("recover-a", intent.identity, AUTHORITY))

        self.assertIs(completed.state.status, WebhookDeliveryStatus.DELIVERED)
        self.assertIs(self._state(intent.identity).status, WebhookDeliveryStatus.DELIVERED)

    def test_command_ledger_failure_rolls_back_all_enqueue_facts(self) -> None:
        intent = _intent()
        service = self.service(
            RecordingOutbound(
                WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 200),
                self.connections,
            ),
            clock=FixedClock(),
            claim_ids=[],
        )
        self._install_command_failure()

        with self.assertRaises(psycopg.errors.RaiseException):
            service.execute(EnqueueWebhook(intent, AUTHORITY))

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
        self._remove_failure_triggers()
        retried = service.execute(EnqueueWebhook(intent, AUTHORITY))
        self.assertFalse(retried.replayed)
        self.assertIs(retried.state.status, WebhookDeliveryStatus.QUEUED)

    def test_malformed_capability_result_cannot_publish_false_result(self) -> None:
        intent = _intent()
        outbound = RecordingOutbound(
            WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 500),
            self.connections,
        )
        service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=3),
            ),
            claim_ids=["claim-a"],
        )
        service.execute(EnqueueWebhook(intent, AUTHORITY))
        claim = service.execute(_claim(intent)).state.active_claim

        with self.assertRaises(ValueError):
            service.execute(_dispatch(intent, claim.claim_id))

        self.assertIs(self._state(intent.identity).status, WebhookDeliveryStatus.IN_FLIGHT)
        self.assertEqual(
            self._event_variants(intent.identity),
            ["enqueued", "claimed", "attempt-started"],
        )
        replay = service.execute(_dispatch(intent, claim.claim_id))
        self.assertTrue(replay.replayed)
        self.assertEqual(len(outbound.requests), 1)

    def test_dispatch_at_expiry_and_recovery_before_expiry_fail_closed(self) -> None:
        intent = _intent()
        outbound = RecordingOutbound(
            WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 200),
            self.connections,
        )
        service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=10),
                NOW + timedelta(seconds=11),
            ),
            claim_ids=["claim-a"],
        )
        service.execute(EnqueueWebhook(intent, AUTHORITY))
        claim = service.execute(
            _claim(intent, lease_seconds=10)
        ).state.active_claim

        with self.assertRaises(WebhookStateConflict):
            service.execute(RecoverWebhook("recover-early", intent.identity, AUTHORITY))
        with self.assertRaises(ValueError):
            service.execute(_dispatch(intent, claim.claim_id))

        self.assertEqual(outbound.requests, [])
        self.assertIs(self._state(intent.identity).status, WebhookDeliveryStatus.CLAIMED)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_webhook_commands WHERE variant = 'start-attempt'"
            ).fetchone()[0],
            0,
        )

    def _state(self, identity: WebhookDeliveryIdentity):
        with self.factory() as work:
            events = work.journal.events_for(identity)
            projection = work.projections.get(identity)
        self.assertEqual(projection.journal_version, len(events))
        self.assertEqual(projection.state, replay_webhook_events(events))
        return projection.state

    def _event_variants(self, identity: WebhookDeliveryIdentity) -> list[str]:
        return [
            row[0]
            for row in self.connection.execute(
                """
                SELECT variant FROM cpk_webhook_events
                WHERE workspace_id = %s AND delivery_id = %s
                ORDER BY ordinal
                """,
                (identity.workspace_id, identity.delivery_id),
            ).fetchall()
        ]

    def _install_projection_failure(self) -> None:
        self.connection.execute(
            """
            CREATE OR REPLACE FUNCTION cpk_test_fail_webhook_projection()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.status = 'delivered' THEN
                RAISE EXCEPTION 'injected projection failure';
              END IF;
              RETURN NEW;
            END
            $$;
            CREATE TRIGGER cpk_test_fail_webhook_projection
            BEFORE UPDATE ON cpk_webhook_projections
            FOR EACH ROW EXECUTE FUNCTION cpk_test_fail_webhook_projection();
            """
        )

    def _install_command_failure(self) -> None:
        self.connection.execute(
            """
            CREATE OR REPLACE FUNCTION cpk_test_fail_webhook_command()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'injected command failure';
            END
            $$;
            CREATE TRIGGER cpk_test_fail_webhook_command
            BEFORE INSERT ON cpk_webhook_commands
            FOR EACH ROW EXECUTE FUNCTION cpk_test_fail_webhook_command();
            """
        )

    def _remove_failure_triggers(self) -> None:
        self.connection.execute(
            """
            DROP TRIGGER IF EXISTS cpk_test_fail_webhook_projection
              ON cpk_webhook_projections;
            DROP FUNCTION IF EXISTS cpk_test_fail_webhook_projection();
            DROP TRIGGER IF EXISTS cpk_test_fail_webhook_command
              ON cpk_webhook_commands;
            DROP FUNCTION IF EXISTS cpk_test_fail_webhook_command();
            """
        )


def _intent() -> WebhookDeliveryIntent:
    return WebhookDeliveryIntent(
        "enqueue-a",
        WebhookDeliveryIdentity("workspace-a", "delivery-a"),
        WebhookEndpoint("orders", "https://hooks.example.test/orders"),
        WebhookPayload(WebhookContentType.JSON, b'{"order_id":42}'),
        WebhookRetryPolicy(3, 1_000, 10_000, 3_600),
        NOW,
        WebhookSigning(SecretReference("secret://hooks/a")),
    )


def _claim(intent: WebhookDeliveryIntent, *, lease_seconds: int = 30) -> ClaimWebhook:
    return ClaimWebhook(
        "claim-command",
        intent.identity,
        "worker-a",
        lease_seconds,
        AUTHORITY,
    )


def _dispatch(intent: WebhookDeliveryIntent, claim_id: str) -> DispatchWebhook:
    return DispatchWebhook(
        "dispatch-command",
        intent.identity,
        claim_id,
        "worker-a",
        AUTHORITY,
    )


if __name__ == "__main__":
    unittest.main()
