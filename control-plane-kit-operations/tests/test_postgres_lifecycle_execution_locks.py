"""#1896 request/run prefixes preserve the existing execution policies."""
import unittest

from control_plane_kit_core.operations.lifecycle import RecoveryDecisionKind
from control_plane_kit_operations.execution_lease_recovery_interpreter import ExecutionLeaseRecoveryCommandService
from control_plane_kit_operations.activity_run_retry_interpreter import ActivityRunRetryCommandService
from control_plane_kit_operations.lifecycle import (
    RunLifecycleCommandService, StartActivityRun, ExecutionWorkerAuthority,
    RunLifecycleError,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.activity_run_retry_interpreter_fixture import PostgresActivityRunRetryFixture
from tests.execution_lease_recovery_fixture import Sequence
from tests.failed_run_compensation_attempt_fixture import FailedRunCompensationAttemptFixture
from tests.lifecycle_lock_fixture import (
    LifecycleLockFixture, LIFECYCLE_LOCK, REQUEST_LOCK, RUN_LOCK,
    ATTEMPT_LOCK, SESSION_LOCK, WORKSPACE_LOCK,
)


class PostgresLifecycleExecutionLockTests(
    LifecycleLockFixture, PostgresActivityRunRetryFixture, unittest.TestCase,
):
    def test_retry_and_recovery_take_request_and_run_before_session_without_new_lifecycle_guard(self):
        for kind in ("retry", "recovery"):
            for blocked in ("request", "run"):
                with self.subTest(kind=kind, blocked=blocked):
                    if kind == "retry":
                        self.reset_retry_truth()
                        command = self.retry_command()
                        execute = lambda uow: ActivityRunRetryCommandService(uow,
                            id_factory=Sequence("run-b", "decision-b", "opened-b", "action-b")).execute(command)
                    else:
                        decision = RecoveryDecisionKind.RENEW_EXPIRED_CLAIM
                        self.reset_truth(decision)
                        command = self.command(decision)
                        execute = lambda uow: ExecutionLeaseRecoveryCommandService(uow,
                            id_factory=Sequence("decision-b", "event-b", "action-b")).execute(command)
                    query, key = (REQUEST_LOCK, "request-a") if blocked == "request" else (RUN_LOCK, "run-a")
                    with self.blocked_command(query, (key,), execute) as future:
                        self.assert_row_lockable(SESSION_LOCK, ("session-a",))
                        self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))
                        self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
                        if blocked == "request":
                            self.assert_row_lockable(RUN_LOCK, ("run-a",))
                        else:
                            self.assert_row_retained(REQUEST_LOCK, ("request-a",))
                    self.assertFalse(future.result(timeout=1).replayed)

    def test_lifecycle_transition_takes_request_before_session_without_new_guard(self):
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        command = StartActivityRun("run-a",
            ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
            ExecutionLeaseFence("worker-a", 7), IdempotencyKey("start-lock-order"))
        def execute(uow):
            return RunLifecycleCommandService(uow, clock=lambda: "2026-08-25T12:00:00Z",
                id_factory=Sequence("started-event", "started-action")).execute(command)
        with self.blocked_command(REQUEST_LOCK, ("request-a",), execute) as future:
            self.assert_row_lockable(SESSION_LOCK, ("session-a",))
            self.assert_row_lockable(RUN_LOCK, ("run-a",))
            self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
        self.assertEqual(future.result(timeout=1).run.status.value, "running")


class PostgresLifecycleCompensationLockTests(
    LifecycleLockFixture, FailedRunCompensationAttemptFixture, unittest.TestCase,
):
    def test_compensation_and_action_key_first_lifecycle_writer_terminate_in_both_orders(self):
        for compensation_first in (False, True):
            with self.subTest(compensation_first=compensation_first):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_truth()
                module = self.require_contract()
                command = self.command()
                def compensate(uow):
                    return module.FailedRunCompensationCommandService(uow,
                        clock=lambda: "2026-08-25T12:00:00Z",
                        id_factory=Sequence("program-a", "compensating-event", "compensating-action")).execute(command)
                def start(uow):
                    return RunLifecycleCommandService(uow,
                        clock=lambda: "2026-08-25T12:00:00Z",
                        id_factory=Sequence("must-not-start", "must-not-record")).execute(
                            StartActivityRun("run-a",
                                ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
                                ExecutionLeaseFence("worker-a", 1), IdempotencyKey("compensate-a")))
                ordered = (compensate, start) if compensation_first else (start, compensate)
                futures = self.opposing_commands(*ordered, pause_after=lambda sql, parameters:
                    "pg_advisory_xact_lock" in sql
                    and parameters == ("operation-action:session-a:compensate-a",))
                comp, life = futures if compensation_first else futures[::-1]
                self.assertFalse(comp.result(timeout=1).replayed)
                with self.assertRaises(RunLifecycleError):
                    life.result(timeout=1)
                self.assertEqual(self.connection.execute(
                    "SELECT count(*) FROM cpk_failed_run_compensations").fetchone()[0], 1)

    def test_compensation_action_key_precedes_request_even_when_key_is_reused(self):
        self.seed_truth()
        command = self.command()
        module = self.require_contract()
        def execute(uow):
            return module.FailedRunCompensationCommandService(uow,
                clock=lambda: "2026-08-25T12:00:00Z",
                id_factory=Sequence("program-a", "compensating-event", "compensating-action")).execute(command)
        with self.blocked_command(LIFECYCLE_LOCK,
                ("operation-action:session-a:compensate-a",), execute) as future:
            self.assert_row_lockable(REQUEST_LOCK, ("request-a",))
            self.assert_row_lockable(RUN_LOCK, ("run-a",))
            self.assert_row_lockable(SESSION_LOCK, ("session-a",))
            self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
        self.assertFalse(future.result(timeout=1).replayed)

    def test_compensation_attempt_prefix_is_request_run_attempt_workspace_program(self):
        self.seed_admitted_program()
        command = self.start_command()
        with self.unit_of_work() as uow:
            _, program = uow.stores.failed_run_compensations.get("program-a")
        source = program.steps[0].source_effect.attempt_identity
        attempt_key = (source.run_id.value, source.activity_id, source.attempt)
        program_lock = "SELECT program_id FROM cpk_failed_run_compensations WHERE program_id=%s FOR UPDATE"
        for blocker, parameters in ((REQUEST_LOCK, ("request-a",)),
                (RUN_LOCK, ("run-a",)), (ATTEMPT_LOCK, attempt_key)):
            with self.subTest(blocker=blocker):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_admitted_program()
                def execute(factory):
                    return self.attempt_service("inverse-start-a", unit_of_work=factory).execute(command)
                before = self.source_truth_snapshot()
                with self.blocked_command(blocker, parameters, execute) as future:
                    self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))
                    self.assert_row_lockable(program_lock, ("program-a",))
                    if blocker != ATTEMPT_LOCK:
                        self.assert_row_lockable(ATTEMPT_LOCK, attempt_key)
                    if blocker == REQUEST_LOCK:
                        self.assert_row_lockable(RUN_LOCK, ("run-a",))
                    self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
                self.assertEqual(future.result(timeout=1).binding.source_attempt, source)
                self.assertEqual(self.source_truth_snapshot(), before)
