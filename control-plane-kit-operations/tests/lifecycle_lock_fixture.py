"""Real PostgreSQL lock witnesses for #1896; no replacement command semantics."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from queue import Queue
import threading
import time

import psycopg

from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.records import RetryIdentity
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus


LIFECYCLE_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
REQUEST_LOCK = "SELECT request_id FROM cpk_execution_requests WHERE request_id=%s FOR UPDATE"
RUN_LOCK = "SELECT run_id FROM cpk_activity_runs WHERE run_id=%s FOR UPDATE"
SESSION_LOCK = "SELECT session_id FROM cpk_operation_sessions WHERE session_id=%s FOR UPDATE"
WORKSPACE_LOCK = "SELECT workspace_id FROM cpk_workspaces WHERE workspace_id=%s FOR UPDATE"
ATTEMPT_LOCK = (
    "SELECT run_id, activity_id, attempt FROM cpk_effect_attempts "
    "WHERE run_id=%s AND activity_id=%s AND attempt=%s FOR UPDATE"
)


class LifecycleLockFixture:
    def opposing_commands(self, leader, follower, pause_after):
        """Hold the real leader after a selected lock; prove the follower waits.

        Both services keep their own SQL, decisions, commit and rollback paths.
        The pause is scheduling only; no row, store result or clock is replaced.
        """
        reached, release = threading.Event(), threading.Event()
        leader_pids, follower_pids = Queue(), Queue()

        class Connection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, query, parameters=None):
                cursor = self.connection.execute(query, parameters)
                if not reached.is_set() and pause_after(" ".join(str(query).split()), parameters):
                    reached.set()
                    if not release.wait(10):
                        raise AssertionError("leader lock barrier was not released")
                return cursor

            def __getattr__(self, name):
                return getattr(self.connection, name)

        def connect(pids, held=False):
            connection = self.lock_connection()
            pids.put(connection.info.backend_pid)
            return Connection(connection) if held else connection

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(leader, lambda: PostgresUnitOfWork(lambda: connect(leader_pids, True)))
            try:
                first_pid = leader_pids.get(timeout=5)
                if not reached.wait(5):
                    if first.done():
                        first.result()
                    self.fail("leader never reached the selected real lock")
                second = pool.submit(follower, lambda: PostgresUnitOfWork(lambda: connect(follower_pids)))
                second_pid = follower_pids.get(timeout=5)
                deadline = time.monotonic() + 5
                while first_pid not in self.connection.execute(
                    "SELECT pg_blocking_pids(%s)", (second_pid,)
                ).fetchone()[0]:
                    if second.done():
                        second.result()
                        self.fail("opposing service did not wait for its leader")
                    if time.monotonic() >= deadline:
                        self.fail("opposing service never reached a real lock wait")
                    time.sleep(0.01)
            finally:
                release.set()
        return first, second

    def seed_distinct_latest_run(self):
        # Durable divergent truth exercises the refusal boundary; this fixture
        # is not evidence that retrying a RUNNING run is an admissible command.
        with self.unit_of_work() as uow:
            prior = uow.stores.execution.get_run("run-a")
            self.assertIs(prior.status, ActivityRunStatus.RUNNING)
            self.assertIsNotNone(prior.started_at)
            # Keep the active-request uniqueness law: a distinct retained
            # failed run can be latest while the selected old run is active.
            later = replace(prior, run_id="run-later", retry=RetryIdentity(2, "run-a"),
                status=ActivityRunStatus.FAILED, settled_at=None)
            uow.stores.execution._add_run(later)
            self.assertEqual(uow.stores.execution.get_run("run-a"), prior)
            uow.commit()
        return later

    def lock_connection(self):
        # Catalogue fixtures own a schema; older execution fixtures own public.
        path = self.connection.execute("SHOW search_path").fetchone()[0]
        connection = psycopg.connect(self.database_url)
        connection.execute("SELECT set_config('search_path', %s, false)", (path,))
        connection.execute("SET lock_timeout='8s'")
        connection.execute("SET statement_timeout='10s'")
        return connection

    def assert_row_lockable(self, query, parameters):
        with self.lock_connection() as probe:
            row = probe.execute(query + " NOWAIT", parameters).fetchone()
            self.assertIsNotNone(row, "an absent tuple cannot witness a free row lock")

    def assert_row_retained(self, query, parameters):
        with self.lock_connection() as probe:
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                probe.execute(query + " NOWAIT", parameters)

    def assert_advisory_available(self, key, *, available):
        with self.lock_connection() as probe:
            acquired = probe.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (key,)
            ).fetchone()[0]
            self.assertIs(acquired, available)

    @contextmanager
    def blocked_command(self, query, parameters, execute):
        """execute(factory) runs the actual service in a separately owned UoW."""
        blocker = self.lock_connection()
        row = blocker.execute(query, parameters).fetchone()
        self.assertIsNotNone(row, "blocker did not select an existing row/key")
        pids = Queue()

        def connect():
            connection = self.lock_connection()
            pids.put(connection.info.backend_pid)
            return connection

        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(execute, lambda: PostgresUnitOfWork(connect))
        try:
            pid = pids.get(timeout=5)
            deadline = time.monotonic() + 5
            while blocker.info.backend_pid not in self.connection.execute(
                "SELECT pg_blocking_pids(%s)", (pid,)
            ).fetchone()[0]:
                if future.done():
                    future.result()  # Surface an unexpected service failure first.
                    self.fail("command completed without taking its required lock")
                if time.monotonic() >= deadline:
                    self.fail("command did not reach its required PostgreSQL blocker")
                time.sleep(0.01)
            yield future
        finally:
            blocker.rollback()
            blocker.close()
            pool.shutdown(wait=True, cancel_futures=True)
