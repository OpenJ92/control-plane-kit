from __future__ import annotations

import concurrent.futures
import queue
import threading
import time
import unittest
from unittest import mock

import psycopg
from psycopg.errors import LockNotAvailable

from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import (
    EffectAttemptFoldService,
)
from control_plane_kit_operations.postgres import (
    PostgresExecutionStore,
    PostgresUnitOfWork,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_fold_fixture import (
    AUTHORITY_ERROR,
    PostgresEffectAttemptFoldFixture,
)


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
            raise AssertionError("effect-fold identity blocker timed out")
        return self.value


class PostgresEffectAttemptFoldConcurrencyTests(
    PostgresEffectAttemptFoldFixture,
    unittest.TestCase,
):
    def _require_transaction_stage(self) -> None:
        self.seed_fold_source("succeeded")
        self.fold_service("transaction-stage-probe").execute(
            self.fold_command("succeeded")
        )
        self.reset_start_truth()

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
                self.fail("effect-attempt fold did not reach the expected blocker")

    def _factory_with_pids(self, pids: queue.Queue[int]):
        def connection_factory():
            connection = psycopg.connect(self.database_url)
            connection.execute("SET lock_timeout = '10s'")
            connection.execute("SET statement_timeout = '12s'")
            pids.put(connection.info.backend_pid)
            return connection

        return lambda: PostgresUnitOfWork(connection_factory)

    def _assert_lockable(self, query: str, expected: tuple[object, ...]) -> None:
        with psycopg.connect(self.database_url) as probe:
            self.assertEqual(probe.execute(query).fetchone(), expected)

    def _assert_retained(self, query: str) -> None:
        with psycopg.connect(self.database_url) as probe:
            with self.assertRaises(LockNotAvailable):
                probe.execute(query)

    def _blocked_execution(self, blocker: str):
        self._require_transaction_stage()
        self.seed_fold_source("succeeded")
        self.seed_foreign_run()
        self.seed_foreign_attempt()
        blocker_connection = psycopg.connect(self.database_url)
        queries = {
            "request": "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id='request-a' FOR UPDATE",
            "run": "SELECT run_id FROM cpk_activity_runs "
            "WHERE run_id='run-a' FOR UPDATE",
            "attempt": "SELECT run_id FROM cpk_effect_attempts "
            "WHERE run_id='run-a' AND activity_id='start-runtime' "
            "AND attempt=1 FOR UPDATE",
        }
        blocker_connection.execute(queries[blocker])
        blocker_pid = blocker_connection.info.backend_pid
        worker_pids: queue.Queue[int] = queue.Queue()
        service = self.checked_fold_service(
            EffectAttemptFoldService(
                self._factory_with_pids(worker_pids),
                id_factory=lambda: "lock-order-event",
            )
        )
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(service.execute, self.fold_command("succeeded"))
        try:
            worker_pid = worker_pids.get(timeout=5)
            self._wait_until_blocked_by(worker_pid, blocker_pid)
            if blocker == "request":
                self._assert_lockable(
                    "SELECT run_id FROM cpk_activity_runs WHERE run_id='run-a' "
                    "FOR UPDATE NOWAIT",
                    ("run-a",),
                )
                self._assert_lockable(
                    "SELECT run_id, activity_id, attempt FROM cpk_effect_attempts "
                    "WHERE run_id='run-a' AND activity_id='start-runtime' "
                    "AND attempt=1 FOR UPDATE NOWAIT",
                    ("run-a", "start-runtime", 1),
                )
            elif blocker == "run":
                self._assert_retained(
                    "SELECT request_id FROM cpk_execution_requests "
                    "WHERE request_id='request-a' FOR UPDATE NOWAIT"
                )
                self._assert_lockable(
                    "SELECT run_id, activity_id, attempt FROM cpk_effect_attempts "
                    "WHERE run_id='run-a' AND activity_id='start-runtime' "
                    "AND attempt=1 FOR UPDATE NOWAIT",
                    ("run-a", "start-runtime", 1),
                )
            else:
                self._assert_retained(
                    "SELECT request_id FROM cpk_execution_requests "
                    "WHERE request_id='request-a' FOR UPDATE NOWAIT"
                )
                self._assert_retained(
                    "SELECT run_id FROM cpk_activity_runs WHERE run_id='run-a' "
                    "FOR UPDATE NOWAIT"
                )
                self._assert_lockable(
                    "SELECT run_id, activity_id, attempt FROM cpk_effect_attempts "
                    "WHERE run_id='run-foreign' AND activity_id='start-runtime' "
                    "AND attempt=1 FOR UPDATE NOWAIT",
                    ("run-foreign", "start-runtime", 1),
                )
            blocker_connection.rollback()
            blocker_connection.close()
            return future.result(timeout=5)
        finally:
            if not blocker_connection.closed:
                blocker_connection.rollback()
                blocker_connection.close()
            executor.shutdown(wait=True, cancel_futures=True)

    def test_request_run_attempt_lock_order_and_unrelated_attempt_freedom(self) -> None:
        for blocker in ("request", "run", "attempt"):
            with self.subTest(blocker=blocker):
                result = self._blocked_execution(blocker)
                self.assertIsInstance(result, NewlyFolded)

    def test_identical_folds_have_one_clock_id_event_and_cas(self) -> None:
        self._require_transaction_stage()
        for first_label in ("left", "right"):
            with self.subTest(first=first_label):
                self.seed_fold_source("succeeded")
                first_id = _BlockingId(f"{first_label}-fold-event")
                second_ids = Sequence("duplicate-must-not-allocate")
                observations = 0
                observation_lock = threading.Lock()
                original_observe = PostgresExecutionStore.observe_request_lease_for_update

                def observe(store, request_id):
                    nonlocal observations
                    with observation_lock:
                        observations += 1
                    return original_observe(store, request_id)

                pids: queue.Queue[int] = queue.Queue()
                first = self.checked_fold_service(
                    EffectAttemptFoldService(
                        self._factory_with_pids(pids), id_factory=first_id
                    )
                )
                second = self.checked_fold_service(
                    EffectAttemptFoldService(
                        self._factory_with_pids(pids), id_factory=second_ids
                    )
                )
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                with mock.patch.object(
                    PostgresExecutionStore,
                    "observe_request_lease_for_update",
                    observe,
                ):
                    first_future = executor.submit(
                        first.execute, self.fold_command("succeeded")
                    )
                    try:
                        self.assertTrue(first_id.entered.wait(timeout=5))
                        first_pid = pids.get(timeout=5)
                        second_future = executor.submit(
                            second.execute, self.fold_command("succeeded")
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

                self.assertEqual(sum(isinstance(v, NewlyFolded) for v in results), 1)
                self.assertEqual(sum(isinstance(v, ExistingFold) for v in results), 1)
                self.assertEqual(observations, 1)
                self.assertEqual(first_id.calls, 1)
                self.assertEqual(second_ids.calls, [])

    def test_incompatible_direct_and_recovery_decisions_have_one_winner(self) -> None:
        self._require_transaction_stage()
        pairs = (
            ("succeeded", "failed"),
            ("recovered-succeeded", "abandoned"),
        )
        for left, right in pairs:
            for first_story, second_story in ((left, right), (right, left)):
                with self.subTest(first=first_story, second=second_story):
                    self.seed_fold_source(first_story)
                    first_id = _BlockingId(f"winner-{first_story}")
                    pids: queue.Queue[int] = queue.Queue()
                    first = self.checked_fold_service(
                        EffectAttemptFoldService(
                            self._factory_with_pids(pids), id_factory=first_id
                        )
                    )
                    second = self.checked_fold_service(
                        EffectAttemptFoldService(
                            self._factory_with_pids(pids),
                            id_factory=Sequence("loser-must-not-allocate"),
                        )
                    )
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                    first_future = executor.submit(
                        first.execute, self.fold_command(first_story)
                    )
                    try:
                        self.assertTrue(first_id.entered.wait(timeout=5))
                        first_pid = pids.get(timeout=5)
                        second_future = executor.submit(
                            second.execute, self.fold_command(second_story)
                        )
                        second_pid = pids.get(timeout=5)
                        self._wait_until_blocked_by(second_pid, first_pid)
                        first_id.release.set()
                        winner = first_future.result(timeout=10)
                        with self.assertRaises(EffectAttemptFoldConflict):
                            second_future.result(timeout=10)
                    finally:
                        first_id.release.set()
                        executor.shutdown(wait=True, cancel_futures=True)
                    self.assertIsInstance(winner, NewlyFolded)
                    self.assertEqual(self.current_attempt(), winner.attempt)

    def test_direct_fold_and_claim_rotation_force_both_winner_orders(self) -> None:
        self._require_transaction_stage()
        for winner in ("fold", "rotation"):
            with self.subTest(winner=winner):
                self.seed_fold_source("succeeded")
                pids: queue.Queue[int] = queue.Queue()
                if winner == "fold":
                    blocker = _BlockingId("direct-before-rotation")
                    service = self.checked_fold_service(
                        EffectAttemptFoldService(
                            self._factory_with_pids(pids), id_factory=blocker
                        )
                    )
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                    fold_future = executor.submit(
                        service.execute, self.fold_command("succeeded")
                    )
                    rotation = psycopg.connect(self.database_url)
                    try:
                        self.assertTrue(blocker.entered.wait(timeout=5))
                        fold_pid = pids.get(timeout=5)
                        rotate_future = executor.submit(
                            rotation.execute,
                            "UPDATE cpk_execution_requests SET claim_worker_id='worker-b', "
                            "claim_generation=8, claimed_at='2098-01-02T00:00:00Z', "
                            "lease_expires_at='2099-01-02T00:00:00Z' "
                            "WHERE request_id='request-a'",
                        )
                        self._wait_until_blocked_by(
                            rotation.info.backend_pid,
                            fold_pid,
                        )
                        blocker.release.set()
                        result = fold_future.result(timeout=10)
                        rotate_future.result(timeout=10)
                        rotation.commit()
                    finally:
                        blocker.release.set()
                        rotation.close()
                        executor.shutdown(wait=True, cancel_futures=True)
                    self.assertIsInstance(result, NewlyFolded)
                else:
                    rotation = psycopg.connect(self.database_url)
                    rotation.execute(
                        "UPDATE cpk_execution_requests SET claim_worker_id='worker-b', "
                        "claim_generation=8, claimed_at='2098-01-02T00:00:00Z', "
                        "lease_expires_at='2099-01-02T00:00:00Z' "
                        "WHERE request_id='request-a'"
                    )
                    pids: queue.Queue[int] = queue.Queue()
                    service = self.checked_fold_service(
                        EffectAttemptFoldService(
                            self._factory_with_pids(pids),
                            id_factory=Sequence("must-not-allocate"),
                        )
                    )
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    with self.reject_fold_database_observation(
                        "stale direct fold sampled database time"
                    ):
                        future = executor.submit(
                            service.execute,
                            self.fold_command("succeeded"),
                        )
                        try:
                            worker_pid = pids.get(timeout=5)
                            self._wait_until_blocked_by(
                                worker_pid,
                                rotation.info.backend_pid,
                            )
                            rotation.commit()
                            with self.assertRaises(EffectAttemptFoldDenied) as caught:
                                future.result(timeout=10)
                        finally:
                            rotation.close()
                            executor.shutdown(wait=True, cancel_futures=True)
                    self.assertEqual(str(caught.exception), AUTHORITY_ERROR)

    def test_recovery_and_claim_rotation_force_both_temporal_worlds(self) -> None:
        self._require_transaction_stage()
        for winner in ("recovery", "rotation"):
            with self.subTest(winner=winner):
                self.seed_fold_source("recovered-succeeded")
                if winner == "recovery":
                    pids: queue.Queue[int] = queue.Queue()
                    blocker = _BlockingId("recovery-before-rotation")
                    service = self.checked_fold_service(
                        EffectAttemptFoldService(
                            self._factory_with_pids(pids), id_factory=blocker
                        )
                    )
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                    fold_future = executor.submit(
                        service.execute,
                        self.fold_command("recovered-succeeded"),
                    )
                    rotation = psycopg.connect(self.database_url)
                    try:
                        self.assertTrue(blocker.entered.wait(timeout=5))
                        fold_pid = pids.get(timeout=5)
                        rotate_future = executor.submit(
                            rotation.execute,
                            "UPDATE cpk_execution_requests SET claim_worker_id='worker-b', "
                            "claim_generation=8, claimed_at='2098-01-02T00:00:00Z', "
                            "lease_expires_at='2099-01-02T00:00:00Z' "
                            "WHERE request_id='request-a'",
                        )
                        self._wait_until_blocked_by(
                            rotation.info.backend_pid,
                            fold_pid,
                        )
                        blocker.release.set()
                        first = fold_future.result(timeout=10)
                        rotate_future.result(timeout=10)
                        rotation.commit()
                    finally:
                        blocker.release.set()
                        rotation.close()
                        executor.shutdown(wait=True, cancel_futures=True)
                    with self.reject_fold_database_observation(
                        "rotated recovery replay sampled database time"
                    ):
                        replay = self.fold_service("must-not-allocate").execute(
                            self.fold_command(
                                "recovered-succeeded",
                                authority=self.authority("worker-b"),
                                fence=self.fence("worker-b", 8),
                            )
                        )
                    self.assertEqual(replay, ExistingFold(first.attempt))
                else:
                    rotation = psycopg.connect(self.database_url)
                    rotation.execute(
                        "UPDATE cpk_execution_requests SET claim_worker_id='worker-b', "
                        "claim_generation=8, claimed_at='2098-01-02T00:00:00Z', "
                        "lease_expires_at='2099-01-02T00:00:00Z' "
                        "WHERE request_id='request-a'"
                    )
                    pids: queue.Queue[int] = queue.Queue()
                    stale_ids = Sequence("stale-must-not-allocate")
                    service = self.checked_fold_service(
                        EffectAttemptFoldService(
                            self._factory_with_pids(pids), id_factory=stale_ids
                        )
                    )
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    with self.reject_fold_database_observation(
                        "stale recovery sampled database time"
                    ):
                        future = executor.submit(
                            service.execute,
                            self.fold_command("recovered-succeeded"),
                        )
                        try:
                            worker_pid = pids.get(timeout=5)
                            self._wait_until_blocked_by(
                                worker_pid,
                                rotation.info.backend_pid,
                            )
                            rotation.commit()
                            with self.assertRaises(EffectAttemptFoldDenied):
                                future.result(timeout=10)
                        finally:
                            rotation.close()
                            executor.shutdown(wait=True, cancel_futures=True)
                    self.assertEqual(stale_ids.calls, [])
                    current = self.fold_service("rotation-before-recovery").execute(
                        self.fold_command(
                            "recovered-succeeded",
                            authority=self.authority("worker-b"),
                            fence=self.fence("worker-b", 8),
                        )
                    )
                    self.assertIsInstance(current, NewlyFolded)


if __name__ == "__main__":
    unittest.main()
