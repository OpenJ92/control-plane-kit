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


class _EmptyCursor:
    def fetchone(self):
        return None


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def execute(self, *args):
        self.calls.append(args)
        return _EmptyCursor()


class _TextSubclass(str):
    pass


class _FenceSubclass(ExecutionLeaseFence):
    pass


def _safe_error(
    test: unittest.TestCase,
    error: BaseException,
    *canaries: str,
) -> None:
    test.assertIsNone(error.__cause__)
    test.assertIsNone(error.__context__)
    rendered = f"{error!s} {error!r}"
    test.assertLessEqual(len(rendered), 512)
    for canary in canaries:
        test.assertNotIn(canary, rendered)


def _candidate_canaries(*values: object) -> tuple[str, ...]:
    canaries: list[str] = []
    for value in values:
        if type(value) is _TextSubclass:
            canaries.append(str(value))
        elif type(value) is str and "canary" in value:
            canaries.append("canary")
        elif type(value) is str and len(value) > 512:
            canaries.append(value[:32])
    return tuple(canaries)


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

    def test_invalid_latest_run_inputs_execute_zero_sql(self) -> None:
        self.require_store_methods()
        for request_id in (
            object(),
            _TextSubclass("request-a"),
            "",
            "request\ncanary",
            "r" * 513,
        ):
            with self.subTest(request_type=type(request_id).__name__):
                connection = _NoSqlConnection()
                store = PostgresExecutionStore(connection)
                with self.assertRaises(OperationsRecordError) as captured:
                    store.get_latest_run_for_request_for_update(request_id)
                self.assertEqual(connection.calls, [])
                _safe_error(
                    self,
                    captured.exception,
                    *_candidate_canaries(request_id),
                )

    def test_invalid_rotate_inputs_execute_zero_sql(self) -> None:
        self.require_store_methods()
        cases = (
            (object(), ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            (_TextSubclass("request-a"), ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("request\ncanary", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("r" * 513, ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("request-a", _FenceSubclass("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), _FenceSubclass("worker-a", 8), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 9), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 2**63 - 1), ExecutionLeaseFence("worker-a", 2**63 - 1), "2026-08-15T04:00:00Z", 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), object(), 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), _TextSubclass("2026-08-15T04:00:00Z"), 600),
            ("request-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8), "timestamp-canary", 600),
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
                _safe_error(
                    self,
                    captured.exception,
                    *_candidate_canaries(request_id, observed_at),
                )

    def test_invalid_abandon_inputs_execute_zero_sql(self) -> None:
        self.require_store_methods()
        cases = (
            (object(), ExecutionLeaseFence("worker-a", 7), "2026-08-15T04:00:00Z"),
            (_TextSubclass("request-a"), ExecutionLeaseFence("worker-a", 7), "2026-08-15T04:00:00Z"),
            ("", ExecutionLeaseFence("worker-a", 7), "2026-08-15T04:00:00Z"),
            ("request\ncanary", ExecutionLeaseFence("worker-a", 7), "2026-08-15T04:00:00Z"),
            ("r" * 513, ExecutionLeaseFence("worker-a", 7), "2026-08-15T04:00:00Z"),
            ("request-a", _FenceSubclass("worker-a", 7), "2026-08-15T04:00:00Z"),
            ("request-a", ExecutionLeaseFence("worker-a", 7), object()),
            ("request-a", ExecutionLeaseFence("worker-a", 7), _TextSubclass("2026-08-15T04:00:00Z")),
            ("request-a", ExecutionLeaseFence("worker-a", 7), "timestamp-canary"),
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
                _safe_error(
                    self,
                    captured.exception,
                    *_candidate_canaries(request_id, observed_at),
                )

    def test_exact_duration_boundaries_reach_sql(self) -> None:
        self.require_store_methods()
        for duration in (1, 3600):
            with self.subTest(duration=duration):
                connection = _RecordingConnection()
                store = PostgresExecutionStore(connection)
                result = store.rotate_request_claim(
                    "request-a",
                    expected_fence=ExecutionLeaseFence("worker-a", 7),
                    replacement_fence=ExecutionLeaseFence("worker-a", 8),
                    observed_at="2026-08-15T04:00:00Z",
                    lease_duration_seconds=duration,
                )
                self.assertIsNone(result)
                self.assertEqual(len(connection.calls), 1)

    def test_maximum_request_identity_reaches_latest_run_sql(self) -> None:
        self.require_store_methods()
        connection = _RecordingConnection()
        store = PostgresExecutionStore(connection)
        try:
            result = store.get_latest_run_for_request_for_update("r" * 512)
        except KeyError:
            result = None
        self.assertIsNone(result)
        self.assertEqual(len(connection.calls), 1)


if __name__ == "__main__":
    unittest.main()
