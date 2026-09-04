from __future__ import annotations

import concurrent.futures
from dataclasses import replace
import threading
import unittest

from control_plane_kit_core.operations import EffectRecoveryResolution
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint,
    runtime_effect_intent_for_request,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.coordinator import (
    CoordinatorStatus,
    ExecutionCoordinatorConflict,
    ExecutionCoordinatorDenied,
)
from tests.postgres_effect_attempt_coordinator_fixture import (
    PostgresEffectAttemptCoordinatorFixture,
    RecordingRuntimeAdapter,
)


class _RequestDerivedObserver:
    def __init__(self, value) -> None:
        self.value = value
        self.calls: list[object] = []

    def observe(self, request, authority):
        self.calls.append((request, authority))
        runtime_request = request.runtime_request
        intent = runtime_effect_intent_for_request(runtime_request)
        return replace(
            self.value,
            effect_id=runtime_request.effect_id,
            request_fingerprint=runtime_effect_intent_fingerprint(intent),
        )


class PostgresEffectAttemptCoordinatorConcurrencyTests(
    PostgresEffectAttemptCoordinatorFixture,
    unittest.TestCase,
):
    @staticmethod
    def concurrent_results(*calls):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = tuple(pool.submit(call) for call in calls)
            values = []
            for future in futures:
                try:
                    values.append(future.result(timeout=20))
                except (ExecutionCoordinatorConflict, ExecutionCoordinatorDenied) as error:
                    values.append(error)
            return tuple(values)

    def test_concurrent_fresh_selection_has_one_global_provider_winner(self) -> None:
        provider_calls: list[str] = []
        admitted = threading.Event()
        release = threading.Event()

        def provider(_context, request):
            provider_calls.append(request.effect_id)
            admitted.set()
            if not release.wait(timeout=5):
                raise AssertionError("admitted command was not released")
            return RuntimeEffectResult.succeeded(request.effect_id)

        observer = _RequestDerivedObserver(self.observed_story().value)
        first = self.coordinator_harness(
            adapter=RecordingRuntimeAdapter(provider),
            observer=observer,
        )
        second = self.coordinator_harness(
            adapter=RecordingRuntimeAdapter(provider),
            observer=observer,
        )
        before = self.graph_request_snapshot()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first_result = pool.submit(
                first.coordinator.execute,
                self.coordinator_command(),
            )
            try:
                self.assertTrue(admitted.wait(timeout=5))
                second_result = pool.submit(
                    second.coordinator.execute,
                    self.coordinator_command(),
                ).result(timeout=5)
                self.assertFalse(first_result.done())
            finally:
                release.set()
            completed = first_result.result(timeout=5)
        replay = second.coordinator.execute(self.coordinator_command())

        self.assertEqual(len(provider_calls), 1)
        self.assertEqual(len(set(provider_calls)), 1)
        self.assertIs(second_result.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(second_result.effects_attempted, 0)
        self.assertIsNone(second_result.activity_id)
        self.assertIs(completed.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(replay, completed)
        self.assertEqual(completed.effects_attempted, 1)
        self.assertEqual(len(first.start.commands) + len(second.start.commands), 1)
        self.assertEqual(
            len(first.reconciliation.commands)
            + len(second.reconciliation.commands),
            0,
        )
        self.assertEqual(self.graph_request_snapshot(), before)

    def test_every_existing_attempt_has_zero_provider_calls(self) -> None:
        current, _intent, _record, _authority, observer = (
            self.seed_running_reconciliation()
        )
        adapter = RecordingRuntimeAdapter(
            AssertionError("existing attempt reached provider")
        )
        harness = self.coordinator_harness(adapter=adapter, observer=observer)

        result = harness.coordinator.execute(self.coordinator_command())

        self.assertEqual(result.effects_attempted, 1)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(len(harness.reconciliation.commands), 1)
        self.assertEqual(
            harness.reconciliation.commands[0].identity,
            current.state.identity,
        )

    def test_claim_rotation_after_start_does_not_revoke_provider_dispatch(self) -> None:
        calls: list[str] = []

        def rotate(_context, request):
            calls.append(request.effect_id)
            self.replace_claim(worker_id="worker-b", generation=8)
            return RuntimeEffectResult.succeeded(request.effect_id)

        harness = self.coordinator_harness(
            adapter=RecordingRuntimeAdapter(rotate)
        )

        with self.assertRaises(ExecutionCoordinatorDenied):
            harness.coordinator.execute(self.coordinator_command())

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(len(harness.fold.commands), 1)
        self.assertEqual(harness.reconciliation.commands, [])

    def test_duplicate_terminal_settlement_and_unrelated_attempt_do_not_interfere(self) -> None:
        terminal = self.persist_recovery_resolution(
            EffectRecoveryResolution.SUCCEEDED
        )
        self.seed_foreign_run()
        unrelated = self.seed_foreign_attempt()
        first = self.coordinator_harness()
        second = self.coordinator_harness()
        admitted = threading.Event()
        release = threading.Event()

        def pause_after_admission() -> None:
            admitted.set()
            if not release.wait(timeout=5):
                raise AssertionError("admitted terminal settlement was not released")

        first.lifecycle.before_execute = pause_after_admission
        command = self.coordinator_command()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first_result = pool.submit(first.coordinator.execute, command)
            try:
                self.assertTrue(admitted.wait(timeout=5))
                incomplete = pool.submit(
                    second.coordinator.execute,
                    command,
                ).result(timeout=5)
                self.assertFalse(first_result.done())
            finally:
                release.set()
            completed = first_result.result(timeout=5)
        replay = second.coordinator.execute(command)

        self.assertEqual(
            len(first.lifecycle.commands) + len(second.lifecycle.commands),
            1,
        )
        self.assertIs(incomplete.status, CoordinatorStatus.UNCERTAIN)
        self.assertEqual(incomplete.effects_attempted, 0)
        self.assertIsNone(incomplete.activity_id)
        self.assertIs(completed.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(replay, completed)
        self.assertEqual(completed.effects_attempted, 0)
        self.assertEqual(first.adapter.runtime_calls, [])
        self.assertEqual(second.adapter.runtime_calls, [])
        self.assertEqual(first.start.commands, [])
        self.assertEqual(second.start.commands, [])
        self.assertEqual(first.reconciliation.commands, [])
        self.assertEqual(second.reconciliation.commands, [])
        with self.unit_of_work() as unit_of_work:
            unrelated_after = unit_of_work.stores.effect_attempts.get(
                unrelated.state.identity
            )
        self.assertEqual(unrelated_after, unrelated)
        self.assertEqual(terminal.original_start_event.run_id, "run-a")


if __name__ == "__main__":
    unittest.main()
