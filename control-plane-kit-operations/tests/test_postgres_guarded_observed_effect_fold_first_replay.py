from __future__ import annotations

from contextlib import ExitStack
from dataclasses import fields, replace
import unittest
from unittest import mock

from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.postgres.effect_attempt_intent_store import (
    EffectAttemptIntentStore,
)
from control_plane_kit_operations.postgres.effect_outcome_store import (
    EffectAttemptOutcomeStore,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.postgres_effect_attempt_fold_fixture import (
    AUTHORITY_ERROR,
    INVALID_TRUTH_ERROR,
    REPLAY_ERROR,
)
from tests.postgres_guarded_observed_effect_fold_fixture import (
    PostgresExecutionStore,
    PostgresGuardedObservedEffectFoldFixture,
    Sequence,
)


def _forge_exact(candidate, **changes):
    forged = object.__new__(type(candidate))
    for item in fields(candidate):
        object.__setattr__(
            forged,
            item.name,
            changes.get(item.name, getattr(candidate, item.name)),
        )
    return forged


class PostgresGuardedObservedEffectFoldFirstReplayTests(
    PostgresGuardedObservedEffectFoldFixture,
    unittest.TestCase,
):
    def test_control_complete_intent_outcome_and_schema_world_is_lawful(self) -> None:
        story = self.observed_story()
        current, _intent, expected = self.seed_guarded_source(story)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.effect_attempt_intents.get(current.state.identity),
                expected,
            )
        attempt, outcome = self.persist_terminal(story)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.effect_outcomes.get(
                    attempt.state.identity,
                    attempt.latest_transition_event.event_id,
                ),
                outcome,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempt_intents"
            ).fetchone(),
            (1,),
        )

    def test_control_ordinary_execution_and_recovery_remain_total(self) -> None:
        original = PostgresExecutionStore.observe_request_lease_for_update

        def expired(store, request_id):
            return replace(original(store, request_id), expired=True)

        for story in ("succeeded", "recovered-succeeded"):
            with self.subTest(story=story):
                self.seed_fold_source(story)
                with mock.patch.object(
                    PostgresExecutionStore,
                    "observe_request_lease_for_update",
                    expired,
                ):
                    result = self.fold_service(f"ordinary-{story}").execute(
                        self.fold_command(story)
                    )
                self.assertIsInstance(result, NewlyFolded)
                with self.reject_fold_database_observation(
                    "ordinary replay sampled lease time"
                ):
                    replay = self.fold_service("must-not-allocate").execute(
                        self.fold_command(story)
                    )
                self.assertIsInstance(replay, ExistingFold)

    def test_terminal_profile_replays_are_exact_for_same_and_newer_claims(self) -> None:
        for profile in ("execution", "observed"):
            for newer in (False, True):
                for expired in (False, True):
                    with self.subTest(profile=profile, newer=newer, expired=expired):
                        story = self.outcome_story(
                            f"{profile}-succeeded",
                            compensation=False,
                        )
                        attempt, outcome = self.persist_terminal(story)
                        worker_id, generation = ("worker-b", 8) if newer else ("worker-a", 7)
                        if newer:
                            self.replace_current_claim(
                                worker_id=worker_id,
                                generation=generation,
                            )
                        if expired:
                            self.expire_claim()
                        command = self.fold_command(
                            story,
                            authority=self.authority(worker_id),
                            fence=self.fence(worker_id, generation),
                        )
                        ids = Sequence("replay-must-not-allocate")
                        service = self.fold_service_with_id_factory(ids)
                        escaped = None
                        try:
                            with self.reject_fold_database_observation(
                                "terminal replay sampled lease time"
                            ):
                                result = (
                                    service.execute(command)
                                    if profile == "execution"
                                    else service.execute_observed(
                                        self.guarded_observed_command(
                                            story,
                                            current=attempt,
                                            fold=command,
                                        )
                                    )
                                )
                        except Exception as error:
                            escaped = error
                        if escaped is not None:
                            self.fail("terminal replay did not return the exact result")
                        self.assertEqual(result, ExistingFold(attempt, outcome))
                        self.assertEqual(ids.calls, [])

    def test_authorized_recovery_lineage_and_cross_profile_precedence_is_exact(self) -> None:
        rows = (
            ("recovery", "observed", "worker-a", 7, REPLAY_ERROR),
            ("cross-observed-over-execution", "execution", "worker-a", 7, REPLAY_ERROR),
            ("lower", "observed", "worker-a", 6, REPLAY_ERROR),
            ("equal-other", "observed", "worker-b", 7, REPLAY_ERROR),
        )
        for label, profile, worker_id, generation, message in rows:
            with self.subTest(row=label):
                story = self.outcome_story(f"{profile}-succeeded")
                if label == "recovery":
                    self.seed_fold_source("recovered-succeeded")
                    current = self.current_attempt()
                else:
                    current, _outcome = self.persist_terminal(story)
                if (worker_id, generation) != ("worker-a", 7):
                    self.replace_current_claim(worker_id=worker_id, generation=generation)
                command = self.fold_command(
                    self.observed_story(),
                    authority=self.authority(worker_id),
                    fence=self.fence(worker_id, generation),
                )
                before = self.complete_snapshot()
                with self.assertRaises(EffectAttemptFoldConflict) as caught:
                    self.fold_service("must-not-allocate").execute_observed(
                        self.guarded_observed_command(
                            self.observed_story(),
                            current=current,
                            fold=command,
                        )
                    )
                self.assertEqual(str(caught.exception), message)
                self.assertEqual(self.complete_snapshot(), before)

        self._assert_stale_claim_confidentiality_matrix()
        self._assert_observed_outcome_requires_guarded_entrypoint()

    def _assert_observed_outcome_requires_guarded_entrypoint(self) -> None:
        story = self.observed_story()
        for world in ("fresh", "terminal"):
            with self.subTest(ordinary_observed=world):
                if world == "fresh":
                    current, _intent, _record = self.seed_guarded_source(story)
                    command = self.guarded_observed_command(
                        story,
                        current=current,
                    ).fold
                else:
                    _current, _outcome = self.persist_terminal(story)
                    command = self.fold_command(story)
                requests = []
                original = PostgresExecutionStore.get_request_for_update

                def request(store, request_id):
                    value = original(store, request_id)
                    requests.append(value)
                    return value

                ids = Sequence("ordinary-observed-must-not-allocate")
                with ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.object(
                            PostgresExecutionStore,
                            "get_request_for_update",
                            request,
                        )
                    )
                    for patcher in self.forbidden_lower_interactions(
                        "ordinary observed outcome crossed the guarded boundary"
                    ):
                        stack.enter_context(patcher)
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service_with_id_factory(ids).execute(command)
                self.assertEqual(str(caught.exception), REPLAY_ERROR)
                self.assertEqual(len(requests), 1)
                self.assertEqual(ids.calls, [])

    def _assert_stale_claim_confidentiality_matrix(self) -> None:
        worlds = ("recovery", "terminal-matching", "terminal-cross", "fresh")
        for world in worlds:
            for worker_id, generation in (("worker-a", 7), ("worker-c", 8)):
                with self.subTest(world=world, worker=worker_id, generation=generation):
                    story = self.observed_story()
                    if world == "recovery":
                        self.seed_fold_source("recovered-succeeded")
                        current = self.current_attempt()
                    elif world.startswith("terminal"):
                        stored = (
                            story
                            if world == "terminal-matching"
                            else self.outcome_story("execution-succeeded")
                        )
                        current, _ = self.persist_terminal(stored)
                    else:
                        current, _intent, _record = self.seed_guarded_source(story)
                    intent = self.persisted_intent(current)
                    runtime_authority = self.register_runtime_authority(intent)
                    self.replace_current_claim(worker_id="worker-b", generation=8)
                    command = self.fold_command(
                        story,
                        authority=self.authority(worker_id),
                        fence=self.fence(worker_id, generation),
                    )
                    ids = Sequence("confidentiality-must-not-allocate")
                    with ExitStack() as stack:
                        for patcher in self.forbidden_lower_interactions(
                            "stale claimant crossed a lower boundary"
                        ):
                            stack.enter_context(patcher)
                        with self.assertRaises(EffectAttemptFoldDenied) as caught:
                            self.fold_service_with_id_factory(ids).execute_observed(
                                self.guarded_observed_command(
                                    story,
                                    current=current,
                                    intent=intent,
                                    runtime_authority=runtime_authority,
                                    fold=command,
                                    register=False,
                                )
                            )
                    self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
                    self.assertEqual(ids.calls, [])

    def test_durable_evidence_faults_conflict_before_lower_effects(self) -> None:
        for profile in ("execution", "observed"):
            story = self.outcome_story(f"{profile}-succeeded")
            for fault in ("missing", "corrupt", "foreign", "drifted", "incomplete", "reordered"):
                with self.subTest(profile=profile, fault=fault):
                    attempt, outcome = self.persist_terminal(story)
                    replacement = outcome
                    side_effect = KeyError("missing") if fault == "missing" else None
                    if fault == "corrupt":
                        side_effect = OperationsRecordError("corrupt")
                    elif fault == "foreign":
                        replacement = _forge_exact(
                            outcome,
                            workspace_id="workspace-foreign",
                        )
                    elif fault in {"drifted", "incomplete", "reordered"}:
                        if fault == "drifted":
                            replacement = _forge_exact(
                                outcome,
                                attempt=self.record("failed"),
                            )
                        elif fault == "incomplete":
                            replacement = _forge_exact(
                                outcome,
                                endpoint_observations=(
                                    outcome.endpoint_observations[:-1]
                                ),
                            )
                        else:
                            replacement = _forge_exact(
                                outcome,
                                endpoint_observations=tuple(
                                    reversed(outcome.endpoint_observations)
                                ),
                            )
                    with mock.patch.object(
                        EffectAttemptOutcomeStore,
                        "get",
                        side_effect=side_effect,
                        return_value=replacement,
                    ):
                        command = self.fold_command(story)
                        service = self.fold_service("must-not-allocate")
                        observed_error = None
                        try:
                            if profile == "execution":
                                service.execute(command)
                            else:
                                service.execute_observed(
                                    self.guarded_observed_command(
                                        story,
                                        current=attempt,
                                        fold=command,
                                    )
                                )
                        except Exception as error:
                            observed_error = error
                        if observed_error is None:
                            self.fail("invalid durable evidence was accepted")
                        self.assertIs(
                            type(observed_error),
                            EffectAttemptFoldConflict,
                            "invalid durable evidence escaped the fixed category",
                        )
                        self.assertTrue(
                            str(observed_error) == INVALID_TRUTH_ERROR,
                            "invalid durable evidence escaped the fixed message",
                        )

    def test_fresh_guarded_observation_is_total_for_all_variants_and_phases(self) -> None:
        for story in self.observed_stories():
            with self.subTest(story=story.name, compensation=story.compensation):
                current, _intent, _record = self.seed_guarded_source(story)
                event_id = f"guarded-{story.name}-{int(story.compensation)}"
                service, ids = self.observed_service(
                    *self.fold_ids_for_story(event_id, story)
                )
                result = service.execute_observed(
                    self.guarded_observed_command(story, current=current)
                )
                self.assertIsInstance(result, NewlyFolded)
                self.assertEqual(ids.calls, list(self.fold_ids_for_story(event_id, story)))
                self.assertEqual(result.outcome_record.outcome, self.fold_outcome(story))


if __name__ == "__main__":
    unittest.main()
