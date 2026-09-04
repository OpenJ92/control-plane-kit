from __future__ import annotations

import unittest

from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleNotFound,
)
from control_plane_kit_operations.postgres import (
    PostgresActivityHistoryStore,
    PostgresExecutionStore,
)
from control_plane_kit_operations.records import OperationsRecordError

from tests.activity_run_retry_interpreter_fixture import (
    PostgresActivityRunRetryFixture,
)
from tests.execution_lease_recovery_fixture import safe_error


EXPECTED_BOUNDARIES = (
    (
        "action",
        PostgresActivityHistoryStore,
        "action_for_idempotency",
        "operation action history is invalid",
    ),
    (
        "session",
        PostgresActivityHistoryStore,
        "get_session_for_update",
        "operation session history is invalid",
    ),
    (
        "locator-request",
        PostgresExecutionStore,
        "get_request",
        "execution request history is invalid",
    ),
    (
        "locked-request",
        PostgresExecutionStore,
        "get_request_for_update",
        "execution request history is invalid",
    ),
    (
        "prior-run",
        PostgresExecutionStore,
        "get_run_for_request_for_update",
        "activity run history is invalid",
    ),
    (
        "latest-run",
        PostgresExecutionStore,
        "get_latest_run_for_request_for_update",
        "activity run history is invalid",
    ),
)


class PostgresActivityRunRetryStoreBoundaryTests(
    PostgresActivityRunRetryFixture,
    unittest.TestCase,
):
    def test_decoder_failures_are_owner_categorical(self) -> None:
        for label, owner, method_name, message in EXPECTED_BOUNDARIES:
            for error_type in (ValueError, OperationsRecordError):
                with self.subTest(boundary=label, error=error_type.__name__):
                    canary = f"{label}-{error_type.__name__}-canary"
                    raised = self._capture_store_failure(
                        owner,
                        method_name,
                        error_type(canary),
                        RunLifecycleConflict,
                    )
                    self.assertEqual(str(raised), message)
                    safe_error(self, raised, canary)

    def test_missing_rows_retain_not_found(self) -> None:
        for label, owner, method_name, _message in EXPECTED_BOUNDARIES[1:]:
            with self.subTest(boundary=label):
                self._capture_store_failure(
                    owner,
                    method_name,
                    KeyError(label),
                    RunLifecycleNotFound,
                )

    def test_unexpected_failures_preserve_exact_identity(self) -> None:
        for _label, owner, method_name, _message in EXPECTED_BOUNDARIES:
            for error_type in (TypeError, RuntimeError):
                with self.subTest(method=method_name, error=error_type.__name__):
                    injected = error_type(f"{method_name}-{error_type.__name__}")
                    raised = self._capture_store_failure(
                        owner,
                        method_name,
                        injected,
                        error_type,
                    )
                    self.assertIs(raised, injected)

    def _capture_store_failure(
        self,
        owner,
        method_name: str,
        injected: Exception,
        expected: type[Exception],
    ) -> Exception:
        self.reset_retry_truth()
        before = self.snapshot()
        original = getattr(owner, method_name)
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_read(*_args, **_kwargs):
            raise injected

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("store failure sampled database time")

        setattr(owner, method_name, fail_read)
        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with self.assertRaises(expected) as raised:
                self.retry_service(
                    "unused-a", "unused-b", "unused-c", "unused-d"
                ).execute(self.retry_command())
        finally:
            setattr(owner, method_name, original)
            PostgresExecutionStore.observe_request_lease_for_update = original_observe
        self.assertEqual(self.snapshot(), before)
        return raised.exception


if __name__ == "__main__":
    unittest.main()
