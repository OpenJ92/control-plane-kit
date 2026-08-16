from __future__ import annotations

from dataclasses import replace
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldConflict,
    ExistingFold,
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
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_fold_fixture import (
    PostgresEffectAttemptFoldFixture,
    SERIALIZATION_ERROR,
)


class _CommitFailureConnection:
    def __init__(self, connection, error: BaseException) -> None:
        self._connection = connection
        self._error = error

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def commit(self) -> None:
        raise self._error


class PostgresEffectAttemptFoldRollbackTests(
    PostgresEffectAttemptFoldFixture,
    unittest.TestCase,
):
    def test_every_late_raw_failure_rolls_back_event_and_attempt_projection(self) -> None:
        stages = (
            ("observation", PostgresExecutionStore, "observe_request_lease_for_update"),
            ("ordinal", PostgresExecutionStore, "next_event_ordinal"),
            ("event", PostgresExecutionStore, "add_event"),
            ("cas", EffectAttemptStore, "compare_and_set"),
        )
        for label, owner, method in stages:
            with self.subTest(stage=label):
                self.seed_fold_source("succeeded")
                before = self.attempt_snapshot()
                error = RuntimeError(f"raw-{label}-failure-canary")
                with mock.patch.object(owner, method, side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        self.fold_service(f"{label}-event-id").execute(
                            self.fold_command("succeeded")
                        )
                self.assertIs(caught.exception, error)
                self.assertEqual(self.attempt_snapshot(), before)

        self.seed_fold_source("succeeded")
        before = self.attempt_snapshot()
        identity_error = RuntimeError("raw-id-failure-canary")
        with self.assertRaises(RuntimeError) as caught:
            self.fold_service_with_id_factory(
                lambda: (_ for _ in ()).throw(identity_error)
            ).execute(self.fold_command("succeeded"))
        self.assertIs(caught.exception, identity_error)
        self.assertEqual(self.attempt_snapshot(), before)

    def test_commit_failure_rolls_back_event_and_attempt_projection(self) -> None:
        self.seed_fold_source("succeeded")
        before = self.attempt_snapshot()
        error = RuntimeError("raw-commit-failure-canary")

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
                    id_factory=Sequence("commit-event-id"),
                )
            ).execute(self.fold_command("succeeded"))
        self.assertIs(caught.exception, error)
        self.assertEqual(self.attempt_snapshot(), before)

    def test_lost_cas_rolls_back_candidate_event(self) -> None:
        self.seed_fold_source("succeeded")
        before = self.attempt_snapshot()
        with mock.patch.object(EffectAttemptStore, "compare_and_set", return_value=None):
            with self.assertRaises(EffectAttemptFoldConflict) as caught:
                self.fold_service("lost-cas-event").execute(
                    self.fold_command("succeeded")
                )
        self.assert_safe_error(caught.exception)
        self.assertEqual(str(caught.exception), SERIALIZATION_ERROR)
        self.assertEqual(self.attempt_snapshot(), before)

    def test_changed_event_and_cas_returns_are_conflicts_and_roll_back(self) -> None:
        for target in ("event", "cas"):
            with self.subTest(target=target):
                self.seed_fold_source("succeeded")
                before = self.attempt_snapshot()
                ids = Sequence(f"changed-{target}-return-event")
                original_event = PostgresExecutionStore.add_event
                original_cas = EffectAttemptStore.compare_and_set

                def changed_event(store, event):
                    original_event(store, event)
                    return replace(event, event_id="changed-event-return-canary")

                def changed_cas(store, current, replacement):
                    original_cas(store, current, replacement)
                    return current

                owner = PostgresExecutionStore if target == "event" else EffectAttemptStore
                method = "add_event" if target == "event" else "compare_and_set"
                replacement = changed_event if target == "event" else changed_cas
                with mock.patch.object(owner, method, replacement):
                    with self.assertRaises(EffectAttemptFoldConflict) as caught:
                        self.fold_service_with_id_factory(ids).execute(
                            self.fold_command("succeeded")
                        )
                self.assert_safe_error(caught.exception, "return-canary")
                self.assertEqual(str(caught.exception), SERIALIZATION_ERROR)
                self.assertEqual(ids.calls, [f"changed-{target}-return-event"])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_exact_replay_never_calls_clock_ordinal_id_event_or_cas(self) -> None:
        self.seed_fold_source("succeeded")
        first = self.fold_service("first-event").execute(
            self.fold_command("succeeded")
        )
        self.assertIsInstance(first, NewlyFolded)
        before = self.attempt_snapshot()
        ids = Sequence("replay-must-not-allocate")
        forbidden = AssertionError("exact replay entered mutation work")

        with mock.patch.object(
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
            EffectAttemptStore,
            "compare_and_set",
            side_effect=forbidden,
        ):
            replay = self.fold_service_with_id_factory(ids).execute(
                self.fold_command("succeeded")
            )

        self.assertEqual(replay, ExistingFold(first.attempt))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_event_append_precedes_cas_and_both_are_one_transaction(self) -> None:
        current = self.seed_fold_source("succeeded")
        calls: list[str] = []
        original_event = PostgresExecutionStore.add_event
        original_cas = EffectAttemptStore.compare_and_set

        def event(store, value):
            calls.append("event")
            return original_event(store, value)

        def cas(store, observed, replacement):
            calls.append("cas")
            self.assertEqual(observed, current)
            with psycopg.connect(self.database_url) as observer:
                self.assertEqual(
                    observer.execute(
                        "SELECT status, latest_event_id FROM cpk_effect_attempts "
                        "WHERE run_id='run-a' AND activity_id='start-runtime' "
                        "AND attempt=1"
                    ).fetchone(),
                    (current.state.status.value, current.latest_transition_event.event_id),
                )
                self.assertIsNone(
                    observer.execute(
                        "SELECT event_id FROM cpk_activity_events WHERE event_id=%s",
                        (replacement.latest_transition_event.event_id,),
                    ).fetchone()
                )
            return original_cas(store, observed, replacement)

        with mock.patch.object(PostgresExecutionStore, "add_event", event), \
            mock.patch.object(EffectAttemptStore, "compare_and_set", cas):
            result = self.fold_service("atomic-event").execute(
                self.fold_command("succeeded")
            )

        self.assertIsInstance(result, NewlyFolded)
        self.assertEqual(calls, ["event", "cas"])


if __name__ == "__main__":
    unittest.main()
