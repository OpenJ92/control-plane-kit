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


class _FailingConnection:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def execute(self, *_args):
        raise self.error


class _TextSubclass(str):
    pass


class _FenceSubclass(ExecutionLeaseFence):
    pass


INVALID_RUN_IDS = (
    (object(), ()),
    (True, ("True",)),
    (_TextSubclass("subclass-canary"), ("subclass-canary",)),
    ("", ()),
    (" ", ()),
    ("-leading-canary", ("leading-canary",)),
    (".leading-canary", ("leading-canary",)),
    ("_leading-canary", ("leading-canary",)),
    (":leading-canary", ("leading-canary",)),
    ("slash/canary", ("slash/canary",)),
    ("space canary", ("space canary",)),
    *tuple(
        (f"a{chr(code)}control-canary", ("control-canary",))
        for code in (*range(32), 127)
    ),
    ("a" * 201, ("a" * 32,)),
)


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
        elif type(value) is str and len(value) > 200:
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

    def require_scoped_selector(self) -> None:
        self.assertTrue(
            hasattr(PostgresExecutionStore, "get_run_for_request_for_update"),
            "request-scoped activity-run selector is missing",
        )

    def test_request_scoped_run_selector_validates_both_ids_before_sql(self) -> None:
        self.require_scoped_selector()
        invalid_requests = (
            (object(), "run-a", ()),
            (True, "run-a", ("True",)),
            (
                _TextSubclass("request-canary"),
                "run-a",
                ("request-canary",),
            ),
            ("", "run-a", ()),
            ("request\ncanary", "run-a", ("canary",)),
            ("r" * 513, "run-a", ("r" * 32,)),
        )
        invalid = invalid_requests + tuple(
            ("request-a", run_id, canaries)
            for run_id, canaries in INVALID_RUN_IDS
        )
        for request_id, run_id, canaries in invalid:
            with self.subTest(
                request_type=type(request_id).__name__,
                run_type=type(run_id).__name__,
            ):
                connection = _NoSqlConnection()
                store = PostgresExecutionStore(connection)
                with self.assertRaises(OperationsRecordError) as captured:
                    store.get_run_for_request_for_update(request_id, run_id)
                self.assertEqual(connection.calls, [])
                _safe_error(
                    self,
                    captured.exception,
                    *canaries,
                )

    def test_request_scoped_run_selector_accepts_exact_identity_boundaries(self) -> None:
        self.require_scoped_selector()
        for request_id in ("r", "r" * 512):
            for run_id in ("r", "r" * 200):
                with self.subTest(
                    request_length=len(request_id),
                    run_length=len(run_id),
                ):
                    connection = _RecordingConnection()
                    store = PostgresExecutionStore(connection)
                    with self.assertRaises(KeyError):
                        store.get_run_for_request_for_update(request_id, run_id)
                    self.assertEqual(len(connection.calls), 1)

    def test_request_scoped_run_selector_has_fixed_candidate_free_miss(self) -> None:
        self.require_scoped_selector()
        connection = _RecordingConnection()
        store = PostgresExecutionStore(connection)
        with self.assertRaises(KeyError) as captured:
            store.get_run_for_request_for_update(
                "request-canary-a",
                "run-canary-a",
            )
        self.assertEqual(str(captured.exception), "'activity run was not found for request'")
        _safe_error(self, captured.exception, "request-canary-a", "run-canary-a")
        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(str(query).split())
        self.assertIn("WHERE request_id = %s AND run_id = %s FOR UPDATE", normalized)
        self.assertEqual(parameters, ("request-canary-a", "run-canary-a"))

    def test_request_scoped_run_selector_preserves_unexpected_sql_errors(self) -> None:
        self.require_scoped_selector()
        canary = RuntimeError("selector-sql-canary")
        store = PostgresExecutionStore(_FailingConnection(canary))
        with self.assertRaises(RuntimeError) as captured:
            store.get_run_for_request_for_update("request-a", "run-a")
        self.assertIs(captured.exception, canary)

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
