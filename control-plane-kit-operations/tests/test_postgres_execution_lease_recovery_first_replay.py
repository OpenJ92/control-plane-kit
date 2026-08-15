from __future__ import annotations

import dataclasses
import json
import os
import unittest
from pathlib import Path

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.activity_run_retry import ActivityRunRetryResult
from control_plane_kit_operations.execution_lease_recovery import (
    ExecutionLeaseRecoveryResult,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    ExecutionWorkerAuthority,
    FailActivityRun,
    RunLifecycleCommandService,
    RunLifecycleConflict,
    RunLifecycleDenied,
    RunLifecycleError,
    RunLifecycleIdempotencyConflict,
    RunLifecycleNotFound,
    StartActivityRun,
)
from control_plane_kit_operations.postgres import (
    GatewayKeyRotationStore,
    PostgresActivityHistoryStore,
    PostgresExecutionStore,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ExecutionLeaseRecoveryEvidence,
    FailureEvidence,
    OperationActionRecord,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey

from tests.execution_lease_recovery_fixture import (
    ExecutionLeaseRecoveryCommandService,
    PostgresExecutionLeaseRecoveryFixture,
    Sequence,
    TARGET_MODULE,
    safe_error,
)
import control_plane_kit_operations.execution_lease_recovery_interpreter as recovery_interpreter


DECISIONS = (
    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
    RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
    RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
    RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
)

REQUIRED_SCOPE = {
    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: RecoveryScope.RENEW_CLAIM,
    RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: RecoveryScope.RENEW_CLAIM,
    RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: RecoveryScope.TAKE_OVER_CLAIM,
    RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: RecoveryScope.ABANDON_CLAIM,
}

CONSEQUENCE = {
    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: ActivityEventKind.REQUEST_CLAIM_RENEWED,
    RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: ActivityEventKind.REQUEST_CLAIM_RENEWED,
    RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER
    ),
    RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: (
        ActivityEventKind.REQUEST_CLAIM_ABANDONED
    ),
}


class PostgresExecutionLeaseRecoveryFirstReplayTests(
    PostgresExecutionLeaseRecoveryFixture,
    unittest.TestCase,
):
    def test_predecessor_delegates_approval_and_journal_to_shared_support(
        self,
    ) -> None:
        required = (
            "locked_recovery_approval",
            "require_recovery_eligible_journal",
        )
        self.assertEqual(
            [name for name in required if not hasattr(recovery_interpreter, name)],
            [],
            "predecessor interpreter does not bind shared recovery support",
        )
        original_approval = recovery_interpreter.locked_recovery_approval
        original_journal = recovery_interpreter.require_recovery_eligible_journal
        calls: list[tuple[str, object]] = []

        def approval(*args, **kwargs):
            calls.append(("approval", args[1].identity.request_id))
            return original_approval(*args, **kwargs)

        def journal(*args, **kwargs):
            calls.append(("journal", args[0]))
            return original_journal(*args, **kwargs)

        recovery_interpreter.locked_recovery_approval = approval
        recovery_interpreter.require_recovery_eligible_journal = journal
        try:
            for subject in ("activity-plan", "gateway-key-rotation"):
                with self.subTest(subject=subject):
                    self.reset_truth(
                        RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                        approval_subject=subject,
                    )
                    self.service(
                        f"decision-{subject}",
                        f"consequence-{subject}",
                        f"action-{subject}",
                    ).execute(
                        self.command(
                            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                            key=f"recover-{subject}",
                        )
                    )
        finally:
            recovery_interpreter.locked_recovery_approval = original_approval
            recovery_interpreter.require_recovery_eligible_journal = original_journal

        self.assertEqual(
            calls,
            [
                ("approval", "request-a"),
                ("journal", RecoveryDecisionKind.RENEW_ACTIVE_CLAIM),
                ("approval", "request-a"),
                ("journal", RecoveryDecisionKind.RENEW_ACTIVE_CLAIM),
            ],
        )

    def persist_linked_retry_truth(
        self,
        *,
        fence: ExecutionLeaseFence,
    ) -> ActivityRunRetryResult:
        observed_at = "2026-08-15T04:30:00Z"
        metadata = BoundedEvidence.from_mapping(
            {"attempt": 2, "prior_run_id": "run-a"}
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            request = stores.execution.get_request("request-a")
            prior = stores.execution.get_run("run-a")
            decision_ordinal = stores.execution.next_event_ordinal("run-a")
            action_ordinal = stores.activity_history.next_action_ordinal(
                "session-a"
            )
            run = ActivityRunRecord(
                "run-b",
                "plan-a",
                AdmittedRun("request-a"),
                RetryIdentity(2, "run-a"),
                ActivityRunStatus.CLAIMED,
                observed_at,
                metadata=metadata,
            )
            recovery = ExecutionLeaseRecoveryEvidence(
                RecoveryDecisionKind.RETRY_AS_NEW_RUN,
                RunId("run-a"),
                fence,
                fence,
            )
            decision = ActivityEventRecord(
                "retry-decision",
                "run-a",
                decision_ordinal,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                observed_at,
                recovery=recovery,
            )
            opened = ActivityEventRecord(
                "run-b-opened",
                "run-b",
                1,
                ActivityEventKind.RUN_OPENED,
                observed_at,
                evidence=metadata,
            )
            action = OperationActionRecord(
                "retry-action",
                "session-a",
                action_ordinal,
                LifecycleOperationKind.RECORD_RECOVERY_DECISION,
                "operator-a",
                {
                    "execution_request_id": "request-a",
                    "plan_id": "plan-a",
                    "prior_run_id": "run-a",
                    "run_id": "run-b",
                    "prior_attempt": 1,
                    "attempt": 2,
                    "decision_event_id": "retry-decision",
                    "decision_event_kind": "recovery_decision_recorded",
                    "decision_event_ordinal": decision_ordinal,
                    "opened_event_id": "run-b-opened",
                    "opened_event_kind": "run_opened",
                    "opened_event_ordinal": 1,
                    "recovery": recovery.descriptor(),
                },
                observed_at,
                "retry-a",
                "b" * 64,
            )
            result = ActivityRunRetryResult(
                request,
                prior,
                run,
                decision,
                opened,
                action,
            )
            self.assertEqual(stores.execution.add_run(run), run)
            self.assertEqual(stores.execution.add_event(decision), decision)
            self.assertEqual(stores.execution.add_event(opened), opened)
            self.assertEqual(stores.activity_history.add_action(action), action)
            unit_of_work.commit()
            return result

    def test_exact_scope_is_required_before_unit_of_work(self) -> None:
        self.require_service()
        for decision in DECISIONS:
            wrong_scope = (
                RecoveryScope.ABANDON_CLAIM
                if REQUIRED_SCOPE[decision] is not RecoveryScope.ABANDON_CLAIM
                else RecoveryScope.RENEW_CLAIM
            )
            for scopes in ((), (wrong_scope,)):
                with self.subTest(decision=decision, scopes=scopes):
                    command = self.command(decision)
                    command = dataclasses.replace(
                        command,
                        authority=dataclasses.replace(
                            command.authority,
                            scopes=scopes,
                        ),
                    )
                    factory_calls = 0

                    def fail_factory():
                        nonlocal factory_calls
                        factory_calls += 1
                        raise AssertionError(
                            "unauthorized recovery opened a unit of work"
                        )

                    service = ExecutionLeaseRecoveryCommandService(
                        fail_factory,
                        id_factory=lambda: (_ for _ in ()).throw(
                            AssertionError(
                                "unauthorized recovery allocated identity"
                            )
                        ),
                    )
                    with self.assertRaises(RunLifecycleDenied) as captured:
                        service.execute(command)
                    self.assertEqual(factory_calls, 0)
                    safe_error(self, captured.exception, "authority-reference-a")

    def test_authority_uses_capability_set_semantics(self) -> None:
        for decision in DECISIONS:
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                unrelated = (
                    RecoveryScope.ABANDON_CLAIM
                    if REQUIRED_SCOPE[decision] is not RecoveryScope.ABANDON_CLAIM
                    else RecoveryScope.RENEW_CLAIM
                )
                result = self.service("decision-a", "consequence-a", "action-a").execute(
                    self.command(decision, extra_scopes=(unrelated,))
                )
                self.assertFalse(result.replayed)

    def test_all_four_decisions_commit_exact_history(self) -> None:
        for decision in DECISIONS:
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                before = self.snapshot()
                service, sequence = self.service_with_sequence(
                    "recovery-decision-a",
                    "recovery-consequence-a",
                    "recovery-action-a",
                )
                command = self.command(decision)
                result = service.execute(command)

                self.assertIs(type(result), ExecutionLeaseRecoveryResult)
                self.assertFalse(result.replayed)
                self.assertEqual(
                    sequence.calls,
                    [
                        "recovery-decision-a",
                        "recovery-consequence-a",
                        "recovery-action-a",
                    ],
                )
                self.assertIs(
                    result.decision_event.kind,
                    ActivityEventKind.RECOVERY_DECISION_RECORDED,
                )
                self.assertIs(result.consequence_event.kind, CONSEQUENCE[decision])
                self.assertEqual(
                    result.decision_event.occurred_at,
                    result.consequence_event.occurred_at,
                )
                self.assertEqual(
                    result.action.created_at,
                    result.decision_event.occurred_at,
                )
                self.assertIs(
                    result.action.action_type,
                    LifecycleOperationKind.RECORD_RECOVERY_DECISION,
                )
                self.assertEqual(result.action.actor_id, "operator-a")
                self.assertEqual(
                    result.action.intent_fingerprint,
                    command.intent_fingerprint(),
                )
                self.assertIs(
                    result.decision_event.recovery.decision_kind,
                    decision,
                )
                rendered = json.dumps(result.descriptor(), sort_keys=True)
                for forbidden in (
                    "authority-reference-a",
                    "recovery:",
                    "lease_expires_at",
                ):
                    self.assertNotIn(forbidden, rendered)

                after = self.snapshot()
                self.assertEqual(len(after[1]), len(before[1]) + 2)
                self.assertEqual(len(after[2]), len(before[2]) + 1)
                self.assertEqual(len(after[3]), len(before[3]))
                self.assertEqual(
                    tuple(row[0] for row in after[1][-2:]),
                    ("recovery-decision-a", "recovery-consequence-a"),
                )
                self.assertEqual(
                    tuple(row[2] for row in after[1][-2:]),
                    (
                        "recovery_decision_recorded",
                        result.consequence_event.kind.value.replace("-", "_"),
                    ),
                )
                self.assertEqual(
                    after[1][-2][4]["recovery"],
                    result.decision_event.recovery.descriptor(),
                )
                self.assertEqual(after[2][0][0], "recovery-action-a")

    def test_exact_replay_survives_session_close_without_clock_or_ids(self) -> None:
        self.require_service()
        for decision in DECISIONS:
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                command = self.command(decision)
                first = self.service(
                    "decision-a", "consequence-a", "action-a"
                ).execute(command)
                snapshot = self.snapshot()
                self.connection.execute(
                    "UPDATE cpk_operation_sessions SET status = 'closed', "
                    "closed_at = clock_timestamp() WHERE session_id = 'session-a'"
                )

                original_observe = PostgresExecutionStore.observe_request_lease_for_update

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("exact replay sampled database time")

                PostgresExecutionStore.observe_request_lease_for_update = fail_observe
                try:
                    replay = ExecutionLeaseRecoveryCommandService(
                        self.unit_of_work,
                        id_factory=lambda: (_ for _ in ()).throw(
                            AssertionError("exact replay allocated identity")
                        ),
                    ).execute(command)
                finally:
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )

                self.assertEqual(replay, dataclasses.replace(first, replayed=True))
                self.assertEqual(self.snapshot(), snapshot)

    def test_active_claim_can_be_renewed_twice_on_one_retained_run(self) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        first = self.service(
            "decision-a", "consequence-a", "action-a"
        ).execute(
            self.command(
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                key="recover-a",
            )
        )
        second = self.service(
            "decision-b", "consequence-b", "action-b"
        ).execute(
            self.command(
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                key="recover-b",
                expected_fence=first.request.claim.fence,
            )
        )

        self.assertEqual(first.retained_run, second.retained_run)
        self.assertEqual(first.request.claim.fence.generation, 8)
        self.assertEqual(second.request.claim.fence.generation, 9)
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot[3]), 1)
        self.assertEqual(
            tuple(row[0] for row in snapshot[1][-4:]),
            ("decision-a", "consequence-a", "decision-b", "consequence-b"),
        )
        self.assertEqual(
            tuple(row[0] for row in snapshot[2]),
            ("action-a", "action-b"),
        )

    def test_expired_replacement_can_be_renewed_without_a_retry_run(self) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        first = self.service(
            "decision-a", "consequence-a", "action-a"
        ).execute(
            self.command(
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                key="recover-a",
            )
        )
        self.connection.execute(
            "UPDATE cpk_execution_requests SET lease_expires_at = "
            "'2000-01-01T00:00:00Z' WHERE request_id = 'request-a'"
        )
        second = self.service(
            "decision-b", "consequence-b", "action-b"
        ).execute(
            self.command(
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                key="recover-b",
                expected_fence=first.request.claim.fence,
            )
        )

        self.assertEqual(first.retained_run, second.retained_run)
        self.assertEqual(second.request.claim.fence.generation, 9)
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot[3]), 1)
        self.assertEqual(
            tuple(row[0] for row in snapshot[1][-4:]),
            ("decision-a", "consequence-a", "decision-b", "consequence-b"),
        )
        self.assertEqual(
            tuple(row[0] for row in snapshot[2]),
            ("action-a", "action-b"),
        )

    def test_active_renewal_can_continue_through_start_failure_and_recovery(
        self,
    ) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        active = self.service(
            "decision-a", "consequence-a", "action-a"
        ).execute(
            self.command(
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                key="recover-a",
            )
        )
        fence = active.request.claim.fence
        authority = ExecutionWorkerAuthority(
            fence.worker_id,
            (PolicyScope.EXECUTION_OPERATE,),
        )
        started_at = active.request.claim.claimed_at
        RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: started_at,
            id_factory=Sequence("start-event", "start-action"),
        ).execute(
            StartActivityRun(
                "run-a",
                authority,
                fence,
                IdempotencyKey("start-after-renewal"),
            )
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_event(
                ActivityEventRecord(
                    "step-started",
                    "run-a",
                    5,
                    ActivityEventKind.STEP_STARTED,
                    started_at,
                    activity_id="start-runtime",
                )
            )
            unit_of_work.stores.execution.add_event(
                ActivityEventRecord(
                    "step-failed",
                    "run-a",
                    6,
                    ActivityEventKind.STEP_FAILED,
                    started_at,
                    activity_id="start-runtime",
                )
            )
            unit_of_work.commit()
        RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: started_at,
            id_factory=Sequence("run-failed", "fail-action"),
        ).execute(
            FailActivityRun(
                "run-a",
                authority,
                fence,
                IdempotencyKey("fail-after-renewal"),
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "adapter-error",
                    "adapter returned a terminal failure",
                    BoundedEvidence(),
                ),
            )
        )
        self.connection.execute(
            "UPDATE cpk_execution_requests SET lease_expires_at = "
            "'2000-01-01T00:00:00Z' WHERE request_id = 'request-a'"
        )

        recovered = self.service(
            "decision-b", "consequence-b", "action-b"
        ).execute(
            self.command(
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                key="recover-b",
                expected_fence=fence,
            )
        )

        self.assertEqual(recovered.request.claim.fence.generation, 9)
        self.assertEqual(
            tuple(row[2] for row in self.snapshot()[1]),
            (
                "run_opened",
                "recovery_decision_recorded",
                "request_claim_renewed",
                "run_started",
                "step_started",
                "step_failed",
                "run_failed",
                "recovery_decision_recorded",
                "request_claim_renewed",
            ),
        )

    def test_active_renew_replay_survives_failure_and_linked_retry(self) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        command = self.command(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            key="recover-a",
        )
        active = self.service(
            "decision-a", "consequence-a", "action-a"
        ).execute(command)
        fence = active.request.claim.fence
        authority = ExecutionWorkerAuthority(
            fence.worker_id,
            (PolicyScope.EXECUTION_OPERATE,),
        )
        started_at = active.request.claim.claimed_at
        RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: started_at,
            id_factory=Sequence("start-event", "start-action"),
        ).execute(
            StartActivityRun(
                "run-a",
                authority,
                fence,
                IdempotencyKey("start-after-renewal"),
            )
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_event(
                ActivityEventRecord(
                    "step-started",
                    "run-a",
                    5,
                    ActivityEventKind.STEP_STARTED,
                    started_at,
                    activity_id="start-runtime",
                )
            )
            unit_of_work.stores.execution.add_event(
                ActivityEventRecord(
                    "step-failed",
                    "run-a",
                    6,
                    ActivityEventKind.STEP_FAILED,
                    started_at,
                    activity_id="start-runtime",
                )
            )
            unit_of_work.commit()
        RunLifecycleCommandService(
            self.unit_of_work,
            clock=lambda: started_at,
            id_factory=Sequence("run-failed", "fail-action"),
        ).execute(
            FailActivityRun(
                "run-a",
                authority,
                fence,
                IdempotencyKey("fail-after-renewal"),
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "adapter-error",
                    "adapter returned a terminal failure",
                    BoundedEvidence(),
                ),
            )
        )
        linked = self.persist_linked_retry_truth(fence=fence)
        self.connection.execute(
            "UPDATE cpk_operation_sessions SET status = 'closed', "
            "closed_at = '2026-08-15T04:31:00Z' "
            "WHERE session_id = 'session-a'"
        )
        before = self.snapshot()
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("historical recovery replay sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            replay = ExecutionLeaseRecoveryCommandService(
                self.unit_of_work,
                id_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("historical recovery replay allocated identity")
                ),
            ).execute(command)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.retained_run.run_id, "run-a")
        self.assertIs(replay.retained_run.status, ActivityRunStatus.FAILED)
        self.assertEqual(linked.run.run_id, "run-b")
        self.assertEqual(self.snapshot(), before)

    def test_replay_requires_durable_semantics_to_equal_submitted_command(
        self,
    ) -> None:
        mutations = {
            "duration": lambda: self.connection.execute(
                "UPDATE cpk_operation_actions SET payload = "
                "jsonb_set(payload, '{lease_duration_seconds}', '601'::jsonb) "
                "WHERE action_id = 'action-a'"
            ),
            "coherent-takeover": self.rewrite_recovery_as_takeover,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                command = self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                self.service("decision-a", "consequence-a", "action-a").execute(
                    command
                )
                mutate()
                before = self.snapshot()
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("semantic drift replay sampled database time")

                PostgresExecutionStore.observe_request_lease_for_update = fail_observe
                try:
                    with self.assertRaises(RunLifecycleConflict) as captured:
                        ExecutionLeaseRecoveryCommandService(
                            self.unit_of_work,
                            id_factory=lambda: (_ for _ in ()).throw(
                                AssertionError(
                                    "semantic drift replay allocated identity"
                                )
                            ),
                        ).execute(command)
                finally:
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )
                safe_error(self, captured.exception, "worker-b")
                self.assertEqual(self.snapshot(), before)

    def test_replay_rejects_same_fence_lease_timestamp_drift(self) -> None:
        for column, drift in (
            ("claimed_at", "2050-01-01T00:00:00Z"),
            ("lease_expires_at", "2050-01-01T00:10:00Z"),
        ):
            with self.subTest(column=column):
                self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                command = self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                self.service("decision-a", "consequence-a", "action-a").execute(
                    command
                )
                self.connection.execute(
                    f"UPDATE cpk_execution_requests SET {column} = %s "
                    "WHERE request_id = 'request-a'",
                    (drift,),
                )
                before = self.snapshot()
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("timestamp drift replay sampled database time")

                PostgresExecutionStore.observe_request_lease_for_update = fail_observe
                try:
                    with self.assertRaises(RunLifecycleConflict) as captured:
                        ExecutionLeaseRecoveryCommandService(
                            self.unit_of_work,
                            id_factory=lambda: (_ for _ in ()).throw(
                                AssertionError(
                                    "timestamp drift replay allocated identity"
                                )
                            ),
                        ).execute(command)
                finally:
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )
                safe_error(self, captured.exception, drift)
                self.assertEqual(self.snapshot(), before)

    def test_malformed_replay_event_is_categorical_without_clock_or_ids(
        self,
    ) -> None:
        self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        command = self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        self.service("decision-a", "consequence-a", "action-a").execute(command)
        self.connection.execute(
            "UPDATE cpk_activity_events SET payload = "
            "jsonb_set(payload, '{evidence}', '"
            + '"replay-event-canary"'
            + "'::jsonb) WHERE event_id = 'decision-a'"
        )
        before = self.snapshot()
        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("malformed replay sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with self.assertRaises(RunLifecycleConflict) as captured:
                ExecutionLeaseRecoveryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("malformed replay allocated identity")
                    ),
                ).execute(command)
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = (
                original_observe
            )
        safe_error(self, captured.exception, "replay-event-canary")
        self.assertEqual(self.snapshot(), before)

    def rewrite_recovery_as_takeover(self) -> None:
        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_worker_id = 'worker-b' "
            "WHERE request_id = 'request-a'"
        )
        self.connection.execute(
            """
            UPDATE cpk_activity_events
            SET payload = jsonb_set(
                    jsonb_set(
                        payload,
                        '{recovery,decision}',
                        %s::jsonb
                    ),
                    '{recovery,replacement_fence,worker_id}',
                    %s::jsonb
                )
            WHERE event_id = 'decision-a'
            """,
            (
                json.dumps(RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM.value),
                json.dumps("worker-b"),
            ),
        )
        self.connection.execute(
            "UPDATE cpk_activity_events SET event_type = %s "
            "WHERE event_id = 'consequence-a'",
            (ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER.value,),
        )
        self.connection.execute(
            """
            UPDATE cpk_operation_actions
            SET payload = jsonb_set(
                    jsonb_set(
                        jsonb_set(
                            payload,
                            '{recovery,decision}',
                            %s::jsonb
                        ),
                        '{recovery,replacement_fence,worker_id}',
                        %s::jsonb
                    ),
                    '{consequence_event_kind}',
                    %s::jsonb
                )
            WHERE action_id = 'action-a'
            """,
            (
                json.dumps(RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM.value),
                json.dumps("worker-b"),
                json.dumps(ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER.value),
            ),
        )

    def test_changed_intent_conflicts_before_request_and_run_locks(self) -> None:
        self.require_service()
        self.seed_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        command = self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        self.service("decision-a", "consequence-a", "action-a").execute(command)
        snapshot = self.snapshot()

        lock_names = (
            "get_request_for_update",
            "get_latest_run_for_request_for_update",
            "observe_request_lease_for_update",
        )
        originals = {
            name: getattr(PostgresExecutionStore, name) for name in lock_names
        }

        def fail_lock(*_args, **_kwargs):
            raise AssertionError("changed replay reached dependent request/run lock")

        for name in lock_names:
            setattr(PostgresExecutionStore, name, fail_lock)
        try:
            with self.assertRaises(RunLifecycleIdempotencyConflict) as captured:
                ExecutionLeaseRecoveryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("changed replay allocated identity")
                    ),
                ).execute(
                    self.command(
                        RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                        duration=601,
                    )
                )
        finally:
            for name, method in originals.items():
                setattr(PostgresExecutionStore, name, method)
        safe_error(self, captured.exception, "authority-reference-a")
        self.assertEqual(self.snapshot(), snapshot)

    def test_replay_revalidates_complete_authoritative_history(self) -> None:
        mutations = {
            "current-fence": (
                lambda: self.connection.execute(
                    "UPDATE cpk_execution_requests SET claim_generation = 9 "
                    "WHERE request_id = 'request-a'"
                ),
                RunLifecycleConflict,
            ),
            "newer-run": (self.add_newer_failed_run, RunLifecycleConflict),
            "approval": (
                lambda: self.connection.execute(
                    "UPDATE cpk_approval_requests SET review_digest = %s "
                    "WHERE request_id = 'approval-request-a'",
                    ("f" * 64,),
                ),
                RunLifecycleConflict,
            ),
            "approval-decision": (
                lambda: self.connection.execute(
                    "UPDATE cpk_approval_decisions SET scope = "
                    "'plan:approve-destructive' "
                    "WHERE decision_id = 'approval-decision-a'"
                ),
                RunLifecycleConflict,
            ),
            "missing-decision-event": (
                lambda: self.connection.execute(
                    "DELETE FROM cpk_activity_events "
                    "WHERE event_id = 'decision-a'"
                ),
                RunLifecycleNotFound,
            ),
            "missing-consequence-event": (
                lambda: self.connection.execute(
                    "DELETE FROM cpk_activity_events "
                    "WHERE event_id = 'consequence-a'"
                ),
                RunLifecycleNotFound,
            ),
            "decision-evidence": (
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_events SET payload = "
                    "jsonb_set(payload, '{recovery,retained_run_id}', '"
                    + '"run-drift"'
                    + "') WHERE event_id = 'decision-a'"
                ),
                RunLifecycleConflict,
            ),
            "decision-time": (
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_events SET occurred_at = "
                    "'2026-08-15T05:00:00Z' WHERE event_id = 'decision-a'"
                ),
                RunLifecycleConflict,
            ),
            "consequence-kind": (
                lambda: self.connection.execute(
                    "UPDATE cpk_activity_events SET event_type = "
                    "'request_claim_abandoned' WHERE event_id = 'consequence-a'"
                ),
                RunLifecycleConflict,
            ),
            "action-type": (
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET action_type = "
                    "'record-operation-action' WHERE action_id = 'action-a'"
                ),
                RunLifecycleConflict,
            ),
            "action-actor": (
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET actor_id = "
                    "'actor-drift-canary' WHERE action_id = 'action-a'"
                ),
                RunLifecycleConflict,
            ),
            "action-fingerprint": (
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET intent_fingerprint = %s "
                    "WHERE action_id = 'action-a'",
                    ("e" * 64,),
                ),
                RunLifecycleIdempotencyConflict,
            ),
            "action-payload": (
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET payload = "
                    "jsonb_set(payload, '{retained_run_id}', '"
                    + '"run-drift"'
                    + "') WHERE action_id = 'action-a'"
                ),
                RunLifecycleConflict,
            ),
        }
        for name, (mutate, expected_error) in mutations.items():
            with self.subTest(name=name):
                self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                command = self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                self.service("decision-a", "consequence-a", "action-a").execute(
                    command
                )
                mutate()
                before = self.snapshot()
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("replay mutation sampled database time")

                PostgresExecutionStore.observe_request_lease_for_update = fail_observe
                try:
                    with self.assertRaises(expected_error) as captured:
                        ExecutionLeaseRecoveryCommandService(
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
                    captured.exception,
                    "actor-drift-canary",
                    "run-drift",
                    "f" * 32,
                    "session-drift-canary",
                    "key-drift-canary",
                )
                self.assertEqual(self.snapshot(), before)

    def test_replay_rejects_action_lookup_coordinate_drift(self) -> None:
        self.require_service()
        for field_name, drift in (
            ("session_id", "session-drift-canary"),
            ("idempotency_key", "key-drift-canary"),
        ):
            with self.subTest(field=field_name):
                self.reset_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                command = self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                self.service("decision-a", "consequence-a", "action-a").execute(
                    command
                )
                before = self.snapshot()
                original_lookup = (
                    PostgresActivityHistoryStore.action_for_idempotency
                )
                original_observe = (
                    PostgresExecutionStore.observe_request_lease_for_update
                )

                def drift_lookup(store, session_id, idempotency_key):
                    action = original_lookup(store, session_id, idempotency_key)
                    self.assertIsNotNone(action)
                    return dataclasses.replace(action, **{field_name: drift})

                def fail_observe(*_args, **_kwargs):
                    raise AssertionError("action drift replay sampled database time")

                PostgresActivityHistoryStore.action_for_idempotency = drift_lookup
                PostgresExecutionStore.observe_request_lease_for_update = fail_observe
                try:
                    with self.assertRaises(RunLifecycleConflict) as captured:
                        ExecutionLeaseRecoveryCommandService(
                            self.unit_of_work,
                            id_factory=lambda: (_ for _ in ()).throw(
                                AssertionError(
                                    "action drift replay allocated identity"
                                )
                            ),
                        ).execute(command)
                finally:
                    PostgresActivityHistoryStore.action_for_idempotency = (
                        original_lookup
                    )
                    PostgresExecutionStore.observe_request_lease_for_update = (
                        original_observe
                    )
                safe_error(self, captured.exception, drift)
                self.assertEqual(self.snapshot(), before)

    def test_both_approval_subjects_are_rechecked_without_gateway_read_or_lock(
        self,
    ) -> None:
        self.require_service()
        originals = {
            name: getattr(GatewayKeyRotationStore, name)
            for name in ("get", "get_for_update")
        }

        def fail_gateway_read(*_args, **_kwargs):
            raise AssertionError("recovery read or locked mutable gateway rotation")

        for name in originals:
            setattr(GatewayKeyRotationStore, name, fail_gateway_read)
        try:
            for subject_kind in ("activity-plan", "gateway-key-rotation"):
                with self.subTest(subject=subject_kind):
                    self.reset_truth(
                        RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                        approval_subject=subject_kind,
                    )
                    result = self.service(
                        "decision-a", "consequence-a", "action-a"
                    ).execute(
                        self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
                    )
                    self.assertFalse(result.replayed)
        finally:
            for name, method in originals.items():
                setattr(GatewayKeyRotationStore, name, method)

        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            approval_subject="gateway-key-rotation",
        )
        self.connection.execute(
            "UPDATE cpk_approval_requests SET review_digest = %s "
            "WHERE request_id = 'approval-request-a'",
            ("f" * 64,),
        )
        before = self.snapshot()
        service, sequence = self.service_with_sequence(
            "unused-a", "unused-b", "unused-c"
        )
        with self.assertRaises(RunLifecycleError) as captured:
            service.execute(self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM))
        self.assertEqual(sequence.calls, [])
        safe_error(self, captured.exception, "f" * 32)
        self.assertEqual(self.snapshot(), before)

    def test_root_and_inventory_name_the_interpreter_without_effect_dependencies(
        self,
    ) -> None:
        self.require_service()
        self.assertIs(
            operations_root.ExecutionLeaseRecoveryCommandService,
            ExecutionLeaseRecoveryCommandService,
        )
        inventory_path = Path(
            os.environ.get(
                "CPK_PACKAGE_MODULE_INVENTORY",
                Path(__file__).resolve().parents[2]
                / "docs"
                / "architecture"
                / "package-module-inventory.json",
            )
        )
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        rows = [row for row in inventory["modules"] if row["module"] == TARGET_MODULE]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            row["canonical_public_exports"],
            ["ExecutionLeaseRecoveryCommandService"],
        )
        self.assertEqual(row["owner"], "operation")
        self.assertTrue(
            {
                "control_plane_kit_interpreters",
                "control_plane_kit_servers",
                "docker",
                "cloudflare",
            }.isdisjoint(row["internal_dependencies"])
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
