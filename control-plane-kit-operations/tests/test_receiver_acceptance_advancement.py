"""#1904 real advancement over assumed completion; no successor-health proof."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest

import psycopg

from control_plane_kit_core.operations import ActivityEventKind, ActivityRunStatus
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.advancement import CurrentGraphAdvancementError, CurrentGraphAdvancementConflict
from control_plane_kit_operations.deployment_transitions import InitialDeployment, NoOpDeployment, TeardownDeployment
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture


class ReceiverAcceptanceAdvancementTests(ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def accept_receiver(self, suffix="first"):
        introduced = self.desired_receiver(suffix, graph=self.canonical_receiver_graph)
        claimed, transition, plan = self.retained_success(suffix)
        self.assertIsInstance(transition, InitialDeployment)
        self.assertTrue(plan.plan.activities)
        self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        accepted = self.advance(claimed, suffix)
        origin = self.receiver_origin()
        self.assertEqual(origin.introducing_action_id, introduced.action.action_id)
        self.assertEqual(origin.first_accepted_action_id, accepted.action.action_id)
        self.assertEqual(origin.first_accepted_session_id, claimed.request.identity.session_id)
        self.assertIsNone(origin.retired_action_id)
        return claimed, accepted, origin

    def test_real_advancement_alone_creates_acceptance_from_assumed_completion(self):
        claimed, accepted, origin = self.accept_receiver()
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            plan = uow.stores.activity_history.get_plan(claimed.request.identity.plan_id)
        self.assertEqual(workspace.current_graph_id, plan.desired_graph_id)
        self.assertEqual(workspace.current_realized_projection_id, plan.desired_realized_projection_id)
        self.assertEqual(self.advance(claimed, "first"), replace(accepted, replayed=True))
        self.assertEqual(self.receiver_origin(), origin)

    def test_canonical_noop_a_to_b_to_c_keeps_original_first_acceptance(self):
        _, _, original = self.accept_receiver()
        graph_ids = [original.introducing_graph_id]
        for suffix in ("second", "third"):
            with self.subTest(suffix=suffix):
                desired = self.desired_receiver(suffix, graph=self.canonical_receiver_graph)
                graph_ids.append(desired.graph_version_id)
                claimed, transition, plan = self.retained_success(suffix)
                self.assertIsInstance(transition, NoOpDeployment)
                self.assertEqual(plan.plan.activities, ())
                self.advance(claimed, suffix)
                self.assertEqual(self.receiver_origin(), original)
        self.assertEqual(len(set(graph_ids)), 3)

    def test_desired_omission_does_not_retire_but_real_accepted_teardown_does(self):
        _, _, original = self.accept_receiver()
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        self.assertEqual(self.receiver_origin(), original)
        claimed, transition, plan = self.retained_success("remove")
        self.assertIsInstance(transition, TeardownDeployment)
        self.assertTrue(plan.plan.activities)
        self.assertEqual(self.receiver_origin(), original)
        removed = self.advance(claimed, "remove")
        self.assertEqual(self.receiver_origin(), replace(original,
            retired_action_id=removed.action.action_id,
            retired_session_id=claimed.request.identity.session_id))

    def test_failed_or_incomplete_teardown_never_changes_current_or_retirement(self):
        _, _, original = self.accept_receiver()
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        claimed, _, _ = self.retained_success("remove")
        row = self.connection.execute("SELECT status,settled_at FROM cpk_activity_runs WHERE run_id=%s",
            (claimed.run.run_id,)).fetchone()
        try:
            for status, settled in ((ActivityRunStatus.FAILED.value, None), (ActivityRunStatus.SUCCEEDED.value, None)):
                with self.subTest(status=status):
                    self.connection.execute("UPDATE cpk_activity_runs SET status=%s,settled_at=%s WHERE run_id=%s",
                        (status, settled, claimed.run.run_id))
                    before = self.acceptance_truth()
                    with self.assertRaises(CurrentGraphAdvancementError):
                        self.advance(claimed, "refused-remove")
                    self.assertEqual(self.acceptance_truth(), before)
                    self.assertEqual(self.receiver_origin(), original)
        finally:
            self.connection.execute("UPDATE cpk_activity_runs SET status=%s,settled_at=%s WHERE run_id=%s",
                (*row, claimed.run.run_id))

    def test_missing_native_outcome_refuses_without_acceptance(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("first")
        # An explicitly corrupted consumer input; no upstream execution claim.
        # Preserve the exact row for restoration even if the assertion fails.
        from psycopg.types.json import Jsonb
        row = self.connection.execute("SELECT to_jsonb(t) FROM cpk_effect_attempt_outcomes t "
            "WHERE run_id=%s ORDER BY activity_id LIMIT 1", (claimed.run.run_id,)).fetchone()[0]
        try:
            self.assertEqual(self.connection.execute("DELETE FROM cpk_effect_attempt_outcomes "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (row["run_id"], row["activity_id"], row["attempt"])).rowcount, 1)
            before = self.acceptance_truth()
            with self.assertRaises(CurrentGraphAdvancementError):
                self.advance(claimed, "missing-outcome")
            self.assertEqual(self.acceptance_truth(), before)
            self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        finally:
            self.connection.execute("INSERT INTO cpk_effect_attempt_outcomes SELECT * FROM "
                "jsonb_populate_record(NULL::cpk_effect_attempt_outcomes,%s)", (Jsonb(row),))

    def test_late_action_and_deferred_commit_failure_roll_back_acceptance_and_retirement(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("first")
        self.assert_late_and_deferred_rollback(claimed)
        self.advance(claimed, "first")
        original = self.receiver_origin()
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        removal, _, _ = self.retained_success("remove")
        self.assert_late_and_deferred_rollback(removal)
        self.assertEqual(self.receiver_origin(), original)

    def assert_late_and_deferred_rollback(self, claimed):
        original = self.receiver_origin()
        self.connection.execute("CREATE FUNCTION c3_reject_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'test acceptance refused' USING ERRCODE='23514'; END $$")
        try:
            for deferred in (False, True):
                with self.subTest(deferred=deferred):
                    if deferred:
                        query = "CREATE CONSTRAINT TRIGGER c3_reject_acceptance AFTER INSERT ON cpk_operation_actions " \
                            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                    else:
                        query = "CREATE TRIGGER c3_reject_acceptance BEFORE INSERT ON cpk_operation_actions FOR EACH ROW "
                    self.connection.execute(query + "WHEN (NEW.action_type='advance-current-graph') "
                        "EXECUTE FUNCTION c3_reject_acceptance()")
                    try:
                        before = self.acceptance_truth()
                        with self.assertRaises(psycopg.errors.CheckViolation):
                            self.advance(claimed, "late-" + str(deferred))
                        self.assertEqual(self.acceptance_truth(), before)
                        self.assertEqual(self.receiver_origin(), original)
                    finally:
                        self.connection.execute("DROP TRIGGER c3_reject_acceptance ON cpk_operation_actions")
        finally:
            self.connection.execute("DROP FUNCTION c3_reject_acceptance()")

    def test_competing_advancements_have_one_atomic_acceptance_winner(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("first")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.advance, claimed, suffix) for suffix in ("winner-a", "winner-b")]
        accepted, refused = [], []
        for future in futures:
            try:
                accepted.append(future.result(timeout=1))
            except CurrentGraphAdvancementConflict as error:
                refused.append(error)
        self.assertEqual((len(accepted), len(refused)), (1, 1))
        self.assertEqual(self.receiver_origin().first_accepted_action_id, accepted[0].action.action_id)
        with self.unit_of_work() as uow:
            events = uow.stores.execution.events_for_run(claimed.run.run_id)
        self.assertEqual(tuple(event.event_id for event in events
            if event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED), (accepted[0].event.event_id,))
