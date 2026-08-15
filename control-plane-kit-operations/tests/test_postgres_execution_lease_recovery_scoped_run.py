from __future__ import annotations

import dataclasses
import unittest

import psycopg

from control_plane_kit_core.operations.lifecycle import (
    ActivityRunStatus,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleNotFound,
)
from control_plane_kit_operations.postgres import (
    PostgresActivityHistoryStore,
    PostgresExecutionStore,
)
from control_plane_kit_operations.records import (
    ActivityRunRecord,
    AdmittedRun,
    OperationsRecordError,
    RetryIdentity,
)

from tests.execution_lease_recovery_fixture import (
    ExecutionLeaseRecoveryCommandService,
    PostgresExecutionLeaseRecoveryFixture,
    safe_error,
)


class PostgresExecutionLeaseRecoveryScopedRunTests(
    PostgresExecutionLeaseRecoveryFixture,
    unittest.TestCase,
):
    def seed_foreign_run(self) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               desired_graph_revision, status, created_at, payload)
            SELECT 'plan-b', session_id, base_graph_id, desired_graph_id,
                   base_realized_projection_id, desired_realized_projection_id,
                   desired_graph_revision, status, created_at, payload
            FROM cpk_activity_plans
            WHERE plan_id = 'plan-a'
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claim_generation, claimed_at, lease_expires_at)
            SELECT 'request-b', workspace_id, session_id, 'plan-b', status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, 'execute-b', intent_fingerprint,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests
            WHERE request_id = 'request-a'
            """
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_run(
                ActivityRunRecord(
                    "run-b",
                    "plan-b",
                    AdmittedRun("request-b"),
                    RetryIdentity(1),
                    ActivityRunStatus.CLAIMED,
                    "2026-08-15T04:20:00Z",
                )
            )
            unit_of_work.commit()

    def test_foreign_request_pair_miss_does_not_lock_foreign_run(self) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        self.seed_foreign_run()

        selector_connection = psycopg.connect(self.database_url)
        probe_connection = psycopg.connect(self.database_url)
        try:
            selector = PostgresExecutionStore(selector_connection)
            with self.assertRaises(KeyError) as captured:
                selector.get_run_for_request_for_update("request-a", "run-b")
            safe_error(self, captured.exception, "request-a", "run-b")
            row = probe_connection.execute(
                "SELECT run_id FROM cpk_activity_runs "
                "WHERE run_id = 'run-b' FOR UPDATE NOWAIT"
            ).fetchone()
            self.assertEqual(row, ("run-b",))
            probe_connection.rollback()
            selector_connection.rollback()
        finally:
            selector_connection.close()
            probe_connection.close()

    def test_scoped_run_miss_translates_without_clock_ids_or_writes(self) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        command = self.command(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            key="recover-a",
        )
        self.service("decision-a", "consequence-a", "action-a").execute(command)
        before = self.snapshot()
        original_selector = getattr(
            PostgresExecutionStore,
            "get_run_for_request_for_update",
            None,
        )
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def missing(*_args, **_kwargs):
            raise KeyError("store-miss-canary")

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("scoped miss sampled database time")

        PostgresExecutionStore.get_run_for_request_for_update = missing
        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with self.assertRaises(RunLifecycleNotFound) as captured:
                ExecutionLeaseRecoveryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("scoped miss allocated identity")
                    ),
                ).execute(command)
        finally:
            if original_selector is None:
                delattr(PostgresExecutionStore, "get_run_for_request_for_update")
            else:
                PostgresExecutionStore.get_run_for_request_for_update = (
                    original_selector
                )
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

        self.assertEqual(
            str(captured.exception),
            "recovery retained run was not found",
        )
        safe_error(self, captured.exception, "store-miss-canary")
        self.assertEqual(self.snapshot(), before)

    def test_scoped_run_corruption_is_categorical_and_unexpected_errors_escape(
        self,
    ) -> None:
        cases = (
            (
                OperationsRecordError("selector-record-canary"),
                RunLifecycleConflict,
            ),
            (RuntimeError("selector-runtime-canary"), RuntimeError),
        )
        for injected, expected in cases:
            with self.subTest(error=type(injected).__name__):
                self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
                command = self.command(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    key="recover-a",
                )
                self.service("decision-a", "consequence-a", "action-a").execute(
                    command
                )
                before = self.snapshot()
                self.assertTrue(
                    hasattr(
                        PostgresExecutionStore,
                        "get_run_for_request_for_update",
                    ),
                    "request-scoped activity-run selector is missing",
                )
                original_selector = (
                    PostgresExecutionStore.get_run_for_request_for_update
                )
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def fail_selector(*_args, **_kwargs):
                    raise injected

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("selector failure sampled database time")

                PostgresExecutionStore.get_run_for_request_for_update = (
                    fail_selector
                )
                PostgresExecutionStore.observe_request_lease_for_update = (
                    fail_observe
                )
                try:
                    with self.assertRaises(expected) as captured:
                        ExecutionLeaseRecoveryCommandService(
                            self.unit_of_work,
                            id_factory=lambda: (_ for _ in ()).throw(
                                AssertionError(
                                    "selector failure allocated identity"
                                )
                            ),
                        ).execute(command)
                finally:
                    PostgresExecutionStore.get_run_for_request_for_update = (
                        original_selector
                    )
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )

                if type(injected) is RuntimeError:
                    self.assertIs(captured.exception, injected)
                else:
                    self.assertEqual(
                        str(captured.exception),
                        "recovery retained run history is invalid",
                    )
                    safe_error(
                        self,
                        captured.exception,
                        "selector-record-canary",
                    )
                self.assertEqual(self.snapshot(), before)

    def test_decoder_value_errors_are_categorical_before_clock_ids_or_writes(
        self,
    ) -> None:
        cases = (
            (
                PostgresActivityHistoryStore,
                "action_for_idempotency",
                True,
                "recovery action history is invalid",
            ),
            (
                PostgresActivityHistoryStore,
                "get_session_for_update",
                False,
                "operation session history is invalid",
            ),
            (
                PostgresExecutionStore,
                "get_request",
                False,
                "execution request history is invalid",
            ),
            (
                PostgresExecutionStore,
                "get_request_for_update",
                False,
                "execution request history is invalid",
            ),
            (
                PostgresExecutionStore,
                "get_latest_run_for_request_for_update",
                False,
                "activity run history is invalid",
            ),
            (
                PostgresExecutionStore,
                "get_run_for_request_for_update",
                True,
                "recovery retained run history is invalid",
            ),
        )
        for store_type, method_name, replay, expected_message in cases:
            with self.subTest(read=method_name):
                self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
                command = self.command(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    key="recover-a",
                )
                if replay:
                    self.service(
                        "decision-a",
                        "consequence-a",
                        "action-a",
                    ).execute(command)
                before = self.snapshot()
                original_read = getattr(store_type, method_name)
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )
                canary = f"{method_name}-value-canary"

                def fail_read(*_args, **_kwargs):
                    raise ValueError(canary)

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError(
                        f"{method_name} failure sampled database time"
                    )

                setattr(store_type, method_name, fail_read)
                PostgresExecutionStore.observe_request_lease_for_update = (
                    fail_observe
                )
                try:
                    with self.assertRaises(RunLifecycleConflict) as captured:
                        ExecutionLeaseRecoveryCommandService(
                            self.unit_of_work,
                            id_factory=lambda: (_ for _ in ()).throw(
                                AssertionError(
                                    f"{method_name} failure allocated identity"
                                )
                            ),
                        ).execute(command)
                finally:
                    setattr(store_type, method_name, original_read)
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )

                self.assertEqual(str(captured.exception), expected_message)
                safe_error(self, captured.exception, canary)
                self.assertEqual(self.snapshot(), before)

    def test_public_replay_rejects_foreign_run_without_locking_it(self) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        original_command = self.command(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            key="recover-a",
        )
        self.service("decision-a", "consequence-a", "action-a").execute(
            original_command
        )
        self.seed_foreign_run()
        foreign_command = self.command(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            key="recover-a",
            retained_run_id="run-b",
        )
        before = self.snapshot()
        self.assertTrue(
            hasattr(PostgresExecutionStore, "get_run_for_request_for_update"),
            "request-scoped activity-run selector is missing",
        )
        original_action_lookup = (
            PostgresActivityHistoryStore.action_for_idempotency
        )
        original_selector = PostgresExecutionStore.get_run_for_request_for_update
        original_observe = PostgresExecutionStore.observe_request_lease_for_update
        calls: list[tuple[str, str]] = []

        def foreign_action(store, session_id, idempotency_key):
            action = original_action_lookup(store, session_id, idempotency_key)
            assert action is not None
            recovery = action.payload["recovery"]
            assert isinstance(recovery, dict)
            return dataclasses.replace(
                action,
                payload={
                    **action.payload,
                    "retained_run_id": "run-b",
                    "recovery": {
                        **recovery,
                        "retained_run_id": "run-b",
                    },
                },
                intent_fingerprint=foreign_command.intent_fingerprint(),
            )

        def scoped_selector(store, request_id, run_id):
            calls.append((request_id, run_id))
            probe = psycopg.connect(self.database_url)
            try:
                row = probe.execute(
                    "SELECT run_id FROM cpk_activity_runs "
                    "WHERE run_id = 'run-b' FOR UPDATE NOWAIT"
                ).fetchone()
                self.assertEqual(row, ("run-b",))
                probe.rollback()
            finally:
                probe.close()
            return original_selector(store, request_id, run_id)

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("foreign replay sampled database time")

        PostgresActivityHistoryStore.action_for_idempotency = foreign_action
        PostgresExecutionStore.get_run_for_request_for_update = scoped_selector
        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with self.assertRaises(RunLifecycleNotFound) as captured:
                ExecutionLeaseRecoveryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("foreign replay allocated identity")
                    ),
                ).execute(foreign_command)
        finally:
            PostgresActivityHistoryStore.action_for_idempotency = (
                original_action_lookup
            )
            PostgresExecutionStore.get_run_for_request_for_update = (
                original_selector
            )
            PostgresExecutionStore.observe_request_lease_for_update = (
                original_observe
            )

        self.assertEqual(calls, [("request-a", "run-b")])
        self.assertEqual(
            str(captured.exception),
            "recovery retained run was not found",
        )
        safe_error(self, captured.exception, "request-a", "run-b")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
