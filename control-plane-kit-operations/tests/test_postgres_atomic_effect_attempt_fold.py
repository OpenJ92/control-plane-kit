from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    ExistingFold,
    NewlyFolded,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.postgres import (
    PostgresExecutionStore,
    PostgresUnitOfWork,
)
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
    ObservationRecord,
    OperationsRecordError,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_fold_fixture import (
    INVALID_TRUTH_ERROR,
    PostgresEffectAttemptFoldFixture,
    REPLAY_ERROR,
    SERIALIZATION_ERROR,
)


class PostgresAtomicEffectAttemptFoldTests(
    PostgresEffectAttemptFoldFixture,
    unittest.TestCase,
):
    def test_all_twenty_direct_rows_commit_and_restart_replay_exact_evidence(
        self,
    ) -> None:
        stories = self.stories()
        self.assertEqual(len(stories), 20)
        for story in stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                current = self.seed_fold_source(story)
                command = self.fold_command(story)
                event_id = f"atomic-{int(story.compensation)}-{story.name}"
                service, ids = self.fold_service_with_sequence(event_id)

                result = service.execute(command)

                self.assertIsInstance(result, NewlyFolded)
                expected = self.expected_outcome_record(
                    result.attempt,
                    command.outcome,
                    event_id=event_id,
                )
                self.assertEqual(result, NewlyFolded(result.attempt, expected))
                self.assertEqual(result.attempt.original_start_event, current.original_start_event)
                self.assertEqual(result.outcome_record, expected)
                self.assertEqual(ids.calls, list(self.fold_ids(event_id, command.outcome)))
                with self.unit_of_work() as fresh:
                    loaded = fresh.stores.effect_outcomes.get(
                        result.attempt.state.identity,
                        result.attempt.latest_transition_event.event_id,
                    )
                self.assertEqual(loaded, expected)

                before = self.attempt_snapshot()
                replay_ids = Sequence("replay-must-not-allocate")
                forbidden = AssertionError("exact replay entered mutation work")
                replay_lookups = []
                original_get = EffectAttemptOutcomeStore.get

                def get(store, identity, transition_event_id):
                    replay_lookups.append((identity, transition_event_id))
                    return original_get(store, identity, transition_event_id)

                with mock.patch.object(
                    EffectAttemptOutcomeStore,
                    "get",
                    get,
                ), mock.patch.object(
                    PostgresExecutionStore,
                    "observe_request_lease_for_update",
                    side_effect=forbidden,
                ), mock.patch.object(
                    PostgresExecutionStore,
                    "next_event_ordinal",
                    side_effect=forbidden,
                ), mock.patch.object(
                    PostgresExecutionStore,
                    "add_event",
                    side_effect=forbidden,
                ), mock.patch.object(
                    PostgresObservedStateStore,
                    "put",
                    side_effect=forbidden,
                ), mock.patch.object(
                    EffectAttemptOutcomeStore,
                    "insert",
                    side_effect=forbidden,
                ), mock.patch.object(
                    EffectAttemptStore,
                    "compare_and_set",
                    side_effect=forbidden,
                ):
                    replay = self.fold_service_with_id_factory(replay_ids).execute(
                        command
                    )
                self.assertEqual(
                    replay,
                    ExistingFold(result.attempt, result.outcome_record),
                )
                self.assertEqual(
                    replay_lookups,
                    [
                        (
                            result.attempt.state.identity,
                            result.attempt.latest_transition_event.event_id,
                        )
                    ],
                )
                self.assertEqual(replay_ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_recovery_first_fold_and_replay_never_touch_direct_outcome_stores(
        self,
    ) -> None:
        forbidden = AssertionError("recovery touched direct outcome evidence")
        for compensation in (False, True):
            for story in ("recovered-succeeded", "recovered-failed", "abandoned"):
                with self.subTest(story=story, compensation=compensation):
                    self.seed_fold_source(story, compensation=compensation)
                    ids = Sequence(f"recovery-{int(compensation)}-{story}")
                    with mock.patch.object(
                        PostgresObservedStateStore,
                        "put",
                        side_effect=forbidden,
                    ), mock.patch.object(
                        EffectAttemptOutcomeStore,
                        "insert",
                        side_effect=forbidden,
                    ), mock.patch.object(
                        EffectAttemptOutcomeStore,
                        "get",
                        side_effect=forbidden,
                    ):
                        first = self.fold_service_with_id_factory(ids).execute(
                            self.fold_command(story)
                        )
                    self.assertEqual(first, NewlyFolded(first.attempt, None))
                    self.assertEqual(ids.calls, [f"recovery-{int(compensation)}-{story}"])
                    self.assertEqual(
                        self.connection.execute(
                            "SELECT count(*) FROM cpk_effect_attempt_outcomes"
                        ).fetchone(),
                        (0,),
                    )

                    before = self.attempt_snapshot()
                    replay_ids = Sequence("recovery-replay-must-not-allocate")
                    with self.reject_fold_database_observation(
                        "recovery replay sampled database time"
                    ), mock.patch.object(
                        PostgresObservedStateStore,
                        "put",
                        side_effect=forbidden,
                    ), mock.patch.object(
                        EffectAttemptOutcomeStore,
                        "insert",
                        side_effect=forbidden,
                    ), mock.patch.object(
                        EffectAttemptOutcomeStore,
                        "get",
                        side_effect=forbidden,
                    ):
                        replay = self.fold_service_with_id_factory(replay_ids).execute(
                            self.fold_command(story)
                        )
                    self.assertEqual(replay, ExistingFold(first.attempt, None))
                    self.assertEqual(replay_ids.calls, [])
                    self.assertEqual(self.attempt_snapshot(), before)

    def test_recovered_current_rejects_original_direct_command_before_lookup(
        self,
    ) -> None:
        story = self.outcome_story("execution-uncertain", compensation=False)
        self.seed_fold_source(story)
        direct_command = self.fold_command(story)
        direct = self.fold_service("direct-uncertain").execute(direct_command)
        self.assertIsInstance(direct, NewlyFolded)
        retained = direct.outcome_record

        forbidden = AssertionError("recovery touched direct outcome evidence")
        with mock.patch.object(
            PostgresObservedStateStore,
            "put",
            side_effect=forbidden,
        ), mock.patch.object(
            EffectAttemptOutcomeStore,
            "insert",
            side_effect=forbidden,
        ), mock.patch.object(
            EffectAttemptOutcomeStore,
            "get",
            side_effect=forbidden,
        ):
            recovered = self.fold_service("recovered-after-direct").execute(
                self.fold_command("recovered-succeeded")
            )
        self.assertEqual(recovered, NewlyFolded(recovered.attempt, None))
        before = self.attempt_snapshot()
        ids = Sequence("incongruent-direct-must-not-allocate")
        with self.reject_fold_database_observation(
            "incongruent direct replay sampled database time"
        ), mock.patch.object(
            EffectAttemptOutcomeStore,
            "get",
            side_effect=forbidden,
        ), mock.patch.object(
            PostgresObservedStateStore,
            "put",
            side_effect=forbidden,
        ):
            with self.assertRaises(EffectAttemptFoldConflict) as caught:
                self.fold_service_with_id_factory(ids).execute(direct_command)
        self.assertEqual(str(caught.exception), REPLAY_ERROR)
        self.assert_safe_error(caught.exception)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)
        with self.unit_of_work() as fresh:
            self.assertEqual(
                fresh.stores.effect_outcomes.get(
                    retained.attempt.state.identity,
                    retained.attempt.latest_transition_event.event_id,
                ),
                retained,
            )

    def test_missing_corrupt_foreign_or_drifted_replay_evidence_is_conflict(
        self,
    ) -> None:
        mutations = (
            "missing",
            "preimage",
            "membership",
            "membership-order",
            "observation",
            "foreign",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                story = self.outcome_story("execution-succeeded", compensation=False)
                self.seed_fold_source(story)
                command = self.fold_command(story)
                first = self.fold_service(f"first-{mutation}").execute(command)
                self.assertIsInstance(first, NewlyFolded)
                record = first.outcome_record
                patched_get = None
                if mutation == "missing":
                    self.connection.execute(
                        "DELETE FROM cpk_effect_attempt_outcome_observations"
                    )
                    self.connection.execute("DELETE FROM cpk_effect_attempt_outcomes")
                elif mutation == "preimage":
                    self.connection.execute(
                        "UPDATE cpk_effect_attempt_outcomes SET preimage=%s",
                        (b'{"kind":"private-row-canary"}',),
                    )
                elif mutation == "membership":
                    self.connection.execute(
                        "DELETE FROM cpk_effect_attempt_outcome_observations "
                        "WHERE position=1"
                    )
                elif mutation == "membership-order":
                    rows = self.connection.execute(
                        "SELECT observation_count, position, observation_id "
                        "FROM cpk_effect_attempt_outcome_observations "
                        "ORDER BY position"
                    ).fetchall()
                    self.assertEqual(len(rows), 2)
                    self.connection.execute(
                        "DELETE FROM cpk_effect_attempt_outcome_observations"
                    )
                    identity = record.attempt.state.identity
                    for position, source in enumerate(reversed(rows)):
                        self.connection.execute(
                            "INSERT INTO cpk_effect_attempt_outcome_observations "
                            "(run_id, activity_id, attempt, workspace_id, "
                            "observation_count, position, observation_id) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (
                                identity.run_id.value,
                                identity.activity_id,
                                identity.attempt,
                                record.workspace_id,
                                source[0],
                                position,
                                source[2],
                            ),
                        )
                elif mutation == "observation":
                    self.connection.execute(
                        "UPDATE cpk_observations SET evidence="
                        "'{\"runtime_endpoint\":{\"candidate\":\"drift\"}}'::jsonb "
                        "WHERE observation_id=%s",
                        (record.endpoint_observations[0].observation_id,),
                    )
                else:
                    foreign_story = self.outcome_story(
                        "observed-absent",
                        compensation=False,
                    )
                    foreign_outcome = self.outcome_for(foreign_story)
                    foreign_record = EffectAttemptOutcomeRecord(
                        "workspace-a",
                        foreign_outcome,
                        foreign_story.attempt,
                        (),
                    )
                    patched_get = mock.patch.object(
                        EffectAttemptOutcomeStore,
                        "get",
                        return_value=foreign_record,
                    )

                before = self.attempt_snapshot()
                ids = Sequence("corrupt-replay-must-not-allocate")
                forbidden = AssertionError("corrupt replay entered mutation work")
                get_context = patched_get or nullcontext()
                with get_context, self.reject_fold_database_observation(
                    "corrupt replay sampled database time"
                ), mock.patch.object(
                    PostgresExecutionStore,
                    "next_event_ordinal",
                    side_effect=forbidden,
                ), mock.patch.object(
                    PostgresExecutionStore,
                    "add_event",
                    side_effect=forbidden,
                ), mock.patch.object(
                    PostgresObservedStateStore,
                    "put",
                    side_effect=forbidden,
                ), mock.patch.object(
                    EffectAttemptStore,
                    "compare_and_set",
                    side_effect=forbidden,
                ):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service_with_id_factory(ids).execute(command)
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assert_safe_error(
                    caught.exception,
                    "private-row-canary",
                    "drift",
                )
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_replay_row_error_translation_is_narrow_and_internal_faults_are_raw(
        self,
    ) -> None:
        story = self.outcome_story("execution-succeeded", compensation=False)
        self.seed_fold_source(story)
        command = self.fold_command(story)
        self.fold_service("first-row-errors").execute(command)
        for error in (
            KeyError("missing-row-canary"),
            OperationsRecordError("invalid-row-canary"),
        ):
            with self.subTest(expected=type(error).__name__):
                with mock.patch.object(
                    EffectAttemptOutcomeStore,
                    "get",
                    side_effect=error,
                ), self.reject_fold_database_observation(
                    "expected row error sampled database time"
                ):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service("must-not-allocate").execute(command)
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assert_safe_error(
                    caught.exception,
                    "missing-row-canary",
                    "invalid-row-canary",
                )

        for error in (
            TypeError("raw-row-type-canary"),
            RuntimeError("raw-row-runtime-canary"),
        ):
            with self.subTest(raw=type(error).__name__):
                with mock.patch.object(
                    EffectAttemptOutcomeStore,
                    "get",
                    side_effect=error,
                ):
                    with self.assertRaises(type(error)) as caught:
                        self.fold_service("must-not-allocate").execute(command)
                self.assertIs(caught.exception, error)

    def test_clock_ids_construction_and_fk_correct_write_order_are_exact(self) -> None:
        story = self.outcome_story("execution-succeeded", compensation=False)
        self.seed_fold_source(story)
        command = self.fold_command(story)
        expected_ids = self.fold_ids("ordered-event", command.outcome)
        sequence = Sequence(*expected_ids)
        calls: list[str] = []
        constructed: list[str] = []
        written_observations: list[ObservationRecord] = []
        original_observe = PostgresExecutionStore.observe_request_lease_for_update
        original_ordinal = PostgresExecutionStore.next_event_ordinal
        original_event = PostgresExecutionStore.add_event
        original_put = PostgresObservedStateStore.put
        original_insert = EffectAttemptOutcomeStore.insert
        original_cas = EffectAttemptStore.compare_and_set
        original_commit = PostgresUnitOfWork.commit
        real_event_post_init = ActivityEventRecord.__post_init__
        real_attempt_post_init = EffectAttemptRecord.__post_init__
        real_observation_post_init = ObservationRecord.__post_init__
        real_outcome_post_init = EffectAttemptOutcomeRecord.__post_init__
        real_result_post_init = NewlyFolded.__post_init__

        def observe(store, request_id):
            value = original_observe(store, request_id)
            calls.append("clock")
            return value

        def ordinal(store, run_id):
            calls.append("ordinal")
            return original_ordinal(store, run_id)

        def identity():
            value = sequence()
            calls.append(f"id:{value}")
            return value

        def event_post_init(value):
            real_event_post_init(value)
            constructed.append("event")

        def attempt_post_init(value):
            real_attempt_post_init(value)
            constructed.append("attempt")

        def observation_post_init(value):
            real_observation_post_init(value)
            constructed.append("observation")

        def outcome_post_init(value):
            real_outcome_post_init(value)
            constructed.append("outcome")

        def result_post_init(value):
            real_result_post_init(value)
            constructed.append("result")

        def require_complete_plan() -> None:
            for required in ("event", "attempt", "outcome", "result"):
                self.assertIn(required, constructed)
            self.assertGreaterEqual(constructed.count("observation"), 2)

        def add_event(store, value):
            require_complete_plan()
            calls.append("write:event")
            return original_event(store, value)

        def put(store, value):
            require_complete_plan()
            calls.append(f"write:observation:{len(written_observations)}")
            written_observations.append(value)
            return original_put(store, value)

        def insert(store, value):
            require_complete_plan()
            self.assertEqual(tuple(written_observations), value.endpoint_observations)
            self.assertEqual(value.workspace_id, "workspace-a")
            calls.append("write:outcome")
            return original_insert(store, value)

        def cas(store, current, replacement):
            require_complete_plan()
            calls.append("write:cas")
            return original_cas(store, current, replacement)

        def commit(unit_of_work):
            calls.append("commit")
            return original_commit(unit_of_work)

        with mock.patch.object(
            PostgresExecutionStore,
            "observe_request_lease_for_update",
            observe,
        ), mock.patch.object(
            PostgresExecutionStore,
            "next_event_ordinal",
            ordinal,
        ), mock.patch.object(
            ActivityEventRecord,
            "__post_init__",
            event_post_init,
        ), mock.patch.object(
            EffectAttemptRecord,
            "__post_init__",
            attempt_post_init,
        ), mock.patch.object(
            ObservationRecord,
            "__post_init__",
            observation_post_init,
        ), mock.patch.object(
            EffectAttemptOutcomeRecord,
            "__post_init__",
            outcome_post_init,
        ), mock.patch.object(
            NewlyFolded,
            "__post_init__",
            result_post_init,
        ), mock.patch.object(
            PostgresExecutionStore,
            "add_event",
            add_event,
        ), mock.patch.object(
            PostgresObservedStateStore,
            "put",
            put,
        ), mock.patch.object(
            EffectAttemptOutcomeStore,
            "insert",
            insert,
        ), mock.patch.object(
            EffectAttemptStore,
            "compare_and_set",
            cas,
        ), mock.patch.object(
            PostgresUnitOfWork,
            "commit",
            commit,
        ):
            result = self.fold_service_with_id_factory(identity).execute(command)

        self.assertIsInstance(result, NewlyFolded)
        self.assertEqual(sequence.calls, list(expected_ids))
        self.assertEqual(
            [value for value in calls if value.startswith("write:") or value == "commit"],
            [
                "write:event",
                "write:observation:0",
                "write:observation:1",
                "write:outcome",
                "write:cas",
                "commit",
            ],
        )
        self.assertEqual(
            result.attempt.latest_transition_event.occurred_at,
            result.outcome_record.endpoint_observations[0].observed_at,
        )
        self.assertEqual(
            {
                observation.observed_at
                for observation in result.outcome_record.endpoint_observations
            },
            {result.attempt.latest_transition_event.occurred_at},
        )

    def test_outcome_workspace_is_derived_only_from_the_locked_request(self) -> None:
        story = self.outcome_story("execution-succeeded", compensation=False)
        self.seed_fold_source(story)
        command = self.fold_command(story)
        original_request = PostgresExecutionStore.get_request_for_update
        original_insert = EffectAttemptOutcomeStore.insert
        captured = []
        error = RuntimeError("workspace-insert-stop")

        def request(store, request_id):
            value = original_request(store, request_id)
            return replace(
                value,
                identity=replace(
                    value.identity,
                    workspace_id="workspace-locked-canary",
                ),
            )

        def insert(store, record):
            captured.append(record)
            self.assertEqual(record.workspace_id, "workspace-locked-canary")
            raise error

        with mock.patch.object(
            PostgresExecutionStore,
            "get_request_for_update",
            request,
        ), mock.patch.object(
            EffectAttemptOutcomeStore,
            "insert",
            insert,
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.fold_service("workspace-derived").execute(command)
        self.assertIs(caught.exception, error)
        self.assertEqual(len(captured), 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempt_outcomes"
            ).fetchone(),
            (0,),
        )

    def test_duplicate_or_invalid_generated_ids_reject_before_first_write(self) -> None:
        story = self.outcome_story("execution-succeeded", compensation=False)
        cases = (
            ("duplicate", ("event-a", "observation-a", "observation-a")),
            ("event-duplicate", ("event-a", "event-a", "observation-b")),
            ("empty", ("event-a", "", "observation-b")),
            ("control", ("event-a", "observation\ncanary", "observation-b")),
            ("surrogate", ("event-a", "observation-\ud800-canary", "observation-b")),
            ("oversized", ("event-a", "x" * 513, "observation-b")),
        )
        for label, values in cases:
            with self.subTest(case=label):
                self.seed_fold_source(story)
                command = self.fold_command(story)
                ids = Sequence(*values)
                forbidden = AssertionError("invalid generated identity reached a write")
                with mock.patch.object(
                    PostgresExecutionStore,
                    "add_event",
                    side_effect=forbidden,
                ), mock.patch.object(
                    PostgresObservedStateStore,
                    "put",
                    side_effect=forbidden,
                ), mock.patch.object(
                    EffectAttemptOutcomeStore,
                    "insert",
                    side_effect=forbidden,
                ), mock.patch.object(
                    EffectAttemptStore,
                    "compare_and_set",
                    side_effect=forbidden,
                ):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service_with_id_factory(ids).execute(command)
                self.assertEqual(str(caught.exception), SERIALIZATION_ERROR)
                self.assert_safe_error(caught.exception, "canary", "x" * 513)
                self.assertEqual(ids.calls, list(values))


if __name__ == "__main__":
    unittest.main()
