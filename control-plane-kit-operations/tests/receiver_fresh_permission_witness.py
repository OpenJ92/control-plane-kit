"""One real contention witness for fresh permission rereads; no policy copy."""

from tests.lifecycle_lock_fixture import LifecycleLockFixture, LIFECYCLE_LOCK


class FreshPermissionWitness(LifecycleLockFixture):
    def permission_truth(self):
        return {table: self.connection.execute("SELECT to_jsonb(t) FROM " + table
            + " t ORDER BY to_jsonb(t)::text").fetchall() for table in (
                "cpk_workspaces", "cpk_operation_sessions", "cpk_operation_actions",
                "cpk_execution_requests", "cpk_execution_receiver_scopes", "cpk_activity_runs",
                "cpk_activity_events", "cpk_effect_attempts", "cpk_effect_attempt_intents",
                "cpk_effect_attempt_outcomes", "cpk_failed_run_compensations",
                "cpk_failed_run_compensation_steps", "cpk_failed_run_compensation_attempt_bindings")}

    def assert_rechecks_pins_while_waiting(self, execute, error):
        original = self.connection.execute("SELECT desired_graph_revision FROM cpk_workspaces "
            "WHERE workspace_id='workspace-a'").fetchone()[0]
        try:
            with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute) as future:
                # An independently committed stale-pin premise, introduced only
                # after the real command is known to be waiting for L. Its run
                # status, claim, approval and immutable original plan stay eligible.
                self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=desired_graph_revision+1 "
                    "WHERE workspace_id='workspace-a'")
                before = self.permission_truth()
            with self.assertRaises(error):
                future.result(timeout=1)
            self.assertEqual(self.permission_truth(), before)
        finally:
            self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=%s "
                "WHERE workspace_id='workspace-a'", (original,))
        # Refusal cannot be explained by some permanently invalid fixture state.
        # The exact unchanged original operation succeeds once pins are restored.
        return execute(self.unit_of_work)
