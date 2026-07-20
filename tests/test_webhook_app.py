from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import unittest

from fastapi.testclient import TestClient
import psycopg

from control_plane_kit.operations.webhook import (
    PostgresWebhookUnitOfWork,
    WebhookDeliveryService,
    WebhookOutboundResult,
    install_webhook_schema,
)
from control_plane_kit.entrypoints.webhook_server.app import (
    MAX_WEBHOOK_API_REQUEST_BYTES,
    create_webhook_delivery_app,
)
from control_plane_kit.domains.webhook import (
    WebhookAttemptOutcome,
    WebhookContentType,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookEndpoint,
    WebhookPayload,
    WebhookRetryPolicy,
)


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


class SuccessfulOutbound:
    def __init__(self) -> None:
        self.requests = []

    def deliver(self, request):
        self.requests.append(request)
        return WebhookOutboundResult(WebhookAttemptOutcome.SUCCEEDED, 204)


class AdvancingClock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current


class IncrementingIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"claim-{self.value}"


class WebhookFastApiTests(unittest.TestCase):
    def test_app_factory_reports_its_entrypoint_home(self) -> None:
        self.assertEqual(
            create_webhook_delivery_app.__module__,
            "control_plane_kit.entrypoints.webhook_server.app",
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = os.environ.get("CPK_TEST_DATABASE_URL")
        if not cls.database_url:
            raise RuntimeError("CPK_TEST_DATABASE_URL is required; run ./test.sh")
        with psycopg.connect(cls.database_url) as connection:
            install_webhook_schema(connection)
        cls.admin = psycopg.connect(cls.database_url, autocommit=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.admin.close()

    def setUp(self) -> None:
        self.admin.execute(
            """
            TRUNCATE TABLE
              cpk_webhook_events,
              cpk_webhook_projections,
              cpk_webhook_commands,
              cpk_webhook_intents
            CASCADE
            """
        )
        self.outbound = SuccessfulOutbound()
        service = WebhookDeliveryService(
            lambda: PostgresWebhookUnitOfWork(
                lambda: psycopg.connect(self.database_url)
            ),
            self.outbound,
            clock=AdvancingClock(),
            id_factory=IncrementingIds(),
        )
        self.client = TestClient(
            create_webhook_delivery_app(
                service,
                identity_attestation_token="server-attestation",
                readiness=lambda: True,
            )
        )

    def test_authenticated_routes_drive_canonical_service_and_read_projection(self) -> None:
        intent = _intent()

        enqueued = self.client.post(
            "/__deploy/webhooks",
            headers=_headers(),
            json=intent.descriptor(),
        )
        claimed = self.client.post(
            "/__deploy/webhooks/delivery-a/claims",
            headers=_headers(),
            json={"command_id": "claim-command", "worker_id": "worker-a", "lease_seconds": 30},
        )
        claim_id = claimed.json()["delivery"]["active_claim"]["claim_id"]
        dispatched = self.client.post(
            "/__deploy/webhooks/delivery-a/dispatch",
            headers=_headers(),
            json={
                "command_id": "dispatch-command",
                "claim_id": claim_id,
                "worker_id": "worker-a",
            },
        )
        observed = self.client.get(
            "/__deploy/webhooks/delivery-a",
            headers=_headers(),
        )

        self.assertEqual(enqueued.status_code, 200)
        self.assertEqual(claimed.status_code, 200)
        self.assertEqual(dispatched.status_code, 200)
        self.assertTrue(dispatched.json()["external_effect_attempted"])
        self.assertEqual(dispatched.json()["delivery"]["status"], "delivered")
        self.assertEqual(observed.status_code, 200)
        self.assertEqual(observed.json()["delivery"]["status"], "delivered")
        self.assertEqual(observed.json()["journal_version"], 4)
        self.assertEqual(len(self.outbound.requests), 1)

    def test_authentication_scope_and_workspace_fail_closed(self) -> None:
        intent = _intent()
        unauthorized = self.client.post("/__deploy/webhooks", json=intent.descriptor())
        insufficient = self.client.post(
            "/__deploy/webhooks",
            headers=_headers(scopes="webhook:read"),
            json=intent.descriptor(),
        )
        foreign = self.client.post(
            "/__deploy/webhooks",
            headers=_headers(workspace="workspace-b"),
            json=intent.descriptor(),
        )
        oversized_identity = self.client.post(
            "/__deploy/webhooks",
            headers=_headers(scopes="webhook:enqueue," + "x" * 1_100),
            json=intent.descriptor(),
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(insufficient.status_code, 403)
        self.assertEqual(foreign.status_code, 403)
        self.assertEqual(oversized_identity.status_code, 400)
        count = self.admin.execute("SELECT count(*) FROM cpk_webhook_intents").fetchone()[0]
        self.assertEqual(count, 0)

    def test_body_is_bounded_before_json_or_service_execution(self) -> None:
        response = self.client.post(
            "/__deploy/webhooks",
            headers={**_headers(), "content-type": "application/json"},
            content=b"{" + b"x" * MAX_WEBHOOK_API_REQUEST_BYTES,
        )

        self.assertEqual(response.status_code, 413)
        count = self.admin.execute("SELECT count(*) FROM cpk_webhook_intents").fetchone()[0]
        self.assertEqual(count, 0)

    def test_unknown_command_fields_and_read_scope_are_exact(self) -> None:
        intent = _intent()
        self.client.post("/__deploy/webhooks", headers=_headers(), json=intent.descriptor())

        malformed = self.client.post(
            "/__deploy/webhooks/delivery-a/claims",
            headers=_headers(),
            json={
                "command_id": "claim-command",
                "worker_id": "worker-a",
                "lease_seconds": 30,
                "unexpected": True,
            },
        )
        denied_read = self.client.get(
            "/__deploy/webhooks/delivery-a",
            headers=_headers(scopes="webhook:enqueue"),
        )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(denied_read.status_code, 403)


def _headers(
    *,
    scopes: str = "webhook:enqueue,webhook:dispatch,webhook:recover,webhook:read",
    workspace: str = "workspace-a",
) -> dict[str, str]:
    return {
        "x-cpk-identity-attestation": "server-attestation",
        "x-cpk-authenticated-subject": "actor-a",
        "x-cpk-authenticated-workspace": workspace,
        "x-cpk-webhook-scopes": scopes,
    }


def _intent() -> WebhookDeliveryIntent:
    return WebhookDeliveryIntent(
        "enqueue-command",
        WebhookDeliveryIdentity("workspace-a", "delivery-a"),
        WebhookEndpoint("orders", "https://hooks.example.test/orders"),
        WebhookPayload(WebhookContentType.JSON, b'{"order_id":42}'),
        WebhookRetryPolicy(max_attempts=3, deadline_seconds=300),
        NOW,
    )


if __name__ == "__main__":
    unittest.main()
