from __future__ import annotations

import unittest
from unittest import mock

from control_plane_kit_operations.effect_attempt_fold import (
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_fold_fixture import (
    FOLD_STORIES,
    PostgresEffectAttemptFoldFixture,
)


class PostgresEffectAttemptFoldFirstReplayTests(
    PostgresEffectAttemptFoldFixture,
    unittest.TestCase,
):
    def test_every_direct_and_recovery_fold_commits_exact_truth(self) -> None:
        for compensation in (False, True):
            for story in FOLD_STORIES:
                with self.subTest(compensation=compensation, story=story):
                    current = self.seed_fold_source(
                        story,
                        compensation=compensation,
                    )
                    before_events = self.persisted_event_count()
                    expected_state = self.expected_fold_state(current, story)
                    service, ids = self.fold_service_with_sequence(
                        f"fold-{int(compensation)}-{story}"
                    )

                    result = service.execute(self.fold_command(story))

                    self.assertIsInstance(result, NewlyFolded)
                    self.assertEqual(ids.calls, [f"fold-{int(compensation)}-{story}"])
                    attempt = result.attempt
                    self.assertEqual(attempt.state, expected_state)
                    self.assertEqual(
                        attempt.original_start_event,
                        current.original_start_event,
                    )
                    self.assertEqual(
                        attempt.latest_transition_event.event_id,
                        f"fold-{int(compensation)}-{story}",
                    )
                    self.assertEqual(
                        attempt.latest_transition_event.ordinal,
                        current.latest_transition_event.ordinal + 1,
                    )
                    self.assertEqual(
                        attempt.latest_transition_event.kind,
                        self.event_kind(story, compensation=compensation),
                    )
                    self.assertEqual(
                        attempt.latest_transition_event.evidence.descriptor(),
                        {
                            "effect_attempt": EffectAttemptEventEvidence(
                                attempt.state.identity.attempt,
                                effect_attempt_state_fingerprint(attempt.state),
                            ).descriptor()
                        },
                    )
                    self.assertEqual(
                        attempt.latest_transition_event.failure,
                        self.fold_command(story).failure,
                    )
                    self.assertEqual(self.current_attempt(), attempt)
                    self.assertEqual(self.persisted_event_count(), before_events + 1)

    def test_exact_restart_replay_is_clock_id_and_write_free(self) -> None:
        for story in FOLD_STORIES:
            with self.subTest(story=story):
                self.seed_fold_source(story)
                first = self.fold_service(f"first-{story}").execute(
                    self.fold_command(story)
                )
                self.assertIsInstance(first, NewlyFolded)
                before = self.attempt_snapshot()
                ids = Sequence(f"replay-{story}-must-not-allocate")

                with self.reject_fold_database_observation(
                    f"exact {story} replay sampled database time"
                ):
                    replay = self.fold_service_with_id_factory(ids).execute(
                        self.fold_command(story)
                    )

                self.assertEqual(replay, ExistingFold(first.attempt))
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_replay_compares_complete_transition_and_failure_meaning(self) -> None:
        self.seed_fold_source("failed")
        first = self.fold_service("first-failed").execute(
            self.fold_command("failed")
        )
        before = self.attempt_snapshot()
        cases = (
            self.fold_command("succeeded"),
            self.fold_command("failed", failure=self.failure("changed-canary")),
        )
        from control_plane_kit_operations.effect_attempt_fold import (
            EffectAttemptFoldConflict,
        )

        for command in cases:
            with self.subTest(kind=command.transition.kind.value):
                with self.reject_fold_database_observation(
                    "incompatible replay sampled database time"
                ):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service("must-not-allocate").execute(command)
                self.assert_safe_error(caught.exception, "changed-canary")
                self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.current_attempt(), first.attempt)

    def test_first_fold_lock_clock_ordinal_id_and_write_order_is_exact(self) -> None:
        self.seed_fold_source("succeeded")
        calls: list[str] = []
        observations = []
        original_request = PostgresExecutionStore.get_request_for_update
        original_run = PostgresExecutionStore.get_run_for_request_for_update
        original_attempt = EffectAttemptStore.get_for_update
        original_observe = PostgresExecutionStore.observe_request_lease_for_update
        original_ordinal = PostgresExecutionStore.next_event_ordinal
        original_event = PostgresExecutionStore.add_event
        original_cas = EffectAttemptStore.compare_and_set

        def request(store, request_id):
            calls.append("request")
            return original_request(store, request_id)

        def run(store, request_id, run_id):
            calls.append("run")
            return original_run(store, request_id, run_id)

        def attempt(store, identity):
            calls.append("attempt")
            return original_attempt(store, identity)

        def observe(store, request_id):
            observation = original_observe(store, request_id)
            calls.append("clock")
            observations.append(observation)
            return observation

        def ordinal(store, run_id):
            calls.append("ordinal")
            return original_ordinal(store, run_id)

        def identity():
            calls.append("identity")
            return "ordered-fold"

        def event(store, value):
            calls.append("event")
            return original_event(store, value)

        def cas(store, current, replacement):
            calls.append("cas")
            return original_cas(store, current, replacement)

        with mock.patch.object(PostgresExecutionStore, "get_request_for_update", request), \
            mock.patch.object(PostgresExecutionStore, "get_run_for_request_for_update", run), \
            mock.patch.object(EffectAttemptStore, "get_for_update", attempt), \
            mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update", observe), \
            mock.patch.object(PostgresExecutionStore, "next_event_ordinal", ordinal), \
            mock.patch.object(PostgresExecutionStore, "add_event", event), \
            mock.patch.object(EffectAttemptStore, "compare_and_set", cas):
            result = self.fold_service_with_id_factory(identity).execute(
                self.fold_command("succeeded")
            )

        self.assertIsInstance(result, NewlyFolded)
        self.assertEqual(
            calls,
            [
                "request",
                "run",
                "attempt",
                "request",
                "clock",
                "ordinal",
                "identity",
                "event",
                "cas",
            ],
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(
            result.attempt.latest_transition_event.occurred_at,
            observations[0].observed_at,
        )

    def test_complete_result_and_event_exist_before_event_and_cas_writes(self) -> None:
        current = self.seed_fold_source("succeeded")
        calls: list[str] = []
        appended = []
        original_event = PostgresExecutionStore.add_event
        original_cas = EffectAttemptStore.compare_and_set

        def add_event(store, event):
            calls.append("event")
            appended.append(event)
            return original_event(store, event)

        def compare_and_set(store, observed, replacement):
            calls.append("cas")
            self.assertEqual(observed, current)
            self.assertEqual(NewlyFolded(replacement).attempt, replacement)
            self.assertEqual(appended, [replacement.latest_transition_event])
            return original_cas(store, observed, replacement)

        with mock.patch.object(PostgresExecutionStore, "add_event", add_event), \
            mock.patch.object(EffectAttemptStore, "compare_and_set", compare_and_set):
            result = self.fold_service("complete-before-write").execute(
                self.fold_command("succeeded")
            )

        self.assertIsInstance(result, NewlyFolded)
        self.assertEqual(calls, ["event", "cas"])


if __name__ == "__main__":
    unittest.main()
