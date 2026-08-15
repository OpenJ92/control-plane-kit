from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ExecutionRequestStatus,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations.execution_lease_recovery import (
    ExecutionLeaseRecoveryResult,
)
from control_plane_kit_operations.lifecycle import (
    RunLifecycleConflict,
    RunLifecycleDenied,
    RunLifecycleError,
    RunLifecycleIdempotencyConflict,
    RunLifecycleNotFound,
)
from control_plane_kit_operations.postgres import (
    GatewayKeyRotationStore,
    PostgresExecutionStore,
)

from tests.execution_lease_recovery_fixture import (
    ExecutionLeaseRecoveryCommandService,
    PostgresExecutionLeaseRecoveryFixture,
    TARGET_MODULE,
    safe_error,
)


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
    PostgresExecutionLeaseRecoveryFixture
):
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
        def drift_action_session() -> None:
            self.connection.execute(
                "INSERT INTO cpk_operation_sessions "
                "(session_id, workspace_id, actor_id, title, status, created_at, "
                "closed_at, metadata, idempotency_key, intent_fingerprint) "
                "SELECT 'session-drift-canary', workspace_id, actor_id, title, "
                "status, created_at, closed_at, metadata, NULL, NULL "
                "FROM cpk_operation_sessions WHERE session_id = 'session-a'"
            )
            self.connection.execute(
                "UPDATE cpk_operation_actions SET session_id = "
                "'session-drift-canary' WHERE action_id = 'action-a'"
            )

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
            "action-session": (drift_action_session, RunLifecycleConflict),
            "action-idempotency": (
                lambda: self.connection.execute(
                    "UPDATE cpk_operation_actions SET idempotency_key = "
                    "'key-drift-canary' WHERE action_id = 'action-a'"
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
