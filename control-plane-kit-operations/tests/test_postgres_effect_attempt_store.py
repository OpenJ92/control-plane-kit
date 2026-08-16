from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

import psycopg
from psycopg.errors import ForeignKeyViolation, LockNotAvailable, UniqueViolation

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.records import OperationsRecordError
from tests.effect_attempt_record_fixture import STORIES
from tests.postgres_effect_attempt_store_fixture import (
    PostgresEffectAttemptStoreFixture,
    store_module,
)


class PostgresEffectAttemptStoreTests(
    PostgresEffectAttemptStoreFixture,
    unittest.TestCase,
):
    def test_every_state_and_phase_reconstructs_after_connection_restart(self) -> None:
        self.require_store()
        for compensation in (False, True):
            for index, story in enumerate(STORIES, start=1):
                with self.subTest(compensation=compensation, story=story):
                    self.reset_truth(
                        RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                        history="active-empty",
                    )
                    record = self.record(
                        story,
                        compensation=compensation,
                        event_prefix=f"attempt-{int(compensation)}-{index}",
                        original_ordinal=10,
                        latest_ordinal=20,
                    )
                    self.assertEqual(self.persist(record), record)
                    with self.unit_of_work() as unit_of_work:
                        self.assertEqual(
                            unit_of_work.stores.effect_attempts.get(
                                record.state.identity
                            ),
                            record,
                        )

    def test_insert_duplicate_rollback_and_fixed_read_miss_are_distinct(self) -> None:
        self.require_store()
        record = self.record(event_prefix="rollback", original_ordinal=10)
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.add_record_events(stores, record)
            self.assertEqual(stores.effect_attempts.insert_absent(record), record)

        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(KeyError) as caught:
                unit_of_work.stores.effect_attempts.get(record.state.identity)
            self.assertEqual(str(caught.exception), "'effect attempt was not found'")

        self.assertEqual(self.persist(record), record)
        with self.unit_of_work() as unit_of_work:
            self.assertIsNone(unit_of_work.stores.effect_attempts.insert_absent(record))
            unit_of_work.commit()

    def test_predecessor_and_event_role_constraints_are_raw_integrity(self) -> None:
        self.require_store()
        retry = self.record(
            "started",
            attempt=2,
            event_prefix="retry",
            original_ordinal=30,
        )
        with self.assertRaises(ForeignKeyViolation):
            with self.unit_of_work() as unit_of_work:
                stores = unit_of_work.stores
                self.add_record_events(stores, retry)
                stores.effect_attempts.insert_absent(retry)
                unit_of_work.commit()

        predecessor = self.record(event_prefix="predecessor", original_ordinal=20)
        self.persist(predecessor)
        self.assertEqual(self.persist(retry), retry)

        columns = tuple(
            row[0]
            for row in self.connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=current_schema()
                  AND table_name='cpk_effect_attempts'
                ORDER BY ordinal_position
                """
            ).fetchall()
        )
        projection = ", ".join(
            "'activity-role-canary'" if name == "activity_id" else name
            for name in columns
        )
        with self.assertRaises(UniqueViolation):
            self.connection.execute(
                f"INSERT INTO cpk_effect_attempts ({', '.join(columns)}) "
                f"SELECT {projection} FROM cpk_effect_attempts "
                "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1"
            )

    def test_get_for_update_locks_only_the_exact_attempt_row(self) -> None:
        self.require_store()
        target = self.record(event_prefix="lock-a", original_ordinal=10)
        unrelated = self.record(
            attempt=2,
            event_prefix="lock-b",
            original_ordinal=11,
        )
        self.persist(target)
        self.persist(unrelated)

        with self.unit_of_work() as first:
            self.assertEqual(
                first.stores.effect_attempts.get_for_update(target.state.identity),
                target,
            )
            second = psycopg.connect(self.database_url)
            try:
                self.assertEqual(
                    second.execute(
                        "SELECT activity_id FROM cpk_effect_attempts "
                        "WHERE run_id=%s AND activity_id=%s AND attempt=%s "
                        "FOR UPDATE NOWAIT",
                        ("run-a", "activity-a", 2),
                    ).fetchone(),
                    ("activity-a",),
                )
                with self.assertRaises(LockNotAvailable):
                    second.execute(
                        "SELECT run_id FROM cpk_effect_attempts "
                        "WHERE run_id=%s AND activity_id=%s AND attempt=%s "
                        "FOR UPDATE NOWAIT",
                        ("run-a", "activity-a", 1),
                    )
            finally:
                second.rollback()
                second.close()

    def test_complete_prior_cas_has_one_bounded_winner(self) -> None:
        self.require_store()
        current = self.record(event_prefix="cas", original_ordinal=10)
        self.persist(current)
        succeeded = self.transition(
            current,
            "succeeded",
            event_id="cas-success",
            ordinal=20,
        )
        failed = self.transition(
            current,
            "failed",
            event_id="cas-failed",
            ordinal=21,
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_event(succeeded.latest_transition_event)
            unit_of_work.stores.execution.add_event(failed.latest_transition_event)
            unit_of_work.commit()

        barrier = threading.Barrier(2, timeout=10)

        def advance(replacement):
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.connection.execute(
                    "SET LOCAL lock_timeout = '10s'"
                )
                barrier.wait(timeout=10)
                result = unit_of_work.stores.effect_attempts.compare_and_set(
                    current,
                    replacement,
                )
                unit_of_work.commit()
                return result

        executor = ThreadPoolExecutor(max_workers=2)
        futures = tuple(executor.submit(advance, value) for value in (succeeded, failed))
        try:
            results = tuple(future.result(timeout=20) for future in futures)
        except BaseException:
            barrier.abort()
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
        finally:
            barrier.abort()

        winners = tuple(result for result in results if result is not None)
        self.assertEqual(len(winners), 1)
        self.assertIn(winners[0], (succeeded, failed))
        loser = failed if winners[0] == succeeded else succeeded
        with self.unit_of_work() as unit_of_work:
            self.assertIsNone(
                unit_of_work.stores.effect_attempts.compare_and_set(current, loser)
            )
            self.assertEqual(
                unit_of_work.stores.effect_attempts.get(current.state.identity),
                winners[0],
            )
            unit_of_work.commit()

    def test_complete_persisted_prior_drift_misses_and_preserves_row(self) -> None:
        self.require_store()
        for drift in ("request", "fence", "state", "latest-event"):
            with self.subTest(drift=drift):
                self.reset_truth(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    history="active-empty",
                )
                current = self.record(
                    "uncertain",
                    event_prefix=f"drift-{drift}",
                    original_ordinal=10,
                    latest_ordinal=20,
                )
                replacement = self.transition(
                    current,
                    "recovered-succeeded",
                    event_id=f"drift-{drift}-replacement",
                    ordinal=40,
                )
                self.persist(current)
                with self.unit_of_work() as unit_of_work:
                    unit_of_work.stores.execution.add_event(
                        replacement.latest_transition_event
                    )
                    unit_of_work.commit()

                if drift == "request":
                    self.connection.execute(
                        "UPDATE cpk_effect_attempts SET request_fingerprint=%s "
                        "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1",
                        ("d" * 64,),
                    )
                elif drift == "fence":
                    self.connection.execute(
                        "UPDATE cpk_effect_attempts "
                        "SET fence_worker_id='worker-b', fence_generation=8 "
                        "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1"
                    )
                elif drift == "state":
                    drifted = self.transition(
                        current,
                        "recovered-failed",
                        event_id="drift-state-terminal",
                        ordinal=30,
                    )
                    with self.unit_of_work() as unit_of_work:
                        unit_of_work.stores.execution.add_event(
                            drifted.latest_transition_event
                        )
                        unit_of_work.commit()
                    recovery = drifted.state.recovery_decision
                    self.connection.execute(
                        """
                        UPDATE cpk_effect_attempts
                        SET status=%s, outcome_fingerprint=%s,
                            recovery_decision_id=%s, recovery_resolution=%s,
                            recovery_uncertain_fingerprint=%s,
                            recovery_evidence_fingerprint=%s,
                            latest_event_id=%s, latest_event_run_id=%s,
                            latest_event_ordinal=%s
                        WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1
                        """,
                        (
                            drifted.state.status.value,
                            drifted.state.outcome_fingerprint,
                            recovery.decision_id,
                            recovery.resolution.value,
                            recovery.uncertain_fingerprint,
                            recovery.evidence_fingerprint,
                            drifted.latest_transition_event.event_id,
                            drifted.latest_transition_event.run_id,
                            drifted.latest_transition_event.ordinal,
                        ),
                    )
                else:
                    alternate = self.event(
                        current.state,
                        current.latest_transition_event.kind,
                        event_id="drift-latest-alternate",
                        ordinal=30,
                        occurred_at="2030-01-01T00:00:01.000000Z",
                    )
                    with self.unit_of_work() as unit_of_work:
                        unit_of_work.stores.execution.add_event(alternate)
                        unit_of_work.commit()
                    self.connection.execute(
                        """
                        UPDATE cpk_effect_attempts
                        SET latest_event_id=%s, latest_event_run_id=%s,
                            latest_event_ordinal=%s
                        WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1
                        """,
                        (alternate.event_id, alternate.run_id, alternate.ordinal),
                    )

                before = self.connection.execute(
                    "SELECT * FROM cpk_effect_attempts "
                    "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1"
                ).fetchone()
                with self.unit_of_work() as unit_of_work:
                    self.assertIsNone(
                        unit_of_work.stores.effect_attempts.compare_and_set(
                            current,
                            replacement,
                        )
                    )
                    unit_of_work.commit()
                after = self.connection.execute(
                    "SELECT * FROM cpk_effect_attempts "
                    "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1"
                ).fetchone()
                self.assertEqual(after, before)

    def test_decoder_translation_is_narrow_symmetric_and_candidate_free(self) -> None:
        self.require_store()
        current = self.record(event_prefix="decode", original_ordinal=10)
        settled = self.transition(
            current,
            "succeeded",
            event_id="decode-latest",
            ordinal=20,
        )
        self.persist(current)
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_event(settled.latest_transition_event)
            self.assertEqual(
                unit_of_work.stores.effect_attempts.compare_and_set(current, settled),
                settled,
            )
            unit_of_work.commit()

        real_state = store_module.EffectAttemptState
        real_get_event = PostgresExecutionStore.get_event

        def scalar(error):
            def fail(*_args, **_kwargs):
                raise error

            store_module.EffectAttemptState = fail

        def event(role, error):
            target = getattr(settled, role).event_id

            def decode(instance, event_id):
                if event_id == target:
                    raise error
                return real_get_event(instance, event_id)

            PostgresExecutionStore.get_event = decode

        for method in ("get", "get_for_update"):
            for boundary in ("scalar", "original_start_event", "latest_transition_event"):
                for error_type in (
                    ValueError,
                    OperationsRecordError,
                    TypeError,
                    RuntimeError,
                ):
                    with self.subTest(
                        method=method,
                        boundary=boundary,
                        error=error_type.__name__,
                    ):
                        canary = error_type("decoder-canary")
                        if boundary == "scalar":
                            scalar(canary)
                        else:
                            event(boundary, canary)
                        try:
                            with self.unit_of_work() as unit_of_work:
                                if error_type in (ValueError, OperationsRecordError):
                                    with self.assertRaises(OperationsRecordError) as caught:
                                        getattr(unit_of_work.stores.effect_attempts, method)(
                                            settled.state.identity
                                        )
                                    self.assertEqual(
                                        str(caught.exception),
                                        "effect attempt row is invalid",
                                    )
                                    self.assert_safe_error(
                                        caught.exception,
                                        "decoder-canary",
                                    )
                                else:
                                    with self.assertRaises(error_type) as caught:
                                        getattr(unit_of_work.stores.effect_attempts, method)(
                                            settled.state.identity
                                        )
                                    self.assertIs(caught.exception, canary)
                        finally:
                            store_module.EffectAttemptState = real_state
                            PostgresExecutionStore.get_event = real_get_event


if __name__ == "__main__":
    unittest.main()
