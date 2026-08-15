from __future__ import annotations

import dataclasses
import unittest

from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    LifecycleOperationKind,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleIdempotencyConflict,
    RunLifecycleNotFound,
)
from control_plane_kit_operations.postgres import (
    GatewayKeyRotationStore,
    PostgresActivityHistoryStore,
    PostgresExecutionStore,
)
from control_plane_kit_operations.records import OperationsRecordError

from tests.activity_run_retry_interpreter_fixture import (
    ActivityRunRetryCommandService,
    PostgresActivityRunRetryFixture,
    retry_interpreter,
)
from tests.execution_lease_recovery_fixture import safe_error
from tests.execution_lease_recovery_fixture import (
    ExecutionLeaseRecoveryCommandService,
)


class PostgresActivityRunRetryFirstReplayTests(
    PostgresActivityRunRetryFixture,
    unittest.TestCase,
):
    def test_first_retry_persists_one_complete_linked_result(self) -> None:
        self.reset_retry_truth()
        service, sequence = self.retry_service_with_sequence(
            "run-b",
            "retry-decision",
            "run-b-opened",
            "retry-action",
        )

        result = service.execute(self.retry_command())

        self.assertFalse(result.replayed)
        self.assertEqual(sequence.calls, [
            "run-b",
            "retry-decision",
            "run-b-opened",
            "retry-action",
        ])
        self.assertEqual(result.request.identity.request_id, "request-a")
        self.assertEqual(result.prior_run.run_id, "run-a")
        self.assertIs(result.prior_run.status, ActivityRunStatus.FAILED)
        self.assertEqual(result.run.run_id, "run-b")
        self.assertIs(result.run.status, ActivityRunStatus.CLAIMED)
        self.assertEqual(result.run.retry.attempt, 2)
        self.assertEqual(result.run.retry.prior_run_id, "run-a")
        self.assertEqual(
            result.run.metadata.descriptor(),
            {"attempt": 2, "prior_run_id": "run-a"},
        )
        self.assertEqual(result.decision_event.event_id, "retry-decision")
        self.assertEqual(result.decision_event.run_id, "run-a")
        self.assertIs(
            result.decision_event.kind,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
        )
        self.assertEqual(result.opened_event.event_id, "run-b-opened")
        self.assertEqual(result.opened_event.run_id, "run-b")
        self.assertEqual(result.opened_event.ordinal, 1)
        self.assertIs(result.opened_event.kind, ActivityEventKind.RUN_OPENED)
        self.assertEqual(result.opened_event.evidence, result.run.metadata)
        self.assertEqual(
            result.run.created_at,
            result.decision_event.occurred_at,
        )
        self.assertEqual(
            result.run.created_at,
            result.opened_event.occurred_at,
        )
        self.assertEqual(result.run.created_at, result.action.created_at)
        self.assertIs(
            result.action.action_type,
            LifecycleOperationKind.RECORD_RECOVERY_DECISION,
        )
        self.assertEqual(result.action.actor_id, "operator-a")
        self.assertEqual(result.action.idempotency_key, "retry-a")
        self.assertEqual(
            result.action.intent_fingerprint,
            self.retry_command().intent_fingerprint(),
        )
        recovery = result.decision_event.recovery
        self.assertIsNotNone(recovery)
        self.assertIs(
            recovery.decision_kind,
            RecoveryDecisionKind.RETRY_AS_NEW_RUN,
        )
        self.assertEqual(recovery.prior_fence, ExecutionLeaseFence("worker-a", 7))
        self.assertEqual(recovery.replacement_fence, recovery.prior_fence)

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.assertEqual(stores.execution.get_run("run-b"), result.run)
            self.assertEqual(
                stores.execution.get_event("retry-decision"),
                result.decision_event,
            )
            self.assertEqual(
                stores.execution.get_event("run-b-opened"),
                result.opened_event,
            )
            self.assertEqual(
                stores.activity_history.action_for_idempotency(
                    "session-a", "retry-a"
                ),
                result.action,
            )
            unit_of_work.commit()
        self.assertEqual(len(self.snapshot()[3]), 2)

    def test_exact_replay_after_session_close_and_expiry_is_read_only(self) -> None:
        self.reset_retry_truth()
        command = self.retry_command()
        first = self.retry_service(
            "run-b", "retry-decision", "run-b-opened", "retry-action"
        ).execute(command)
        self.connection.execute(
            "UPDATE cpk_operation_sessions SET status = 'closed', "
            "closed_at = '2026-08-15T05:00:00Z' "
            "WHERE session_id = 'session-a'"
        )
        self.connection.execute(
            "UPDATE cpk_execution_requests SET lease_expires_at = "
            "'2000-01-01T00:00:00Z' WHERE request_id = 'request-a'"
        )
        before = self.snapshot()
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("retry replay sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            replay = ActivityRunRetryCommandService(
                self.unit_of_work,
                id_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("retry replay allocated identity")
                ),
            ).execute(command)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

        self.assertEqual(
            replay,
            dataclasses.replace(first, request=replay.request, replayed=True),
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(self.snapshot(), before)

    def test_replay_accepts_lawful_evolved_new_run_statuses(self) -> None:
        accepted = (
            "claimed",
            "running",
            "paused",
            "succeeded",
            "failed",
            "compensating",
            "compensated",
            "partially_failed",
            "uncompensated_failure",
            "cancelled",
        )
        for status in accepted:
            with self.subTest(status=status):
                self.reset_retry_truth()
                command = self.retry_command()
                self.retry_service(
                    "run-b", "retry-decision", "run-b-opened", "retry-action"
                ).execute(command)
                timing = (
                    ", started_at = '2026-08-15T04:30:01Z'"
                    if status not in {"claimed"}
                    else ""
                )
                settled = (
                    ", settled_at = '2026-08-15T04:30:02Z'"
                    if status
                    in {
                        "succeeded",
                        "compensated",
                        "partially_failed",
                        "uncompensated_failure",
                        "cancelled",
                    }
                    else ""
                )
                self.connection.execute(
                    f"UPDATE cpk_activity_runs SET status = %s{timing}{settled} "
                    "WHERE run_id = 'run-b'",
                    (status,),
                )
                replay = ActivityRunRetryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("evolved replay allocated identity")
                    ),
                ).execute(command)
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.run.status.value, status)

    def test_replay_revalidates_current_fence_and_complete_result_truth(self) -> None:
        mutations = {
            "replaced-fence": (
                lambda: self.connection.execute(
                    "UPDATE cpk_execution_requests SET claim_generation = 8 "
                    "WHERE request_id = 'request-a'"
                ),
                RunLifecycleConflict,
            ),
            "abandoned-fence": (
                lambda: self.connection.execute(
                    "UPDATE cpk_execution_requests SET status = 'abandoned', "
                    "claim_worker_id = NULL, claim_generation = NULL, "
                    "claimed_at = NULL, lease_expires_at = NULL "
                    "WHERE request_id = 'request-a'"
                ),
                RunLifecycleConflict,
            ),
            "missing-prior": (self._delete_retry_runs, RunLifecycleNotFound),
            "missing-new": (self._delete_new_run, RunLifecycleNotFound),
            "missing-decision": (
                lambda: self.connection.execute(
                    "DELETE FROM cpk_activity_events "
                    "WHERE event_id = 'retry-decision'"
                ),
                RunLifecycleNotFound,
            ),
            "missing-opened": (
                lambda: self.connection.execute(
                    "DELETE FROM cpk_activity_events "
                    "WHERE event_id = 'run-b-opened'"
                ),
                RunLifecycleNotFound,
            ),
            "attempt": (
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_runs SET attempt = 3, metadata = "
                    "'{\"attempt\":3,\"prior_run_id\":\"run-a\"}'::jsonb "
                    "WHERE run_id = 'run-b'"
                ),
                RunLifecycleConflict,
            ),
            "action-coordinate": (
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET payload = "
                    "jsonb_set(payload, '{execution_request_id}', "
                    "'\"request-canary\"'::jsonb) "
                    "WHERE action_id = 'retry-action'"
                ),
                RunLifecycleConflict,
            ),
            "action-actor": (
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET actor_id = "
                    "'actor-canary' WHERE action_id = 'retry-action'"
                ),
                RunLifecycleConflict,
            ),
            "event-time": (
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_events SET occurred_at = "
                    "occurred_at + interval '1 second' "
                    "WHERE event_id = 'run-b-opened'"
                ),
                RunLifecycleConflict,
            ),
            "recovery": (
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_events SET payload = "
                    "jsonb_set(payload, '{recovery,replacement_fence,generation}', "
                    "'8'::jsonb) WHERE event_id = 'retry-decision'"
                ),
                RunLifecycleConflict,
            ),
        }
        for label, (mutate, expected) in mutations.items():
            with self.subTest(mutation=label):
                self.reset_retry_truth()
                command = self.retry_command()
                self.retry_service(
                    "run-b", "retry-decision", "run-b-opened", "retry-action"
                ).execute(command)
                mutate()
                before = self.snapshot()
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("invalid replay sampled database time")

                PostgresExecutionStore.observe_request_lease_for_update = (
                    fail_observe
                )
                try:
                    with self.assertRaises(expected) as raised:
                        ActivityRunRetryCommandService(
                            self.unit_of_work,
                            id_factory=lambda: (_ for _ in ()).throw(
                                AssertionError("invalid replay allocated identity")
                            ),
                        ).execute(command)
                finally:
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )
                safe_error(
                    self,
                    raised.exception,
                    "request-canary",
                    "actor-canary",
                    "authority-reference-a",
                )
                self.assertEqual(self.snapshot(), before)

    def _delete_retry_runs(self) -> None:
        self.connection.execute(
            "DELETE FROM cpk_activity_events WHERE run_id IN ('run-a', 'run-b')"
        )
        self.connection.execute(
            "DELETE FROM cpk_activity_runs WHERE run_id = 'run-b'"
        )
        self.connection.execute(
            "DELETE FROM cpk_activity_runs WHERE run_id = 'run-a'"
        )

    def _delete_new_run(self) -> None:
        self.connection.execute(
            "DELETE FROM cpk_activity_events WHERE run_id = 'run-b'"
        )
        self.connection.execute(
            "DELETE FROM cpk_activity_runs WHERE run_id = 'run-b'"
        )

    def test_replay_selector_corruption_is_categorical_and_raw_errors_escape(
        self,
    ) -> None:
        cases = (
            (OperationsRecordError("selector-record-canary"), RunLifecycleConflict),
            (RuntimeError("selector-runtime-canary"), RuntimeError),
        )
        for injected, expected in cases:
            with self.subTest(error=type(injected).__name__):
                self.reset_retry_truth()
                command = self.retry_command()
                self.retry_service(
                    "run-b", "retry-decision", "run-b-opened", "retry-action"
                ).execute(command)
                before = self.snapshot()
                original = PostgresExecutionStore.get_run_for_request_for_update
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def fail_selector(*_args, **_kwargs):
                    raise injected

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("selector failure sampled database time")

                PostgresExecutionStore.get_run_for_request_for_update = fail_selector
                PostgresExecutionStore.observe_request_lease_for_update = (
                    fail_observe
                )
                try:
                    with self.assertRaises(expected) as raised:
                        ActivityRunRetryCommandService(
                            self.unit_of_work,
                            id_factory=lambda: (_ for _ in ()).throw(
                                AssertionError("selector failure allocated identity")
                            ),
                        ).execute(command)
                finally:
                    PostgresExecutionStore.get_run_for_request_for_update = original
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )
                if type(injected) is RuntimeError:
                    self.assertIs(raised.exception, injected)
                else:
                    safe_error(self, raised.exception, "selector-record-canary")
                self.assertEqual(self.snapshot(), before)

    def test_foreign_action_new_run_does_not_lock_foreign_row(self) -> None:
        self.reset_retry_truth()
        command = self.retry_command()
        self.retry_service(
            "run-b", "retry-decision", "run-b-opened", "retry-action"
        ).execute(command)
        self.seed_foreign_run()
        before = self.snapshot()
        original_lookup = PostgresActivityHistoryStore.action_for_idempotency
        original_selector = PostgresExecutionStore.get_run_for_request_for_update
        original_observe = PostgresExecutionStore.observe_request_lease_for_update
        calls: list[tuple[str, str]] = []

        def foreign_action(store, session_id, idempotency_key):
            action = original_lookup(store, session_id, idempotency_key)
            assert action is not None
            return dataclasses.replace(
                action,
                payload={**action.payload, "run_id": "run-foreign"},
            )

        def selector(store, request_id, run_id):
            calls.append((request_id, run_id))
            if run_id == "run-foreign":
                import psycopg

                with psycopg.connect(self.database_url) as probe:
                    row = probe.execute(
                        "SELECT run_id FROM cpk_activity_runs "
                        "WHERE run_id = 'run-foreign' FOR UPDATE NOWAIT"
                    ).fetchone()
                    self.assertEqual(row, ("run-foreign",))
                    probe.rollback()
            return original_selector(store, request_id, run_id)

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("foreign replay sampled database time")

        PostgresActivityHistoryStore.action_for_idempotency = foreign_action
        PostgresExecutionStore.get_run_for_request_for_update = selector
        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with self.assertRaises(RunLifecycleNotFound) as raised:
                ActivityRunRetryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("foreign replay allocated identity")
                    ),
                ).execute(command)
        finally:
            PostgresActivityHistoryStore.action_for_idempotency = original_lookup
            PostgresExecutionStore.get_run_for_request_for_update = original_selector
            PostgresExecutionStore.observe_request_lease_for_update = original_observe
        self.assertEqual(calls, [("request-a", "run-a"), ("request-a", "run-foreign")])
        safe_error(self, raised.exception, "run-foreign")
        self.assertEqual(self.snapshot(), before)

    def test_same_key_changed_intent_conflicts_without_mutation(self) -> None:
        self.reset_retry_truth()
        self.retry_service(
            "run-b", "retry-decision", "run-b-opened", "retry-action"
        ).execute(self.retry_command())
        before = self.snapshot()
        changed = self.retry_command(
            expected_fence=ExecutionLeaseFence("worker-a", 8),
        )
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("changed replay sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with self.assertRaises(RunLifecycleIdempotencyConflict) as raised:
                ActivityRunRetryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("changed intent allocated identity")
                    ),
                ).execute(changed)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe
        safe_error(self, raised.exception, "authority-reference-a", "worker-a")
        self.assertEqual(self.snapshot(), before)

    def test_both_approval_subjects_are_admitted(self) -> None:
        originals = {
            name: getattr(GatewayKeyRotationStore, name)
            for name in ("get", "get_for_update")
        }

        def fail_gateway_read(*_args, **_kwargs):
            raise AssertionError("retry read or locked mutable gateway rotation")

        for name in originals:
            setattr(GatewayKeyRotationStore, name, fail_gateway_read)
        try:
            for subject in ("activity-plan", "gateway-key-rotation"):
                with self.subTest(subject=subject):
                    self.reset_retry_truth(approval_subject=subject)
                    result = self.retry_service(
                        f"run-{subject}",
                        f"decision-{subject}",
                        f"opened-{subject}",
                        f"action-{subject}",
                    ).execute(self.retry_command(key=f"retry-{subject}"))
                    self.assertFalse(result.replayed)
        finally:
            for name, method in originals.items():
                setattr(GatewayKeyRotationStore, name, method)

    def test_retry_delegates_approval_and_journal_to_shared_support(self) -> None:
        self.require_retry_service()
        original_approval = retry_interpreter.locked_recovery_approval
        original_journal = retry_interpreter.require_recovery_eligible_journal
        calls: list[tuple[str, object]] = []

        def approval(*args, **kwargs):
            calls.append(("approval", args[1].identity.request_id))
            return original_approval(*args, **kwargs)

        def journal(*args, **kwargs):
            calls.append(("journal", args[0]))
            return original_journal(*args, **kwargs)

        retry_interpreter.locked_recovery_approval = approval
        retry_interpreter.require_recovery_eligible_journal = journal
        try:
            for subject in ("activity-plan", "gateway-key-rotation"):
                self.reset_retry_truth(approval_subject=subject)
                self.retry_service(
                    f"run-{subject}",
                    f"decision-{subject}",
                    f"opened-{subject}",
                    f"action-{subject}",
                ).execute(self.retry_command(key=f"retry-{subject}"))
        finally:
            retry_interpreter.locked_recovery_approval = original_approval
            retry_interpreter.require_recovery_eligible_journal = original_journal

        self.assertEqual(
            calls,
            [
                ("approval", "request-a"),
                ("journal", RecoveryDecisionKind.RETRY_AS_NEW_RUN),
                ("approval", "request-a"),
                ("journal", RecoveryDecisionKind.RETRY_AS_NEW_RUN),
            ],
        )

    def test_approval_corruption_rejects_before_clock_ids_and_writes(self) -> None:
        self.require_retry_service()
        mutations = {
            "subject": lambda: self.connection.execute(
                "UPDATE cpk_approval_requests SET review_digest = %s "
                "WHERE request_id = 'approval-request-a'",
                ("f" * 64,),
            ),
            "scope": lambda: self.connection.execute(
                "UPDATE cpk_approval_decisions SET scope = "
                "'plan:approve-destructive' "
                "WHERE decision_id = 'approval-decision-a'"
            ),
            "decision": lambda: self.connection.execute(
                "UPDATE cpk_approval_decisions SET decision = 'rejected' "
                "WHERE decision_id = 'approval-decision-a'"
            ),
            "plan-link": self._drift_approval_session,
        }
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("invalid retry approval sampled database time")

        for subject in ("activity-plan", "gateway-key-rotation"):
            for label, mutate in mutations.items():
                with self.subTest(subject=subject, mutation=label):
                    self.reset_retry_truth(approval_subject=subject)
                    mutate()
                    before = self.snapshot()
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        fail_observe
                    )
                    try:
                        with self.assertRaises(RunLifecycleConflict) as raised:
                            ActivityRunRetryCommandService(
                                self.unit_of_work,
                                id_factory=lambda: (_ for _ in ()).throw(
                                    AssertionError(
                                        "invalid retry approval allocated identity"
                                    )
                                ),
                            ).execute(self.retry_command())
                    finally:
                        PostgresExecutionStore.observe_request_lease_for_update = (
                            original_observe
                        )
                    safe_error(self, raised.exception, "f" * 32, "rotation-a")
                    self.assertEqual(self.snapshot(), before)

    def _drift_approval_session(self) -> None:
        self.connection.execute(
            "INSERT INTO cpk_operation_sessions "
            "(session_id, workspace_id, actor_id, title, status, created_at, "
            "metadata) VALUES ('session-b', 'workspace-a', 'operator-a', "
            "'Foreign', 'open', '2026-08-15T03:55:01Z', '{}'::jsonb)"
        )
        self.connection.execute(
            "UPDATE cpk_approval_requests SET session_id = 'session-b' "
            "WHERE request_id = 'approval-request-a'"
        )

    def test_retry_and_lease_rotation_preserve_both_temporal_worlds(self) -> None:
        self.reset_retry_truth()
        retry = self.retry_service(
            "run-b", "retry-decision", "run-b-opened", "retry-action"
        ).execute(self.retry_command())
        self.connection.execute(
            "UPDATE cpk_execution_requests SET lease_expires_at = "
            "'2000-01-01T00:00:00Z' WHERE request_id = 'request-a'"
        )
        before = self.snapshot()
        with self.assertRaises(RunLifecycleConflict):
            ExecutionLeaseRecoveryCommandService(
                self.unit_of_work,
                id_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("post-retry rotation allocated identity")
                ),
            ).execute(
                self.command(
                    RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                    key="rotate-after-retry",
                    retained_run_id="run-a",
                )
            )
        self.assertEqual(retry.run.run_id, "run-b")
        self.assertEqual(self.snapshot(), before)

        self.reset_retry_truth()
        self.connection.execute(
            "UPDATE cpk_execution_requests SET lease_expires_at = "
            "'2000-01-01T00:00:00Z' WHERE request_id = 'request-a'"
        )
        rotated = self.service(
            "rotate-decision", "rotate-consequence", "rotate-action"
        ).execute(
            self.command(
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                key="rotate-first",
            )
        )
        before = self.snapshot()
        with self.assertRaises(RunLifecycleConflict):
            ActivityRunRetryCommandService(
                self.unit_of_work,
                id_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("stale retry allocated identity")
                ),
            ).execute(self.retry_command())
        self.assertEqual(rotated.request.claim.fence.generation, 8)
        self.assertEqual(self.snapshot(), before)

    def test_arbitrary_newer_run_rejects_the_presented_prior(self) -> None:
        self.reset_retry_truth()
        self.add_newer_failed_run()
        before = self.snapshot()
        with self.assertRaises(RunLifecycleConflict) as raised:
            self.retry_service("unused-a", "unused-b", "unused-c", "unused-d").execute(
                self.retry_command()
            )
        safe_error(self, raised.exception, "run-a", "run-b")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
