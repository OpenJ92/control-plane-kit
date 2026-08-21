from __future__ import annotations

import unittest
from unittest import mock

from control_plane_kit_core.operations import (
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
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
from control_plane_kit_operations.postgres.effect_outcome_store import (
    EffectAttemptOutcomeStore,
)
from control_plane_kit_operations.postgres.observed_state import (
    PostgresObservedStateStore,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_fold_fixture import (
    FOLD_STORIES,
    REPLAY_ERROR,
)
from tests.postgres_guarded_observed_effect_fold_fixture import (
    PostgresGuardedObservedEffectFoldFixture,
)


class PostgresEffectAttemptFoldFirstReplayTests(
    PostgresGuardedObservedEffectFoldFixture,
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
                    observations = []
                    original_observe = (
                        PostgresExecutionStore.observe_request_lease_for_update
                    )

                    def observe(store, request_id):
                        observation = original_observe(store, request_id)
                        observations.append(observation)
                        return observation

                    with mock.patch.object(
                        PostgresExecutionStore,
                        "observe_request_lease_for_update",
                        observe,
                    ):
                        result = service.execute(self.fold_command(story))

                    self.assertIsInstance(result, NewlyFolded)
                    self.assertEqual(
                        ids.calls,
                        list(self.fold_ids(f"fold-{int(compensation)}-{story}")),
                    )
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
                    self.assertEqual(len(observations), 1)
                    self.assertEqual(
                        attempt.latest_transition_event.occurred_at,
                        observations[0].observed_at,
                    )
                    self.assertEqual(self.current_attempt(), attempt)
                    self.assertEqual(self.persisted_event_count(), before_events + 1)
                    if story in {"succeeded", "failed", "unsupported", "uncertain"}:
                        self.assertEqual(
                            result.outcome_record,
                            self.expected_outcome_record(
                                attempt,
                                self.fold_command(story).outcome,
                                event_id=f"fold-{int(compensation)}-{story}",
                            ),
                        )
                    else:
                        self.assertIsNone(result.outcome_record)

    def test_direct_and_recovery_folds_do_not_advance_program_or_graph_truth(
        self,
    ) -> None:
        direct_worlds = tuple(self.stories())
        self.assertEqual(len(direct_worlds), 20)
        recovery_worlds = tuple(
            (story, compensation)
            for compensation in (False, True)
            for story in ("recovered-succeeded", "recovered-failed", "abandoned")
        )
        worlds = direct_worlds + recovery_worlds

        for world in worlds:
            if type(world) is tuple:
                story, compensation = world
                label = story
            else:
                story = world
                compensation = world.compensation
                label = world.name
            with self.subTest(compensation=compensation, story=label):
                self.seed_fold_source(story, compensation=compensation)
                before = self.non_advancement_snapshot()

                service = self.fold_service(
                    f"non-advancing-{int(compensation)}-{label}"
                )
                result = self.execute_fold(service, story)

                self.assertIsInstance(result, NewlyFolded)
                self.assertEqual(self.non_advancement_snapshot(), before)

    def test_outcome_evidence_relations_are_intentionally_outside_nonadvancement(
        self,
    ) -> None:
        story = self.outcome_story("execution-succeeded", compensation=False)
        self.seed_fold_source(story)
        before = self.non_advancement_snapshot()

        result = self.fold_service("evidence-is-not-program-truth").execute(
            self.fold_command(story)
        )

        self.assertEqual(self.non_advancement_snapshot(), before)
        self.assertIsNotNone(result.outcome_record)
        self.assertEqual(len(result.outcome_record.endpoint_observations), 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempt_outcomes"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempt_outcome_observations"
            ).fetchone(),
            (2,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_observations"
            ).fetchone(),
            (2,),
        )

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

                self.assertEqual(
                    replay,
                    ExistingFold(first.attempt, first.outcome_record),
                )
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
            self.fold_command("unsupported"),
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

    def test_same_status_replay_binds_outcome_and_every_recovery_coordinate(
        self,
    ) -> None:
        direct_story = self.outcome_story("execution-succeeded", compensation=False)
        alternate_story = self.outcome_story("observed-succeeded", compensation=False)
        cases = (("direct-outcome-canary", direct_story, alternate_story),)

        recovery = self.fold_transition("recovered-succeeded")
        decision = recovery.recovery_decision
        recovery_drifts = (
            (
                "decision-id-canary",
                EffectRecoveryDecision(
                    "changed-decision-id-canary",
                    decision.attempt_identity,
                    decision.resolution,
                    decision.uncertain_fingerprint,
                    decision.evidence_fingerprint,
                ),
            ),
            (
                "uncertain-fingerprint-canary",
                EffectRecoveryDecision(
                    decision.decision_id,
                    decision.attempt_identity,
                    decision.resolution,
                    "e" * 64,
                    decision.evidence_fingerprint,
                ),
            ),
            (
                "evidence-fingerprint-canary",
                EffectRecoveryDecision(
                    decision.decision_id,
                    decision.attempt_identity,
                    decision.resolution,
                    decision.uncertain_fingerprint,
                    "f" * 64,
                ),
            ),
        )
        cases += tuple(
            (
                label,
                "recovered-succeeded",
                EffectAttemptTransition(
                    EffectAttemptTransitionKind.RECONCILED,
                    recovery.identity,
                    recovery_decision=changed_decision,
                ),
            )
            for label, changed_decision in recovery_drifts
        )

        for label, story, changed in cases:
            with self.subTest(case=label):
                self.seed_fold_source(story)
                first = self.fold_service(f"first-{label}").execute(
                    self.fold_command(story)
                )
                self.assertIsInstance(first, NewlyFolded)
                before = self.attempt_snapshot()
                ids = Sequence("same-status-drift-must-not-allocate")
                with self.reject_fold_database_observation(
                    "same-status replay drift sampled database time"
                ):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service_with_id_factory(ids).execute(
                            self.fold_command(changed)
                            if type(story) is not str
                            else self.fold_command(story, transition=changed)
                        )
                self.assert_safe_error(caught.exception, label, "e" * 64, "f" * 64)
                self.assertEqual(str(caught.exception), REPLAY_ERROR)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

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
        original_observation = PostgresObservedStateStore.put
        original_outcome = EffectAttemptOutcomeStore.insert
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

        identity_values = iter(self.fold_ids("ordered-fold"))

        def identity():
            calls.append("identity")
            return next(identity_values)

        def event(store, value):
            calls.append("event")
            return original_event(store, value)

        def observation(store, value):
            calls.append("observation")
            return original_observation(store, value)

        def outcome(store, value):
            calls.append("outcome")
            return original_outcome(store, value)

        def cas(store, current, replacement):
            calls.append("cas")
            return original_cas(store, current, replacement)

        with mock.patch.object(PostgresExecutionStore, "get_request_for_update", request), \
            mock.patch.object(PostgresExecutionStore, "get_run_for_request_for_update", run), \
            mock.patch.object(EffectAttemptStore, "get_for_update", attempt), \
            mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update", observe), \
            mock.patch.object(PostgresExecutionStore, "next_event_ordinal", ordinal), \
            mock.patch.object(PostgresExecutionStore, "add_event", event), \
            mock.patch.object(PostgresObservedStateStore, "put", observation), \
            mock.patch.object(EffectAttemptOutcomeStore, "insert", outcome), \
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
                "identity",
                "identity",
                "event",
                "observation",
                "observation",
                "outcome",
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
        planned_results = []
        original_event = PostgresExecutionStore.add_event
        original_cas = EffectAttemptStore.compare_and_set
        real_post_init = NewlyFolded.__post_init__

        def result_post_init(value):
            real_post_init(value)
            calls.append("result")
            planned_results.append(value)

        def add_event(store, event):
            calls.append("event")
            self.assertEqual(calls, ["result", "event"])
            self.assertEqual(len(planned_results), 1)
            self.assertEqual(
                planned_results[0].attempt.latest_transition_event,
                event,
            )
            appended.append(event)
            return original_event(store, event)

        def compare_and_set(store, observed, replacement):
            calls.append("cas")
            self.assertEqual(calls, ["result", "event", "cas"])
            self.assertEqual(observed, current)
            self.assertEqual(len(planned_results), 1)
            self.assertEqual(planned_results[0].attempt, replacement)
            self.assertEqual(appended, [replacement.latest_transition_event])
            return original_cas(store, observed, replacement)

        with mock.patch.object(
            NewlyFolded,
            "__post_init__",
            result_post_init,
        ), mock.patch.object(PostgresExecutionStore, "add_event", add_event), \
            mock.patch.object(EffectAttemptStore, "compare_and_set", compare_and_set):
            result = self.fold_service("complete-before-write").execute(
                self.fold_command("succeeded")
            )

        self.assertIsInstance(result, NewlyFolded)
        self.assertEqual(calls, ["result", "event", "cas"])


if __name__ == "__main__":
    unittest.main()
