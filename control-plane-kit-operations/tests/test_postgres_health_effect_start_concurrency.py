"""One real concurrent health admission and one exact retained observation."""
import concurrent.futures
import queue
import threading
import unittest
from unittest import mock

from control_plane_kit_operations.effect_attempt_start import ExistingAttempt, NewlyStarted
from control_plane_kit_operations.postgres import PostgresExecutionStore
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture
from tests import test_postgres_effect_attempt_start_concurrency as existing_concurrency


class _FirstHealthIdBlocks(Sequence):
    def __init__(self):
        super().__init__("health-original", "health-request", "health-transit-jti", "health-workload-jti")
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self):
        value = super().__call__()
        if len(self.calls) == 1:
            self.entered.set()
            if not self.release.wait(timeout=10):
                raise AssertionError("health admission blocker timed out")
        return value


class PostgresHealthEffectStartConcurrencyTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def test_two_connections_create_one_pair_and_one_observation_without_second_clock_or_ids(self):
        command = self.start_health_command()
        first_ids = _FirstHealthIdBlocks()
        second_ids = Sequence("must-not-allocate")
        pids = queue.Queue()
        factory = existing_concurrency.PostgresEffectAttemptStartConcurrencyTests._factory_with_pids(self, pids)
        first, _ = self.health_service(ids=first_ids, unit_of_work=factory)
        second, _ = self.health_service(ids=second_ids, unit_of_work=factory)
        observations = []
        observation_lock = threading.Lock()
        original = PostgresExecutionStore.observe_request_lease_for_update
        def observe(store, request_id):
            value = original(store, request_id)
            with observation_lock:
                observations.append(value)
            return value
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        with mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update", observe):
            first_future = executor.submit(first.execute_health, command)
            try:
                if not first_ids.entered.wait(timeout=5):
                    first_future.result(timeout=5)
                    self.fail("first health admission did not reach ID allocation")
                first_pid = pids.get(timeout=5)
                second_future = executor.submit(second.execute_health, command)
                second_pid = pids.get(timeout=5)
                existing_concurrency.PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by(self, second_pid, first_pid)
                first_ids.release.set()
                results = first_future.result(timeout=10), second_future.result(timeout=10)
            finally:
                first_ids.release.set()
                executor.shutdown(wait=True, cancel_futures=True)
        self.assertEqual(sum(type(result.start) is NewlyStarted for result in results), 1)
        self.assertEqual(sum(type(result.start) is ExistingAttempt for result in results), 1)
        self.assertEqual(results[0].preparation, results[1].preparation)
        self.assertEqual(results[0].start.attempt, results[1].start.attempt)
        self.assertEqual(first_ids.calls, ["health-original", "health-request", "health-transit-jti", "health-workload-jti"])
        self.assertEqual(second_ids.calls, [])
        self.assertEqual(len(observations), 1)
        self.assertEqual(self.health_counts(), (1, 1, 2, 1))
