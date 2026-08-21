from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    EffectAttemptFoldDenied,
    NewlyFolded,
)
from control_plane_kit_operations.effect_attempt_fold_interpreter import (
    EffectAttemptFoldService,
)
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
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityStore,
)
from tests.postgres_effect_attempt_fold_fixture import (
    AUTHORITY_ERROR,
    SERIALIZATION_ERROR,
)
from tests.postgres_guarded_observed_effect_fold_fixture import (
    ActivityEventRecord,
    PostgresGuardedObservedEffectFoldFixture,
    Sequence,
)


class _CommitFailureConnection:
    def __init__(self, connection, error: BaseException) -> None:
        self._connection = connection
        self._error = error

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def commit(self) -> None:
        raise self._error


class PostgresGuardedObservedEffectFoldRollbackTests(
    PostgresGuardedObservedEffectFoldFixture,
    unittest.TestCase,
):
    def test_one_relock_observation_owns_lease_expiry_before_authority_lookup(self) -> None:
        original = PostgresExecutionStore.observe_request_lease_for_update
        for label, observed_at, denied in (
            ("before", "2099-01-01T23:59:59Z", False),
            ("equal", "2099-01-02T00:00:00Z", True),
            ("after", "2099-01-02T00:00:01Z", True),
        ):
            with self.subTest(label=label):
                story = self.observed_story()
                current, intent, record = self.seed_guarded_source(story)
                authority = self.register_runtime_authority(intent)
                observations = []
                authority_calls = []

                def observe(store, request_id):
                    value = original(store, request_id)
                    observations.append(value)
                    return replace(value, observed_at=observed_at)

                def active(store, workspace_id, authority_ref):
                    authority_calls.append((workspace_id, authority_ref))
                    return authority

                with mock.patch.object(
                    PostgresExecutionStore,
                    "observe_request_lease_for_update",
                    observe,
                ), mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    active,
                    create=True,
                ):
                    command = self.guarded_observed_command(
                        story,
                        current=current,
                        intent=intent,
                        intent_record=record,
                        runtime_authority=authority,
                        register=False,
                    )
                    if denied:
                        with self.assertRaises(EffectAttemptFoldDenied) as caught:
                            self.fold_service("must-not-allocate").execute_observed(command)
                        self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
                    else:
                        self.assertIsInstance(
                            self.fold_service("lease-before").execute_observed(command),
                            NewlyFolded,
                        )
                self.assertEqual(len(observations), 1)
                self.assertEqual(
                    authority_calls,
                    [] if denied else [(intent.source.workspace_id, intent.authority_ref)],
                )

    def test_ids_and_complete_result_are_constructed_before_first_write(self) -> None:
        for story in (self.observed_stories()[0], self.observed_stories()[2]):
            with self.subTest(story=story.name, endpoints=len(story.endpoint_observations)):
                current, _intent, _record = self.seed_guarded_source(story)
                event_id = f"constructed-{story.name}"
                values = self.fold_ids_for_story(event_id, story)
                ids = Sequence(*values)
                seen = []

                def event(_store, candidate):
                    seen.append(candidate)
                    self.assertIs(type(candidate), ActivityEventRecord)
                    self.assertEqual(ids.calls, list(values))
                    raise RuntimeError("first-write-canary")

                before = self.complete_snapshot()
                with mock.patch.object(PostgresExecutionStore, "add_event", event):
                    with self.assertRaises(RuntimeError) as caught:
                        self.fold_service_with_id_factory(ids).execute_observed(
                            self.guarded_observed_command(story, current=current)
                        )
                self.assertEqual(str(caught.exception), "first-write-canary")
                self.assertEqual(len(seen), 1)
                self.assertEqual(self.complete_snapshot(), before)

    def test_every_write_acknowledgement_is_exact_and_rolls_back_on_change(self) -> None:
        for target in ("event", "observation-0", "observation-1", "outcome", "cas"):
            with self.subTest(target=target):
                story = self.observed_story()
                current, _intent, _record = self.seed_guarded_source(story)
                before = self.complete_snapshot()
                original_event = PostgresExecutionStore.add_event
                original_observation = PostgresObservedStateStore.put
                original_outcome = EffectAttemptOutcomeStore.insert
                original_cas = EffectAttemptStore.compare_and_set
                observation_calls = 0

                def event(store, value):
                    original_event(store, value)
                    return None if target == "event" else value

                def observation(store, value):
                    nonlocal observation_calls
                    position = observation_calls
                    observation_calls += 1
                    original_observation(store, value)
                    return None if target == f"observation-{position}" else value

                def outcome(store, value):
                    original_outcome(store, value)
                    return None if target == "outcome" else value

                def cas(store, observed, replacement):
                    original_cas(store, observed, replacement)
                    return None if target == "cas" else replacement

                with mock.patch.object(PostgresExecutionStore, "add_event", event), \
                    mock.patch.object(PostgresObservedStateStore, "put", observation), \
                    mock.patch.object(EffectAttemptOutcomeStore, "insert", outcome), \
                    mock.patch.object(EffectAttemptStore, "compare_and_set", cas):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service(f"changed-{target}").execute_observed(
                            self.guarded_observed_command(story, current=current)
                        )
                self.assertEqual(str(caught.exception), SERIALIZATION_ERROR)
                self.assertEqual(self.complete_snapshot(), before)

    def test_every_late_raw_fault_and_commit_failure_roll_back_unchanged(self) -> None:
        stages = (
            ("event", PostgresExecutionStore, "add_event"),
            ("observation", PostgresObservedStateStore, "put"),
            ("outcome", EffectAttemptOutcomeStore, "insert"),
            ("cas", EffectAttemptStore, "compare_and_set"),
        )
        for label, owner, method in stages:
            with self.subTest(stage=label):
                story = self.observed_story()
                current, _intent, _record = self.seed_guarded_source(story)
                before = self.complete_snapshot()
                error = RuntimeError(f"raw-{label}-canary")
                with mock.patch.object(owner, method, side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        self.fold_service(f"raw-{label}").execute_observed(
                            self.guarded_observed_command(story, current=current)
                        )
                self.assertIs(caught.exception, error)
                self.assertEqual(self.complete_snapshot(), before)

        story = self.observed_story()
        current, _intent, _record = self.seed_guarded_source(story)
        before = self.complete_snapshot()
        error = RuntimeError("raw-guarded-commit-canary")
        event_id = "guarded-commit-event"
        values = self.fold_ids_for_story(event_id, story)
        ids = Sequence(*values)

        def failing_uow():
            return PostgresUnitOfWork(
                lambda: _CommitFailureConnection(
                    psycopg.connect(self.database_url),
                    error,
                )
            )

        with self.assertRaises(RuntimeError) as caught:
            self.checked_fold_service(
                EffectAttemptFoldService(
                    failing_uow,
                    id_factory=ids,
                )
            ).execute_observed(
                self.guarded_observed_command(story, current=current)
            )
        self.assertIs(caught.exception, error)
        self.assertEqual(ids.calls, list(values))
        self.assertEqual(self.complete_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
