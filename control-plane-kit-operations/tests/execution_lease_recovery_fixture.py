from __future__ import annotations

import importlib
import os
import unittest

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs
from tests.gateway_rotation_overlap_fixture import GatewayRotationOverlapFixture

from control_plane_kit_core.approval_subjects import (
    ActivityPlanApprovalSubject,
)
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
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
from control_plane_kit_operations.lifecycle import ExecutionLeaseDuration
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
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


TARGET_MODULE = "control_plane_kit_operations.execution_lease_recovery_interpreter"

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
        self.calls: list[str] = []

    def __call__(self) -> str:
        value = self.values.pop(0)
        self.calls.append(value)
        return value


def safe_error(
    test: unittest.TestCase,
    error: BaseException,
    *canaries: str,
) -> None:
    test.assertIsNone(error.__cause__)
    test.assertIsNone(error.__context__)
    rendered = f"{error!s} {error!r}"
    test.assertLessEqual(len(rendered), 512)
    for canary in canaries:
        test.assertNotIn(canary, rendered)


class PostgresExecutionLeaseRecoveryFixture(GatewayRotationOverlapFixture):
    maxDiff = None
    seeded_fence = ExecutionLeaseFence("worker-a", 7)

    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run through Docker"
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        install_schema(self.connection)
        # Registered only after schema acceptance; never repair a failed schema.
        self.addCleanup(self._cleanup_fixture_rows)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        try:
            self._cleanup_fixture_rows()
        finally:
            connection = getattr(self, "connection", None)
            if connection is not None:
                connection.close()

    def _cleanup_fixture_rows(self) -> None:
        connection = getattr(self, "connection", None)
        if connection is not None and not connection.closed:
            connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def require_service(self) -> None:
        self.assertIsNotNone(
            ExecutionLeaseRecoveryCommandService,
            "execution-lease recovery interpreter is missing",
        )

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service_with_sequence(self, *ids: str):
        self.require_service()
        sequence = Sequence(*ids)
        return (
            ExecutionLeaseRecoveryCommandService(
                self.unit_of_work,
                id_factory=sequence,
            ),
            sequence,
        )

    def service(self, *ids: str):
        return self.service_with_sequence(*ids)[0]

    def authority(
        self,
        scope: RecoveryScope,
        *,
        actor_id: str = "operator-a",
        authority_reference: str = "authority-reference-a",
        extra_scopes: tuple[RecoveryScope, ...] = (),
    ) -> RecoveryAuthority:
        return RecoveryAuthority(
            actor_id,
            authority_reference,
            (scope, *extra_scopes),
        )

    def command(
        self,
        decision: RecoveryDecisionKind,
        *,
        key: str = "recover-a",
        duration: int = 600,
        actor_id: str = "operator-a",
        authority_reference: str = "authority-reference-a",
        extra_scopes: tuple[RecoveryScope, ...] = (),
        expected_fence: ExecutionLeaseFence | None = None,
        retained_run_id: str = "run-a",
    ):
        scope = {
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: RecoveryScope.RENEW_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: RecoveryScope.RENEW_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: RecoveryScope.TAKE_OVER_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: RecoveryScope.ABANDON_CLAIM,
        }[decision]
        common = {
            "request_id": "request-a",
            "retained_run_id": RunId(retained_run_id),
            "expected_fence": expected_fence or self.seeded_fence,
            "authority": self.authority(
                scope,
                actor_id=actor_id,
                authority_reference=authority_reference,
                extra_scopes=extra_scopes,
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

    def snapshot(self) -> tuple[object, ...]:
        return (
            self.connection.execute(
                "SELECT status, claim_worker_id, claim_generation, claimed_at, "
                "lease_expires_at FROM cpk_execution_requests "
                "WHERE request_id = 'request-a'"
            ).fetchone(),
            tuple(
                self.connection.execute(
                    "SELECT event_id, ordinal, event_type, occurred_at, payload "
                    "FROM cpk_activity_events WHERE run_id IN "
                    "(SELECT run_id FROM cpk_activity_runs "
                    " WHERE request_id = 'request-a') ORDER BY run_id, ordinal"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT action_id, ordinal, action_type, actor_id, payload, "
                    "idempotency_key, intent_fingerprint "
                    "FROM cpk_operation_actions WHERE session_id = 'session-a' "
                    "ORDER BY ordinal"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT run_id, attempt, prior_run_id, status, started_at, "
                    "settled_at FROM cpk_activity_runs "
                    "WHERE request_id = 'request-a' ORDER BY attempt"
                ).fetchall()
            ),
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

    def seed_execution_request(self) -> None:
        """Service fixtures establish request truth through real admission."""
        from tests.receiver_scope_history_fixture import admit_fixture_plan
        admit_fixture_plan(self, requested_at="2026-08-15T03:59:00Z")

    def seed_truth(
        self,
        decision: RecoveryDecisionKind,
        *,
        history: str | None = None,
        approval_subject: str = "activity-plan",
    ) -> None:
        from tests.graph_lineage_fixture import execution_graph
        active = decision is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM
        history = history or ("active-empty" if active else "failed")
        self.seeded_fence = ExecutionLeaseFence("worker-a", 7)
        if approval_subject == "gateway-key-rotation":
            self._seed_gateway_truth(active=active, history=history)
            return
        self.assertEqual(approval_subject, "activity-plan")
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
                graphs={key: execution_graph(key) for key in ("graph-current", "graph-desired")},
            )
            stores.workspaces.set_current_graph("workspace-a", "graph-current")
            stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
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

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.activity_history.add_approval_request(
                ApprovalRequestRecord(
                    "approval-request-a",
                    "session-a",
                    ActivityPlanApprovalSubject("plan-a"),
                    "operator-a",
                    "2026-08-15T03:57:00Z",
                    PolicyScope.PLAN_APPROVE,
                    RiskLevel.LOW,
                    False,
                )
            )
            stores.activity_history.add_approval_decision(
                ApprovalDecisionRecord(
                    "approval-decision-a",
                    "approval-request-a",
                    "manager-a",
                    ApprovalDecisionKind.APPROVED,
                    PolicyScope.PLAN_APPROVE,
                    "2026-08-15T03:58:00Z",
                )
            )
            unit_of_work.commit()
        self.seed_execution_request()
        # Recorded lease/run overlays preserve the recovery law's original fence and time.
        self.connection.execute(
            "UPDATE cpk_execution_requests SET status='claimed', claim_worker_id='worker-a', "
            "claim_generation=7, claimed_at=%s, lease_expires_at=%s WHERE request_id='request-a'",
            ("2098-01-01T00:00:00Z" if active else "1999-01-01T00:00:00Z",
             "2099-01-01T00:00:00Z" if active else "2000-01-01T00:00:00Z"))
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution._add_run(
                ActivityRunRecord(
                    "run-a",
                    "plan-a",
                    AdmittedRun("request-a"),
                    RetryIdentity(1),
                    ActivityRunStatus.CLAIMED if active else ActivityRunStatus.FAILED,
                    "2026-08-15T03:59:10Z",
                    started_at=None if active else "2026-08-15T03:59:20Z",
                    metadata=BoundedEvidence.from_mapping({"attempt": 1}),
                )
            )
            for event in self.history_events(history):
                stores.execution.add_event(event)
            unit_of_work.commit()

    def _seed_gateway_truth(self, *, active, history):
        """Actual parent approval and child admission; no approval overlays.

        The active case stops after public ClaimAndOpen. Failed cases use the
        real coordinator and a failed provider substitute, then only expire
        the lease's clock fixture for the recovery operation under test.
        """
        from datetime import datetime, timezone
        from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
        from control_plane_kit_operations.coordinator import ExecuteActivityRun
        from control_plane_kit_operations.gateway_key_rotation_overlap import (
            GatewayKeyRotationOverlapProjectionService, PublishGatewayKeyRotationOverlapProjection,
        )
        from control_plane_kit_operations.lifecycle import (
            ClaimAndOpenActivityRun, ExecutionWorkerAuthority, RunLifecycleCommandService, StartActivityRun,
        )
        from control_plane_kit_operations.planning import ActivityPlanningCommandService, RequestActivityPlan
        from control_plane_kit_operations.postgres.temporal import decode_postgres_timestamp
        from control_plane_kit_operations.workflows import OperationCommandService, StartOperationSession
        from tests.gateway_rotation_overlap_fixture import effect_attempt_execution_coordinator
        from tests.receiver_scope_history_fixture import admit_fixture_plan

        self.assertEqual(history, "active-empty" if active else "failed")
        clock = lambda: decode_postgres_timestamp(datetime.now(timezone.utc))
        self.seed_graph_and_keys()
        self.seed_rotation_approval(approval_request_id="approval-request-a",
            approval_decision_id="approval-decision-a")
        OperationCommandService(self.unit_of_work, clock=clock,
            id_factory=Sequence("session-a", "child-session-action")).execute(StartOperationSession(
                "workspace-a", "operator-a", "Recover gateway child", IdempotencyKey("child-session")))
        publication = GatewayKeyRotationOverlapProjectionService(self.unit_of_work, clock=clock,
            action_id_factory=Sequence("child-publication")).execute(PublishGatewayKeyRotationOverlapProjection(
                self.rotation_id, "session-a", "operator-a", self.rotation_version,
                "graph-a", "projection-a", "projection-a", 1,
                (PolicyScope.DELEGATION_KEY_ROTATE,), IdempotencyKey("child-publication"))).publication
        ActivityPlanningCommandService(self.unit_of_work, clock=clock,
            id_factory=Sequence("plan-a", "child-plan-action")).execute(RequestActivityPlan(
                "session-a", "workspace-a", "operator-a", "graph-a", "graph-a", IdempotencyKey("child-plan"),
                publication.previous_realized_projection_id, publication.desired_realized_projection_id,
                publication.desired_graph_revision))
        admit_fixture_plan(self, requested_at=clock(),
            actor_scopes=(PolicyScope.PLAN_EXECUTE, PolicyScope.DELEGATION_KEY_ROTATE))
        authority = ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,))
        claimed = RunLifecycleCommandService(self.unit_of_work, clock=clock,
            id_factory=Sequence("run-a", "seed-event-1", "child-claim-action")).execute(ClaimAndOpenActivityRun(
                "request-a", authority, ExecutionLeaseDuration(3600), IdempotencyKey("child-claim")))
        self.seeded_fence = claimed.request.claim.fence
        if active:
            self.assertIs(claimed.run.status, ActivityRunStatus.CLAIMED)
            return
        RunLifecycleCommandService(self.unit_of_work, clock=clock,
            id_factory=Sequence("seed-event-2", "child-start-action")).execute(StartActivityRun(
                "run-a", authority, self.seeded_fence, IdempotencyKey("child-start")))

        class FailedRuntime:
            def execute_runtime(self, context, request):
                return RuntimeEffectResult.failed(request.effect_id,
                    RuntimeEffectFailure("fixture.failure", "Provider substitute failed"))

        effect_attempt_execution_coordinator(self.unit_of_work, FailedRuntime(), clock=clock,
            prefix="child-failure").execute(ExecuteActivityRun(
                "run-a", authority, self.seeded_fence, IdempotencyKey("child-failure")))
        with self.unit_of_work() as uow:
            self.assertIs(uow.stores.execution.get_run("run-a").status, ActivityRunStatus.FAILED)
        self.connection.execute("UPDATE cpk_execution_requests SET claimed_at='1999-01-01T00:00:00Z', "
            "lease_expires_at='2000-01-01T00:00:00Z' WHERE request_id='request-a'")

    def add_newer_failed_run(self) -> None:
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution._add_run(
                ActivityRunRecord(
                    "run-b",
                    "plan-a",
                    AdmittedRun("request-a"),
                    RetryIdentity(2, "run-a"),
                    ActivityRunStatus.FAILED,
                    "2026-08-15T05:00:00Z",
                    started_at="2026-08-15T05:00:01Z",
                )
            )
            stores.execution.add_event(
                ActivityEventRecord(
                    "run-b-opened",
                    "run-b",
                    1,
                    ActivityEventKind.RUN_OPENED,
                    "2026-08-15T05:00:00Z",
                )
            )
            stores.execution.add_event(
                ActivityEventRecord(
                    "run-b-started",
                    "run-b",
                    2,
                    ActivityEventKind.RUN_STARTED,
                    "2026-08-15T05:00:01Z",
                )
            )
            stores.execution.add_event(
                ActivityEventRecord(
                    "run-b-step-started",
                    "run-b",
                    3,
                    ActivityEventKind.STEP_STARTED,
                    "2026-08-15T05:00:02Z",
                    activity_id="start-runtime",
                )
            )
            stores.execution.add_event(
                ActivityEventRecord(
                    "run-b-step-failed",
                    "run-b",
                    4,
                    ActivityEventKind.STEP_FAILED,
                    "2026-08-15T05:00:03Z",
                    activity_id="start-runtime",
                )
            )
            stores.execution.add_event(
                ActivityEventRecord(
                    "run-b-failed",
                    "run-b",
                    5,
                    ActivityEventKind.RUN_FAILED,
                    "2026-08-15T05:00:04Z",
                )
            )
            unit_of_work.commit()

    def history_events(self, history: str) -> tuple[ActivityEventRecord, ...]:
        histories = {
            "active-empty": ((ActivityEventKind.RUN_OPENED, None),),
            "active-corruption-effect": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
            ),
            "active-run-started": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
            ),
            "failed": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_FAILED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            ),
            "duplicate-start": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_FAILED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            ),
            "post-terminal-success": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_FAILED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
                (ActivityEventKind.RUN_SUCCEEDED, None),
            ),
            "orphan-recovery-consequence": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_FAILED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
                (ActivityEventKind.REQUEST_CLAIM_RENEWED, None),
            ),
            "resolved-forward-failure": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_UNCERTAIN, "start-runtime"),
                (ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            ),
            "in-flight": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            ),
            "uncertain": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_UNCERTAIN, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
            ),
            "compensation-requested": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_SUCCEEDED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
                (ActivityEventKind.RUN_COMPENSATION_STARTED, None),
            ),
            "compensation-completed": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_SUCCEEDED, "start-runtime"),
                (ActivityEventKind.RUN_FAILED, None),
                (ActivityEventKind.RUN_COMPENSATION_STARTED, None),
                (ActivityEventKind.STEP_COMPENSATION_STARTED, "start-runtime"),
                (ActivityEventKind.STEP_COMPENSATION_SUCCEEDED, "start-runtime"),
                (ActivityEventKind.RUN_COMPENSATION_SUCCEEDED, None),
            ),
            "foreign-step": (
                (ActivityEventKind.RUN_OPENED, None),
                (ActivityEventKind.RUN_STARTED, None),
                (ActivityEventKind.STEP_STARTED, "foreign-step-canary"),
                (ActivityEventKind.STEP_FAILED, "foreign-step-canary"),
                (ActivityEventKind.RUN_FAILED, None),
            ),
        }
        try:
            kinds = histories[history]
        except KeyError:
            raise AssertionError(f"unknown history fixture {history}") from None
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
