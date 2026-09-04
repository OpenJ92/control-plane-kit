from __future__ import annotations

import concurrent.futures
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
)
from tests.postgres_effect_attempt_reconciliation_fixture import (
    PostgresEffectAttemptReconciliationFixture,
    RecordingObserver,
)


class _MutatingObserver(RecordingObserver):
    def __init__(self, result, mutation) -> None:
        super().__init__(result)
        self._mutation = mutation

    def observe(self, request, authority):
        self._mutation()
        return super().observe(request, authority)


class PostgresEffectAttemptReconciliationConcurrencyTests(
    PostgresEffectAttemptReconciliationFixture,
    unittest.TestCase,
):
    @staticmethod
    def _results(*calls):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = tuple(pool.submit(call) for call in calls)
            values = []
            for future in futures:
                try:
                    values.append(future.result(timeout=20))
                except (
                    EffectAttemptFoldConflict,
                    EffectAttemptFoldDenied,
                    EffectAttemptReconciliationConflict,
                    EffectAttemptReconciliationDenied,
                ) as error:
                    values.append(error)
            return tuple(values)

    def test_authority_replacement_and_expiry_during_observation_have_one_decision(self) -> None:
        for race in ("replacement", "expiry"):
            with self.subTest(race=race):
                story = self.observed_story()
                current, intent, record, authority = self.seed_reconciliation_source(story)
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)

                def mutation():
                    if race == "expiry":
                        with self.unit_of_work() as unit_of_work:
                            unit_of_work.stores.connection.execute(
                                "UPDATE cpk_execution_requests SET lease_expires_at="
                                "'2000-01-01T00:00:00Z' WHERE request_id='request-a'"
                            )
                            unit_of_work.commit()
                    else:
                        with self.unit_of_work() as unit_of_work:
                            unit_of_work.stores.runtime_authorities.revoke(
                                intent.source.workspace_id,
                                intent.authority_ref,
                            )
                            unit_of_work.commit()
                        replacement = self.register_runtime_authority(intent, remote=True)
                        self.assertNotEqual(
                            replacement.registration_id,
                            authority.registration_id,
                        )

                observer = _MutatingObserver(
                    self.observation_for(story, current, intent),
                    mutation,
                )
                before_event = current.original_start_event
                with self.assertRaises(EffectAttemptReconciliationDenied):
                    self.reconciliation_service(observer, story).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
                self.assertEqual(self.current_attempt().original_start_event, before_event)
                with self.unit_of_work() as unit_of_work:
                    stored_intent = unit_of_work.stores.effect_attempt_intents.get(
                        current.state.identity
                    )
                self.assertEqual(stored_intent, record)

    def test_original_execution_or_recovery_and_observer_have_one_durable_winner(self) -> None:
        for ordinary in ("succeeded", "recovered-succeeded"):
            with self.subTest(ordinary=ordinary):
                story = self.observed_story()
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                command = self.reconciliation_command(
                    current,
                    scopes=(
                        PolicyScope.EXECUTION_OPERATE,
                        PolicyScope.SECRET_PROVIDER_USE,
                    ),
                )
                observer = self.observer_for(story, current, intent)
                results = self._results(
                    lambda: self.fold_service(f"ordinary-{ordinary}").execute(
                        self.fold_command(ordinary)
                    ),
                    lambda: self.reconciliation_service(
                        observer,
                        story,
                    ).execute(command),
                )
                self.assertEqual(sum(isinstance(value, NewlyFolded) for value in results), 1)
                self.assertEqual(
                    sum(
                        isinstance(
                            value,
                            (EffectAttemptFoldConflict, EffectAttemptReconciliationConflict),
                        )
                        for value in results
                    ),
                    1,
                )

    def test_identical_incompatible_and_unrelated_reconciliation_do_not_interfere(self) -> None:
        for relation in ("identical", "incompatible"):
            with self.subTest(relation=relation):
                story = self.observed_story()
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                second_story = (
                    story
                    if relation == "identical"
                    else self.observed_story("observed-failed")
                )
                command = self.reconciliation_command(
                    current,
                    scopes=(
                        PolicyScope.EXECUTION_OPERATE,
                        PolicyScope.SECRET_PROVIDER_USE,
                    ),
                )
                results = self._results(
                    lambda: self.reconciliation_service(
                        self.observer_for(story, current, intent),
                        story,
                    ).execute(command),
                    lambda: self.reconciliation_service(
                        self.observer_for(second_story, current, intent),
                        second_story,
                    ).execute(command),
                )
                self.assertEqual(sum(isinstance(value, NewlyFolded) for value in results), 1)
                if relation == "identical":
                    self.assertEqual(
                        sum(isinstance(value, ExistingFold) for value in results),
                        1,
                    )
                else:
                    self.assertEqual(
                        sum(isinstance(value, EffectAttemptReconciliationConflict) for value in results),
                        1,
                    )

        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(story)
        uses = self.required_secret_uses(current, intent, authority)
        self.admit_secret_uses(uses)
        self.seed_foreign_run()
        unrelated = self.seed_foreign_attempt()
        before = self.non_advancement_snapshot()
        blocker = psycopg.connect(self.database_url)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            blocker.execute(
                "SELECT run_id FROM cpk_effect_attempts WHERE run_id='run-foreign' "
                "AND activity_id='start-runtime' AND attempt=1 FOR UPDATE"
            )
            future = executor.submit(
                self.reconciliation_service(
                    self.observer_for(story, current, intent),
                    story,
                ).execute,
                self.reconciliation_command(
                    current,
                    scopes=(
                        PolicyScope.EXECUTION_OPERATE,
                        PolicyScope.SECRET_PROVIDER_USE,
                    ),
                ),
            )
            result = future.result(timeout=5)
        except concurrent.futures.TimeoutError:
            self.fail("unrelated attempt lock blocked reconciliation")
        finally:
            blocker.rollback()
            blocker.close()
            executor.shutdown(wait=True, cancel_futures=True)
        self.assertIsInstance(result, NewlyFolded)
        with self.unit_of_work() as unit_of_work:
            unrelated_after = unit_of_work.stores.effect_attempts.get(
                unrelated.state.identity
            )
        self.assertEqual(unrelated_after, unrelated)
        self.assertEqual(self.non_advancement_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
