"""#1904 real commit and unlocked native dispatch, without provider mutation."""

import unittest

import psycopg

from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.planning import DesiredGraphCommandError
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture


class ReceiverDispatchTransactionTests(ReceiverFreshExecutionFixture, unittest.TestCase):
    def test_adapter_observes_complete_committed_start_and_lifecycle_lock_is_released(self):
        self.admit()
        claimed = self.ready_run()
        observed = []

        def dispatch(_context, request):
            # Independent connection sees only committed start truth. Taking L
            # here proves the owning transaction does not span adapter I/O.
            with self.unit_of_work() as uow:
                available = uow.stores.connection.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                    ("receiver-lifecycle:workspace-a",)).fetchone()[0]
                counts = tuple(uow.stores.connection.execute(query).fetchone()[0] for query in (
                    "SELECT count(*) FROM cpk_activity_events WHERE run_id='run-a' AND event_type='step_started'",
                    "SELECT count(*) FROM cpk_effect_attempt_intents WHERE run_id='run-a'",
                    "SELECT count(*) FROM cpk_effect_attempts WHERE run_id='run-a' AND status='started'"))
            before = self.graph_truth()
            refused = False
            try:
                self.desired_receiver("during-io")
            except DesiredGraphCommandError:
                refused = True
            observed.append((available, counts, refused, before == self.graph_truth()))
            return RuntimeEffectResult.succeeded(request.effect_id)

        adapter = RecordingRuntimeAdapter(dispatch)
        self.coordinator(self.unit_of_work, adapter, "committed").execute(self.execution_command(claimed, "committed"))
        self.assertEqual(observed, [(True, (1, 1, 1), True, True)])
        self.assertEqual(len(adapter.runtime_calls), 1)

    def test_deferred_start_commit_failure_calls_no_adapter_and_leaves_no_partial_start(self):
        self.admit()
        claimed = self.ready_run()
        before = self.execution_truth()
        adapter = RecordingRuntimeAdapter()
        # Temporary constraint fault, removed even on assertion failure.
        # This tests the actual database commit, not a replacement UoW.
        self.connection.execute("CREATE FUNCTION c3_reject_start_commit() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'test start commit refused' USING ERRCODE='23514'; END $$")
        try:
            self.connection.execute("CREATE CONSTRAINT TRIGGER c3_reject_start_commit "
                "AFTER INSERT ON cpk_effect_attempts DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW EXECUTE FUNCTION c3_reject_start_commit()")
            try:
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.coordinator(self.unit_of_work, adapter, "commit-failure").execute(
                        self.execution_command(claimed, "commit-failure"))
                self.assertEqual(adapter.runtime_calls, [])
                self.assertEqual(adapter.legacy_calls, [])
                self.assertEqual(self.execution_truth(), before)
            finally:
                self.connection.execute("DROP TRIGGER c3_reject_start_commit ON cpk_effect_attempts")
        finally:
            self.connection.execute("DROP FUNCTION c3_reject_start_commit()")
