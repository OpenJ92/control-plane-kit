from __future__ import annotations

import concurrent.futures
import os
import queue
import time
import unittest

import psycopg

from gateway_rotation_overlap_fixture import GatewayRotationOverlapFixture
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    AdvanceGatewayKeyRotationDeployment,
    GatewayKeyRotationDeploymentHandoff,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema


class CountingIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


class GatewayKeyRotationDeploymentLockOrderTests(
    GatewayRotationOverlapFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through an isolated Operations PostgreSQL target")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()
        self.prepared = self._prepare()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def _prepare(self):
        timestamps = iter(
            f"2026-08-02T02:{minute:02d}:00Z" for minute in range(30)
        )
        program = GatewayKeyRotationOverlapPreparationProgram(
            self.unit_of_work,
            clock=lambda: next(timestamps),
            trusted_epoch_clock=lambda: 2_000,
            id_factory=CountingIds("deployment-lock-order"),
        )
        return program.prepare(
            PrepareGatewayKeyRotationOverlap(
                rotation_id=self.rotation_id,
                expected_rotation_version=self.rotation_version,
                expected_authored_graph_id="graph-a",
                expected_current_realized_projection_id="projection-a",
                expected_desired_realized_projection_id="projection-a",
                expected_desired_graph_revision=1,
                actor_id="operator-a",
                actor_scopes=(
                    PolicyScope.DELEGATION_KEY_ROTATE,
                    PolicyScope.PLAN_EXECUTE,
                    PolicyScope.EXECUTION_OPERATE,
                ),
                worker_authority=ExecutionWorkerAuthority(
                    "worker-a",
                    (PolicyScope.EXECUTION_OPERATE,),
                ),
                lease_duration=ExecutionLeaseDuration(1800),
            )
        )

    def _command(self) -> AdvanceGatewayKeyRotationDeployment:
        return AdvanceGatewayKeyRotationDeployment(
            transition=AdvanceGatewayKeyRotation(
                rotation_id=self.rotation_id,
                transition_id="deployment-overlap-blocked-lock-order",
                expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                expected_version=self.prepared.rotation.version,
                target_status=GatewayKeyRotationStatus.BLOCKED,
                advanced_by="operator-a",
                advanced_at="2026-08-02T03:00:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                failure_code="overlap-effect-failed",
            ),
            handoff=GatewayKeyRotationDeploymentHandoff(
                self.rotation_id,
                self.prepared.checkpoint,
                self.prepared.handoff.fence,
            ),
        )

    def _service(self, connection_factory=None) -> GatewayKeyRotationService:
        factory = self.unit_of_work
        if connection_factory is not None:
            factory = lambda: PostgresUnitOfWork(connection_factory)
        return GatewayKeyRotationService(factory, clock=lambda: 3_000)

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
                self.fail("deployment transition did not reach the expected blocker")

    def _row(self, name: str) -> tuple[str, str, str]:
        if name == "request":
            return (
                "cpk_execution_requests",
                "request_id",
                self.prepared.checkpoint.execution_request_id,
            )
        if name == "run":
            return (
                "cpk_activity_runs",
                "run_id",
                self.prepared.checkpoint.run_id,
            )
        if name == "rotation":
            return ("cpk_gateway_key_rotations", "rotation_id", self.rotation_id)
        raise AssertionError(f"unknown lock target: {name}")

    def _lock_row(self, connection, name: str) -> None:
        table, column, value = self._row(name)
        connection.execute(
            f"SELECT {column} FROM {table} WHERE {column}=%s FOR UPDATE",
            (value,),
        )

    def _assert_lockable(self, name: str) -> None:
        table, column, value = self._row(name)
        with psycopg.connect(self.database_url) as probe:
            probe.execute(
                f"SELECT {column} FROM {table} "
                f"WHERE {column}=%s FOR UPDATE NOWAIT",
                (value,),
            )

    def _assert_retained(self, name: str) -> None:
        table, column, value = self._row(name)
        with psycopg.connect(self.database_url) as probe:
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                probe.execute(
                    f"SELECT {column} FROM {table} "
                    f"WHERE {column}=%s FOR UPDATE NOWAIT",
                    (value,),
                )

    def _execute_at_blocker(
        self,
        command: AdvanceGatewayKeyRotationDeployment,
        *,
        blocker_name: str,
        lockable: tuple[str, ...],
        retained: tuple[str, ...],
    ):
        blocker = psycopg.connect(self.database_url)
        self._lock_row(blocker, blocker_name)
        blocker_pid = blocker.info.backend_pid
        worker_pids: queue.Queue[int] = queue.Queue()

        def connection_factory():
            connection = psycopg.connect(self.database_url)
            worker_pids.put(connection.info.backend_pid)
            return connection

        service = self._service(connection_factory)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.advance_deployment, command)
            try:
                worker_pid = worker_pids.get(timeout=5)
                self._wait_until_blocked_by(worker_pid, blocker_pid)
                for name in lockable:
                    self._assert_lockable(name)
                for name in retained:
                    self._assert_retained(name)
            finally:
                blocker.rollback()
                blocker.close()
            return future.result(timeout=5)

    def _assert_first_and_replay_lock_boundary(
        self,
        *,
        blocker_name: str,
        lockable: tuple[str, ...],
        retained: tuple[str, ...],
    ) -> None:
        command = self._command()
        first = self._execute_at_blocker(
            command,
            blocker_name=blocker_name,
            lockable=lockable,
            retained=retained,
        )
        self.assertIs(first.status, GatewayKeyRotationStatus.BLOCKED)

        replay = self._execute_at_blocker(
            command,
            blocker_name=blocker_name,
            lockable=lockable,
            retained=retained,
        )
        self.assertEqual(replay, first)
        transitions = tuple(
            value
            for value in self._service().transitions(self.rotation_id)
            if value.transition_id == command.transition.transition_id
        )
        self.assertEqual(len(transitions), 1)

    def test_request_blocker_leaves_run_and_rotation_lockable(self) -> None:
        self._assert_first_and_replay_lock_boundary(
            blocker_name="request",
            lockable=("run", "rotation"),
            retained=(),
        )

    def test_run_blocker_retains_request_and_leaves_rotation_lockable(self) -> None:
        self._assert_first_and_replay_lock_boundary(
            blocker_name="run",
            lockable=("rotation",),
            retained=("request",),
        )

    def test_rotation_blocker_retains_request_and_run(self) -> None:
        self._assert_first_and_replay_lock_boundary(
            blocker_name="rotation",
            lockable=(),
            retained=("request", "run"),
        )


if __name__ == "__main__":
    unittest.main()
