from __future__ import annotations

import concurrent.futures
import unittest
import dataclasses
from datetime import datetime
import queue
import time

import psycopg

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    RecoveryDecisionKind,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_lease_recovery_interpreter import (
    ExecutionLeaseRecoveryCommandService,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
    RunLifecycleError,
    StartActivityRun,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey

from tests.execution_lease_recovery_fixture import (
    PostgresExecutionLeaseRecoveryFixture,
    Sequence,
    safe_error,
)


class PostgresExecutionLeaseRecoveryConcurrencyTests(
    PostgresExecutionLeaseRecoveryFixture,
    unittest.TestCase,
):
    def _wait_until_blocked_by(
        self,
        worker_pid: int,
        blocker_pid: int,
        *,
        label: str,
    ) -> None:
        deadline = time.monotonic() + 5
        while True:
            blocked_by = self.connection.execute(
                "SELECT pg_blocking_pids(%s)",
                (worker_pid,),
            ).fetchone()[0]
            if blocker_pid in blocked_by:
                return
            if time.monotonic() >= deadline:
                self.fail(f"{label} did not reach its causal lock barrier")

    def _connection_factory(self, pids: queue.Queue[int]):
        def factory():
            connection = psycopg.connect(self.database_url)
            pids.put(connection.info.backend_pid)
            return connection

        return factory

    def _contender(self, kind: str, label: str):
        pids: queue.Queue[int] = queue.Queue()
        sequence = Sequence(
            f"{label}-decision",
            f"{label}-consequence",
            f"{label}-action",
        )
        unit_of_work_factory = lambda: PostgresUnitOfWork(
            self._connection_factory(pids)
        )
        if kind == "start":
            service = RunLifecycleCommandService(
                unit_of_work_factory,
                clock=lambda: "2026-08-15T04:30:00Z",
                id_factory=sequence,
            )
            command = StartActivityRun(
                "run-a",
                ExecutionWorkerAuthority(
                    "worker-a",
                    (PolicyScope.EXECUTION_OPERATE,),
                ),
                ExecutionLeaseFence("worker-a", 7),
                IdempotencyKey(f"{label}-start"),
            )
        else:
            decision = {
                "renew-active": RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "renew-expired": RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "takeover-b": RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                "takeover-c": RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                "abandon": RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
            }[kind]
            service = ExecutionLeaseRecoveryCommandService(
                unit_of_work_factory,
                id_factory=sequence,
            )
            command = self.command(decision, key=f"{label}-{kind}")
            if kind == "takeover-c":
                command = dataclasses.replace(command, next_worker_id="worker-c")
        return service, command, pids, sequence

    def _complete_snapshot(self) -> tuple[object, ...]:
        return (
            self.snapshot(),
            tuple(
                self.connection.execute(
                    "SELECT current_graph_id, current_realized_projection_id, "
                    "desired_graph_id, desired_realized_projection_id, "
                    "desired_graph_revision FROM cpk_workspaces "
                    "WHERE workspace_id = 'workspace-a'"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT rotation_id, md5(row_to_json(rotation)::text) "
                    "FROM cpk_gateway_key_rotations AS rotation "
                    "WHERE workspace_id = 'workspace-a' ORDER BY rotation_id"
                ).fetchall()
            ),
        )

    def _assert_winner_truth(
        self,
        *,
        kind: str,
        result: object,
        before: tuple[object, ...],
        winner_sequence: Sequence,
        loser_sequence: Sequence,
    ) -> None:
        after = self._complete_snapshot()
        before_operations = before[0]
        after_operations = after[0]
        self.assertEqual(after[1:], before[1:])
        self.assertEqual(len(after_operations[3]), len(before_operations[3]))
        self.assertEqual(len(after_operations[2]), len(before_operations[2]) + 1)
        self.assertEqual(loser_sequence.calls, [])

        if kind == "start":
            self.assertEqual(len(winner_sequence.calls), 2)
            self.assertEqual(
                len(after_operations[1]),
                len(before_operations[1]) + 1,
            )
            self.assertEqual(after_operations[0][0], "claimed")
            self.assertEqual(after_operations[0][1:3], ("worker-a", 7))
            self.assertEqual(after_operations[3][0][3], "running")
            self.assertEqual(after_operations[1][-1][2], "run_started")
        else:
            self.assertEqual(len(winner_sequence.calls), 3)
            self.assertEqual(
                len(after_operations[1]),
                len(before_operations[1]) + 2,
            )
            self.assertEqual(
                tuple(row[2] for row in after_operations[1][-2:]),
                (
                    "recovery_decision_recorded",
                    {
                        "renew-active": "request_claim_renewed",
                        "renew-expired": "request_claim_renewed",
                        "takeover-b": "request_claim_taken_over",
                        "takeover-c": "request_claim_taken_over",
                        "abandon": "request_claim_abandoned",
                    }[kind],
                ),
            )
            if kind == "abandon":
                self.assertEqual(after_operations[0][0], "abandoned")
                self.assertEqual(after_operations[0][1:3], (None, None))
            else:
                worker = {
                    "renew-active": "worker-a",
                    "renew-expired": "worker-a",
                    "takeover-b": "worker-b",
                    "takeover-c": "worker-c",
                }[kind]
                self.assertEqual(after_operations[0][0], "claimed")
                self.assertEqual(after_operations[0][1:3], (worker, 8))
            expected_run_status = (
                "claimed" if kind == "renew-active" else "failed"
            )
            self.assertEqual(after_operations[3][0][3], expected_run_status)

        self.assertEqual(after_operations[2][-1][0], result.action.action_id)
        self.assertFalse(
            any(
                row[2] == ActivityEventKind.CURRENT_GRAPH_ADVANCED.value
                for row in after_operations[1]
            )
        )

    def _force_winner(
        self,
        *,
        seed_decision: RecoveryDecisionKind,
        winner_kind: str,
        loser_kind: str,
        case: str,
        approval_subject: str = "activity-plan",
    ) -> None:
        self.reset_truth(seed_decision, approval_subject=approval_subject)
        before = self._complete_snapshot()
        blocker = psycopg.connect(self.database_url)
        blocker.execute(
            "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id = 'request-a' FOR UPDATE"
        )
        blocker_pid = blocker.info.backend_pid
        winner_service, winner_command, winner_pids, winner_sequence = (
            self._contender(winner_kind, f"{case}-winner")
        )
        loser_service, loser_command, loser_pids, loser_sequence = self._contender(
            loser_kind,
            f"{case}-loser",
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            winner_future = executor.submit(winner_service.execute, winner_command)
            loser_future = None
            try:
                winner_pid = winner_pids.get(timeout=5)
                self._wait_until_blocked_by(
                    winner_pid,
                    blocker_pid,
                    label=f"{case} intended winner",
                )
                loser_future = executor.submit(loser_service.execute, loser_command)
                loser_pid = loser_pids.get(timeout=5)
                self._wait_until_blocked_by(
                    loser_pid,
                    winner_pid,
                    label=f"{case} intended loser",
                )
                blocker.commit()
                winner_result = winner_future.result(timeout=10)
                with self.assertRaises(RunLifecycleError) as captured:
                    loser_future.result(timeout=10)
            finally:
                blocker.rollback()
                blocker.close()
                if loser_future is not None and not loser_future.done():
                    loser_future.cancel()

        safe_error(self, captured.exception, case)
        self._assert_winner_truth(
            kind=winner_kind,
            result=winner_result,
            before=before,
            winner_sequence=winner_sequence,
            loser_sequence=loser_sequence,
        )

    def test_every_asymmetric_race_forces_both_winner_orders(self) -> None:
        cases = (
            (
                "active-renew-before-start",
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "renew-active",
                "start",
            ),
            (
                "start-before-active-renew",
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "start",
                "renew-active",
            ),
            (
                "active-renew-a-before-active-renew-b",
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "renew-active",
                "renew-active",
            ),
            (
                "expired-renew-before-takeover",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "renew-expired",
                "takeover-b",
            ),
            (
                "takeover-before-expired-renew",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "takeover-b",
                "renew-expired",
            ),
            (
                "takeover-b-before-takeover-c",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "takeover-b",
                "takeover-c",
            ),
            (
                "takeover-c-before-takeover-b",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "takeover-c",
                "takeover-b",
            ),
            (
                "abandon-before-takeover",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "abandon",
                "takeover-b",
            ),
            (
                "takeover-before-abandon",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "takeover-b",
                "abandon",
            ),
        )
        gateway_approval_cases = frozenset(
            {
                "abandon-before-takeover",
                "takeover-before-abandon",
            }
        )
        for case, seed_decision, winner_kind, loser_kind in cases:
            with self.subTest(case=case):
                self._force_winner(
                    seed_decision=seed_decision,
                    winner_kind=winner_kind,
                    loser_kind=loser_kind,
                    case=case,
                    approval_subject=(
                        "gateway-rotation"
                        if case in gateway_approval_cases
                        else "activity-plan"
                    ),
                )

    def test_database_clock_is_after_request_and_run_lock_release(self) -> None:
        barriers = (
            (
                "request",
                "SELECT request_id FROM cpk_execution_requests "
                "WHERE request_id = 'request-a' FOR UPDATE",
            ),
            (
                "run",
                "SELECT run_id FROM cpk_activity_runs "
                "WHERE run_id = 'run-a' FOR UPDATE",
            ),
        )
        for label, lock_sql in barriers:
            with self.subTest(barrier=label):
                self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
                blocker = psycopg.connect(self.database_url)
                blocker.execute(lock_sql)
                blocker_pid = blocker.info.backend_pid
                service, command, pids, sequence = self._contender(
                    "renew-active",
                    f"clock-after-{label}",
                )
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(service.execute, command)
                    try:
                        worker_pid = pids.get(timeout=5)
                        self._wait_until_blocked_by(
                            worker_pid,
                            blocker_pid,
                            label=f"clock {label} blocker",
                        )
                        with psycopg.connect(self.database_url) as probe:
                            if label == "request":
                                probe.execute(
                                    "SELECT run_id FROM cpk_activity_runs "
                                    "WHERE run_id = 'run-a' FOR UPDATE NOWAIT"
                                )
                            else:
                                with self.assertRaises(
                                    psycopg.errors.LockNotAvailable
                                ):
                                    probe.execute(
                                        "SELECT request_id "
                                        "FROM cpk_execution_requests "
                                        "WHERE request_id = 'request-a' "
                                        "FOR UPDATE NOWAIT"
                                    )
                        released_at = blocker.execute(
                            "SELECT clock_timestamp()"
                        ).fetchone()[0]
                        blocker.commit()
                        result = future.result(timeout=10)
                    finally:
                        blocker.rollback()
                        blocker.close()

                claimed_at = datetime.fromisoformat(
                    result.request.claim.claimed_at.replace("Z", "+00:00")
                )
                self.assertGreaterEqual(claimed_at, released_at)
                self.assertEqual(
                    result.request.claim.claimed_at,
                    result.decision_event.occurred_at,
                )
                self.assertEqual(
                    result.decision_event.occurred_at,
                    result.consequence_event.occurred_at,
                )
                self.assertEqual(
                    result.consequence_event.occurred_at,
                    result.action.created_at,
                )
                persisted_times = self.connection.execute(
                    "SELECT request.claimed_at, decision.occurred_at, "
                    "consequence.occurred_at, action.created_at "
                    "FROM cpk_execution_requests AS request "
                    "JOIN cpk_activity_events AS decision "
                    "ON decision.event_id = %s "
                    "JOIN cpk_activity_events AS consequence "
                    "ON consequence.event_id = %s "
                    "JOIN cpk_operation_actions AS action "
                    "ON action.action_id = %s "
                    "WHERE request.request_id = 'request-a'",
                    (
                        result.decision_event.event_id,
                        result.consequence_event.event_id,
                        result.action.action_id,
                    ),
                ).fetchone()
                self.assertEqual(len(set(persisted_times)), 1)
                for persisted_at in persisted_times:
                    self.assertGreaterEqual(persisted_at, released_at)
                self.assertEqual(len(sequence.calls), 3)
                self.assertIs(result.request.status, ExecutionRequestStatus.CLAIMED)
                self.assertIs(result.retained_run.status, ActivityRunStatus.CLAIMED)
