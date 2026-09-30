"""#1904 opposing real owners serialize clearance and native first-start."""

import unittest

from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.planning import DesiredGraphCommandError
from control_plane_kit_operations.lifecycle import PauseActivityRun
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture


def lifecycle_acquired(query, parameters):
    return "pg_advisory_xact_lock" in query and parameters == ("receiver-lifecycle:workspace-a",)


class ReceiverFreshExecutionScheduleTests(ReceiverFreshExecutionFixture, LifecycleLockFixture, unittest.TestCase):
    def start_with(self, factory, command):
        return EffectAttemptStartService(factory, id_factory=GeneratedIds("schedule-start")).execute(command)

    def pause_then_cancel(self, claimed, start):
        self.lifecycle("schedule-pause", "schedule-pause-action").execute(PauseActivityRun(
            claimed.run.run_id, start.authority, start.fence, IdempotencyKey("schedule-pause")))
        return self.cancel(claimed)

    def test_clearance_and_receiver_reuse_first_refuses_waiting_old_native_activation(self):
        self.admit()
        claimed = self.ready_run()
        old_start = self.native_start_command(claimed)
        cancelled = self.pause_then_cancel(claimed, old_start)
        competing = self.receiver_command()
        before = self.execution_truth()
        first, second = self.opposing_commands(
            lambda factory: self.desired_service(factory).execute(competing),
            lambda factory: self.start_with(factory, old_start), lifecycle_acquired)
        introduced = first.result(timeout=1)
        with self.assertRaises(EffectAttemptStartError):
            second.result(timeout=1)
        origin = self.receiver_origin()
        self.assertEqual(origin.introducing_action_id, introduced.action.action_id)
        self.assertIsNone(origin.first_accepted_action_id)
        after = self.execution_truth()
        # Only the real desired-selection action may be added. The refused
        # activation cannot add an event, attempt, intent, request or run.
        for table in before.keys() - {"cpk_operation_actions"}:
            self.assertEqual(after[table], before[table])
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_event(cancelled.event.event_id), cancelled.event)

    def test_native_start_first_blocks_waiting_reuse_and_cancellation_or_expiry_cannot_clear_it(self):
        self.admit()
        claimed = self.ready_run()
        start = self.native_start_command(claimed)
        competing = self.receiver_command()
        before = self.graph_truth()
        first, second = self.opposing_commands(
            lambda factory: self.start_with(factory, start),
            lambda factory: self.desired_service(factory).execute(competing), lifecycle_acquired)
        started = first.result(timeout=1)
        self.assertIsInstance(started, NewlyStarted)
        with self.assertRaises(DesiredGraphCommandError):
            second.result(timeout=1)
        self.assertEqual(self.graph_truth(), before)
        self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
        self.pause_then_cancel(claimed, start)
        before = self.graph_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(competing)
        self.assertEqual(self.graph_truth(), before)
        # Explicit retained expiry premise; it grants no replay/redispatch.
        original = self.connection.execute("SELECT lease_expires_at FROM cpk_execution_requests "
            "WHERE request_id='execution-a'").fetchone()[0]
        try:
            self.connection.execute("UPDATE cpk_execution_requests SET lease_expires_at=claimed_at "
                "WHERE request_id='execution-a'")
            with self.assertRaises(DesiredGraphCommandError):
                self.desired_service().execute(competing)
            self.assertEqual(self.graph_truth(), before)
            with self.unit_of_work() as uow:
                self.assertEqual(uow.stores.effect_attempts.get(start.transition.identity), started.attempt)
                self.assertEqual(uow.stores.effect_attempt_intents.get(start.transition.identity).intent, start.intent)
        finally:
            self.connection.execute("UPDATE cpk_execution_requests SET lease_expires_at=%s "
                "WHERE request_id='execution-a'", (original,))
