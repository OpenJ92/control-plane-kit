from __future__ import annotations

import concurrent.futures
import queue
import threading
import time
import unittest

import psycopg
from psycopg.errors import LockNotAvailable

from control_plane_kit_operations.lifecycle import RunLifecycleConflict
from control_plane_kit_operations.postgres import (
    PostgresExecutionStore,
    PostgresUnitOfWork,
)

from tests.activity_run_retry_interpreter_fixture import (
    ActivityRunRetryCommandService,
    PostgresActivityRunRetryFixture,
)
from tests.execution_lease_recovery_fixture import Sequence


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
        observations = 0
        observation_lock = threading.Lock()
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def observe(store, request_id):
            nonlocal observations
            with observation_lock:
                observations += 1
            return original_observe(store, request_id)

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

        PostgresExecutionStore.observe_request_lease_for_update = observe
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = tuple(
                    executor.submit(execute, prefix) for prefix in ("b", "c")
                )
                results = tuple(future.result(timeout=10) for future in futures)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

        self.assertEqual(sum(not result.replayed for result in results), 1)
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assertEqual(results[0].run, results[1].run)
        self.assertEqual(results[0].action, results[1].action)
        self.assertEqual(observations, 1)
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

    def test_distinct_key_race_forces_both_winner_identities(self) -> None:
        self.require_retry_service()
        for winner, loser in (("b", "c"), ("c", "b")):
            with self.subTest(winner=winner):
                self._force_distinct_winner(winner, loser)

    def _force_distinct_winner(self, winner: str, loser: str) -> None:
        self.reset_retry_truth()
        blocker = psycopg.connect(self.database_url)
        blocker.execute(
            "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id = 'request-a' FOR UPDATE"
        )
        blocker_pid = blocker.info.backend_pid
        winner_pids: queue.Queue[int] = queue.Queue()
        loser_pids: queue.Queue[int] = queue.Queue()
        winner_ids = Sequence(
            f"run-{winner}",
            f"decision-{winner}",
            f"opened-{winner}",
            f"action-{winner}",
        )
        loser_ids = Sequence(
            f"run-{loser}",
            f"decision-{loser}",
            f"opened-{loser}",
            f"action-{loser}",
        )
        winner_service = ActivityRunRetryCommandService(
            lambda: PostgresUnitOfWork(self.reporting_factory(winner_pids)),
            id_factory=winner_ids,
        )
        loser_service = ActivityRunRetryCommandService(
            lambda: PostgresUnitOfWork(self.reporting_factory(loser_pids)),
            id_factory=loser_ids,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            winner_future = executor.submit(
                winner_service.execute,
                self.retry_command(key=f"retry-{winner}"),
            )
            loser_future = None
            try:
                winner_pid = winner_pids.get(timeout=5)
                self.wait_until_blocked_by(winner_pid, blocker_pid)
                loser_future = executor.submit(
                    loser_service.execute,
                    self.retry_command(key=f"retry-{loser}"),
                )
                loser_pid = loser_pids.get(timeout=5)
                self.wait_until_blocked_by(loser_pid, winner_pid)
                blocker.commit()
                result = winner_future.result(timeout=10)
                with self.assertRaises(RunLifecycleConflict):
                    loser_future.result(timeout=10)
            finally:
                blocker.rollback()
                blocker.close()
                if loser_future is not None and not loser_future.done():
                    loser_future.cancel()
        self.assertEqual(result.run.run_id, f"run-{winner}")
        self.assertEqual(winner_ids.calls, [
            f"run-{winner}",
            f"decision-{winner}",
            f"opened-{winner}",
            f"action-{winner}",
        ])
        self.assertEqual(loser_ids.calls, [])
        self.assertEqual(len(self.snapshot()[3]), 2)
        self.assertEqual(len(self.snapshot()[2]), 1)

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
        observations = 0
        observation_lock = threading.Lock()
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def observe(store, request_id):
            nonlocal observations
            with observation_lock:
                observations += 1
            return original_observe(store, request_id)

        PostgresExecutionStore.observe_request_lease_for_update = observe
        try:
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
                    with observation_lock:
                        self.assertEqual(observations, 0)
                finally:
                    blocker.rollback()
                    blocker.close()
                result = future.result(timeout=10)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe
        self.assertEqual(result.run.run_id, "run-b")
        self.assertEqual(observations, 1)

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
        observations = 0
        observation_lock = threading.Lock()
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def observe(store, request_id):
            nonlocal observations
            with observation_lock:
                observations += 1
            return original_observe(store, request_id)

        PostgresExecutionStore.observe_request_lease_for_update = observe
        try:
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
                    with observation_lock:
                        self.assertEqual(observations, 0)
                finally:
                    blocker.rollback()
                    blocker.close()
                result = future.result(timeout=10)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe
        self.assertEqual(result.run.run_id, "run-b")
        self.assertEqual(observations, 1)

    def test_replay_locks_request_then_prior_then_new_run(self) -> None:
        self.require_retry_service()
        for blocked in ("request", "prior", "new"):
            with self.subTest(blocked=blocked):
                self._assert_replay_lock_stage(blocked)

    def _assert_replay_lock_stage(self, blocked: str) -> None:
        self.reset_retry_truth()
        command = self.retry_command()
        self.retry_service(
            "run-b", "retry-decision", "run-b-opened", "retry-action"
        ).execute(command)
        table, column, value = {
            "request": ("cpk_execution_requests", "request_id", "request-a"),
            "prior": ("cpk_activity_runs", "run_id", "run-a"),
            "new": ("cpk_activity_runs", "run_id", "run-b"),
        }[blocked]
        blocker = psycopg.connect(self.database_url)
        blocker.execute(
            f"SELECT {column} FROM {table} WHERE {column} = %s FOR UPDATE",
            (value,),
        )
        blocker_pid = blocker.info.backend_pid
        pids: queue.Queue[int] = queue.Queue()
        service = ActivityRunRetryCommandService(
            lambda: PostgresUnitOfWork(self.reporting_factory(pids)),
            id_factory=lambda: (_ for _ in ()).throw(
                AssertionError("retry replay allocated identity")
            ),
        )
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("retry replay sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(service.execute, command)
                try:
                    worker_pid = pids.get(timeout=5)
                    self.wait_until_blocked_by(worker_pid, blocker_pid)
                    with psycopg.connect(self.database_url) as probe:
                        if blocked == "request":
                            for run_id in ("run-a", "run-b"):
                                row = probe.execute(
                                    "SELECT run_id FROM cpk_activity_runs "
                                    "WHERE run_id = %s FOR UPDATE NOWAIT",
                                    (run_id,),
                                ).fetchone()
                                self.assertEqual(row, (run_id,))
                            probe.rollback()
                        elif blocked == "prior":
                            self._assert_nowait_locked(
                                probe,
                                "cpk_execution_requests",
                                "request_id",
                                "request-a",
                            )
                            row = probe.execute(
                                "SELECT run_id FROM cpk_activity_runs "
                                "WHERE run_id = 'run-b' FOR UPDATE NOWAIT"
                            ).fetchone()
                            self.assertEqual(row, ("run-b",))
                            probe.rollback()
                        else:
                            self._assert_nowait_locked(
                                probe,
                                "cpk_execution_requests",
                                "request_id",
                                "request-a",
                            )
                            self._assert_nowait_locked(
                                probe,
                                "cpk_activity_runs",
                                "run_id",
                                "run-a",
                            )
                finally:
                    blocker.rollback()
                    blocker.close()
                result = future.result(timeout=10)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe
        self.assertTrue(result.replayed)

    def _assert_nowait_locked(
        self,
        connection,
        table: str,
        column: str,
        value: str,
    ) -> None:
        with self.assertRaises(LockNotAvailable):
            connection.execute(
                f"SELECT {column} FROM {table} "
                f"WHERE {column} = %s FOR UPDATE NOWAIT",
                (value,),
            )
        connection.rollback()


if __name__ == "__main__":
    unittest.main()
