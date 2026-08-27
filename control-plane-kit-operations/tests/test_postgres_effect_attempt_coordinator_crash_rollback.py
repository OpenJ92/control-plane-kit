from __future__ import annotations

import unittest
from unittest import mock

from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.coordinator import (
    CoordinatorStatus,
    ExecutionCoordinatorConflict,
    ExecutionCoordinatorDenied,
    ExecutionCoordinatorNotFound,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    EffectAttemptFoldNotFound,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
    EffectAttemptReconciliationNotFound,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from tests.postgres_effect_attempt_coordinator_fixture import (
    PostgresEffectAttemptCoordinatorFixture,
    RecordingRuntimeAdapter,
)


class PostgresEffectAttemptCoordinatorCrashRollbackTests(
    PostgresEffectAttemptCoordinatorFixture,
    unittest.TestCase,
):
    def test_committed_start_then_crash_has_zero_provider_and_restart_reconciles(self) -> None:
        error = RuntimeError("post-start-crash-canary")
        adapter = RecordingRuntimeAdapter(
            AssertionError("crashed start reached provider")
        )
        harness = self.coordinator_harness(adapter=adapter)
        original = EffectAttemptStartService.execute

        def crash_after_commit(service, command):
            original(service, command)
            raise error

        with mock.patch.object(EffectAttemptStartService, "execute", crash_after_commit):
            with self.assertRaises(RuntimeError) as caught:
                harness.coordinator.execute(self.coordinator_command())

        self.assertIs(caught.exception, error)
        self.assertEqual(adapter.runtime_calls, [])
        started = self.current_attempt()
        self.assertIs(started.state.status, EffectAttemptStatus.STARTED)

        with self.unit_of_work() as unit_of_work:
            intent = unit_of_work.stores.effect_attempt_intents.get(
                started.state.identity
            ).intent
        observer = self.observer_for(self.observed_story(), started, intent)
        restart = self.coordinator_harness(
            adapter=RecordingRuntimeAdapter(
                AssertionError("restart redispatched provider")
            ),
            observer=observer,
        )
        result = restart.coordinator.execute(self.coordinator_command())
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(restart.adapter.runtime_calls, [])
        self.assertEqual(len(restart.reconciliation.commands), 1)

    def test_provider_fault_becomes_one_direct_uncertain_fold(self) -> None:
        error = RuntimeError("provider-fault-canary")
        harness = self.coordinator_harness(
            adapter=RecordingRuntimeAdapter(error)
        )

        result = harness.coordinator.execute(self.coordinator_command())

        self.assertIs(result.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(len(harness.adapter.runtime_calls), 1)
        self.assertEqual(len(harness.fold.commands), 1)
        self.assertIs(
            harness.fold.commands[0].outcome.status,
            EffectAttemptStatus.UNCERTAIN,
        )
        self.assertNotIn("provider-fault-canary", repr(harness.fold.commands[0]))

    def test_fold_expected_errors_are_fixed_and_leave_started_truth(self) -> None:
        rows = (
            (
                EffectAttemptFoldNotFound("fold-not-found-canary"),
                ExecutionCoordinatorNotFound,
                "effect attempt fold truth was not found",
            ),
            (
                EffectAttemptFoldConflict("fold-conflict-canary"),
                ExecutionCoordinatorConflict,
                "effect attempt fold truth is invalid",
            ),
            (
                EffectAttemptFoldDenied("fold-denied-canary"),
                ExecutionCoordinatorDenied,
                "effect attempt fold authority is invalid",
            ),
        )
        for error, expected_type, message in rows:
            with self.subTest(error=type(error).__name__):
                self.reset_start_truth()
                harness = self.coordinator_harness()
                with mock.patch.object(
                    harness.fold.inner,
                    "execute",
                    side_effect=error,
                ):
                    with self.assertRaises(expected_type) as caught:
                        harness.coordinator.execute(self.coordinator_command())
                self.assertEqual(str(caught.exception), message)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertIs(self.current_attempt().state.status, EffectAttemptStatus.STARTED)
                self.assertEqual(harness.lifecycle.commands, [])

    def test_reconciliation_expected_errors_are_fixed_without_provider(self) -> None:
        rows = (
            (
                EffectAttemptReconciliationNotFound("reconcile-not-found-canary"),
                ExecutionCoordinatorNotFound,
                "effect attempt reconciliation truth was not found",
            ),
            (
                EffectAttemptReconciliationConflict("reconcile-conflict-canary"),
                ExecutionCoordinatorConflict,
                "effect attempt reconciliation truth is invalid",
            ),
            (
                EffectAttemptReconciliationDenied("reconcile-denied-canary"),
                ExecutionCoordinatorDenied,
                "effect attempt reconciliation authority is invalid",
            ),
        )
        for error, expected_type, message in rows:
            with self.subTest(error=type(error).__name__):
                self.reset_start_truth()
                current, _intent, _record, _authority, observer = (
                    self.seed_running_reconciliation()
                )
                adapter = RecordingRuntimeAdapter(
                    AssertionError("failed reconciliation dispatched provider")
                )
                harness = self.coordinator_harness(adapter=adapter, observer=observer)
                with mock.patch.object(
                    harness.reconciliation.inner,
                    "execute",
                    side_effect=error,
                ):
                    with self.assertRaises(expected_type) as caught:
                        harness.coordinator.execute(self.coordinator_command())
                self.assertEqual(str(caught.exception), message)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(self.current_attempt(), current)
                self.assertEqual(adapter.runtime_calls, [])

    def test_unexpected_service_faults_remain_raw_and_do_not_advance_graph(self) -> None:
        for stage in ("start", "fold", "reconciliation"):
            for error in (
                TypeError(f"raw-{stage}-type-canary"),
                RuntimeError(f"raw-{stage}-runtime-canary"),
            ):
                with self.subTest(stage=stage, error=type(error).__name__):
                    self.reset_start_truth()
                    observer = None
                    if stage == "reconciliation":
                        _current, _intent, _record, _authority, observer = (
                            self.seed_running_reconciliation()
                        )
                    harness = self.coordinator_harness(observer=observer)
                    owner = getattr(harness, stage).inner
                    before = self.graph_request_snapshot()
                    with mock.patch.object(owner, "execute", side_effect=error):
                        with self.assertRaises(type(error)) as caught:
                            harness.coordinator.execute(self.coordinator_command())
                    self.assertIs(caught.exception, error)
                    self.assertEqual(self.graph_request_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
