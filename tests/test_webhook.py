from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from control_plane_kit import (
    MAX_WEBHOOK_PAYLOAD_BYTES,
    SecretReference,
    WebhookAttemptFinished,
    WebhookAttemptOutcome,
    WebhookAttemptStarted,
    WebhookAuthority,
    WebhookContentType,
    WebhookDeadLettered,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookDeliveryState,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookEnqueued,
    WebhookOperatorRequired,
    WebhookPayload,
    WebhookRetryPolicy,
    WebhookRetryScheduled,
    WebhookScope,
    WebhookSigning,
    evolve_webhook_delivery,
    replay_webhook_events,
    webhook_authority_from_descriptor,
    webhook_event_descriptor,
    webhook_event_from_descriptor,
    webhook_intent_from_descriptor,
)


NOW = datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc)


class WebhookAlgebraTests(unittest.TestCase):
    def test_intent_is_closed_bounded_and_deterministically_fingerprinted(self) -> None:
        first = _intent()
        second = _intent()
        changed = replace(first, payload=WebhookPayload(WebhookContentType.JSON, b'{"id":43}'))

        self.assertEqual(first.intent_fingerprint, second.intent_fingerprint)
        self.assertNotEqual(first.intent_fingerprint, changed.intent_fingerprint)
        self.assertEqual(first.deadline_at, NOW + timedelta(hours=1))
        self.assertNotIn("signing-value", repr(first))
        self.assertNotIn('{"id":42}', repr(first))
        self.assertEqual(webhook_intent_from_descriptor(first.descriptor()), first)

    def test_payload_is_nonempty_bounded_and_digest_verified(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonempty and bounded"):
            WebhookPayload(WebhookContentType.JSON, b"")
        with self.assertRaisesRegex(ValueError, "nonempty and bounded"):
            WebhookPayload(
                WebhookContentType.OCTET_STREAM,
                b"x" * (MAX_WEBHOOK_PAYLOAD_BYTES + 1),
            )
        with self.assertRaisesRegex(ValueError, "JSON payload is malformed"):
            WebhookPayload(WebhookContentType.JSON, b"not-json")
        with self.assertRaisesRegex(ValueError, "must be an object"):
            WebhookPayload(WebhookContentType.CLOUD_EVENTS_JSON, b"[]")
        descriptor = webhook_event_descriptor(WebhookEnqueued(_intent()))
        descriptor["intent"]["payload"]["content_digest"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "digest does not match"):
            webhook_event_from_descriptor(descriptor)

    def test_endpoint_shape_rejects_unsafe_or_open_forms(self) -> None:
        for url in (
            "ftp://hooks.example.test/events",
            "https://user:password@hooks.example.test/events",
            "https://hooks.example.test/events?token=value",
            "https://hooks.example.test/events#fragment",
            "https://hooks.example.test",
            "https://hooks.example.test/bad path",
            "https://hooks.example.test\\events",
            "https://hooks.example.test:0/events",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                WebhookEndpoint("orders", url)

    def test_signing_is_opaque_closed_and_uses_non_reserved_header(self) -> None:
        signing = _intent().signing

        self.assertEqual(signing.descriptor()["reference_id"], "secret://webhook/orders")
        self.assertNotIn("signing-value", repr(signing))
        with self.assertRaisesRegex(ValueError, "reserved"):
            WebhookSigning(
                SecretReference("secret://webhook/orders"),
                header_name="Authorization",
            )

    def test_backoff_is_deterministic_bounded_and_capped(self) -> None:
        policy = WebhookRetryPolicy(5, 100, 250, 3_600)

        self.assertEqual(
            tuple(policy.backoff_ms(value) for value in range(1, 6)),
            (100, 200, 250, 250, 250),
        )
        with self.assertRaises(ValueError):
            policy.backoff_ms(0)

    def test_success_history_reconstructs_without_mutable_cursor(self) -> None:
        intent = _intent()
        events = (
            WebhookEnqueued(intent),
            WebhookAttemptStarted(intent.identity, 1, NOW + timedelta(seconds=1)),
            WebhookAttemptFinished(
                intent.identity,
                1,
                WebhookAttemptOutcome.SUCCEEDED,
                NOW + timedelta(seconds=2),
                response_status=204,
            ),
        )

        state = replay_webhook_events(events)

        self.assertIs(state.status, WebhookDeliveryStatus.DELIVERED)
        self.assertEqual((state.attempts_started, state.attempts_completed), (1, 1))
        self.assertIs(state.last_outcome, WebhookAttemptOutcome.SUCCEEDED)
        self.assertEqual(replay_webhook_events(events), state)

    def test_retry_history_requires_exact_policy_time_and_attempt_order(self) -> None:
        intent = _intent()
        failed_at = NOW + timedelta(seconds=2)
        available_at = failed_at + timedelta(
            milliseconds=intent.retry_policy.backoff_ms(1)
        )
        failed = replay_webhook_events(
            (
                WebhookEnqueued(intent),
                WebhookAttemptStarted(intent.identity, 1, NOW + timedelta(seconds=1)),
                WebhookAttemptFinished(
                    intent.identity,
                    1,
                WebhookAttemptOutcome.RETRYABLE_FAILURE,
                failed_at,
                response_status=503,
                failure_code="http.server-error",
                ),
            )
        )

        scheduled = evolve_webhook_delivery(
            failed,
            WebhookRetryScheduled(intent.identity, 2, available_at, failed_at),
        )

        self.assertIs(scheduled.status, WebhookDeliveryStatus.RETRY_SCHEDULED)
        self.assertEqual(scheduled.next_attempt_at, available_at)
        with self.assertRaisesRegex(ValueError, "before availability"):
            evolve_webhook_delivery(
                scheduled,
                WebhookAttemptStarted(intent.identity, 2, failed_at),
            )
        started = evolve_webhook_delivery(
            scheduled,
            WebhookAttemptStarted(intent.identity, 2, available_at),
        )
        self.assertIs(started.status, WebhookDeliveryStatus.IN_FLIGHT)

        with self.assertRaisesRegex(ValueError, "before exhaustion"):
            evolve_webhook_delivery(
                failed,
                WebhookDeadLettered(
                    intent.identity,
                    "delivery.exhausted",
                    failed_at,
                ),
            )

    def test_terminal_and_uncertain_histories_have_distinct_destinations(self) -> None:
        intent = _intent()
        in_flight = replay_webhook_events(
            (
                WebhookEnqueued(intent),
                WebhookAttemptStarted(intent.identity, 1, NOW + timedelta(seconds=1)),
            )
        )
        terminal = evolve_webhook_delivery(
            in_flight,
            WebhookAttemptFinished(
                intent.identity,
                1,
                WebhookAttemptOutcome.TERMINAL_FAILURE,
                NOW + timedelta(seconds=2),
                failure_code="http.rejected",
            ),
        )
        dead = evolve_webhook_delivery(
            terminal,
            WebhookDeadLettered(
                intent.identity,
                "delivery.terminal",
                NOW + timedelta(seconds=3),
            ),
        )
        uncertain = evolve_webhook_delivery(
            in_flight,
            WebhookAttemptFinished(
                intent.identity,
                1,
                WebhookAttemptOutcome.UNCERTAIN,
                NOW + timedelta(seconds=2),
            ),
        )
        operator = evolve_webhook_delivery(
            uncertain,
            WebhookOperatorRequired(
                intent.identity,
                "delivery.uncertain",
                NOW + timedelta(seconds=3),
            ),
        )

        self.assertIs(dead.status, WebhookDeliveryStatus.DEAD_LETTER)
        self.assertIs(operator.status, WebhookDeliveryStatus.OPERATOR_REQUIRED)
        self.assertIs(uncertain.last_outcome, WebhookAttemptOutcome.UNCERTAIN)

    def test_impossible_histories_fail_at_the_pure_boundary(self) -> None:
        intent = _intent()
        queued = evolve_webhook_delivery(None, WebhookEnqueued(intent))
        foreign = WebhookDeliveryIdentity("workspace-a", "delivery-b")

        with self.assertRaisesRegex(ValueError, "another delivery"):
            evolve_webhook_delivery(
                queued,
                WebhookAttemptStarted(foreign, 1, NOW + timedelta(seconds=1)),
            )
        with self.assertRaisesRegex(ValueError, "finish only while in flight"):
            evolve_webhook_delivery(
                queued,
                WebhookAttemptFinished(
                    intent.identity,
                    1,
                    WebhookAttemptOutcome.SUCCEEDED,
                    NOW + timedelta(seconds=1),
                    response_status=200,
                ),
            )
        with self.assertRaisesRegex(ValueError, "begin with enqueue"):
            replay_webhook_events(
                (WebhookAttemptStarted(intent.identity, 1, NOW),)
            )
        with self.assertRaisesRegex(ValueError, "state shape is inconsistent"):
            WebhookDeliveryState(
                intent,
                WebhookDeliveryStatus.DELIVERED,
                0,
                0,
                NOW,
            )

    def test_every_event_variant_round_trips_through_exact_codec(self) -> None:
        intent = _intent()
        events = (
            WebhookEnqueued(intent),
            WebhookAttemptStarted(intent.identity, 1, NOW + timedelta(seconds=1)),
            WebhookAttemptFinished(
                intent.identity,
                1,
                WebhookAttemptOutcome.SUCCEEDED,
                NOW + timedelta(seconds=2),
                response_status=200,
            ),
            WebhookAttemptFinished(
                intent.identity,
                1,
                WebhookAttemptOutcome.RETRYABLE_FAILURE,
                NOW + timedelta(seconds=2),
                response_status=503,
                failure_code="http.server-error",
            ),
            WebhookAttemptFinished(
                intent.identity,
                1,
                WebhookAttemptOutcome.UNCERTAIN,
                NOW + timedelta(seconds=2),
            ),
            WebhookRetryScheduled(
                intent.identity,
                2,
                NOW + timedelta(seconds=3),
                NOW + timedelta(seconds=2),
            ),
            WebhookDeadLettered(
                intent.identity,
                "delivery.terminal",
                NOW + timedelta(seconds=3),
            ),
            WebhookOperatorRequired(
                intent.identity,
                "delivery.uncertain",
                NOW + timedelta(seconds=3),
            ),
        )

        for event in events:
            with self.subTest(event=type(event).__name__):
                descriptor = webhook_event_descriptor(event)
                self.assertEqual(webhook_event_from_descriptor(descriptor), event)
                self.assertEqual(
                    webhook_event_descriptor(webhook_event_from_descriptor(descriptor)),
                    descriptor,
                )

    def test_unknown_extra_and_malformed_descriptor_values_fail_closed(self) -> None:
        descriptor = webhook_event_descriptor(WebhookEnqueued(_intent()))
        descriptor["extra"] = "field"
        with self.assertRaisesRegex(ValueError, "unknown or missing"):
            webhook_event_from_descriptor(descriptor)

        unknown = {"variant": "executed"}
        with self.assertRaisesRegex(ValueError, "unknown webhook event"):
            webhook_event_from_descriptor(unknown)

        finished = webhook_event_descriptor(
            WebhookAttemptFinished(
                _intent().identity,
                1,
                WebhookAttemptOutcome.SUCCEEDED,
                NOW,
                response_status=200,
            )
        )
        finished["outcome"] = "maybe"
        with self.assertRaisesRegex(ValueError, "unknown webhook attempt"):
            webhook_event_from_descriptor(finished)

    def test_authority_descriptor_is_closed_typed_and_deterministic(self) -> None:
        authority = WebhookAuthority(
            "operator-a",
            "workspace-a",
            frozenset((WebhookScope.READ, WebhookScope.ENQUEUE)),
        )

        descriptor = authority.descriptor()

        self.assertEqual(descriptor["scopes"], ["webhook:enqueue", "webhook:read"])
        self.assertEqual(webhook_authority_from_descriptor(descriptor), authority)
        descriptor["scopes"].append("webhook:unknown")
        with self.assertRaisesRegex(ValueError, "unknown webhook authority scope"):
            webhook_authority_from_descriptor(descriptor)


def _intent() -> WebhookDeliveryIntent:
    return WebhookDeliveryIntent(
        "enqueue-a",
        WebhookDeliveryIdentity("workspace-a", "delivery-a"),
        WebhookEndpoint("orders", "https://hooks.example.test/orders"),
        WebhookPayload(WebhookContentType.JSON, b'{"id":42}'),
        WebhookRetryPolicy(3, 1_000, 10_000, 3_600),
        NOW,
        WebhookSigning(SecretReference("secret://webhook/orders")),
    )


if __name__ == "__main__":
    unittest.main()
