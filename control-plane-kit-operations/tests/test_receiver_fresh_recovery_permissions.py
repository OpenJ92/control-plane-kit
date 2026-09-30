"""#1904 original recovery gains fresh permission only after lifecycle serialization."""

import unittest

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.activity_run_retry_interpreter import ActivityRunRetryCommandService
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError, NewlyStarted
from control_plane_kit_operations.execution_lease_recovery_interpreter import ExecutionLeaseRecoveryCommandService
from control_plane_kit_operations.failed_run_compensation import FailedRunCompensationCommandService
from control_plane_kit_operations.failed_run_compensation_attempt import (
    FailedRunCompensationAttemptError, FailedRunCompensationAttemptStartService,
)
from control_plane_kit_operations.lifecycle import RunLifecycleError
from tests.activity_run_retry_interpreter_fixture import PostgresActivityRunRetryFixture
from tests.execution_lease_recovery_fixture import PostgresExecutionLeaseRecoveryFixture
from tests.failed_run_compensation_attempt_fixture import FailedRunCompensationAttemptFixture
from tests.failed_run_compensation_fixture import FailedRunCompensationFixture
from tests.lifecycle_lock_fixture import (
    LIFECYCLE_LOCK, REQUEST_LOCK, RUN_LOCK, SESSION_LOCK, WORKSPACE_LOCK,
)
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds
from tests.postgres_effect_attempt_start_fixture import PostgresEffectAttemptStartFixture
from tests.receiver_fresh_permission_witness import FreshPermissionWitness


class FreshRecoveryLockWitness(FreshPermissionWitness):
    def assert_fresh_waits_before_execution_rows(self, execute):
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute) as future:
            self.assert_row_lockable(REQUEST_LOCK, ("request-a",))
            self.assert_row_lockable(RUN_LOCK, ("run-a",))
            self.assert_row_lockable(SESSION_LOCK, ("session-a",))
            self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))
        return future.result(timeout=1)


class ReceiverFreshLeasePermissionTests(PostgresExecutionLeaseRecoveryFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_renewals_and_takeover_recheck_changed_pins_after_lifecycle_wait(self):
        for decision in (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                         RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                         RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM):
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                command = self.command(decision)
                result = self.assert_rechecks_pins_while_waiting(lambda factory:
                    ExecutionLeaseRecoveryCommandService(factory,
                        id_factory=GeneratedIds("lease-recheck")).execute(command), RunLifecycleError)
                self.assertFalse(result.replayed)

    def test_active_expired_renewal_and_takeover_wait_for_lifecycle_before_rows(self):
        for decision in (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                         RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                         RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM):
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                command = self.command(decision)
                result = self.assert_fresh_waits_before_execution_rows(lambda factory:
                    ExecutionLeaseRecoveryCommandService(factory, id_factory=GeneratedIds("lease-lock")).execute(command))
                self.assertFalse(result.replayed)


class ReceiverFreshRetryPermissionTests(PostgresActivityRunRetryFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_original_failed_retry_waits_for_lifecycle_before_prior_and_latest_rows(self):
        self.reset_retry_truth()
        command = self.retry_command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            ActivityRunRetryCommandService(factory, id_factory=GeneratedIds("retry-lock")).execute(command))
        self.assertFalse(result.replayed)
        self.assertEqual(result.run.retry.prior_run_id, "run-a")


class ReceiverFreshCompensationPermissionTests(FailedRunCompensationFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_begin_compensation_rechecks_changed_pins_after_lifecycle_wait(self):
        self.seed_truth()
        command = self.command()
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            FailedRunCompensationCommandService(factory, clock=lambda: "2026-08-25T12:00:00Z",
                id_factory=GeneratedIds("compensation-recheck")).execute(command), RunLifecycleError)
        self.assertFalse(result.replayed)

    def test_begin_compensation_waits_for_lifecycle_before_execution_and_program_truth(self):
        self.seed_truth()
        command = self.command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            FailedRunCompensationCommandService(factory, clock=lambda: "2026-08-25T12:00:00Z",
                id_factory=GeneratedIds("compensation-lock")).execute(command))
        self.assertFalse(result.replayed)


class ReceiverFreshInversePermissionTests(FailedRunCompensationAttemptFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_inverse_rechecks_required_program_set_after_waiting_on_workspace(self):
        self.seed_admitted_program()
        command = self.start_command()
        from psycopg.types.json import Jsonb
        row = self.connection.execute("SELECT to_jsonb(t) FROM cpk_failed_run_compensation_steps t "
            "WHERE program_id='program-a' ORDER BY position DESC LIMIT 1").fetchone()[0]
        deleted = False
        try:
            with self.blocked_command(WORKSPACE_LOCK, ("workspace-a",), lambda factory:
                    FailedRunCompensationAttemptStartService(factory,
                        id_factory=GeneratedIds("inverse-set-recheck")).execute(command)) as future:
                # The real owner has selected and locked its earlier attempt
                # set. Corrupt only the retained program collection while it
                # waits; it must reread/refuse rather than use that old set.
                self.assertEqual(self.connection.execute("DELETE FROM cpk_failed_run_compensation_steps "
                    "WHERE program_id='program-a' AND position=%s", (row["position"],)).rowcount, 1)
                deleted = True
                before = self.permission_truth()
            with self.assertRaises(FailedRunCompensationAttemptError):
                future.result(timeout=1)
            self.assertEqual(self.permission_truth(), before)
        finally:
            if deleted:
                self.connection.execute("INSERT INTO cpk_failed_run_compensation_steps SELECT * FROM "
                    "jsonb_populate_record(NULL::cpk_failed_run_compensation_steps,%s)", (Jsonb(row),))
        self.assertFalse(self.attempt_service("inverse-set-valid").execute(command).replayed)

    def test_fresh_inverse_rechecks_changed_pins_after_lifecycle_wait(self):
        self.seed_admitted_program()
        command = self.start_command()
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            FailedRunCompensationAttemptStartService(factory,
                id_factory=GeneratedIds("inverse-recheck")).execute(command), FailedRunCompensationAttemptError)
        self.assertFalse(result.replayed)

    def test_fresh_inverse_waits_for_lifecycle_before_request_run_and_attempts(self):
        self.seed_admitted_program()
        command = self.start_command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            FailedRunCompensationAttemptStartService(factory, id_factory=GeneratedIds("inverse-lock")).execute(command))
        self.assertFalse(result.replayed)


class ReceiverFreshEffectPermissionTests(PostgresEffectAttemptStartFixture, FreshRecoveryLockWitness, unittest.TestCase):
    def test_fresh_effect_rechecks_changed_pins_after_lifecycle_wait(self):
        command = self.start_command()
        result = self.assert_rechecks_pins_while_waiting(lambda factory:
            EffectAttemptStartService(factory, id_factory=GeneratedIds("effect-recheck")).execute(command),
            EffectAttemptStartError)
        self.assertIsInstance(result, NewlyStarted)

    def test_fresh_effect_waits_for_lifecycle_before_request_run_and_attempts(self):
        command = self.start_command()
        result = self.assert_fresh_waits_before_execution_rows(lambda factory:
            EffectAttemptStartService(factory, id_factory=GeneratedIds("effect-lock")).execute(command))
        self.assertIsInstance(result, NewlyStarted)
