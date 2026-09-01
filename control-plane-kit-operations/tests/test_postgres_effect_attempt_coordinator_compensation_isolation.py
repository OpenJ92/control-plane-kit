from __future__ import annotations

import unittest

from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.failed_run_compensation_attempt_fixture import (
    FailedRunCompensationAttemptFixture,
)
from tests.failed_run_compensation_fixture import Sequence
from tests.postgres_effect_attempt_coordinator_fixture import (
    PostgresEffectAttemptCoordinatorFixture,
)


class PostgresEffectAttemptCoordinatorCompensationIsolationTests(
    PostgresEffectAttemptCoordinatorFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        PostgresEffectAttemptCoordinatorFixture.setUp(self)

    def tearDown(self) -> None:
        PostgresEffectAttemptCoordinatorFixture.tearDown(self)

    def test_failed_forward_run_admits_compensation_but_never_dispatches_it(
        self,
    ) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        compensation = object.__new__(FailedRunCompensationAttemptFixture)
        compensation.connection = self.connection
        compensation.database_url = self.database_url
        compensation.assertIsNotNone = self.assertIsNotNone
        compensation.seed_truth()
        self.connection.execute(
            "DELETE FROM cpk_activity_events WHERE run_id='run-a' "
            "AND event_type='run_failed'"
        )
        self.connection.execute(
            "UPDATE cpk_activity_runs SET status='running', settled_at=NULL "
            "WHERE run_id='run-a'"
        )
        forward = self.coordinator_harness()

        failed = forward.coordinator.execute(
            self.coordinator_command(generation=1)
        )

        self.assertIs(failed.status, CoordinatorStatus.FAILED)
        self.assertEqual(failed.effects_attempted, 0)
        self.assertEqual(forward.start.commands, [])
        self.assertEqual(forward.reconciliation.commands, [])
        self.assertEqual(forward.fold.commands, [])
        self.assertEqual(forward.adapter.runtime_calls, [])
        self.assertEqual(len(forward.lifecycle.commands), 1)
        with self.unit_of_work() as unit_of_work:
            run_failed = tuple(
                event
                for event in unit_of_work.stores.execution.events_for_run("run-a")
                if event.kind is ActivityEventKind.RUN_FAILED
            )
        self.assertEqual(len(run_failed), 1)
        self.assertIsNotNone(run_failed[0].failure)

        source_truth = compensation.source_truth_snapshot()
        try:
            command = compensation.command(source_failure=run_failed[0].failure)
        except InvalidOperationCommand:
            self.fail("coordinator failure evidence is not compensation-admissible")
        admitted = compensation.service(
            Sequence("program-a", "compensation-started", "action-a")
        ).execute(command)
        self.connection.execute(
            "UPDATE cpk_execution_requests SET "
            "claimed_at='2098-01-01T00:00:00Z', "
            "lease_expires_at='2099-01-01T00:00:00Z' "
            "WHERE request_id='request-a'"
        )
        bound = compensation.attempt_service("inverse-start-a").execute(
            compensation.start_command(position=1)
        )

        self.assertEqual(admitted.program.program_id, "program-a")
        self.assertEqual(bound.binding.program_id, "program-a")
        self.assertEqual(bound.binding.position, 1)
        self.assertEqual(compensation.source_truth_snapshot(), source_truth)
        protected = self._compensation_snapshot()
        blocked = self.coordinator_harness()

        result = blocked.coordinator.execute(
            self.coordinator_command(
                generation=1,
                idempotency_key="coordinator-after-compensation",
            )
        )

        self.assertIs(result.status, CoordinatorStatus.BLOCKED)
        self.assertEqual(result.effects_attempted, 0)
        self.assertEqual(blocked.start.commands, [])
        self.assertEqual(blocked.reconciliation.commands, [])
        self.assertEqual(blocked.fold.commands, [])
        self.assertEqual(blocked.adapter.runtime_calls, [])
        self.assertEqual(blocked.adapter.legacy_calls, [])
        self.assertEqual(blocked.lifecycle.commands, [])
        self.assertEqual(blocked.lifecycle_ids.calls, [])
        self.assertEqual(blocked.start_ids.calls, [])
        self.assertEqual(blocked.fold_ids.calls, [])
        self.assertEqual(blocked.coordinator_ids.calls, [])
        self.assertEqual(self._compensation_snapshot(), protected)

    def _compensation_snapshot(self) -> tuple[object, ...]:
        relations = (
            ("cpk_failed_run_compensations", "program_id"),
            ("cpk_failed_run_compensation_steps", "program_id, position"),
            (
                "cpk_failed_run_compensation_attempt_bindings",
                "program_id, position",
            ),
            (
                "cpk_effect_attempt_intents",
                "run_id, activity_id, attempt",
            ),
            ("cpk_effect_attempts", "run_id, activity_id, attempt"),
            (
                "cpk_effect_attempt_outcomes",
                "run_id, activity_id, attempt",
            ),
            ("cpk_activity_events", "run_id, ordinal"),
            ("cpk_activity_runs", "run_id"),
            ("cpk_operation_actions", "action_id"),
        )
        return tuple(
            (
                relation,
                tuple(
                    self.connection.execute(
                        f"SELECT to_jsonb(candidate) FROM {relation} AS candidate "
                        f"ORDER BY {order_by}"
                    ).fetchall()
                ),
            )
            for relation, order_by in relations
        )


if __name__ == "__main__":
    unittest.main()
