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
    ReleaseWebhookClaim,
    SecretReference,
    WebhookAttemptOutcome,
    WebhookAuthority,
    WebhookAuthorizationError,
    WebhookCommandConflict,
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
)


NOW = datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc)
ALL_SCOPES = frozenset(
    {
        WebhookScope.ENQUEUE,
        WebhookScope.DISPATCH,
        WebhookScope.RECOVER,
        WebhookScope.READ,
    }
)


class RecordingOutbound:
    def __init__(
        self,
        results: list[WebhookOutboundResult | BaseException],
        connections: list[psycopg.Connection],
    ) -> None:
        self._results = results
        self._connections = connections
        self.requests: list[WebhookOutboundRequest] = []

    def deliver(self, request: WebhookOutboundRequest) -> WebhookOutboundResult:
        if any(not connection.closed for connection in self._connections):
            raise AssertionError("webhook outbound effect ran inside a transaction")
        self.requests.append(request)
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FixedClock:
    def __init__(self, *values: datetime) -> None:
        self._values = list(values)

    def __call__(self) -> datetime:
        if not self._values:
            raise AssertionError("webhook test clock was exhausted")
        return self._values.pop(0)


class Ids:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        if not self._values:
            raise AssertionError("webhook test id factory was exhausted")
        return self._values.pop(0)


class WebhookServiceTests(unittest.TestCase):
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
        self.connections: list[psycopg.Connection] = []

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
        ids: Ids,
    ) -> WebhookDeliveryService:
        return WebhookDeliveryService(
            self.factory,
            outbound,
            clock=clock,
            id_factory=ids,
        )

    def test_enqueue_is_atomic_exactly_idempotent_and_authorized(self) -> None:
        outbound = RecordingOutbound([], self.connections)
        service = self.service(outbound, clock=FixedClock(), ids=Ids())
        intent = _intent()

        created = service.execute(EnqueueWebhook(intent, _authority()))
        replayed = service.execute(EnqueueWebhook(intent, _authority()))

        self.assertIs(created.state.status, WebhookDeliveryStatus.QUEUED)
        self.assertFalse(created.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(created.state, replayed.state)
        changed = _intent(payload=b'{"order_id":43}')
        with self.assertRaises(WebhookCommandConflict):
            service.execute(EnqueueWebhook(changed, _authority()))
        with self.assertRaises(WebhookAuthorizationError):
            service.execute(
                EnqueueWebhook(
                    _intent(command_id="foreign", delivery_id="foreign"),
                    WebhookAuthority("actor-b", "workspace-b", ALL_SCOPES),
                )
            )
        self.assertEqual(outbound.requests, [])

    def test_successful_dispatch_uses_split_transactions_and_never_replays_effect(self) -> None:
        outbound = RecordingOutbound(
            [WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 204)],
            self.connections,
        )
        service = self.service(
            outbound,
            clock=FixedClock(NOW + timedelta(seconds=1), NOW + timedelta(seconds=2), NOW + timedelta(seconds=3)),
            ids=Ids("claim-a"),
        )
        intent = _intent()
        service.execute(EnqueueWebhook(intent, _authority()))
        claimed = service.execute(_claim_command(intent))
        claim = claimed.state.active_claim

        completed = service.execute(_dispatch_command(intent, claim.claim_id))
        replayed = service.execute(_dispatch_command(intent, claim.claim_id))

        self.assertIs(completed.state.status, WebhookDeliveryStatus.DELIVERED)
        self.assertTrue(completed.external_effect_attempted)
        self.assertTrue(replayed.replayed)
        self.assertFalse(replayed.external_effect_attempted)
        self.assertEqual(len(outbound.requests), 1)
        self.assertEqual(outbound.requests[0].attempt_number, 1)
        self.assertTrue(all(connection.closed for connection in self.connections))
        self.assertEqual(
            self._event_variants(intent.identity),
            ["enqueued", "claimed", "attempt-started", "attempt-finished"],
        )

    def test_retry_policy_schedules_next_attempt_then_delivers(self) -> None:
        outbound = RecordingOutbound(
            [
                WebhookOutboundResult(
                    WebhookAttemptOutcome.RETRYABLE_FAILURE,
                    503,
                    "http.server-error",
                ),
                WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 200),
            ],
            self.connections,
        )
        retry_at = NOW + timedelta(seconds=3, milliseconds=1_000)
        service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=3),
                retry_at,
                retry_at + timedelta(seconds=1),
                retry_at + timedelta(seconds=2),
            ),
            ids=Ids("claim-a", "claim-b"),
        )
        intent = _intent()
        service.execute(EnqueueWebhook(intent, _authority()))
        first_claim = service.execute(_claim_command(intent, command_id="claim-1"))
        failed = service.execute(
            _dispatch_command(intent, first_claim.state.active_claim.claim_id, "dispatch-1")
        )

        self.assertIs(failed.state.status, WebhookDeliveryStatus.RETRY_SCHEDULED)
        self.assertEqual(failed.state.next_attempt_at, retry_at)
        second_claim = service.execute(_claim_command(intent, command_id="claim-2"))
        delivered = service.execute(
            _dispatch_command(intent, second_claim.state.active_claim.claim_id, "dispatch-2")
        )
        self.assertIs(delivered.state.status, WebhookDeliveryStatus.DELIVERED)
        self.assertEqual(delivered.state.attempts_completed, 2)
        self.assertEqual(len(outbound.requests), 2)

    def test_terminal_failure_dead_letters_without_retry(self) -> None:
        intent = _intent()
        outbound = RecordingOutbound(
            [
                WebhookOutboundResult(
                    WebhookAttemptOutcome.TERMINAL_FAILURE,
                    400,
                    "http.client-error",
                )
            ],
            self.connections,
        )
        service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=3),
            ),
            ids=Ids("claim-terminal"),
        )
        service.execute(EnqueueWebhook(intent, _authority()))
        claim = service.execute(_claim_command(intent)).state.active_claim

        completed = service.execute(_dispatch_command(intent, claim.claim_id))

        self.assertIs(completed.state.status, WebhookDeliveryStatus.DEAD_LETTER)

    def test_retry_failure_dead_letters_when_backoff_cannot_fit_deadline(self) -> None:
        intent = _intent(
            retry_policy=WebhookRetryPolicy(3, 5_000, 5_000, 4),
        )
        outbound = RecordingOutbound(
            [
                WebhookOutboundResult(
                    WebhookAttemptOutcome.RETRYABLE_FAILURE,
                    503,
                    "http.server-error",
                )
            ],
            self.connections,
        )
        service = self.service(
            outbound,
            clock=FixedClock(
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
                NOW + timedelta(seconds=3),
            ),
            ids=Ids("claim-window"),
        )
        service.execute(EnqueueWebhook(intent, _authority()))
        claim = service.execute(
            _claim_command(intent, lease_seconds=2)
        ).state.active_claim

        completed = service.execute(_dispatch_command(intent, claim.claim_id))

        self.assertIs(completed.state.status, WebhookDeliveryStatus.DEAD_LETTER)

    def test_effect_exception_becomes_operator_required_without_secret_or_payload_evidence(self) -> None:
        outbound = RecordingOutbound([RuntimeError("contains-secret")], self.connections)
        service = self.service(
            outbound,
            clock=FixedClock(NOW + timedelta(seconds=1), NOW + timedelta(seconds=2), NOW + timedelta(seconds=3)),
            ids=Ids("claim-a"),
        )
        intent = _intent()
        service.execute(EnqueueWebhook(intent, _authority()))
        claim = service.execute(_claim_command(intent)).state.active_claim

        result = service.execute(_dispatch_command(intent, claim.claim_id))

        self.assertIs(result.state.status, WebhookDeliveryStatus.OPERATOR_REQUIRED)
        descriptors = self.connection.execute(
            "SELECT descriptor::text FROM cpk_webhook_events ORDER BY ordinal"
        ).fetchall()
        terminal = " ".join(row[0] for row in descriptors[-2:])
        self.assertNotIn("contains-secret", terminal)
        self.assertNotIn("order_id", terminal)

    def test_crash_after_effect_start_is_reconstructed_as_uncertain_and_never_replayed(self) -> None:
        outbound = RecordingOutbound([SystemExit("simulated process loss")], self.connections)
        service = self.service(
            outbound,
            clock=FixedClock(NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)),
            ids=Ids("claim-a"),
        )
        intent = _intent()
        service.execute(EnqueueWebhook(intent, _authority()))
        claim = service.execute(_claim_command(intent, lease_seconds=10)).state.active_claim

        with self.assertRaises(SystemExit):
            service.execute(_dispatch_command(intent, claim.claim_id))

        restarted_outbound = RecordingOutbound([], self.connections)
        restarted = self.service(
            restarted_outbound,
            clock=FixedClock(NOW + timedelta(seconds=12)),
            ids=Ids(),
        )
        replay = restarted.execute(_dispatch_command(intent, claim.claim_id))
        recovered = restarted.execute(
            RecoverWebhook("recover-a", intent.identity, _authority())
        )
        self.assertIs(replay.state.status, WebhookDeliveryStatus.IN_FLIGHT)
        self.assertTrue(replay.replayed)
        self.assertEqual(restarted_outbound.requests, [])
        self.assertIs(recovered.state.status, WebhookDeliveryStatus.OPERATOR_REQUIRED)

    def test_crash_before_dispatch_releases_expired_claim_for_new_worker(self) -> None:
        outbound = RecordingOutbound([], self.connections)
        service = self.service(
            outbound,
            clock=FixedClock(NOW + timedelta(seconds=1), NOW + timedelta(seconds=12), NOW + timedelta(seconds=13)),
            ids=Ids("claim-a", "claim-b"),
        )
        intent = _intent()
        service.execute(EnqueueWebhook(intent, _authority()))
        claimed = service.execute(_claim_command(intent, lease_seconds=10))
        recovered = service.execute(
            RecoverWebhook("recover-a", intent.identity, _authority())
        )
        reclaimed = service.execute(
            _claim_command(intent, command_id="claim-second", worker_id="worker-b")
        )

        self.assertIs(claimed.state.status, WebhookDeliveryStatus.CLAIMED)
        self.assertIs(recovered.state.status, WebhookDeliveryStatus.QUEUED)
        self.assertEqual(reclaimed.state.active_claim.worker_id, "worker-b")
        self.assertEqual(outbound.requests, [])

    def test_worker_can_abandon_claim_before_attempt_start(self) -> None:
        outbound = RecordingOutbound([], self.connections)
        service = self.service(
            outbound,
            clock=FixedClock(NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)),
            ids=Ids("claim-a"),
        )
        intent = _intent()
        service.execute(EnqueueWebhook(intent, _authority()))
        claim = service.execute(_claim_command(intent)).state.active_claim

        released = service.execute(
            ReleaseWebhookClaim(
                "release-a",
                intent.identity,
                claim.claim_id,
                claim.worker_id,
                _authority(),
            )
        )

        self.assertIs(released.state.status, WebhookDeliveryStatus.QUEUED)
        self.assertIsNone(released.state.active_claim)

    def test_expired_claim_cannot_be_relabelled_as_voluntary_abandonment(self) -> None:
        outbound = RecordingOutbound([], self.connections)
        service = self.service(
            outbound,
            clock=FixedClock(NOW + timedelta(seconds=1), NOW + timedelta(seconds=11)),
            ids=Ids("claim-a"),
        )
        intent = _intent()
        service.execute(EnqueueWebhook(intent, _authority()))
        claim = service.execute(
            _claim_command(intent, lease_seconds=10)
        ).state.active_claim

        with self.assertRaises(WebhookStateConflict):
            service.execute(
                ReleaseWebhookClaim(
                    "release-a",
                    intent.identity,
                    claim.claim_id,
                    claim.worker_id,
                    _authority(),
                )
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_webhook_events WHERE variant = 'claim-released'"
            ).fetchone()[0],
            0,
        )

    def test_competing_service_claims_have_one_winner(self) -> None:
        intent = _intent()
        bootstrap = self.service(
            RecordingOutbound([], self.connections),
            clock=FixedClock(),
            ids=Ids(),
        )
        bootstrap.execute(EnqueueWebhook(intent, _authority()))
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def claim(command_id: str, worker_id: str, claim_id: str) -> None:
            service = self.service(
                RecordingOutbound([], self.connections),
                clock=FixedClock(NOW + timedelta(seconds=1)),
                ids=Ids(claim_id),
            )
            barrier.wait(timeout=5)
            try:
                service.execute(
                    _claim_command(intent, command_id=command_id, worker_id=worker_id)
                )
                outcome = "claimed"
            except (ValueError, WebhookStateConflict):
                outcome = "conflict"
            with lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=claim, args=("claim-a", "worker-a", "claim-a")),
            threading.Thread(target=claim, args=("claim-b", "worker-b", "claim-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["claimed", "conflict"])
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_webhook_events WHERE variant = 'claimed'"
            ).fetchone()[0],
            1,
        )

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


def _intent(
    *,
    command_id: str = "enqueue-a",
    delivery_id: str = "delivery-a",
    payload: bytes = b'{"order_id":42}',
    retry_policy: WebhookRetryPolicy | None = None,
) -> WebhookDeliveryIntent:
    return WebhookDeliveryIntent(
        command_id,
        WebhookDeliveryIdentity("workspace-a", delivery_id),
        WebhookEndpoint("orders", "https://hooks.example.test/orders"),
        WebhookPayload(WebhookContentType.JSON, payload),
        retry_policy or WebhookRetryPolicy(3, 1_000, 10_000, 3_600),
        NOW,
        WebhookSigning(SecretReference("secret://hooks/a")),
    )


def _authority() -> WebhookAuthority:
    return WebhookAuthority("operator-a", "workspace-a", ALL_SCOPES)


def _claim_command(
    intent: WebhookDeliveryIntent,
    *,
    command_id: str = "claim-command",
    worker_id: str = "worker-a",
    lease_seconds: int = 30,
) -> ClaimWebhook:
    return ClaimWebhook(
        command_id,
        intent.identity,
        worker_id,
        lease_seconds,
        _authority(),
    )


def _dispatch_command(
    intent: WebhookDeliveryIntent,
    claim_id: str,
    command_id: str = "dispatch-command",
    worker_id: str = "worker-a",
) -> DispatchWebhook:
    return DispatchWebhook(
        command_id,
        intent.identity,
        claim_id,
        worker_id,
        _authority(),
    )


if __name__ == "__main__":
    unittest.main()
