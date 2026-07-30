from __future__ import annotations

import unittest
from unittest.mock import call, patch

from control_plane_kit_core.verification import VerificationPolicy

from scripts import cpk_server_hosted_activity


class HostedActivityReadinessTests(unittest.TestCase):
    def test_policy_cadence_occurs_only_between_failed_attempts(self) -> None:
        policy = VerificationPolicy(
            timeout_seconds=3.0,
            interval_seconds=1.25,
            maximum_attempts=2,
        )

        with (
            patch.object(
                cpk_server_hosted_activity,
                "_http",
                side_effect=(
                    OSError("not ready"),
                    {"status": "ready", "runtime_interpreters": "docker"},
                ),
            ) as request,
            patch.object(cpk_server_hosted_activity.time, "sleep") as sleep,
        ):
            cpk_server_hosted_activity._wait_ready(
                "http://cpk-server",
                policy=policy,
            )

        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            [item.kwargs["timeout"] for item in request.call_args_list],
            [3.0, 3.0],
        )
        self.assertEqual(sleep.call_args_list, [call(1.25)])

    def test_first_attempt_success_has_no_delay(self) -> None:
        policy = VerificationPolicy(
            interval_seconds=2.0,
            maximum_attempts=3,
        )

        with (
            patch.object(
                cpk_server_hosted_activity,
                "_http",
                return_value={"status": "ready", "runtime_interpreters": "docker"},
            ),
            patch.object(cpk_server_hosted_activity.time, "sleep") as sleep,
        ):
            cpk_server_hosted_activity._wait_ready(
                "http://cpk-server",
                policy=policy,
            )

        sleep.assert_not_called()

    def test_exhaustion_has_no_trailing_delay(self) -> None:
        policy = VerificationPolicy(
            interval_seconds=1.5,
            maximum_attempts=2,
        )

        with (
            patch.object(
                cpk_server_hosted_activity,
                "_http",
                return_value={"status": "starting"},
            ),
            patch.object(cpk_server_hosted_activity.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                cpk_server_hosted_activity._wait_ready(
                    "http://cpk-server",
                    policy=policy,
                )

        self.assertEqual(sleep.call_args_list, [call(1.5)])


if __name__ == "__main__":
    unittest.main()
