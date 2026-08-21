from __future__ import annotations

import unittest
from unittest import mock

from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from tests.effect_attempt_start_fixture import ExistingAttempt, NewlyStarted
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_start_fixture import (
    PostgresEffectAttemptStartFixture,
)
from tests.postgres_effect_attempt_intent_store_fixture import (
    EffectAttemptIntentStore,
)


class PostgresEffectAttemptStartFirstReplayTests(
    PostgresEffectAttemptStartFixture,
    unittest.TestCase,
):
    def test_forward_and_compensation_first_start_commit_complete_truth(self) -> None:
        for compensation in (False, True):
            with self.subTest(compensation=compensation):
                self.reset_start_truth(compensation=compensation)
                before = self.attempt_snapshot()
                service, sequence = self.start_service_with_sequence(
                    f"effect-{int(compensation)}-start"
                )
                intent = self.intent(compensation=compensation)
                command = self.start_command(
                    intent=intent,
                    transition=self.transition(intent=intent),
                )

                result = service.execute(command)

                self.assertIsInstance(result, NewlyStarted)
                self.assertEqual(sequence.calls, [f"effect-{int(compensation)}-start"])
                attempt = result.attempt
                self.assertEqual(attempt.state.identity.run_id.value, "run-a")
                self.assertEqual(attempt.state.identity.activity_id, "start-runtime")
                self.assertEqual(attempt.state.identity.attempt, 1)
                self.assertEqual(
                    attempt.state.request_fingerprint,
                    command.transition.request_fingerprint,
                )
                self.assertEqual(attempt.state.fence.worker_id, "worker-a")
                self.assertEqual(attempt.state.fence.generation, 7)
                self.assertIsNone(attempt.state.prior_attempt)
                self.assertEqual(
                    attempt.original_start_event.kind,
                    ActivityEventKind.STEP_COMPENSATION_STARTED
                    if compensation
                    else ActivityEventKind.STEP_STARTED,
                )
                self.assertEqual(attempt.original_start_event.run_id, "run-a")
                self.assertEqual(
                    attempt.original_start_event.ordinal,
                    7 if compensation else 3,
                )
                self.assertEqual(
                    attempt.original_start_event,
                    attempt.latest_transition_event,
                )
                self.assertEqual(
                    attempt.original_start_event.evidence,
                    self.evidence_for(attempt.state),
                )
                with self.unit_of_work() as unit_of_work:
                    self.assertEqual(
                        unit_of_work.stores.effect_attempts.get(
                            attempt.state.identity
                        ),
                        attempt,
                    )
                self.assertNotEqual(self.attempt_snapshot(), before)

    def test_exact_restart_replay_is_observation_only_after_expiry(self) -> None:
        current = self.persisted_started()
        self.expire_claim()
        before = self.attempt_snapshot()
        ids = Sequence("replay-must-not-allocate")

        with self.reject_database_observation("replay sampled database time"):
            replay = self.start_service_with_id_factory(ids).execute(
                self.start_command()
            )

        self.assertEqual(replay, ExistingAttempt(current))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_replay_returns_evolved_attempt_and_immutable_original_event(self) -> None:
        started = self.persisted_started()
        evolved = self.fold_persisted_attempt(started, story="succeeded")
        before = self.attempt_snapshot()
        ids = Sequence("folded-replay-must-not-allocate")

        with self.reject_database_observation("folded replay sampled database time"):
            replay = self.start_service_with_id_factory(ids).execute(
                self.start_command()
            )

        self.assertEqual(replay, ExistingAttempt(evolved))
        self.assertEqual(
            replay.attempt.original_start_event,
            started.original_start_event,
        )
        self.assertNotEqual(
            replay.attempt.latest_transition_event,
            replay.attempt.original_start_event,
        )
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_historical_attempt_replay_survives_a_lawful_linked_retry(self) -> None:
        started = self.persisted_started()
        self.add_lawful_linked_retry()
        before = self.attempt_snapshot()
        ids = Sequence("historical-replay-must-not-allocate")

        with self.reject_database_observation(
            "historical attempt replay sampled database time"
        ):
            replay = self.start_service_with_id_factory(ids).execute(
                self.start_command()
            )

        self.assertEqual(replay, ExistingAttempt(started))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT run_id FROM cpk_activity_runs "
                "WHERE request_id='request-a' ORDER BY attempt"
            ).fetchall(),
            [("run-a",), ("run-b",)],
        )

    def test_existing_replay_uses_request_then_run_then_attempt_reads(self) -> None:
        self.assertIsNotNone(
            EffectAttemptIntentStore,
            "effect-attempt intent replay store is missing",
        )
        self.persisted_started()
        calls: list[str] = []
        original_request = PostgresExecutionStore.get_request_for_update
        original_run = PostgresExecutionStore.get_run_for_request_for_update
        original_attempt = EffectAttemptStore.get_for_update
        original_intent = EffectAttemptIntentStore.get if EffectAttemptIntentStore else None

        def request(store, request_id):
            calls.append("request")
            return original_request(store, request_id)

        def run(store, request_id, run_id):
            calls.append("run")
            return original_run(store, request_id, run_id)

        def attempt(store, identity):
            calls.append("attempt")
            return original_attempt(store, identity)

        def intent(store, identity):
            calls.append("intent")
            return original_intent(store, identity)

        with mock.patch.object(
            PostgresExecutionStore,
            "get_request_for_update",
            request,
        ), mock.patch.object(
            PostgresExecutionStore,
            "get_run_for_request_for_update",
            run,
        ), mock.patch.object(
            EffectAttemptStore,
            "get_for_update",
            attempt,
        ), mock.patch.object(
            EffectAttemptIntentStore,
            "get",
            intent,
        ):
            self.start_service("unused").execute(self.start_command())

        self.assertEqual(calls, ["request", "run", "attempt", "intent"])

    def test_first_start_locks_complete_truth_before_clock_and_uses_db_time(self) -> None:
        calls: list[str] = []
        observations = []
        original_request = PostgresExecutionStore.get_request_for_update
        original_run = PostgresExecutionStore.get_run_for_request_for_update
        original_attempt = EffectAttemptStore.get_for_update
        original_latest = PostgresExecutionStore.get_latest_run_for_request_for_update
        original_observe = PostgresExecutionStore.observe_request_lease_for_update
        original_ordinal = PostgresExecutionStore.next_event_ordinal

        def request(store, request_id):
            calls.append("request")
            return original_request(store, request_id)

        def run(store, request_id, run_id):
            calls.append("run")
            return original_run(store, request_id, run_id)

        def attempt(store, identity):
            calls.append("attempt")
            return original_attempt(store, identity)

        def latest(store, request_id):
            calls.append("latest")
            return original_latest(store, request_id)

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
            return "ordered-effect-start"

        with mock.patch.object(
            PostgresExecutionStore,
            "get_request_for_update",
            request,
        ), mock.patch.object(
            PostgresExecutionStore,
            "get_run_for_request_for_update",
            run,
        ), mock.patch.object(
            EffectAttemptStore,
            "get_for_update",
            attempt,
        ), mock.patch.object(
            PostgresExecutionStore,
            "get_latest_run_for_request_for_update",
            latest,
        ), mock.patch.object(
            PostgresExecutionStore,
            "observe_request_lease_for_update",
            observe,
        ), mock.patch.object(
            PostgresExecutionStore,
            "next_event_ordinal",
            ordinal,
        ):
            result = self.start_service_with_id_factory(identity).execute(
                self.start_command()
            )

        self.assertIsInstance(result, NewlyStarted)
        self.assertEqual(
            calls,
            [
                "request",
                "run",
                "attempt",
                "latest",
                "request",
                "clock",
                "ordinal",
                "identity",
            ],
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(
            result.attempt.original_start_event.occurred_at,
            observations[0].observed_at,
        )

    def test_complete_result_and_event_exist_before_attempt_insert(self) -> None:
        self.assertIsNotNone(
            EffectAttemptIntentStore,
            "effect-attempt intent write store is missing",
        )
        calls: list[str] = []
        appended = []
        original_event = PostgresExecutionStore.add_event
        original_evidence = EffectAttemptIntentStore.insert
        original_insert = EffectAttemptStore.insert_absent

        def add_event(store, event):
            calls.append("event")
            appended.append(event)
            return original_event(store, event)

        def insert(store, record):
            calls.append("attempt")
            self.assertEqual(NewlyStarted(record).attempt, record)
            self.assertEqual(appended, [record.original_start_event])
            return original_insert(store, record)

        def insert_evidence(store, record):
            calls.append("evidence")
            self.assertEqual(appended, [record.original_start_event])
            return original_evidence(store, record)

        with mock.patch.object(
            PostgresExecutionStore,
            "add_event",
            add_event,
        ), mock.patch.object(
            EffectAttemptIntentStore,
            "insert",
            insert_evidence,
        ), mock.patch.object(
            EffectAttemptStore,
            "insert_absent",
            insert,
        ):
            result = self.start_service("complete-before-write").execute(
                self.start_command()
            )

        self.assertIsInstance(result, NewlyStarted)
        self.assertEqual(calls, ["event", "evidence", "attempt"])


if __name__ == "__main__":
    unittest.main()
