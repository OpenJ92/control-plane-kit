from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from control_plane_kit_operations.postgres import (
    PostgresActivityHistoryStore,
    PostgresExecutionStore,
    PostgresUnitOfWork,
)
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    OperationsRecordError,
)
from tests.effect_attempt_start_fixture import (
    EffectAttemptStartConflict,
    EffectAttemptStartDenied,
    EffectAttemptStartNotFound,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_start_fixture import (
    AUTHORITY_ERROR,
    ELIGIBILITY_ERROR,
    INVALID_TRUTH_ERROR,
    NOT_FOUND_ERROR,
    PostgresEffectAttemptStartFixture,
    REPLAY_ERROR,
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


class _ClockRejectingConnection:
    def __init__(self, connection) -> None:
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def execute(self, query, params=None):
        if "clock_timestamp()" in str(query):
            raise AssertionError("claimless request sampled database time")
        if params is None:
            return self._connection.execute(query)
        return self._connection.execute(query, params)


class PostgresEffectAttemptStartEligibilityRollbackTests(
    PostgresEffectAttemptStartFixture,
    unittest.TestCase,
):
    def test_first_start_requires_exact_phase_membership_and_run_status(self) -> None:
        cases = (
            ("forward-paused", False, ActivityRunStatus.PAUSED, False),
            ("forward-compensating", False, ActivityRunStatus.COMPENSATING, False),
            ("compensation-running", True, ActivityRunStatus.RUNNING, False),
            ("compensation-paused", True, ActivityRunStatus.PAUSED, False),
            ("forward-duplicate-start", False, ActivityRunStatus.RUNNING, True),
        )
        for label, compensation, status, duplicate in cases:
            with self.subTest(case=label):
                self.reset_start_truth(compensation=compensation)
                self.connection.execute(
                    "UPDATE cpk_activity_runs SET status=%s WHERE run_id='run-a'",
                    (status.value,),
                )
                if duplicate:
                    with self.unit_of_work() as unit_of_work:
                        stores = unit_of_work.stores
                        stores.execution.add_event(
                            ActivityEventRecord(
                                "duplicate-step-started",
                                "run-a",
                                3,
                                ActivityEventKind.STEP_STARTED,
                                "2026-08-15T04:00:00Z",
                                activity_id="start-runtime",
                            )
                        )
                        unit_of_work.commit()
                before = self.attempt_snapshot()
                ids = Sequence("phase-rejection-must-not-allocate")
                with self.reject_database_observation(
                    "phase rejection sampled database time"
                ):
                    with self.assertRaises(EffectAttemptStartConflict) as caught:
                        self.start_service_with_id_factory(ids).execute(
                            self.start_command()
                        )
                self.assert_safe_error(caught.exception, label)
                self.assertEqual(str(caught.exception), ELIGIBILITY_ERROR)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_first_start_requires_the_exact_ready_activity(self) -> None:
        for compensation in (False, True):
            with self.subTest(compensation=compensation):
                self.reset_start_truth(compensation=compensation)
                before = self.attempt_snapshot()
                ids = Sequence("foreign-activity-must-not-allocate")
                command = self.start_command(
                    transition=self.transition(
                        identity=self.identity(
                            run_id="run-a",
                            activity_id="foreign-activity-canary",
                        )
                    )
                )
                with self.reject_database_observation(
                    "foreign activity sampled database time"
                ):
                    with self.assertRaises(EffectAttemptStartConflict) as caught:
                        self.start_service_with_id_factory(ids).execute(command)
                self.assert_safe_error(caught.exception, "foreign-activity-canary")
                self.assertEqual(str(caught.exception), ELIGIBILITY_ERROR)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_absent_start_requires_current_unexpired_exact_fence(self) -> None:
        cases = (
            ("expired", "worker-a", 7, True),
            ("foreign-worker-canary", "worker-b", 7, False),
            ("stale-generation-canary", "worker-a", 8, False),
        )
        for label, worker_id, generation, observed in cases:
            with self.subTest(case=label):
                self.reset_start_truth()
                if label == "expired":
                    self.expire_claim()
                command = self.start_command(
                    authority=self.authority(worker_id),
                    fence=self.fence(worker_id, generation),
                )
                before = self.attempt_snapshot()
                ids = Sequence("authority-rejection-must-not-allocate")
                calls = 0
                original = PostgresExecutionStore.observe_request_lease_for_update

                def observe(store, request_id):
                    nonlocal calls
                    calls += 1
                    return original(store, request_id)

                with mock.patch.object(
                    PostgresExecutionStore,
                    "observe_request_lease_for_update",
                    observe,
                ):
                    with self.assertRaises(EffectAttemptStartDenied) as caught:
                        self.start_service_with_id_factory(ids).execute(command)
                self.assert_safe_error(caught.exception, label)
                self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
                self.assertEqual(calls, 1 if observed else 0)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_claimless_request_rejects_before_clock_ids_or_writes(self) -> None:
        self.connection.execute(
            "UPDATE cpk_execution_requests SET status='queued', "
            "claim_worker_id=NULL, claim_generation=NULL, claimed_at=NULL, "
            "lease_expires_at=NULL WHERE request_id='request-a'"
        )
        before = self.attempt_snapshot()
        ids = Sequence("claimless-request-must-not-allocate")

        def unit_of_work():
            return PostgresUnitOfWork(
                lambda: _ClockRejectingConnection(
                    psycopg.connect(self.database_url)
                )
            )

        with self.assertRaises(EffectAttemptStartDenied) as caught:
            EffectAttemptStartService(
                unit_of_work,
                id_factory=ids,
            ).execute(self.start_command())
        self.assert_safe_error(caught.exception)
        self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_existing_attempt_rejects_replaced_authority_without_clock(self) -> None:
        self.persisted_started()
        self.replace_claim()
        before = self.attempt_snapshot()
        ids = Sequence("stale-replay-must-not-allocate")
        with self.reject_database_observation("stale replay sampled database time"):
            with self.assertRaises(EffectAttemptStartDenied) as caught:
                self.start_service_with_id_factory(ids).execute(
                    self.start_command()
                )
        self.assert_safe_error(caught.exception, "worker-b")
        self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_current_rotated_authority_rejects_old_attempt_fence_without_clock(
        self,
    ) -> None:
        self.persisted_started()
        self.replace_claim(worker_id="worker-b", generation=8)
        before = self.attempt_snapshot()
        ids = Sequence("rotated-current-replay-must-not-allocate")
        command = self.start_command(
            authority=self.authority("worker-b"),
            fence=self.fence("worker-b", 8),
        )
        with self.reject_database_observation(
            "rotated-current replay sampled database time"
        ):
            with self.assertRaises(EffectAttemptStartConflict) as caught:
                self.start_service_with_id_factory(ids).execute(command)
        self.assert_safe_error(caught.exception, "worker-a", "worker-b")
        self.assertEqual(str(caught.exception), REPLAY_ERROR)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_lease_observation_must_match_the_locked_request(self) -> None:
        original = PostgresExecutionStore.observe_request_lease_for_update
        before = self.attempt_snapshot()
        ids = Sequence("changed-observation-must-not-allocate")

        def changed_observation(store, request_id):
            observation = original(store, request_id)
            changed_request = replace(
                observation.request,
                requested_by="changed-observation-request-canary",
            )
            return replace(observation, request=changed_request)

        with mock.patch.object(
            PostgresExecutionStore,
            "observe_request_lease_for_update",
            changed_observation,
        ):
            with self.assertRaises(EffectAttemptStartConflict) as caught:
                self.start_service_with_id_factory(ids).execute(
                    self.start_command()
                )
        self.assert_safe_error(
            caught.exception,
            "changed-observation-request-canary",
        )
        self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_missing_request_and_request_scoped_run_are_categorical(self) -> None:
        cases = ("request", "run")
        for target in cases:
            with self.subTest(target=target):
                self.reset_start_truth()
                command = self.start_command()
                if target == "request":
                    command = self.start_command(
                        request_id="missing-request-canary"
                    )
                else:
                    command = self.start_command(
                        transition=self.transition(
                            identity=self.identity(
                                run_id="missing-run-canary",
                                activity_id="start-runtime",
                            )
                        )
                    )
                ids = Sequence("missing-target-must-not-allocate")
                with self.reject_database_observation(
                    "missing target sampled database time"
                ):
                    with self.assertRaises(EffectAttemptStartNotFound) as caught:
                        self.start_service_with_id_factory(ids).execute(command)
                self.assert_safe_error(caught.exception, "canary")
                self.assertEqual(str(caught.exception), NOT_FOUND_ERROR)
                self.assertEqual(ids.calls, [])

    def test_malformed_plan_and_journal_reject_before_clock_ids_or_writes(self) -> None:
        cases = ("plan", "journal")
        for target in cases:
            with self.subTest(target=target):
                self.reset_start_truth()
                if target == "plan":
                    self.connection.execute(
                        "UPDATE cpk_activity_plans SET payload='1'::jsonb "
                        "WHERE plan_id='plan-a'"
                    )
                else:
                    self.connection.execute(
                        "UPDATE cpk_activity_events SET payload="
                        "jsonb_set(payload, '{evidence}', '1'::jsonb) "
                        "WHERE event_id='seed-run-started'"
                    )
                before = self.attempt_snapshot()
                ids = Sequence("malformed-history-must-not-allocate")
                with self.reject_database_observation(
                    "malformed history sampled database time"
                ):
                    with self.assertRaises(EffectAttemptStartConflict) as caught:
                        self.start_service_with_id_factory(ids).execute(
                            self.start_command()
                        )
                self.assert_safe_error(caught.exception)
                self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_expected_store_decoder_failures_are_categorical_and_internal_raw(self) -> None:
        boundaries = (
            (PostgresExecutionStore, "get_request_for_update", EffectAttemptStartConflict),
            (
                PostgresExecutionStore,
                "get_run_for_request_for_update",
                EffectAttemptStartConflict,
            ),
            (
                PostgresExecutionStore,
                "get_latest_run_for_request_for_update",
                EffectAttemptStartConflict,
            ),
            (PostgresActivityHistoryStore, "get_plan", EffectAttemptStartConflict),
            (PostgresExecutionStore, "events_for_run", EffectAttemptStartConflict),
            (EffectAttemptStore, "get_for_update", EffectAttemptStartConflict),
            (
                PostgresExecutionStore,
                "observe_request_lease_for_update",
                EffectAttemptStartConflict,
            ),
        )
        for owner, method, category in boundaries:
            for error_type in (ValueError, OperationsRecordError):
                with self.subTest(method=method, error=error_type.__name__):
                    self.reset_start_truth()
                    before = self.attempt_snapshot()
                    canary = f"{method}-{error_type.__name__}-canary"
                    error = error_type(canary)
                    ids = Sequence("decoder-rejection-must-not-allocate")
                    clock_guard = (
                        nullcontext()
                        if method == "observe_request_lease_for_update"
                        else self.reject_database_observation(
                            "decoder rejection sampled database time"
                        )
                    )
                    with mock.patch.object(owner, method, side_effect=error):
                        with clock_guard:
                            with self.assertRaises(category) as caught:
                                self.start_service_with_id_factory(ids).execute(
                                    self.start_command()
                                )
                    self.assert_safe_error(caught.exception, canary)
                    self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                    self.assertEqual(ids.calls, [])
                    self.assertEqual(self.attempt_snapshot(), before)

            with self.subTest(method=method, error="RuntimeError"):
                self.reset_start_truth()
                error = RuntimeError(f"{method}-raw-internal-canary")
                with mock.patch.object(owner, method, side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        self.start_service("raw-must-not-allocate").execute(
                            self.start_command()
                        )
                self.assertIs(caught.exception, error)

    def test_changed_replay_fingerprint_and_malformed_attempt_are_conflicts(self) -> None:
        cases = (
            ("fingerprint", REPLAY_ERROR),
            ("event-evidence", INVALID_TRUTH_ERROR),
        )
        for target, expected_message in cases:
            with self.subTest(target=target):
                self.reset_start_truth()
                current = self.persisted_started()
                command = self.start_command()
                if target == "fingerprint":
                    foreign_intent = replace(command.intent, products=())
                    command = self.start_command(
                        intent=foreign_intent,
                        transition=self.transition(intent=foreign_intent),
                    )
                else:
                    self.connection.execute(
                        "UPDATE cpk_activity_events SET payload="
                        "jsonb_set(payload, '{evidence}', '1'::jsonb) "
                        "WHERE event_id=%s",
                        (current.original_start_event.event_id,),
                    )
                before = self.attempt_snapshot()
                ids = Sequence("replay-conflict-must-not-allocate")
                with self.reject_database_observation(
                    "replay conflict sampled database time"
                ):
                    with self.assertRaises(EffectAttemptStartConflict) as caught:
                        self.start_service_with_id_factory(ids).execute(command)
                self.assert_safe_error(caught.exception, "c" * 64)
                self.assertEqual(str(caught.exception), expected_message)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.attempt_snapshot(), before)

    def test_historical_run_cannot_receive_a_new_direct_attempt(self) -> None:
        started = self.persisted_started()
        self.add_lawful_linked_retry()
        self.connection.execute(
            "DELETE FROM cpk_effect_attempts WHERE run_id=%s "
            "AND activity_id=%s AND attempt=%s",
            (
                started.state.identity.run_id.value,
                started.state.identity.activity_id,
                started.state.identity.attempt,
            ),
        )
        before = self.attempt_snapshot()
        ids = Sequence("historical-start-must-not-allocate")
        with self.reject_database_observation(
            "historical direct start sampled database time"
        ):
            with self.assertRaises(EffectAttemptStartConflict) as caught:
                self.start_service_with_id_factory(ids).execute(
                    self.start_command()
                )
        self.assert_safe_error(caught.exception)
        self.assertEqual(str(caught.exception), ELIGIBILITY_ERROR)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.attempt_snapshot(), before)

    def test_event_ordinal_id_event_insert_attempt_insert_and_commit_roll_back(self) -> None:
        raw_failures = (
            (
                "ordinal",
                PostgresExecutionStore,
                "next_event_ordinal",
            ),
            (
                "event",
                PostgresExecutionStore,
                "add_event",
            ),
            (
                "attempt",
                EffectAttemptStore,
                "insert_absent",
            ),
        )
        for label, owner, method in raw_failures:
            with self.subTest(stage=label):
                self.reset_start_truth()
                before = self.attempt_snapshot()
                error = RuntimeError(f"raw-{label}-failure-canary")
                with mock.patch.object(owner, method, side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        self.start_service(f"{label}-event-id").execute(
                            self.start_command()
                        )
                self.assertIs(caught.exception, error)
                self.assertEqual(self.attempt_snapshot(), before)

        self.reset_start_truth()
        before = self.attempt_snapshot()
        id_error = RuntimeError("raw-id-failure-canary")
        with self.assertRaises(RuntimeError) as caught:
            self.start_service_with_id_factory(
                lambda: (_ for _ in ()).throw(id_error)
            ).execute(self.start_command())
        self.assertIs(caught.exception, id_error)
        self.assertEqual(self.attempt_snapshot(), before)

        self.reset_start_truth()
        before = self.attempt_snapshot()
        commit_error = RuntimeError("raw-commit-failure-canary")

        def failing_uow():
            return PostgresUnitOfWork(
                lambda: _CommitFailureConnection(
                    psycopg.connect(self.database_url),
                    commit_error,
                )
            )

        with self.assertRaises(RuntimeError) as caught:
            EffectAttemptStartService(
                failing_uow,
                id_factory=Sequence("commit-event-id"),
            ).execute(self.start_command())
        self.assertIs(caught.exception, commit_error)
        self.assertEqual(self.attempt_snapshot(), before)

    def test_insert_absent_miss_is_conflict_and_rolls_back_candidate_event(self) -> None:
        self.reset_start_truth()
        before = self.attempt_snapshot()
        with mock.patch.object(
            EffectAttemptStore,
            "insert_absent",
            return_value=None,
        ):
            with self.assertRaises(EffectAttemptStartConflict) as caught:
                self.start_service("lost-insert-event").execute(
                    self.start_command()
                )
        self.assert_safe_error(caught.exception)
        self.assertEqual(str(caught.exception), SERIALIZATION_ERROR)
        self.assertEqual(self.attempt_snapshot(), before)

    def test_changed_store_returns_are_conflicts_and_roll_back(self) -> None:
        for target in ("event", "attempt"):
            with self.subTest(target=target):
                self.reset_start_truth()
                before = self.attempt_snapshot()
                ids = Sequence(f"changed-{target}-return-event")
                original_event = PostgresExecutionStore.add_event
                original_attempt = EffectAttemptStore.insert_absent

                def changed_event(store, event):
                    original_event(store, event)
                    return replace(
                        event,
                        event_id="changed-event-return-canary",
                    )

                def changed_attempt(store, record):
                    original_attempt(store, record)
                    return self.record(
                        "started",
                        run_id=record.state.identity.run_id.value,
                        activity_id=record.state.identity.activity_id,
                        event_prefix="changed-attempt-return-canary",
                        original_ordinal=record.original_start_event.ordinal,
                        original_time=record.original_start_event.occurred_at,
                    )

                owner = (
                    PostgresExecutionStore
                    if target == "event"
                    else EffectAttemptStore
                )
                method = "add_event" if target == "event" else "insert_absent"
                replacement = (
                    changed_event if target == "event" else changed_attempt
                )
                with mock.patch.object(owner, method, replacement):
                    with self.assertRaises(EffectAttemptStartConflict) as caught:
                        self.start_service_with_id_factory(ids).execute(
                            self.start_command()
                        )
                self.assert_safe_error(caught.exception, "return-canary")
                self.assertEqual(str(caught.exception), SERIALIZATION_ERROR)
                self.assertEqual(ids.calls, [f"changed-{target}-return-event"])
                self.assertEqual(self.attempt_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
