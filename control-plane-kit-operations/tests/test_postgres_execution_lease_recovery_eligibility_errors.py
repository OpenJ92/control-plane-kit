from __future__ import annotations

import dataclasses

import psycopg

from control_plane_kit_core.operations.lifecycle import (
    RecoveryDecisionKind,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import RunLifecycleConflict
from control_plane_kit_operations.postgres import (
    PostgresActivityHistoryStore,
    PostgresExecutionStore,
    PostgresUnitOfWork,
)

from tests.execution_lease_recovery_fixture import (
    ExecutionLeaseRecoveryCommandService,
    PostgresExecutionLeaseRecoveryFixture,
    safe_error,
)


class PostgresExecutionLeaseRecoveryEligibilityErrorTests(
    PostgresExecutionLeaseRecoveryFixture
):
    def test_activity_journal_eligibility_is_exact(self) -> None:
        accepted = (
            (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, "active-empty"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "failed"),
            (
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "resolved-forward-failure",
            ),
        )
        for decision, history in accepted:
            with self.subTest(accepted=history):
                self.reset_truth(decision, history=history)
                result = self.service(
                    f"decision-{history}",
                    f"consequence-{history}",
                    f"action-{history}",
                ).execute(self.command(decision, key=f"recover-{history}"))
                self.assertFalse(result.replayed)

        rejected = (
            (
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "active-corruption-effect",
            ),
            (
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "active-run-started",
            ),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "in-flight"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "uncertain"),
            (
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "compensation-requested",
            ),
            (
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "compensation-completed",
            ),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "foreign-step"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "duplicate-start"),
            (
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "post-terminal-success",
            ),
            (
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "orphan-recovery-consequence",
            ),
        )
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("invalid journal sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            for decision, history in rejected:
                with self.subTest(rejected=history):
                    self.reset_truth(decision, history=history)
                    before = self.snapshot()
                    service, sequence = self.service_with_sequence(
                        "unused-a", "unused-b", "unused-c"
                    )
                    with self.assertRaises(RunLifecycleConflict) as captured:
                        service.execute(
                            self.command(decision, key=f"reject-{history}")
                        )
                    self.assertEqual(sequence.calls, [])
                    safe_error(
                        self,
                        captured.exception,
                        "foreign-step-canary",
                        "seed-event-4",
                    )
                    self.assertEqual(self.snapshot(), before)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = (
                original_observe
            )

    def test_malformed_persisted_journal_is_categorical_before_clock_or_ids(
        self,
    ) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        self.connection.execute(
            "UPDATE cpk_activity_events SET payload = "
            "jsonb_set(payload, '{evidence}', '"
            + '"journal-event-canary"'
            + "'::jsonb) WHERE event_id = 'seed-event-3'"
        )
        before = self.snapshot()
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("malformed journal sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        service, sequence = self.service_with_sequence(
            "unused-a", "unused-b", "unused-c"
        )
        try:
            with self.assertRaises(RunLifecycleConflict) as captured:
                service.execute(
                    self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                )
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = (
                original_observe
            )
        self.assertEqual(sequence.calls, [])
        safe_error(self, captured.exception, "journal-event-canary")
        self.assertEqual(self.snapshot(), before)

    def test_request_run_expiry_fence_and_generation_matrix_is_exact(self) -> None:
        cases = (
            (
                "active-expired",
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "UPDATE cpk_execution_requests SET lease_expires_at = "
                "'2000-01-01T00:00:00Z' WHERE request_id = 'request-a'",
                {},
            ),
            (
                "expired-active",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "UPDATE cpk_execution_requests SET lease_expires_at = "
                "'2099-01-01T00:00:00Z' WHERE request_id = 'request-a'",
                {},
            ),
            (
                "active-run-status",
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                "UPDATE cpk_activity_runs SET status = 'running', "
                "started_at = '2026-08-15T03:59:20Z' WHERE run_id = 'run-a'",
                {},
            ),
            (
                "expired-run-status",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "UPDATE cpk_activity_runs SET status = 'running' "
                "WHERE run_id = 'run-a'",
                {},
            ),
            (
                "request-status",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "UPDATE cpk_execution_requests SET status = 'queued', "
                "claim_worker_id = NULL, claim_generation = NULL, "
                "claimed_at = NULL, lease_expires_at = NULL "
                "WHERE request_id = 'request-a'",
                {},
            ),
            (
                "foreign-worker",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                None,
                {"expected_fence": ExecutionLeaseFence("worker-z", 7)},
            ),
            (
                "foreign-generation",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                None,
                {"expected_fence": ExecutionLeaseFence("worker-a", 6)},
            ),
            (
                "generation-exhausted",
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                "UPDATE cpk_execution_requests SET claim_generation = "
                "9223372036854775807 WHERE request_id = 'request-a'",
                {
                    "expected_fence": ExecutionLeaseFence(
                        "worker-a", 9223372036854775807
                    )
                },
            ),
        )
        for name, decision, mutation, command_changes in cases:
            with self.subTest(name=name):
                self.reset_truth(decision)
                if mutation is not None:
                    self.connection.execute(mutation)
                before = self.snapshot()
                service, sequence = self.service_with_sequence(
                    "unused-a", "unused-b", "unused-c"
                )
                with self.assertRaises(RunLifecycleConflict) as captured:
                    service.execute(self.command(decision, **command_changes))
                self.assertEqual(sequence.calls, [])
                safe_error(self, captured.exception)
                self.assertEqual(self.snapshot(), before)

    def test_retained_run_must_be_authoritative_latest_run(self) -> None:
        self.seed_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        self.add_newer_failed_run()
        before = self.snapshot()
        service, sequence = self.service_with_sequence(
            "unused-a", "unused-b", "unused-c"
        )
        with self.assertRaises(RunLifecycleConflict) as captured:
            service.execute(
                self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
            )
        self.assertEqual(sequence.calls, [])
        safe_error(self, captured.exception)
        self.assertEqual(self.snapshot(), before)

    def test_expiry_equality_is_expired_for_all_four_decisions(self) -> None:
        self.require_service()
        decisions = (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        )
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def observe_at_expiry(store, request_id):
            observed = original_observe(store, request_id)
            return dataclasses.replace(
                observed,
                observed_at=observed.request.claim.lease_expires_at,
                expired=True,
            )

        PostgresExecutionStore.observe_request_lease_for_update = observe_at_expiry
        try:
            for decision in decisions:
                with self.subTest(decision=decision):
                    self.reset_truth(decision)
                    before = self.snapshot()
                    service, sequence = self.service_with_sequence(
                        "decision-a", "consequence-a", "action-a"
                    )
                    if decision is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM:
                        with self.assertRaises(RunLifecycleConflict) as captured:
                            service.execute(self.command(decision))
                        safe_error(self, captured.exception)
                        self.assertEqual(sequence.calls, [])
                        self.assertEqual(self.snapshot(), before)
                    else:
                        result = service.execute(self.command(decision))
                        self.assertFalse(result.replayed)
                        self.assertEqual(
                            sequence.calls,
                            ["decision-a", "consequence-a", "action-a"],
                        )
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

    def test_nonexpiry_rejections_do_not_sample_database_clock(self) -> None:
        self.require_service()
        cases = (
            (
                "wrong-run-status",
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_runs SET status = 'running' "
                    "WHERE run_id = 'run-a'"
                ),
                {},
            ),
            (
                "wrong-request-status",
                lambda: self.connection.execute(
                    "UPDATE cpk_execution_requests SET status = 'queued', "
                    "claim_worker_id = NULL, claim_generation = NULL, "
                    "claimed_at = NULL, lease_expires_at = NULL "
                    "WHERE request_id = 'request-a'"
                ),
                {},
            ),
            (
                "wrong-fence",
                lambda: None,
                {"expected_fence": ExecutionLeaseFence("worker-z", 7)},
            ),
            (
                "wrong-generation",
                lambda: None,
                {"expected_fence": ExecutionLeaseFence("worker-a", 6)},
            ),
            (
                "generation-exhausted",
                lambda: self.connection.execute(
                    "UPDATE cpk_execution_requests SET claim_generation = "
                    "9223372036854775807 WHERE request_id = 'request-a'"
                ),
                {
                    "expected_fence": ExecutionLeaseFence(
                        "worker-a", 9223372036854775807
                    )
                },
            ),
            ("latest-run", self.add_newer_failed_run, {}),
            (
                "approval-drift",
                lambda: self.connection.execute(
                    "UPDATE cpk_approval_requests SET review_digest = %s "
                    "WHERE request_id = 'approval-request-a'",
                    ("f" * 64,),
                ),
                {},
            ),
        )
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("non-expiry rejection sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            for name, setup, command_changes in cases:
                with self.subTest(name=name):
                    self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                    setup()
                    before = self.snapshot()
                    service, sequence = self.service_with_sequence(
                        "unused-a", "unused-b", "unused-c"
                    )
                    with self.assertRaises(RunLifecycleConflict):
                        service.execute(
                            self.command(
                                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                                **command_changes,
                            )
                        )
                    self.assertEqual(sequence.calls, [])
                    self.assertEqual(self.snapshot(), before)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

    def test_cas_miss_is_categorical_and_rolls_back(self) -> None:
        self.require_service()
        for decision, method_name in (
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "rotate_request_claim"),
            (RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM, "abandon_request_claim"),
        ):
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                before = self.snapshot()
                original = getattr(PostgresExecutionStore, method_name)
                setattr(PostgresExecutionStore, method_name, lambda *_a, **_k: None)
                service, sequence = self.service_with_sequence(
                    "unused-a", "unused-b", "unused-c"
                )
                try:
                    with self.assertRaises(RunLifecycleConflict) as captured:
                        service.execute(self.command(decision))
                finally:
                    setattr(PostgresExecutionStore, method_name, original)
                self.assertEqual(
                    sequence.calls,
                    ["unused-a", "unused-b", "unused-c"],
                )
                safe_error(self, captured.exception)
                self.assertEqual(self.snapshot(), before)

    def test_every_durable_write_failure_rolls_back_complete_snapshot(self) -> None:
        self.require_service()
        stages = ("claim-cas", "first-event", "second-event", "action")
        for stage in stages:
            with self.subTest(stage=stage):
                self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                before = self.snapshot()
                injected = RuntimeError(f"{stage}-driver-canary")
                original_event = PostgresExecutionStore.add_event
                original_action = PostgresActivityHistoryStore.add_action
                original_rotate = PostgresExecutionStore.rotate_request_claim
                event_calls = 0

                def rotate_claim(store, *args, **kwargs):
                    if stage == "claim-cas":
                        raise injected
                    return original_rotate(store, *args, **kwargs)

                def add_event(store, event):
                    nonlocal event_calls
                    event_calls += 1
                    if stage == "first-event" and event_calls == 1:
                        raise injected
                    if stage == "second-event" and event_calls == 2:
                        raise injected
                    return original_event(store, event)

                def add_action(store, action):
                    if stage == "action":
                        raise injected
                    return original_action(store, action)

                PostgresExecutionStore.rotate_request_claim = rotate_claim
                PostgresExecutionStore.add_event = add_event
                PostgresActivityHistoryStore.add_action = add_action
                try:
                    with self.assertRaises(RuntimeError) as captured:
                        self.service(
                            "decision-a", "consequence-a", "action-a"
                        ).execute(
                            self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                        )
                finally:
                    PostgresExecutionStore.rotate_request_claim = original_rotate
                    PostgresExecutionStore.add_event = original_event
                    PostgresActivityHistoryStore.add_action = original_action
                self.assertIs(captured.exception, injected)
                self.assertEqual(self.snapshot(), before)

    def test_identity_factory_error_escapes_after_observation_before_write(self) -> None:
        self.require_service()
        self.seed_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        before = self.snapshot()
        observed = False
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def record_observation(store, request_id):
            nonlocal observed
            result = original_observe(store, request_id)
            observed = True
            return result

        injected = RuntimeError("identity-factory-canary")

        def fail_identity():
            self.assertTrue(observed, "identity allocated before lease observation")
            raise injected

        PostgresExecutionStore.observe_request_lease_for_update = record_observation
        try:
            with self.assertRaises(RuntimeError) as captured:
                ExecutionLeaseRecoveryCommandService(
                    self.unit_of_work,
                    id_factory=fail_identity,
                ).execute(
                    self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                )
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe
        self.assertIs(captured.exception, injected)
        self.assertEqual(self.snapshot(), before)

    def test_factory_and_commit_errors_escape_and_rollback(self) -> None:
        self.require_service()
        self.seed_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        before = self.snapshot()

        factory_error = RuntimeError("factory-canary")

        def fail_factory():
            raise factory_error

        with self.assertRaises(RuntimeError) as factory_captured:
            ExecutionLeaseRecoveryCommandService(
                fail_factory,
                id_factory=lambda: "unused-a",
            ).execute(self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM))
        self.assertIs(factory_captured.exception, factory_error)
        self.assertEqual(self.snapshot(), before)

        commit_error = RuntimeError("commit-canary")
        raw_connection = psycopg.connect(self.database_url)

        class CommitFailureConnection:
            def execute(self, *args, **kwargs):
                return raw_connection.execute(*args, **kwargs)

            def commit(self):
                raise commit_error

            def rollback(self):
                return raw_connection.rollback()

            def close(self):
                return raw_connection.close()

        def commit_failure_uow():
            return PostgresUnitOfWork(CommitFailureConnection)

        with self.assertRaises(RuntimeError) as commit_captured:
            ExecutionLeaseRecoveryCommandService(
                commit_failure_uow,
                id_factory=iter(
                    ("decision-a", "consequence-a", "action-a")
                ).__next__,
            ).execute(self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM))
        self.assertIs(commit_captured.exception, commit_error)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    import unittest

    unittest.main()
