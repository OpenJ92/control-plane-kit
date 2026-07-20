from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from examples.webhook_delivery_live import _wait_for_webhook_ready


class WebhookLiveHarnessTests(unittest.TestCase):
    def test_restart_readiness_retries_only_startup_unavailability(self) -> None:
        with (
            patch(
                "examples.webhook_delivery_live._request",
                side_effect=(
                    URLError(ConnectionRefusedError()),
                    (503, {"detail": "not ready"}),
                    (200, {"status": "ready"}),
                ),
            ) as request,
            patch("examples.webhook_delivery_live.sleep") as sleep,
        ):
            _wait_for_webhook_ready(
                "http://webhook:8080",
                timeout_seconds=30,
                poll_interval_seconds=0.01,
            )

        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_restart_readiness_rejects_malformed_success(self) -> None:
        with patch(
            "examples.webhook_delivery_live._request",
            return_value=(200, {"status": "healthy"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid response"):
                _wait_for_webhook_ready("http://webhook:8080")

    def test_restart_readiness_timeout_is_bounded_and_non_secret(self) -> None:
        with patch(
            "examples.webhook_delivery_live.monotonic",
            side_effect=(0.0, 0.0, 1.0, 1.0),
        ), patch(
            "examples.webhook_delivery_live._request",
            side_effect=URLError(ConnectionRefusedError()),
        ), patch("examples.webhook_delivery_live.sleep"):
            with self.assertRaisesRegex(
                RuntimeError,
                r"within 1s \(last observation: URLError\)",
            ):
                _wait_for_webhook_ready(
                    "http://webhook:8080",
                    timeout_seconds=1,
                    poll_interval_seconds=0.01,
                )


if __name__ == "__main__":
    unittest.main()
