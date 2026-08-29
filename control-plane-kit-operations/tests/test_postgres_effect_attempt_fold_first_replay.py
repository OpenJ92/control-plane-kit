from __future__ import annotations

from dataclasses import replace
import json
import unittest
from unittest import mock

from control_plane_kit_core.operations import (
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
    fold_effect_attempt,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure,
    RuntimeEffectResult,
)
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    EffectAttemptRecord,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ExecutionEffectOutcome,
    effect_outcome_failure,
    effect_outcome_transition,
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
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureCategory,
    FailureEvidence,
)
from tests.effect_outcome_evidence_fixture import WORKSPACE_ID
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
    def _current_failed_command(self):
        base_outcome = self.fold_outcome("failed")
        result = RuntimeEffectResult.failed(
            base_outcome.result.effect_id,
            RuntimeEffectFailure(
                "docker.secret-resolution-reference-not-found",
                "raw-runtime-message-sentinel",
                {"provider_payload": "raw-provider-payload-sentinel"},
            ),
        )
        outcome = ExecutionEffectOutcome(
            base_outcome.identity,
            base_outcome.request_fingerprint,
            result,
        )
        return replace(
            self.fold_command("failed"),
            transition=effect_outcome_transition(outcome),
            failure=effect_outcome_failure(outcome),
            outcome=outcome,
        )

    def _persist_legacy_failed_projection(self):
        current_attempt = self.seed_fold_source("failed")
        command = self._current_failed_command()
        outcome = command.outcome
        current_failure = effect_outcome_failure(outcome)
        legacy_failure = FailureEvidence(
            current_failure.category,
            current_failure.code,
            current_failure.message,
            BoundedEvidence.from_mapping(
                {
                    "effect_outcome": {
                        "profile": "execution-result",
                        "outcome_fingerprint": outcome.outcome_fingerprint,
                    }
                }
            ),
        )
        next_state = fold_effect_attempt(
            current_attempt.state,
            command.transition,
            fence=current_attempt.state.fence,
        )

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            observation = stores.execution.observe_request_lease_for_update(
                command.request_id
            )
            event = ActivityEventRecord(
                "legacy-fold-failed",
                next_state.identity.run_id.value,
                stores.execution.next_event_ordinal(next_state.identity.run_id.value),
                self.event_kind("failed", compensation=False),
                observation.observed_at,
                activity_id=next_state.identity.activity_id,
                evidence=BoundedEvidence.from_mapping(
                    {
                        "effect_attempt": EffectAttemptEventEvidence(
                            next_state.identity.attempt,
                            effect_attempt_state_fingerprint(next_state),
                        ).descriptor()
                    }
                ),
                failure=legacy_failure,
            )
            persisted_attempt = EffectAttemptRecord(
                next_state,
                current_attempt.original_start_event,
                event,
            )
            persisted_record = EffectAttemptOutcomeRecord(
                WORKSPACE_ID,
                outcome,
                persisted_attempt,
                (),
            )
            self.assertEqual(stores.execution.add_event(event), event)
            self.assertEqual(
                stores.effect_outcomes.insert(persisted_record),
                persisted_record,
            )
            self.assertEqual(
                stores.effect_attempts.compare_and_set(
                    current_attempt,
                    persisted_attempt,
                ),
                persisted_attempt,
            )
            unit_of_work.commit()
        return command, persisted_attempt, persisted_record

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

    def test_current_failed_projection_persists_exact_three_field_evidence(
        self,
    ) -> None:
        self.seed_fold_source("failed")
        command = self._current_failed_command()
        folded = self.fold_service("current-failed-fold").execute(command)

        self.assertIsNotNone(folded)
        self.assertIsInstance(folded, NewlyFolded)
        self.assertEqual(
            folded.attempt.latest_transition_event.failure,
            command.failure,
        )
        self.assertEqual(
            json.loads(command.failure.details.canonical_json),
            {
                "effect_outcome": {
                    "profile": "execution-result",
                    "outcome_fingerprint": command.outcome.outcome_fingerprint,
                    "runtime_failure_code": (
                        "docker.secret-resolution-reference-not-found"
                    ),
                }
            },
        )
        self.assertEqual(
            self.current_attempt(),
            folded.attempt,
        )
        with self.unit_of_work() as unit_of_work:
            persisted = unit_of_work.stores.effect_outcomes.get(
                command.transition.identity,
                folded.attempt.latest_transition_event.event_id,
            )
        self.assertEqual(
            persisted,
            folded.outcome_record,
        )

    def test_legacy_failed_projection_replays_current_command_write_free(
        self,
    ) -> None:
        command, persisted_attempt, persisted_record = (
            self._persist_legacy_failed_projection()
        )

        before = self.attempt_snapshot()
        ids = Sequence("legacy-replay-must-not-allocate")
        with self.reject_fold_database_observation(
            "legacy exact replay sampled database time"
        ):
            replay = self.fold_service_with_id_factory(ids).execute(command)

        self.assertEqual(
            replay,
            ExistingFold(persisted_attempt, persisted_record),
        )
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_legacy_replay_rejects_each_near_miss_before_clock_id_or_write(
        self,
    ) -> None:
        cases = (
            "extra-key",
            "wrong-profile",
            "wrong-fingerprint",
            "outer-category",
            "outer-code",
            "outer-message",
        )
        for label in cases:
            with self.subTest(case=label):
                command, persisted_attempt, _ = (
                    self._persist_legacy_failed_projection()
                )
                failure = persisted_attempt.latest_transition_event.failure
                descriptor = {
                    "category": failure.category.value,
                    "code": failure.code,
                    "message": failure.message,
                    "details": failure.details.descriptor(),
                }
                if label == "extra-key":
                    descriptor["details"]["effect_outcome"]["extra"] = (
                        "bounded-canary"
                    )
                elif label == "wrong-profile":
                    descriptor["details"]["effect_outcome"]["profile"] = (
                        "provider-observation"
                    )
                elif label == "wrong-fingerprint":
                    descriptor["details"]["effect_outcome"][
                        "outcome_fingerprint"
                    ] = "f" * 64
                elif label == "outer-category":
                    descriptor["category"] = FailureCategory.OPERATOR_REVIEW.value
                elif label == "outer-code":
                    descriptor["code"] = "runtime.effect-uncertain"
                else:
                    descriptor["message"] = "bounded changed message"
                encoded = json.dumps(
                    descriptor,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                updated = self.connection.execute(
                    "UPDATE cpk_activity_events SET payload = "
                    "jsonb_set(payload, '{failure}', %s::jsonb, false) "
                    "WHERE event_id = %s",
                    (encoded, persisted_attempt.latest_transition_event.event_id),
                )
                self.assertEqual(updated.rowcount, 1)
                before = (
                    self.attempt_snapshot(),
                    self.connection.execute(
                        "SELECT payload->'failure' FROM cpk_activity_events "
                        "WHERE event_id = %s",
                        (persisted_attempt.latest_transition_event.event_id,),
                    ).fetchone(),
                )
                ids = Sequence("near-legacy-must-not-allocate")

                with self.reject_fold_database_observation(
                    "near-legacy replay sampled database time"
                ):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service_with_id_factory(ids).execute(command)

                self.assertEqual(str(caught.exception), REPLAY_ERROR)
                self.assert_safe_error(
                    caught.exception,
                    "bounded-canary",
                    "provider-observation",
                    "f" * 64,
                    "runtime.effect-uncertain",
                    "bounded changed message",
                )
                self.assertEqual(ids.calls, [])
                self.assertEqual(
                    (
                        self.attempt_snapshot(),
                        self.connection.execute(
                            "SELECT payload->'failure' FROM cpk_activity_events "
                            "WHERE event_id = %s",
                            (persisted_attempt.latest_transition_event.event_id,),
                        ).fetchone(),
                    ),
                    before,
                )

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
