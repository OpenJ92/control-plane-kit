from __future__ import annotations

import concurrent.futures
import queue
import threading
import time
import unittest

import psycopg
from psycopg.errors import LockNotAvailable

from control_plane_kit_operations.lifecycle import RunLifecycleConflict
from control_plane_kit_operations.postgres import PostgresUnitOfWork

from tests.activity_run_retry_interpreter_fixture import (
    ActivityRunRetryCommandService,
    PostgresActivityRunRetryFixture,
)


class PostgresActivityRunRetryConcurrencyTests(
    PostgresActivityRunRetryFixture,
    unittest.TestCase,
):
    def reporting_factory(self, pids: queue.Queue[int]):
        def factory():
            connection = psycopg.connect(self.database_url)
            pids.put(connection.info.backend_pid)
            return connection

        return factory

    def wait_until_blocked_by(self, worker_pid: int, blocker_pid: int) -> None:
        deadline = time.monotonic() + 5
        while True:
            blocked_by = self.connection.execute(
                "SELECT pg_blocking_pids(%s)",
                (worker_pid,),
            ).fetchone()[0]
            if blocker_pid in blocked_by:
                return
            if time.monotonic() >= deadline:
                self.fail("retry worker did not reach the expected row lock")

    def test_same_key_concurrent_commands_converge_on_one_result(self) -> None:
        self.require_retry_service()
        self.reset_retry_truth()
        barrier = threading.Barrier(2)

        def execute(prefix: str):
            barrier.wait(timeout=5)
            return ActivityRunRetryCommandService(
                self.unit_of_work,
                id_factory=iter(
                    (
                        f"run-{prefix}",
                        f"decision-{prefix}",
                        f"opened-{prefix}",
                        f"action-{prefix}",
                    )
                ).__next__,
            ).execute(self.retry_command())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(execute, prefix) for prefix in ("b", "c"))
            results = tuple(future.result(timeout=10) for future in futures)

        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(results[0].run, results[1].run)
        self.assertEqual(results[0].action, results[1].action)
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot[3]), 2)
        self.assertEqual(
            sum(row[2] == "recovery_decision_recorded" for row in snapshot[1]),
            1,
        )
        self.assertEqual(len(snapshot[2]), 1)

    def test_distinct_keys_racing_one_prior_create_one_successor(self) -> None:
        self.require_retry_service()
        self.reset_retry_truth()
        barrier = threading.Barrier(2)

        def execute(prefix: str):
            barrier.wait(timeout=5)
            service = ActivityRunRetryCommandService(
                self.unit_of_work,
                id_factory=iter(
                    (
                        f"run-{prefix}",
                        f"decision-{prefix}",
                        f"opened-{prefix}",
                        f"action-{prefix}",
                    )
                ).__next__,
            )
            try:
                return service.execute(self.retry_command(key=f"retry-{prefix}"))
            except RunLifecycleConflict as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                future.result(timeout=10)
                for future in (
                    executor.submit(execute, "b"),
                    executor.submit(execute, "c"),
                )
            )

        self.assertEqual(
            sum(not isinstance(result, BaseException) for result in results),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, RunLifecycleConflict) for result in results),
            1,
        )
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot[3]), 2)
        self.assertEqual(len(snapshot[2]), 1)

    def test_run_remains_free_while_retry_waits_on_request(self) -> None:
        self.require_retry_service()
        self.reset_retry_truth()
        blocker = psycopg.connect(self.database_url)
        blocker.execute(
            "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id = 'request-a' FOR UPDATE"
        )
        blocker_pid = blocker.info.backend_pid
        pids: queue.Queue[int] = queue.Queue()
        service = ActivityRunRetryCommandService(
            lambda: PostgresUnitOfWork(self.reporting_factory(pids)),
            id_factory=iter(
                ("run-b", "retry-decision", "run-b-opened", "retry-action")
            ).__next__,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.execute, self.retry_command())
            try:
                worker_pid = pids.get(timeout=5)
                self.wait_until_blocked_by(worker_pid, blocker_pid)
                with psycopg.connect(self.database_url) as probe:
                    row = probe.execute(
                        "SELECT run_id FROM cpk_activity_runs "
                        "WHERE run_id = 'run-a' FOR UPDATE NOWAIT"
                    ).fetchone()
                    self.assertEqual(row[0], "run-a")
                    probe.rollback()
            finally:
                blocker.rollback()
                blocker.close()
            result = future.result(timeout=10)
        self.assertEqual(result.run.run_id, "run-b")

    def test_request_remains_held_while_retry_waits_on_run(self) -> None:
        self.require_retry_service()
        self.reset_retry_truth()
        blocker = psycopg.connect(self.database_url)
        blocker.execute(
            "SELECT run_id FROM cpk_activity_runs "
            "WHERE run_id = 'run-a' FOR UPDATE"
        )
        blocker_pid = blocker.info.backend_pid
        pids: queue.Queue[int] = queue.Queue()
        service = ActivityRunRetryCommandService(
            lambda: PostgresUnitOfWork(self.reporting_factory(pids)),
            id_factory=iter(
                ("run-b", "retry-decision", "run-b-opened", "retry-action")
            ).__next__,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.execute, self.retry_command())
            try:
                worker_pid = pids.get(timeout=5)
                self.wait_until_blocked_by(worker_pid, blocker_pid)
                with psycopg.connect(self.database_url) as probe:
                    with self.assertRaises(LockNotAvailable):
                        probe.execute(
                            "SELECT request_id FROM cpk_execution_requests "
                            "WHERE request_id = 'request-a' FOR UPDATE NOWAIT"
                        )
                    probe.rollback()
            finally:
                blocker.rollback()
                blocker.close()
            result = future.result(timeout=10)
        self.assertEqual(result.run.run_id, "run-b")


if __name__ == "__main__":
    unittest.main()
