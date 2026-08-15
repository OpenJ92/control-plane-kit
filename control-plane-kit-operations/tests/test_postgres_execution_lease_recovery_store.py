from __future__ import annotations

import unittest

from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.records import OperationsRecordError


class _NoSqlConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def execute(self, *args):
        self.calls.append(args)
        raise AssertionError("invalid store input reached SQL")


class _TextSubclass(str):
    pass


class _FenceSubclass(ExecutionLeaseFence):
    pass


def _safe_error(test: unittest.TestCase, error: BaseException) -> None:
    test.assertIsNone(error.__cause__)
    test.assertIsNone(error.__context__)
    test.assertLessEqual(len(f"{error!s} {error!r}"), 512)


class ExecutionLeaseRecoveryStoreContractTests(unittest.TestCase):
    def require_store_methods(self) -> None:
        methods = (
            "get_latest_run_for_request_for_update",
            "rotate_request_claim",
            "abandon_request_claim",
        )
        self.assertEqual(
            [name for name in methods if not hasattr(PostgresExecutionStore, name)],
            [],
            "execution-lease recovery store operations are missing",
        )

    def test_invalid_rotate_inputs_execute_zero_sql(self) -> None:
        self.require_store_methods()
        cases = (
            (object(), ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            (_TextSubclass("request-a"), ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("request-a", _FenceSubclass("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), _FenceSubclass("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 9), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 2**63 - 1), ExecutionLeaseFence("worker-a", 2**63 - 1), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), object(), 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", True),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 0),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 3601),
        )
        for request_id, expected, replacement, observed_at, duration in cases:
            with self.subTest(
                request_type=type(request_id).__name__,
                expected_type=type(expected).__name__,
                replacement_type=type(replacement).__name__,
                observed_type=type(observed_at).__name__,
                duration=duration,
            ):
                connection = _NoSqlConnection()
                store = PostgresExecutionStore(connection)
                with self.assertRaises(OperationsRecordError) as captured:
                    store.rotate_request_claim(
                        request_id,
                        expected_fence=expected,
                        replacement_fence=replacement,
                        observed_at=observed_at,
                        lease_duration_seconds=duration,
                    )
                self.assertEqual(connection.calls, [])
                _safe_error(self, captured.exception)

    def test_invalid_abandon_inputs_execute_zero_sql(self) -> None:
        self.require_store_methods()
        cases = (
            (object(), ExecutionLeaseFence("worker-a", 7), "2026-08-15T04:00:00Z"),
            (_TextSubclass("request-a"), ExecutionLeaseFence("worker-a", 7), "2026-08-15T04:00:00Z"),
            ("request-a", _FenceSubclass("worker-a", 7), "2026-08-15T04:00:00Z"),
            ("request-a", ExecutionLeaseFence("worker-a", 7), object()),
        )
        for request_id, expected, observed_at in cases:
            with self.subTest(
                request_type=type(request_id).__name__,
                fence_type=type(expected).__name__,
                observed_type=type(observed_at).__name__,
            ):
                connection = _NoSqlConnection()
                store = PostgresExecutionStore(connection)
                with self.assertRaises(OperationsRecordError) as captured:
                    store.abandon_request_claim(
                        request_id,
                        expected_fence=expected,
                        observed_at=observed_at,
                    )
                self.assertEqual(connection.calls, [])
                _safe_error(self, captured.exception)


if __name__ == "__main__":
    unittest.main()
