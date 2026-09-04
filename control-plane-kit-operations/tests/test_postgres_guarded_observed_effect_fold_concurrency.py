from __future__ import annotations

import concurrent.futures
import unittest

import psycopg

from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    ExistingFold,
    NewlyFolded,
)
from tests.postgres_guarded_observed_effect_fold_fixture import (
    PostgresGuardedObservedEffectFoldFixture,
    Sequence,
)


class PostgresGuardedObservedEffectFoldConcurrencyTests(
    PostgresGuardedObservedEffectFoldFixture,
    unittest.TestCase,
):
    def _concurrent(self, *calls):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = tuple(executor.submit(call) for call in calls)
            results = []
            for future in futures:
                try:
                    results.append(future.result(timeout=15))
                except (EffectAttemptFoldConflict, EffectAttemptFoldDenied) as error:
                    results.append(error)
            return tuple(results)

    def test_authority_replacement_revocation_and_expiry_have_one_guarded_decision(self) -> None:
        for race in ("replacement", "revocation", "expiry"):
            with self.subTest(race=race):
                story = self.observed_story()
                current, intent, record = self.seed_guarded_source(story)
                authority = self.register_runtime_authority(intent)
                command = self.guarded_observed_command(
                    story,
                    current=current,
                    intent=intent,
                    intent_record=record,
                    runtime_authority=authority,
                    register=False,
                )

                def mutate():
                    with self.unit_of_work() as unit_of_work:
                        if race == "expiry":
                            unit_of_work.stores.connection.execute(
                                "UPDATE cpk_execution_requests SET lease_expires_at="
                                "'2000-01-01T00:00:00Z' WHERE request_id='request-a'"
                            )
                        else:
                            unit_of_work.stores.runtime_authorities.revoke(
                                intent.source.workspace_id,
                                intent.authority_ref,
                            )
                        unit_of_work.commit()
                    if race == "replacement":
                        replacement = self.register_runtime_authority(
                            intent,
                            remote=True,
                        )
                        self.assertNotEqual(
                            replacement.registration_id,
                            authority.registration_id,
                        )
                    return race

                results = self._concurrent(
                    lambda: self.fold_service(f"race-{race}").execute_observed(command),
                    mutate,
                )
                folds = tuple(value for value in results if value != race)
                self.assertEqual(len(folds), 1)
                self.assertIsInstance(
                    folds[0],
                    (NewlyFolded, EffectAttemptFoldDenied),
                )
                self.assertLessEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM cpk_effect_attempt_outcomes"
                    ).fetchone()[0],
                    1,
                )

    def test_original_execution_or_recovery_and_observation_have_one_winner(self) -> None:
        for ordinary in ("succeeded", "recovered-succeeded"):
            with self.subTest(ordinary=ordinary):
                story = self.observed_story()
                current, intent, record = self.seed_guarded_source(story)
                authority = self.register_runtime_authority(intent)
                guarded = self.guarded_observed_command(
                    story,
                    current=current,
                    intent=intent,
                    intent_record=record,
                    runtime_authority=authority,
                    register=False,
                )
                results = self._concurrent(
                    lambda: self.fold_service(f"ordinary-{ordinary}").execute(
                        self.fold_command(ordinary)
                    ),
                    lambda: self.fold_service("observed-racer").execute_observed(guarded),
                )
                self.assertEqual(sum(isinstance(v, NewlyFolded) for v in results), 1)
                self.assertEqual(
                    sum(isinstance(v, EffectAttemptFoldConflict) for v in results),
                    1,
                )

    def test_identical_incompatible_and_unrelated_observations_are_deterministic(self) -> None:
        for relation in ("identical", "incompatible"):
            with self.subTest(relation=relation):
                story = self.observed_story()
                current, intent, record = self.seed_guarded_source(story)
                authority = self.register_runtime_authority(intent)
                first = self.guarded_observed_command(
                    story,
                    current=current,
                    intent=intent,
                    intent_record=record,
                    runtime_authority=authority,
                    register=False,
                )
                second_story = (
                    story
                    if relation == "identical"
                    else self.observed_story("observed-failed")
                )
                second = self.guarded_observed_command(
                    second_story,
                    current=current,
                    intent=intent,
                    intent_record=record,
                    runtime_authority=authority,
                    register=False,
                )
                results = self._concurrent(
                    lambda: self.fold_service("observed-left").execute_observed(first),
                    lambda: self.fold_service("observed-right").execute_observed(second),
                )
                self.assertEqual(sum(isinstance(v, NewlyFolded) for v in results), 1)
                if relation == "identical":
                    self.assertEqual(sum(isinstance(v, ExistingFold) for v in results), 1)
                else:
                    self.assertEqual(
                        sum(isinstance(v, EffectAttemptFoldConflict) for v in results),
                        1,
                    )

        story = self.observed_story()
        current, intent, record = self.seed_guarded_source(
            story,
            authority_ref=False,
        )
        self.seed_foreign_run()
        unrelated = self.seed_foreign_attempt()
        with self.unit_of_work() as unit_of_work:
            unrelated_before = unit_of_work.stores.effect_attempts.get(
                unrelated.state.identity
            )
        blocker = psycopg.connect(self.database_url)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            blocker.execute(
                "SELECT run_id FROM cpk_effect_attempts WHERE run_id='run-foreign' "
                "AND activity_id='start-runtime' AND attempt=1 FOR UPDATE"
            )
            future = executor.submit(
                self.fold_service("unrelated-observation").execute_observed,
                self.guarded_observed_command(
                    story,
                    current=current,
                    intent=intent,
                    intent_record=record,
                    runtime_authority=None,
                    register=False,
                ),
            )
            try:
                result = future.result(timeout=5)
            except concurrent.futures.TimeoutError:
                self.fail("unrelated attempt lock blocked guarded observation")
        finally:
            blocker.rollback()
            blocker.close()
            executor.shutdown(wait=True, cancel_futures=True)
        self.assertIsInstance(result, NewlyFolded)
        with self.unit_of_work() as unit_of_work:
            unrelated_after = unit_of_work.stores.effect_attempts.get(
                unrelated.state.identity
            )
        self.assertEqual(unrelated_after, unrelated_before)


if __name__ == "__main__":
    unittest.main()
