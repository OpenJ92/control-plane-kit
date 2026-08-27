from __future__ import annotations

import unittest

from control_plane_kit_core.operations import EffectRecoveryResolution
from control_plane_kit_core.planning import (
    ActivityId,
    AddSocketConnection,
    AllocatePublicIngress,
    PlannedActivity,
    PublicIngressActivityTarget,
    SocketConnectionTarget,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.coordinator import (
    ActivityExecutionDispatcher,
    CoordinatorStatus,
)
from tests.effect_attempt_coordinator_fixture import RecordingCoordinatorAdapter
from tests.postgres_effect_attempt_coordinator_fixture import (
    PostgresEffectAttemptCoordinatorFixture,
    RecordingRuntimeAdapter,
)
from tests.test_runtime_effect_translation import _context


class PostgresEffectAttemptCoordinatorBudgetLifecycleTests(
    PostgresEffectAttemptCoordinatorFixture,
    unittest.TestCase,
):
    def test_control_terminal_projection_and_legacy_ingress_socket_arms(self) -> None:
        runtime = RecordingCoordinatorAdapter()
        ingress = RecordingCoordinatorAdapter()
        dispatcher = ActivityExecutionDispatcher(runtime=runtime, ingress=ingress)
        ingress_context = _context(
            activity=PlannedActivity(
                ActivityId("allocate-ingress"),
                AllocatePublicIngress(PublicIngressActivityTarget("gateway-public")),
            )
        )
        socket_context = _context(
            activity=PlannedActivity(
                ActivityId("connect-socket"),
                AddSocketConnection(SocketConnectionTarget("api.database")),
            )
        )

        dispatcher.execute(ingress_context)
        dispatcher.execute(socket_context)

        self.assertEqual(ingress.legacy_contexts, [ingress_context])
        self.assertEqual(runtime.legacy_contexts, [socket_context])

        self.reset_start_truth()
        self.persist_recovery_resolution(EffectRecoveryResolution.SUCCEEDED)
        harness = self.coordinator_harness()
        terminal = harness.coordinator.execute(self.coordinator_command())
        self.assertIs(terminal.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(terminal.effects_attempted, 0)

    def test_fresh_start_fold_and_same_invocation_settlement_consume_one_unit(self) -> None:
        harness = self.coordinator_harness(
            adapter=RecordingRuntimeAdapter(
                lambda _context, request: RuntimeEffectResult.succeeded(
                    request.effect_id
                )
            )
        )

        result = harness.coordinator.execute(self.coordinator_command(max_effects=1))

        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(len(harness.adapter.runtime_calls), 1)
        self.assertEqual(len(harness.fold.commands), 1)
        self.assertEqual(len(harness.lifecycle.commands), 1)

    def test_existing_reconciliation_and_settlement_consume_one_unit(self) -> None:
        _current, _intent, _record, _authority, observer = (
            self.seed_running_reconciliation()
        )
        harness = self.coordinator_harness(observer=observer)

        result = harness.coordinator.execute(self.coordinator_command(max_effects=1))

        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(len(harness.reconciliation.commands), 1)
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(len(harness.lifecycle.commands), 1)

    def test_lifecycle_only_recovery_settlement_consumes_no_budget(self) -> None:
        for resolution, status in (
            (EffectRecoveryResolution.SUCCEEDED, CoordinatorStatus.COMPLETED),
            (EffectRecoveryResolution.FAILED, CoordinatorStatus.FAILED),
        ):
            with self.subTest(resolution=resolution.value):
                self.reset_start_truth()
                self.persist_recovery_resolution(resolution)
                harness = self.coordinator_harness()

                result = harness.coordinator.execute(
                    self.coordinator_command(max_effects=3)
                )

                self.assertIs(result.status, status)
                self.assertEqual(result.effects_attempted, 0)
                self.assertEqual(harness.start.commands, [])
                self.assertEqual(harness.reconciliation.commands, [])
                self.assertEqual(harness.adapter.runtime_calls, [])
                self.assertEqual(len(harness.lifecycle.commands), 1)

    def test_budget_counts_selected_iterations_not_provider_calls(self) -> None:
        _current, _intent, _record, _authority, observer = (
            self.seed_running_reconciliation()
        )
        harness = self.coordinator_harness(observer=observer)

        result = harness.coordinator.execute(self.coordinator_command(max_effects=2))

        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(harness.reconciliation.commands), 1)
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertLessEqual(len(harness.start.commands), 1)


if __name__ == "__main__":
    unittest.main()
