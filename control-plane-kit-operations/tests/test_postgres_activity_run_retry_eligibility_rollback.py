from __future__ import annotations

import unittest

import psycopg

from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_operations.lifecycle import RunLifecycleConflict
from control_plane_kit_operations.postgres import (
    PostgresActivityHistoryStore,
    PostgresExecutionStore,
    PostgresUnitOfWork,
)

from tests.activity_run_retry_interpreter_fixture import (
    ActivityRunRetryCommandService,
    PostgresActivityRunRetryFixture,
)
from tests.execution_lease_recovery_fixture import safe_error


class RawDependencyFailure(RuntimeError):
    pass


class PostgresActivityRunRetryEligibilityRollbackTests(
    PostgresActivityRunRetryFixture,
    unittest.TestCase,
):
    def test_non_temporal_rejections_precede_clock_ids_and_writes(self) -> None:
        self.require_retry_service()
        cases = (
            ("wrong-fence", None),
            ("wrong-status", None),
            ("malformed-journal", "duplicate-start"),
            ("attempt-exhausted", None),
        )
        original_observe = PostgresExecutionStore.observe_request_lease_for_update
        original_add_run = PostgresExecutionStore.add_run

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("rejected retry sampled database time")

        def fail_add_run(*_args, **_kwargs):
            raise AssertionError("rejected retry wrote a run")

        for label, history in cases:
            with self.subTest(label=label):
                self.reset_retry_truth(history=history or "failed")
                PostgresExecutionStore.observe_request_lease_for_update = fail_observe
                PostgresExecutionStore.add_run = fail_add_run
                try:
                    command = self.retry_command()
                    if label == "wrong-fence":
                        self.connection.execute(
                            "UPDATE cpk_execution_requests SET claim_generation = 8 "
                            "WHERE request_id = 'request-a'"
                        )
                    elif label == "wrong-status":
                        self.connection.execute(
                            "UPDATE cpk_activity_runs SET status = 'running', "
                            "settled_at = NULL WHERE run_id = 'run-a'"
                        )
                    elif label == "attempt-exhausted":
                        self.connection.execute(
                            "INSERT INTO cpk_activity_runs "
                            "(run_id, plan_id, request_id, attempt, prior_run_id, "
                            "status, created_at, started_at, settled_at, metadata) "
                            "VALUES ('run-z', 'plan-a', 'request-a', 2, 'run-a', "
                            "'failed', '2026-08-15T03:59:11Z', "
                            "'2026-08-15T03:59:12Z', NULL, "
                            "'{\"attempt\":2,\"prior_run_id\":\"run-a\"}'::jsonb)"
                        )
                        self.connection.execute(
                            "UPDATE cpk_activity_runs SET attempt = 2147483647, "
                            "prior_run_id = 'run-z', metadata = "
                            "'{\"attempt\":2147483647," 
                            "\"prior_run_id\":\"run-z\"}'::jsonb "
                            "WHERE run_id = 'run-a'"
                        )
                    before = self.snapshot()
                    service = ActivityRunRetryCommandService(
                        self.unit_of_work,
                        id_factory=lambda: (_ for _ in ()).throw(
                            AssertionError("rejected retry allocated identity")
                        ),
                    )
                    with self.assertRaises(RunLifecycleConflict) as raised:
                        service.execute(command)
                    safe_error(
                        self,
                        raised.exception,
                        "authority-reference-a",
                        "worker-a",
                        "run-a",
                    )
                    self.assertEqual(self.snapshot(), before)
                finally:
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )
                    PostgresExecutionStore.add_run = original_add_run

    def test_expired_claim_rejects_after_one_observation_before_ids(self) -> None:
        self.require_retry_service()
        self.reset_retry_truth()
        self.connection.execute(
            "UPDATE cpk_execution_requests SET lease_expires_at = "
            "'2000-01-01T00:00:00Z' WHERE request_id = 'request-a'"
        )
        calls = 0
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def count_observe(store, request_id):
            nonlocal calls
            calls += 1
            return original_observe(store, request_id)

        PostgresExecutionStore.observe_request_lease_for_update = count_observe
        try:
            before = self.snapshot()
            with self.assertRaises(RunLifecycleConflict) as raised:
                ActivityRunRetryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("expired retry allocated identity")
                    ),
                ).execute(self.retry_command())
            safe_error(self, raised.exception, "worker-a")
            self.assertEqual(calls, 1)
            self.assertEqual(self.snapshot(), before)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

    def test_all_four_identities_are_allocated_before_first_write(self) -> None:
        self.require_retry_service()
        self.reset_retry_truth()
        allocated = iter(("run-b", "retry-decision", "run-b-opened"))
        raw = RawDependencyFailure("identity-canary-secret")
        original_add_run = PostgresExecutionStore.add_run

        def fail_if_written(*_args, **_kwargs):
            raise AssertionError("retry wrote before complete result planning")

        def identity():
            try:
                return next(allocated)
            except StopIteration:
                raise raw

        PostgresExecutionStore.add_run = fail_if_written
        try:
            before = self.snapshot()
            with self.assertRaises(RawDependencyFailure) as raised:
                ActivityRunRetryCommandService(
                    self.unit_of_work,
                    id_factory=identity,
                ).execute(self.retry_command())
            self.assertIs(raised.exception, raw)
            self.assertEqual(self.snapshot(), before)
        finally:
            PostgresExecutionStore.add_run = original_add_run

    def test_persistence_order_is_run_decision_opened_action(self) -> None:
        self.require_retry_service()
        self.reset_retry_truth()
        calls: list[str] = []
        original_add_run = PostgresExecutionStore.add_run
        original_add_event = PostgresExecutionStore.add_event
        original_add_action = PostgresActivityHistoryStore.add_action

        def add_run(store, record):
            calls.append("run")
            return original_add_run(store, record)

        def add_event(store, record):
            calls.append(f"event:{record.kind.value}")
            return original_add_event(store, record)

        def add_action(store, record):
            calls.append("action")
            return original_add_action(store, record)

        PostgresExecutionStore.add_run = add_run
        PostgresExecutionStore.add_event = add_event
        PostgresActivityHistoryStore.add_action = add_action
        try:
            self.retry_service(
                "run-b", "retry-decision", "run-b-opened", "retry-action"
            ).execute(self.retry_command())
        finally:
            PostgresExecutionStore.add_run = original_add_run
            PostgresExecutionStore.add_event = original_add_event
            PostgresActivityHistoryStore.add_action = original_add_action
        self.assertEqual(
            calls,
            [
                "run",
                "event:recovery_decision_recorded",
                "event:run_opened",
                "action",
            ],
        )

    def test_each_late_failure_escapes_and_rolls_back_every_record(self) -> None:
        self.require_retry_service()
        stages = ("run", "decision", "opened", "action", "commit")
        for stage in stages:
            with self.subTest(stage=stage):
                self.reset_retry_truth()
                before = self.snapshot()
                raw = RawDependencyFailure(f"late-{stage}-canary")
                original_add_run = PostgresExecutionStore.add_run
                original_add_event = PostgresExecutionStore.add_event
                original_add_action = PostgresActivityHistoryStore.add_action
                event_calls = 0

                def add_run(store, record):
                    if stage == "run":
                        raise raw
                    return original_add_run(store, record)

                def add_event(store, record):
                    nonlocal event_calls
                    event_calls += 1
                    if (stage == "decision" and event_calls == 1) or (
                        stage == "opened" and event_calls == 2
                    ):
                        raise raw
                    return original_add_event(store, record)

                def add_action(store, record):
                    if stage == "action":
                        raise raw
                    return original_add_action(store, record)

                PostgresExecutionStore.add_run = add_run
                PostgresExecutionStore.add_event = add_event
                PostgresActivityHistoryStore.add_action = add_action
                try:
                    factory = self.unit_of_work
                    if stage == "commit":
                        factory = self.commit_failing_unit_of_work(raw)
                    with self.assertRaises(RawDependencyFailure) as raised:
                        ActivityRunRetryCommandService(
                            factory,
                            id_factory=iter(
                                (
                                    "run-b",
                                    "retry-decision",
                                    "run-b-opened",
                                    "retry-action",
                                )
                            ).__next__,
                        ).execute(self.retry_command())
                    self.assertIs(raised.exception, raw)
                finally:
                    PostgresExecutionStore.add_run = original_add_run
                    PostgresExecutionStore.add_event = original_add_event
                    PostgresActivityHistoryStore.add_action = original_add_action
                self.assertEqual(self.snapshot(), before)

    def commit_failing_unit_of_work(self, error: BaseException):
        database_url = self.database_url

        class Connection:
            def __init__(self):
                self._connection = psycopg.connect(database_url)

            def execute(self, *args, **kwargs):
                return self._connection.execute(*args, **kwargs)

            def commit(self):
                raise error

            def rollback(self):
                return self._connection.rollback()

            def close(self):
                return self._connection.close()

        return lambda: PostgresUnitOfWork(Connection)


if __name__ == "__main__":
    unittest.main()
