"""Cleanup accounting markers are bounded routing, never execution authority."""
import unittest

from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture


class PostgresConfigurationCleanupRoutingTests(ReceiverFreshExecutionFixture, unittest.TestCase):
    def test_artifact_free_cleanup_markers_cannot_downgrade_command_accounting(self):
        # Real ordinary admission/lifecycle supplies artifact-free pinned input.
        # Cleanup marker corruption below never grants cleanup permission.
        admitted = self.admit("routing")
        claimed = self.ready_run("routing")
        plan_id = admitted.request.identity.plan_id
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(plan_id)
            for projection_id in (plan.base_realized_projection_id, plan.desired_realized_projection_id):
                graph = DEFAULT_GRAPH_CODEC.decode(uow.stores.realized_graphs.get(projection_id).graph_descriptor)
                self.assertFalse(any(node.configuration_artifacts for node in graph.nodes.values()))
        original = self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s",
            (plan_id,)).fetchone()[0]
        cases = (
            ("actual-ordinary", original, False),
            ("profile-marker", {"derivation_profile": "configuration-cleanup-v1"}, True),
            ("malformed-proposal", {"cleanup_proposal": None}, True),
            ("malformed-fingerprint", {"cleanup_proposal_fingerprint": False}, True),
            ("current-operation", {"plan": {"activities": [{"operation": {
                "kind": "cleanup-configuration-instances"}}]}}, True),
            ("legacy-operation", {"activities": [{"operation": {
                "kind": "cleanup-configuration-instances"}}]}, True),
            ("contradictory-profile", {"derivation_profile": "structural-v1", "cleanup_proposal": {}}, True),
            ("ordinary-empty", {"schema": "control-plane-kit.activity-plan", "version": 1, "activities": []}, False),
        )
        before = self.execution_truth()
        for label, payload, expected in cases:
            with self.subTest(marker=label), self.unit_of_work() as uow:
                # Deliberately malformed below-owner marker premises are rolled
                # back. Their downstream semantic validators are not bypassed.
                uow.stores.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s",
                    (Jsonb(payload), plan_id))
                with _configuration_accounting(claimed.run.run_id, active=False) as accounting:
                    uow.stores.configuration_preparation._configure_run(claimed.run.run_id)
                    self.assertIs(accounting.active, expected)
                    self.assertEqual(accounting.used.statements, 1)
                    self.assertLessEqual(accounting.used.value_octets, 3)
                    self.assertLessEqual(accounting.used.scalar_markers, 3)
        self.assertEqual(self.execution_truth(), before)
        self.assertEqual(self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s",
            (plan_id,)).fetchone()[0], original)
