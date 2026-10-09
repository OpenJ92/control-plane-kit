"""Cleanup accounting markers are bounded routing, never execution authority."""
import unittest
from copy import deepcopy

from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture


class PostgresConfigurationCleanupRoutingTests(ReceiverFreshExecutionFixture, unittest.TestCase):
    def test_terminal_route_is_exact_fail_closed_and_preserves_active_prefix(self):
        admitted = self.admit("terminal-routing")
        claimed = self.ready_run("terminal-routing")
        plan_id = admitted.request.identity.plan_id
        original = self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s",
            (plan_id,)).fetchone()[0]
        core = original.get("plan", original)
        activity_id = core["activities"][0]["activity_id"]
        stored = lambda version, profile, plan: dict(schema="control-plane-kit.operations.activity-plan-record",
            version=version, derivation_profile=profile, plan=plan)
        cleanup = deepcopy(core)
        cleanup["activities"][0]["operation"] = {"kind": "cleanup-configuration-instances"}
        duplicate = deepcopy(core)
        duplicate["activities"].append(duplicate["activities"][0])
        cases = (
            ("legacy", core, "ordinary"),
            ("structural", stored(1, "structural-v1", core), "ordinary"),
            ("management", stored(1, "management-graph-pair-v1", core), "ordinary"),
            ("cleanup", stored(2, "configuration-cleanup-v1", cleanup), "cleanup"),
            ("contradictory-proposal", {**stored(1, "structural-v1", core), "cleanup_proposal": None}, "cleanup"),
            ("stripped-v2", dict(schema="control-plane-kit.operations.activity-plan-record", version=2, plan=core), "unavailable"),
            ("contradictory-v2", stored(2, "structural-v1", core), "unavailable"),
            ("string-version", stored("1", "structural-v1", core), "unavailable"),
            ("unknown-profile", stored(1, "unknown", core), "unavailable"),
            ("unknown-schema", {**core, "schema": "unknown"}, "unavailable"),
            ("invalid-nested-no-legacy-fallback", {**core, **stored(1, "structural-v1", None)}, "unavailable"),
            ("null-array", {**core, "activities": None}, "unavailable"),
            ("missing-target", {**core, "activities": []}, "unavailable"),
            ("duplicate-target", duplicate, "unavailable"),
            ("malformed-target", {**core, "activities": [{"activity_id": activity_id, "operation": None}]}, "unavailable"),
        )
        before = self.execution_truth()
        for active in (False, True):
            for label, payload, expected in cases:
                with self.subTest(active=active, route=label), self.unit_of_work() as uow:
                    uow.stores.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s",
                        (Jsonb(payload), plan_id))
                    with _configuration_accounting(claimed.run.run_id, active=active) as accounting:
                        # A real earlier routing query supplies a nonzero prefix.
                        uow.stores.configuration_preparation._configure_run(claimed.run.run_id)
                        accounting.active = active
                        prefix = accounting.used
                        result = uow.stores.configuration_preparation._configure_run(claimed.run.run_id,
                            replay_request_id=admitted.request.identity.request_id, replay_activity_id=activity_id)
                        self.assertEqual(result, expected)
                        self.assertEqual(accounting.used.statements, prefix.statements + 1)
                        self.assertGreaterEqual(accounting.used.records, prefix.records)
                        self.assertLessEqual(accounting.used.value_octets - prefix.value_octets, 4)
                        self.assertEqual(accounting.used.scalar_markers - prefix.scalar_markers, 4)
                        if active or expected == "cleanup":
                            self.assertTrue(accounting.active)
        for run_id, request_id, target_id in (("missing-run", admitted.request.identity.request_id, activity_id),
                (claimed.run.run_id, "wrong-request", activity_id),
                (claimed.run.run_id, admitted.request.identity.request_id, "missing-activity")):
            with self.subTest(identity=(run_id, request_id, target_id)), self.unit_of_work() as uow:
                with _configuration_accounting(claimed.run.run_id, active=True) as accounting:
                    self.assertEqual(uow.stores.configuration_preparation._configure_run(run_id,
                        replay_request_id=request_id, replay_activity_id=target_id), "unavailable")
                    self.assertTrue(accounting.active)
                    self.assertEqual(accounting.used.statements, 1)
        self.assertEqual(self.execution_truth(), before)
        self.assertEqual(self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s",
            (plan_id,)).fetchone()[0], original)

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
