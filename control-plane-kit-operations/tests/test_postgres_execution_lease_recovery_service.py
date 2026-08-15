from __future__ import annotations

import dataclasses
import importlib
import json
import os
from pathlib import Path
import unittest

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs

import control_plane_kit_operations as operations_root
from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityPlan,
    PlannedActivity,
    RiskLevel,
    RuntimeTarget,
    StartRuntime,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_lease_recovery import (
    AbandonExpiredExecutionClaim,
    RecoveryAuthority,
    RenewActiveExecutionClaim,
    RenewExpiredExecutionClaim,
    TakeOverExpiredExecutionClaim,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    RunLifecycleConflict,
    RunLifecycleDenied,
    RunLifecycleError,
    RunLifecycleIdempotencyConflict,
)
from control_plane_kit_operations.postgres import (
    PostgresExecutionStore,
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey


TARGET_MODULE = (
    "control_plane_kit_operations.execution_lease_recovery_interpreter"
)

try:
    recovery_interpreter = importlib.import_module(TARGET_MODULE)
except ModuleNotFoundError as error:
    if error.name != TARGET_MODULE:
        raise
    recovery_interpreter = None

ExecutionLeaseRecoveryCommandService = getattr(
    recovery_interpreter,
    "ExecutionLeaseRecoveryCommandService",
    None,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self.values = list(values)

    def __call__(self) -> str:
        return self.values.pop(0)


def _safe_error(test: unittest.TestCase, error: BaseException, *canaries: str) -> None:
    test.assertIsNone(error.__cause__)
    test.assertIsNone(error.__context__)
    rendered = f"{error!s} {error!r}"
    test.assertLessEqual(len(rendered), 512)
    for canary in canaries:
        test.assertNotIn(canary, rendered)
class PostgresExecutionLeaseRecoveryTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run through Docker"
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        if not self.connection.closed:
            self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
            self.connection.close()

    def require_service(self):
        self.assertIsNotNone(
            ExecutionLeaseRecoveryCommandService,
            "execution-lease recovery interpreter is missing",
        )

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self, *ids: str):
        self.require_service()
        return ExecutionLeaseRecoveryCommandService(
            self.unit_of_work,
            id_factory=Sequence(*ids),
        )

    def authority(
        self,
        scope: RecoveryScope,
        *,
        actor_id: str = "operator-a",
        authority_reference: str = "authority-reference-a",
    ) -> RecoveryAuthority:
        return RecoveryAuthority(
            actor_id,
            authority_reference,
            (scope,),
        )

    def command(
        self,
        decision: RecoveryDecisionKind,
        *,
        key: str = "recover-a",
        duration: int = 600,
        actor_id: str = "operator-a",
        authority_reference: str = "authority-reference-a",
    ):
        scope = {
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: RecoveryScope.RENEW_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: RecoveryScope.RENEW_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: RecoveryScope.TAKE_OVER_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: RecoveryScope.ABANDON_CLAIM,
        }[decision]
        common = {
            "request_id": "request-a",
            "retained_run_id": RunId("run-a"),
            "expected_fence": ExecutionLeaseFence("worker-a", 7),
            "authority": self.authority(
                scope,
                actor_id=actor_id,
                authority_reference=authority_reference,
            ),
            "idempotency_key": IdempotencyKey(key),
        }
        if decision is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM:
            return RenewActiveExecutionClaim(
                **common,
                lease_duration=ExecutionLeaseDuration(duration),
            )
        if decision is RecoveryDecisionKind.RENEW_EXPIRED_CLAIM:
            return RenewExpiredExecutionClaim(
                **common,
                lease_duration=ExecutionLeaseDuration(duration),
            )
        if decision is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM:
            return TakeOverExpiredExecutionClaim(
                **common,
                next_worker_id="worker-b",
                lease_duration=ExecutionLeaseDuration(duration),
            )
        return AbandonExpiredExecutionClaim(**common)

    def test_store_cas_owns_generation_expiry_and_row_preservation(self) -> None:
        self.seed_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        store = PostgresExecutionStore(self.connection)
        self.assertTrue(hasattr(store, "rotate_request_claim"))
        before = store.get_request("request-a")
        observed = "2026-08-15T04:00:00Z"

        rotated = store.rotate_request_claim(
            "request-a",
            expected_fence=ExecutionLeaseFence("worker-a", 7),
            replacement_fence=ExecutionLeaseFence("worker-a", 8),
            observed_at=observed,
            lease_duration_seconds=600,
        )
        self.assertEqual(rotated.claim.fence, ExecutionLeaseFence("worker-a", 8))
        self.assertEqual(rotated.claim.claimed_at, observed)
        self.assertEqual(rotated.claim.lease_expires_at, "2026-08-15T04:10:00Z")
        snapshot = store.get_request("request-a")
        self.assertEqual(snapshot, rotated)
        self.assertNotEqual(snapshot, before)

        self.assertIsNone(
            store.rotate_request_claim(
                "request-a",
                expected_fence=ExecutionLeaseFence("worker-a", 7),
                replacement_fence=ExecutionLeaseFence("worker-a", 8),
                observed_at=observed,
                lease_duration_seconds=600,
            )
        )
        self.assertEqual(store.get_request("request-a"), snapshot)

        self.assertIsNone(
            store.abandon_request_claim(
                "request-a",
                expected_fence=ExecutionLeaseFence("worker-a", 8),
                observed_at="2026-08-15T04:09:59.999999Z",
            )
        )
        self.assertEqual(store.get_request("request-a"), snapshot)
        abandoned = store.abandon_request_claim(
            "request-a",
            expected_fence=ExecutionLeaseFence("worker-a", 8),
            observed_at="2026-08-15T04:10:00Z",
        )
        self.assertIs(abandoned.status, ExecutionRequestStatus.ABANDONED)
        self.assertIsNone(abandoned.claim)
        self.assertEqual(
            self.connection.execute(
                "SELECT claim_worker_id, claim_generation, claimed_at, "
                "lease_expires_at FROM cpk_execution_requests "
                "WHERE request_id = 'request-a'"
            ).fetchone(),
            (None, None, None, None),
        )

    def test_exact_recovery_scope_is_required_before_unit_of_work(self) -> None:
        self.require_service()
        decisions = (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        )
        for decision in decisions:
            with self.subTest(decision=decision):
                command = self.command(decision)
                command = dataclasses.replace(
                    command,
                    authority=dataclasses.replace(command.authority, scopes=()),
                )
                factory_calls = 0

                def fail_factory():
                    nonlocal factory_calls
                    factory_calls += 1
                    raise AssertionError("unauthorized recovery opened a unit of work")

                service = ExecutionLeaseRecoveryCommandService(
                    fail_factory,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("unauthorized recovery allocated identity")
                    ),
                )
                with self.assertRaises(RunLifecycleDenied) as captured:
                    service.execute(command)
                self.assertEqual(factory_calls, 0)
                _safe_error(self, captured.exception, "authority-reference-a")

    def test_all_four_decisions_commit_exact_history_and_replay_after_close(self) -> None:
        decisions = (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        )
        consequence = {
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: ActivityEventKind.REQUEST_CLAIM_RENEWED,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: ActivityEventKind.REQUEST_CLAIM_RENEWED,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: ActivityEventKind.REQUEST_CLAIM_TAKEN_OVER,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: ActivityEventKind.REQUEST_CLAIM_ABANDONED,
        }
        for decision in decisions:
            with self.subTest(decision=decision):
                self.reset_truth(decision)
                command = self.command(decision)
                first = self.service(
                    "recovery-decision-a",
                    "recovery-consequence-a",
                    "recovery-action-a",
                ).execute(command)

                self.assertFalse(first.replayed)
                self.assertIs(first.decision_event.kind, ActivityEventKind.RECOVERY_DECISION_RECORDED)
                self.assertIs(first.consequence_event.kind, consequence[decision])
                self.assertEqual(
                    first.decision_event.occurred_at,
                    first.consequence_event.occurred_at,
                )
                self.assertEqual(first.action.created_at, first.decision_event.occurred_at)
                self.assertIs(
                    first.action.action_type,
                    LifecycleOperationKind.RECORD_RECOVERY_DECISION,
                )
                self.assertEqual(first.action.actor_id, "operator-a")
                self.assertEqual(first.action.intent_fingerprint, command.intent_fingerprint())
                self.assertEqual(
                    first.decision_event.recovery.decision_kind,
                    decision,
                )
                self.assertEqual(
                    first.decision_event.recovery.prior_fence,
                    ExecutionLeaseFence("worker-a", 7),
                )
                if decision is RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM:
                    self.assertIs(first.request.status, ExecutionRequestStatus.ABANDONED)
                    self.assertIsNone(first.request.claim)
                else:
                    expected_worker = (
                        "worker-b"
                        if decision is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM
                        else "worker-a"
                    )
                    self.assertEqual(
                        first.request.claim.fence,
                        ExecutionLeaseFence(expected_worker, 8),
                    )
                    self.assertEqual(
                        first.request.claim.claimed_at,
                        first.decision_event.occurred_at,
                    )
                rendered = json.dumps(first.descriptor(), sort_keys=True)
                self.assertNotIn("authority-reference-a", rendered)
                self.assertNotIn("recovery:", rendered)
                self.assertNotIn("lease_expires_at", rendered)

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
                    PostgresExecutionStore.observe_request_lease_for_update = original_observe

                self.assertEqual(replay, dataclasses.replace(first, replayed=True))
                events = PostgresExecutionStore(self.connection).events_for_run("run-a")
                actions = self.connection.execute(
                    "SELECT action_id FROM cpk_operation_actions "
                    "WHERE action_type = 'record-recovery-decision'"
                ).fetchall()
                self.assertEqual(events[-2:], (first.decision_event, first.consequence_event))
                self.assertEqual(actions, [("recovery-action-a",)])
                self.assertEqual(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM cpk_activity_runs "
                        "WHERE request_id = 'request-a'"
                    ).fetchone()[0],
                    1,
                )

    def test_changed_intent_and_persisted_actor_drift_fail_without_new_work(self) -> None:
        self.seed_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        command = self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        self.service("decision-a", "consequence-a", "action-a").execute(command)

        original_observe = PostgresExecutionStore.observe_request_lease_for_update

        def fail_observe(*_args, **_kwargs):
            raise AssertionError("replay conflict sampled database time")

        PostgresExecutionStore.observe_request_lease_for_update = fail_observe
        try:
            with self.assertRaises(RunLifecycleIdempotencyConflict) as changed:
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
            _safe_error(self, changed.exception, "authority-reference-a")

            self.connection.execute(
                "UPDATE cpk_operation_actions SET actor_id = 'actor-drift-canary' "
                "WHERE action_id = 'action-a'"
            )
            with self.assertRaises(RunLifecycleConflict) as drift:
                ExecutionLeaseRecoveryCommandService(
                    self.unit_of_work,
                    id_factory=lambda: (_ for _ in ()).throw(
                        AssertionError("drift replay allocated identity")
                    ),
                ).execute(command)
            _safe_error(self, drift.exception, "actor-drift-canary")
        finally:
            PostgresExecutionStore.observe_request_lease_for_update = original_observe

    def test_activity_journal_eligibility_is_exact(self) -> None:
        accepted = (
            (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, "active-empty"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "failed"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "resolved-forward-failure"),
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
            (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, "active-effect"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "in-flight"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "uncertain"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "compensation-requested"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "compensation-completed"),
            (RecoveryDecisionKind.RENEW_EXPIRED_CLAIM, "foreign-step"),
        )
        for decision, history in rejected:
            with self.subTest(rejected=history):
                self.reset_truth(decision, history=history)
                before = self.snapshot()
                with self.assertRaises(RunLifecycleConflict) as captured:
                    self.service("unused-a", "unused-b", "unused-c").execute(
                        self.command(decision, key=f"reject-{history}")
                    )
                _safe_error(self, captured.exception, "foreign-step-canary")
                self.assertEqual(self.snapshot(), before)

    def test_both_immutable_approval_subjects_are_rechecked_without_rotation_lock(self) -> None:
        for subject_kind in ("activity-plan", "gateway-key-rotation"):
            with self.subTest(subject=subject_kind):
                self.reset_truth(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    approval_subject=subject_kind,
                )
                result = self.service("decision-a", "consequence-a", "action-a").execute(
                    self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
                )
                self.assertFalse(result.replayed)
                if subject_kind == "gateway-key-rotation":
                    self.assertEqual(
                        self.connection.execute(
                            "SELECT status, version FROM cpk_gateway_key_rotations "
                            "WHERE rotation_id = 'rotation-a'"
                        ).fetchone(),
                        ("requested", 1),
                    )

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
        with self.assertRaises(RunLifecycleError) as captured:
            self.service("unused-a", "unused-b", "unused-c").execute(
                self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
            )
        _safe_error(self, captured.exception, "f" * 32)
        self.assertEqual(self.snapshot(), before)

    def test_late_history_failure_rolls_back_claim_events_and_action(self) -> None:
        self.seed_truth(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
        before = self.snapshot()
        original_add_event = PostgresExecutionStore.add_event
        calls = 0
        injected = RuntimeError("late-driver-canary")

        def fail_second_event(store, event):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise injected
            return original_add_event(store, event)

        PostgresExecutionStore.add_event = fail_second_event
        try:
            with self.assertRaises(RuntimeError) as captured:
                self.service("decision-a", "consequence-a", "action-a").execute(
                    self.command(RecoveryDecisionKind.RENEW_EXPIRED_CLAIM)
                )
            self.assertIs(captured.exception, injected)
        finally:
            PostgresExecutionStore.add_event = original_add_event
        self.assertEqual(self.snapshot(), before)

    def test_root_and_inventory_name_the_interpreter_without_effect_dependencies(self) -> None:
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

    def snapshot(self) -> tuple[object, ...]:
        return (
            self.connection.execute(
                "SELECT status, claim_worker_id, claim_generation, claimed_at, "
                "lease_expires_at FROM cpk_execution_requests "
                "WHERE request_id = 'request-a'"
            ).fetchone(),
            tuple(
                self.connection.execute(
                    "SELECT event_id, ordinal, event_type, payload "
                    "FROM cpk_activity_events WHERE run_id = 'run-a' "
                    "ORDER BY ordinal"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT action_id, action_type, actor_id, payload "
                    "FROM cpk_operation_actions WHERE session_id = 'session-a' "
                    "ORDER BY ordinal"
                ).fetchall()
            ),
            self.connection.execute(
                "SELECT COUNT(*) FROM cpk_activity_runs "
                "WHERE request_id = 'request-a'"
            ).fetchone()[0],
        )

    def reset_truth(
        self,
        decision: RecoveryDecisionKind,
        *,
        history: str | None = None,
        approval_subject: str = "activity-plan",
    ) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_truth(
            decision,
            history=history,
            approval_subject=approval_subject,
        )

    def seed_truth(
        self,
        decision: RecoveryDecisionKind,
        *,
        history: str | None = None,
        approval_subject: str = "activity-plan",
    ) -> None:
        active = decision is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM
        if history is None:
            history = "active-empty" if active else "failed"
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("start-runtime"),
                    StartRuntime(RuntimeTarget("runtime-a")),
                ),
            )
        )
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            lineage = seed_identity_graphs(
                stores,
                workspace_id="workspace-a",
                graph_ids=("graph-current", "graph-desired"),
            )
            stores.activity_history.add_session(
                OperationSessionRecord(
                    "session-a",
                    "workspace-a",
                    "operator-a",
                    "Deploy",
                    OperationSessionStatus.OPEN,
                    "2026-08-15T03:55:00Z",
                )
            )
            stores.activity_history.add_plan(
                ActivityPlanRecord(
                    "plan-a",
                    "session-a",
                    "graph-current",
                    "graph-desired",
                    ActivityPlanStatus.PLANNED,
                    "2026-08-15T03:56:00Z",
                    plan,
                    base_realized_projection_id=lineage["graph-current"],
                    desired_realized_projection_id=lineage["graph-desired"],
                    desired_graph_revision=1,
                )
            )
            unit_of_work.commit()

        if approval_subject == "activity-plan":
            subject = ActivityPlanApprovalSubject("plan-a")
            approval_scope = PolicyScope.PLAN_APPROVE
            approval_risk = RiskLevel.LOW
            destructive = False
        else:
            self.connection.execute(
                """
                INSERT INTO cpk_gateway_key_rotations
                  (rotation_id, workspace_id, gateway_node_id, purpose, issuer,
                   old_key_id, new_secret_reference, key_generation_correlation,
                   maximum_grant_lifetime_seconds, clock_skew_seconds,
                   correlation_id, requested_by, requested_at,
                   intent_fingerprint, status, version)
                VALUES
                  ('rotation-a', 'workspace-a', 'gateway-a', 'gateway-probe',
                   'cpk-server', 'old-key-a', 'secret-reference-a',
                   'generation-a', 60, 5, 'correlation-a', 'operator-a',
                   '2026-08-15T03:56:10Z', %s, 'requested', 1)
                """,
                ("a" * 64,),
            )
            subject = GatewayKeyRotationApprovalSubject(
                rotation_id="rotation-a",
                workspace_id="workspace-a",
                gateway_node_id="gateway-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="old-key-a",
                maximum_grant_lifetime_seconds=60,
                clock_skew_seconds=5,
                rotation_intent_digest="a" * 64,
            )
            approval_scope = PolicyScope.DELEGATION_KEY_ROTATE_APPROVE
            approval_risk = RiskLevel.HIGH
            destructive = True

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.activity_history.add_approval_request(
                ApprovalRequestRecord(
                    "approval-request-a",
                    "session-a",
                    subject,
                    "operator-a",
                    "2026-08-15T03:57:00Z",
                    approval_scope,
                    approval_risk,
                    destructive,
                )
            )
            stores.activity_history.add_approval_decision(
                ApprovalDecisionRecord(
                    "approval-decision-a",
                    "approval-request-a",
                    "manager-a",
                    ApprovalDecisionKind.APPROVED,
                    approval_scope,
                    "2026-08-15T03:58:00Z",
                )
            )
            stores.execution.add_request(
                ExecutionRequestRecord(
                    ExecutionRequestIdentity(
                        "request-a",
                        "workspace-a",
                        "session-a",
                        "plan-a",
                    ),
                    ExecutionRequestStatus.CLAIMED,
                    "operator-a",
                    "2026-08-15T03:59:00Z",
                    "approval-request-a",
                    "approval-decision-a",
                    ExecutionIdempotency("execute-a", "execute-fingerprint-a"),
                    ClaimIdentity(
                        "worker-a",
                        7,
                        (
                            "2098-01-01T00:00:00Z"
                            if active
                            else "1999-01-01T00:00:00Z"
                        ),
                        (
                            "2099-01-01T00:00:00Z"
                            if active
                            else "2000-01-01T00:00:00Z"
                        ),
                    ),
                )
            )
            stores.execution.add_run(
                ActivityRunRecord(
                    "run-a",
                    "plan-a",
                    AdmittedRun("request-a"),
                    RetryIdentity(1),
                    ActivityRunStatus.CLAIMED if active else ActivityRunStatus.FAILED,
                    "2026-08-15T03:59:10Z",
                    started_at=None if active else "2026-08-15T03:59:20Z",
                    settled_at=None,
                )
            )
            for event in self.history_events(history):
                stores.execution.add_event(event)
            unit_of_work.commit()

    def history_events(self, history: str) -> tuple[ActivityEventRecord, ...]:
        kinds: tuple[tuple[ActivityEventKind, str | None], ...]
        if history == "active-empty":
            kinds = ((ActivityEventKind.RUN_OPENED, None),)
        elif history == "active-effect":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
            )
        elif history == "failed":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_FAILED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            )
        elif history == "resolved-forward-failure":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_UNCERTAIN, "start-runtime"),
                (
                    ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
                    "start-runtime",
                ),
                (ActivityEventKind.RUN_FAILED, None),
            )
        elif history == "in-flight":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            )
        elif history == "uncertain":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_UNCERTAIN, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            )
        elif history == "compensation-requested":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_SUCCEEDED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
                (ActivityEventKind.RUN_COMPENSATION_STARTED, None),
            )
        elif history == "compensation-completed":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_SUCCEEDED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
                (ActivityEventKind.RUN_COMPENSATION_STARTED, None),
                (ActivityEventKind.STEP_COMPENSATION_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_COMPENSATION_SUCCEEDED, "start-runtime"),
                (ActivityEventKind.RUN_COMPENSATION_SUCCEEDED, None),
            )
        elif history == "foreign-step":
            kinds = (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "foreign-step-canary"),
                (ActivityEventKind.STEP_FAILED, "foreign-step-canary"),
                (ActivityEventKind.RUN_FAILED, None),
            )
        else:
            raise AssertionError(f"unknown history fixture {history}")
        return tuple(
            ActivityEventRecord(
                f"seed-event-{ordinal}",
                "run-a",
                ordinal,
                kind,
                f"2026-08-15T03:59:{20 + ordinal:02d}Z",
                activity_id=activity_id,
                evidence=BoundedEvidence(),
            )
            for ordinal, (kind, activity_id) in enumerate(kinds, start=1)
        )


if __name__ == "__main__":
    unittest.main()
