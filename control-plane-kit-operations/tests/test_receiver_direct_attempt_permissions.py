"""#1904 a retained event/intent or held guard does not authorize fresh starts."""

import unittest

from control_plane_kit_operations.receiver_execution_scopes import ReceiverScopeUnavailable
from control_plane_kit_core.planning import NodeTarget, WaitForHealthy
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds
from tests.postgres_effect_attempt_start_fixture import PostgresEffectAttemptStartFixture
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture


class ReceiverDirectAttemptPermissionTests(PostgresEffectAttemptStartFixture, unittest.TestCase):
    def test_bare_affecting_attempt_insert_refuses_with_exact_intent_and_lifecycle_guard(self):
        started = self.start_service("real-start").execute(self.start_command()).attempt
        before = self.attempt_snapshot()
        # Rollback-scoped retained gap is solely the bare-writer premise.
        # The original lawful service supplied the real typed intent/event.
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_effect_attempts WHERE run_id='run-a'").rowcount, 1)
            self.assertEqual(uow.stores.effect_attempt_intents.get(started.state.identity).original_start_event,
                started.original_start_event)
            with self.assertRaises(ReceiverScopeUnavailable):
                uow.stores.effect_attempts.insert_absent(started)
            self.assertEqual(uow.stores.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempts WHERE run_id='run-a'").fetchone()[0], 0)
        self.assertEqual(self.attempt_snapshot(), before)

    def test_bare_affecting_intent_insert_refuses_with_real_event_and_lifecycle_guard(self):
        started = self.start_service("real-start").execute(self.start_command()).attempt
        before = self.attempt_snapshot()
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            record = uow.stores.effect_attempt_intents.get(started.state.identity)
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_effect_attempts WHERE run_id='run-a'").rowcount, 1)
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_effect_attempt_intents WHERE run_id='run-a'").rowcount, 1)
            with self.assertRaises(ReceiverScopeUnavailable):
                uow.stores.effect_attempt_intents.insert(record)
            self.assertEqual(uow.stores.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempt_intents WHERE run_id='run-a'").fetchone()[0], 0)
        self.assertEqual(self.attempt_snapshot(), before)


class NonaffectingDirectAttemptPermissionTests(ReceiverFreshExecutionFixture, unittest.TestCase):
    def test_nonaffecting_intent_and_attempt_writers_remain_supported(self):
        self.admit_operations("observation", WaitForHealthy(NodeTarget("app")))
        self.assertEqual(self.scope_header("execution-observation")[0], 0)
        claimed = self.ready_run("observation")
        command = self.native_start_command(claimed, "observation")
        started = EffectAttemptStartService(self.unit_of_work,
            id_factory=GeneratedIds("observation-start")).execute(command).attempt
        before = self.execution_truth()
        with self.unit_of_work() as uow:
            record = uow.stores.effect_attempt_intents.get(started.state.identity)
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_effect_attempts WHERE run_id=%s", (claimed.run.run_id,)).rowcount, 1)
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_effect_attempt_intents WHERE run_id=%s", (claimed.run.run_id,)).rowcount, 1)
            self.assertEqual(uow.stores.effect_attempt_intents.insert(record), record)
            self.assertEqual(uow.stores.effect_attempts.insert_absent(started), started)
        self.assertEqual(self.execution_truth(), before)
