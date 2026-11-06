"""#1904 historical no-dispatch proof and original retry are distinct permissions."""

from dataclasses import replace
import unittest

from control_plane_kit_core.operations import ActivityRunStatus
from control_plane_kit_core.planning import NodeTarget, StartNode
from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
from control_plane_kit_operations.lifecycle import RunLifecycleConflict
from control_plane_kit_operations.planning import DesiredGraphCommandError
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture


class ReceiverFreshReusePermissionTests(ReceiverFreshExecutionFixture, unittest.TestCase):
    def test_missing_exact_cancellation_receipt_does_not_clear_scope(self):
        self.admit()
        cancelled = self.cancel(self.claim())
        # Deliberate retained-evidence corruption only; restore on every exit.
        original = self.connection.execute("SELECT payload FROM cpk_operation_actions WHERE action_id=%s",
            (cancelled.action.action_id,)).fetchone()[0]
        from psycopg.types.json import Jsonb
        try:
            self.connection.execute("UPDATE cpk_operation_actions SET payload='{}'::jsonb WHERE action_id=%s",
                (cancelled.action.action_id,))
            before = self.graph_truth()
            with self.assertRaises(DesiredGraphCommandError):
                self.desired_receiver("missing-cancel")
            self.assertEqual(self.graph_truth(), before)
        finally:
            self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                (Jsonb(original), cancelled.action.action_id))

    def test_original_failed_retry_succeeds_while_competing_receiver_reuse_refuses(self):
        # The supported legacy effect occupies the same runtime/node scope as
        # the proposed successor receiver. This proves native retry permission,
        # not successor workload-health execution.
        self.admit()
        adapter = RecordingRuntimeAdapter(lambda _context, request: RuntimeEffectResult.failed(
            request.effect_id, RuntimeEffectFailure("original-failed", "bounded failure")))
        claimed, failed = self.execute_effects("a", adapter)
        self.assertIs(failed.run.status, ActivityRunStatus.FAILED)
        retried = self.retry(claimed, "original")
        self.assertEqual(retried.run.retry.prior_run_id, claimed.run.run_id)
        self.assertIs(retried.run.status, ActivityRunStatus.CLAIMED)
        competitor = replace(self.receiver_command(),
            graph=self.receiver_graph(node_id="app", receiver="b" * 32)[0],
            idempotency_key=IdempotencyKey("competing-receiver"))
        before = self.graph_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(competitor)
        self.assertEqual(self.graph_truth(), before)
        self.assertEqual(self.retry(claimed, "original"), replace(retried, replayed=True))

    def test_original_inverse_can_start_without_clearing_scope_for_competing_receiver(self):
        self.admit_operations("inverse", StartNode(NodeTarget("app")), StartNode(NodeTarget("app")))
        adapter = RecordingRuntimeAdapter(
            lambda _context, request: RuntimeEffectResult.succeeded(request.effect_id),
            lambda _context, request: RuntimeEffectResult.failed(request.effect_id,
                RuntimeEffectFailure("after-success", "bounded later failure")))
        claimed, failed = self.execute_effects("inverse", adapter)
        self.assertIs(failed.run.status, ActivityRunStatus.FAILED)
        compensation = self.begin_compensation(claimed, "inverse")
        self.assertEqual(len(compensation.program.steps), 1)
        inverse = self.start_inverse(claimed, compensation, "inverse")
        self.assertEqual(inverse.intent.intent.operation, compensation.program.steps[0].operation)
        before = self.graph_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_receiver("competing-with-inverse")
        self.assertEqual(self.graph_truth(), before)
        replay = self.start_inverse(claimed, compensation, "inverse")
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.binding, inverse.binding)

    def test_stale_original_retry_refuses_but_completed_retry_receipt_stays_original(self):
        self.admit()
        adapter = RecordingRuntimeAdapter(lambda _context, request: RuntimeEffectResult.failed(
            request.effect_id, RuntimeEffectFailure("original-failed", "bounded failure")))
        claimed, _ = self.execute_effects("a", adapter)
        # Retained stale-generation premise, not a supported scope-reuse action.
        original_revision = self.connection.execute(
            "SELECT desired_graph_revision FROM cpk_workspaces WHERE workspace_id='workspace-a'").fetchone()[0]
        try:
            self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=desired_graph_revision+1 "
                "WHERE workspace_id='workspace-a'")
            before = self.execution_truth()
            with self.assertRaises(RunLifecycleConflict):
                self.retry(claimed, "fresh-after-stale")
            self.assertEqual(self.execution_truth(), before)
        finally:
            self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=%s "
                "WHERE workspace_id='workspace-a'", (original_revision,))
        # Only now create the legitimate retry. The stale refusal above cannot
        # pass merely because a newer run already made the old run ineligible.
        retried = self.retry(claimed, "original")
        try:
            self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=desired_graph_revision+1 "
                "WHERE workspace_id='workspace-a'")
            before = self.execution_truth()
            replay = self.retry(claimed, "original")
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.run, retried.run)
            self.assertEqual(self.execution_truth(), before)
        finally:
            self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=%s "
                "WHERE workspace_id='workspace-a'", (original_revision,))
