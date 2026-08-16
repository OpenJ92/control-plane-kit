from __future__ import annotations

import concurrent.futures
import queue
import threading
import time
import unittest
from unittest import mock

import psycopg
from psycopg.errors import LockNotAvailable

from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from control_plane_kit_operations.postgres import (
    PostgresExecutionStore,
    PostgresUnitOfWork,
)
from tests.effect_attempt_start_fixture import (
    EffectAttemptStartDenied,
    ExistingAttempt,
    NewlyStarted,
)
from tests.postgres_effect_attempt_start_fixture import (
    PostgresEffectAttemptStartFixture,
)
from tests.execution_lease_recovery_fixture import Sequence


class _BlockingId:
    def __init__(self, value: str) -> None:
        self.value = value
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("effect-start identity blocker timed out")
        return self.value


class PostgresEffectAttemptStartConcurrencyTests(
    PostgresEffectAttemptStartFixture,
    unittest.TestCase,
):
    def _wait_until_blocked_by(self, worker_pid: int, blocker_pid: int) -> None:
        deadline = time.monotonic() + 5
        while True:
            blocked_by = self.connection.execute(
                "SELECT pg_blocking_pids(%s)",
                (worker_pid,),
            ).fetchone()[0]
            if blocker_pid in blocked_by:
                return
            if time.monotonic() >= deadline:
                self.fail("effect-attempt start did not reach the expected blocker")

    def _factory_with_pids(self, pids: queue.Queue[int]):
        def connection_factory():
            connection = psycopg.connect(self.database_url)
            connection.execute("SET lock_timeout = '10s'")
            connection.execute("SET statement_timeout = '12s'")
            pids.put(connection.info.backend_pid)
            return connection

        return lambda: PostgresUnitOfWork(connection_factory)

    def _assert_run_lockable(self) -> None:
        with psycopg.connect(self.database_url) as probe:
            self.assertEqual(
                probe.execute(
                    "SELECT run_id FROM cpk_activity_runs "
                    "WHERE run_id='run-a' FOR UPDATE NOWAIT"
                ).fetchone(),
                ("run-a",),
            )

    def _assert_attempt_absent_and_lockable(self) -> None:
        with psycopg.connect(self.database_url) as probe:
            self.assertIsNone(
                probe.execute(
                    "SELECT run_id FROM cpk_effect_attempts "
                    "WHERE run_id='run-a' AND activity_id='start-runtime' "
                    "AND attempt=1 FOR UPDATE NOWAIT"
                ).fetchone()
            )

    def _assert_retained(self, table: str, column: str, value: str) -> None:
        with psycopg.connect(self.database_url) as probe:
            with self.assertRaises(LockNotAvailable):
                probe.execute(
                    f"SELECT {column} FROM {table} WHERE {column}=%s "
                    "FOR UPDATE NOWAIT",
                    (value,),
                )

    def _assert_foreign_truth_lockable(self) -> None:
        with psycopg.connect(self.database_url) as probe:
            self.assertEqual(
                probe.execute(
                    "SELECT request_id FROM cpk_execution_requests "
                    "WHERE request_id='request-b' FOR UPDATE NOWAIT"
                ).fetchone(),
                ("request-b",),
            )
            self.assertEqual(
                probe.execute(
                    "SELECT run_id FROM cpk_activity_runs "
                    "WHERE run_id='run-foreign' FOR UPDATE NOWAIT"
                ).fetchone(),
                ("run-foreign",),
            )
            self.assertEqual(
                probe.execute(
                    "SELECT run_id, activity_id, attempt "
                    "FROM cpk_effect_attempts WHERE run_id='run-foreign' "
                    "AND activity_id='start-runtime' AND attempt=1 "
                    "FOR UPDATE NOWAIT"
                ).fetchone(),
                ("run-foreign", "start-runtime", 1),
            )

    def _blocked_execution(self, blocker: str, *, existing: bool):
        if existing:
            self.persisted_started()
            self.seed_foreign_run()
            self.seed_foreign_attempt()
        blocker_connection = psycopg.connect(self.database_url)
        if blocker == "request":
            blocker_connection.execute(
                "SELECT request_id FROM cpk_execution_requests "
                "WHERE request_id='request-a' FOR UPDATE"
            )
        elif blocker == "run":
            blocker_connection.execute(
                "SELECT run_id FROM cpk_activity_runs "
                "WHERE run_id='run-a' FOR UPDATE"
            )
        else:
            blocker_connection.execute(
                "SELECT run_id FROM cpk_effect_attempts "
                "WHERE run_id='run-a' AND activity_id='start-runtime' "
                "AND attempt=1 FOR UPDATE"
            )
        blocker_pid = blocker_connection.info.backend_pid
        worker_pids: queue.Queue[int] = queue.Queue()
        service = EffectAttemptStartService(
            self._factory_with_pids(worker_pids),
            id_factory=lambda: "lock-order-event",
        )
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(service.execute, self.start_command())
        try:
            try:
                immediate = future.result(timeout=0.1)
            except concurrent.futures.TimeoutError:
                worker_pid = worker_pids.get(timeout=5)
                self._wait_until_blocked_by(worker_pid, blocker_pid)
                if blocker == "request":
                    self._assert_run_lockable()
                    self._assert_attempt_absent_and_lockable()
                elif blocker == "run":
                    self._assert_retained(
                        "cpk_execution_requests", "request_id", "request-a"
                    )
                    self._assert_attempt_absent_and_lockable()
                else:
                    self._assert_retained(
                        "cpk_execution_requests", "request_id", "request-a"
                    )
                    self._assert_retained(
                        "cpk_activity_runs", "run_id", "run-a"
                    )
                    self._assert_foreign_truth_lockable()
                blocker_connection.rollback()
                blocker_connection.close()
                return future.result(timeout=5)
            else:
                return immediate
        finally:
            if not blocker_connection.closed:
                blocker_connection.rollback()
                blocker_connection.close()
            executor.shutdown(wait=True, cancel_futures=True)

    def test_request_blocker_leaves_run_and_absent_attempt_free(self) -> None:
        result = self._blocked_execution("request", existing=False)
        self.assertIsInstance(result, NewlyStarted)

    def test_run_blocker_retains_request_and_leaves_absent_attempt_free(self) -> None:
        result = self._blocked_execution("run", existing=False)
        self.assertIsInstance(result, NewlyStarted)

    def test_existing_attempt_blocker_retains_request_and_run(self) -> None:
        result = self._blocked_execution("attempt", existing=True)
        self.assertIsInstance(result, ExistingAttempt)

    def test_identical_starters_have_one_observation_event_and_dispatch_result(self) -> None:
        for first_label in ("left", "right"):
            with self.subTest(first=first_label):
                self.reset_start_truth()
                first_id = _BlockingId(f"{first_label}-start-event")
                second_label = "right" if first_label == "left" else "left"
                observations = 0
                observation_lock = threading.Lock()
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def observe(store, request_id):
                    nonlocal observations
                    with observation_lock:
                        observations += 1
                    return original_observe(store, request_id)

                pids: queue.Queue[int] = queue.Queue()
                first = EffectAttemptStartService(
                    self._factory_with_pids(pids),
                    id_factory=first_id,
                )
                second_ids = Sequence(f"{second_label}-must-not-allocate")
                second = EffectAttemptStartService(
                    self._factory_with_pids(pids),
                    id_factory=second_ids,
                )
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                with mock.patch.object(
                    PostgresExecutionStore,
                    "observe_request_lease_for_update",
                    observe,
                ):
                    first_future = executor.submit(
                        first.execute,
                        self.start_command(),
                    )
                    try:
                        if not first_id.entered.wait(timeout=0.1):
                            first_future.result(timeout=0.1)
                        first_pid = pids.get(timeout=5)
                        second_future = executor.submit(
                            second.execute,
                            self.start_command(),
                        )
                        second_pid = pids.get(timeout=5)
                        self._wait_until_blocked_by(second_pid, first_pid)
                        first_id.release.set()
                        results = (
                            first_future.result(timeout=10),
                            second_future.result(timeout=10),
                        )
                    finally:
                        first_id.release.set()
                        executor.shutdown(wait=True, cancel_futures=True)

                self.assertEqual(
                    sum(isinstance(value, NewlyStarted) for value in results),
                    1,
                )
                self.assertEqual(
                    sum(isinstance(value, ExistingAttempt) for value in results),
                    1,
                )
                self.assertEqual(observations, 1)
                self.assertEqual(first_id.calls, 1)
                self.assertEqual(second_ids.calls, [])
                self.assertEqual(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM cpk_effect_attempts"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM cpk_activity_events "
                        "WHERE event_type='step_started' "
                        "AND run_id='run-a' AND payload->>'activity_id'="
                        "'start-runtime'"
                    ).fetchone()[0],
                    1,
                )

    def test_start_then_claim_replacement_makes_old_replay_stale(self) -> None:
        first = self.start_service("start-before-rotation").execute(
            self.start_command()
        )
        self.assertIsInstance(first, NewlyStarted)
        self.replace_claim()
        before = self.attempt_snapshot()
        with self.reject_database_observation(
            "stale post-rotation replay sampled database time"
        ):
            with self.assertRaises(EffectAttemptStartDenied) as caught:
                self.start_service("stale-replay-must-not-allocate").execute(
                    self.start_command()
                )
        self.assert_safe_error(caught.exception)
        self.assertEqual(self.attempt_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
