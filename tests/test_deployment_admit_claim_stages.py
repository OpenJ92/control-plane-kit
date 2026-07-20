from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg

from control_plane_kit.application.deploy import (
    Advance,
    AdvancedDeployment,
    AdvancementGrant,
    AdmissionGrant,
    Admit,
    ApprovalGrant,
    ApprovalSuspension,
    Approve,
    Claim,
    ClaimGrant,
    ClaimedDeployment,
    DeploymentPlanRequest,
    DeploymentExecutionGrant,
    Deploy,
    Execute,
    ExecuteApprovedDeployment,
    ExecutedDeployment,
    ExecutionContinuation,
    ExecutionLimits,
    Plan,
    PlanningServices,
    PrepareDeployment,
    RecoverySuspension,
    classify_transition,
)
from control_plane_kit.execution import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
)
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ApprovalCommandService,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    ExecutionCoordinatorResult,
    ExecutionWorkerAuthority,
    CoordinatorStatus,
    CurrentGraphAdvancementCommandService,
    IdempotencyKey,
    OperationCommandService,
    RunLifecycleCommandService,
)
from examples.router_runtime import router_graph
from examples.scenarios.runner import ScenarioEffectInterpreter
from tests.postgres_case import PostgresStoreTestCase


class Ids:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next_value = 1

    def __call__(self) -> str:
        value = f"{self.prefix}-{self.next_value}"
        self.next_value += 1
        return value


class FixedCoordinator:
    """Return one canonical coordinator result to exercise stage classification."""

    def __init__(self, result: ExecutionCoordinatorResult) -> None:
        self.result = result

    def execute(self, _command) -> ExecutionCoordinatorResult:
        return self.result


class DeploymentAdmitClaimStageTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.current = router_graph("api-v1")
        self.desired = router_graph("api-v2")
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Deploy test"))
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-current",
                workspace_id="workspace-a",
                version=1,
                graph=self.current,
                created_by="operator",
                created_at="2026-07-18T00:00:00Z",
            )
        )
        self.stores.workspace.set_current_graph("workspace-a", "graph-current")
        self.stores.workspace.set_desired_graph("workspace-a", "graph-current")
        self.factory = lambda: PostgresUnitOfWork(
            lambda: psycopg.connect(os.environ["CPK_TEST_DATABASE_URL"])
        )

    def test_approved_plan_reaches_execution_through_callable_stages(self) -> None:
        planning, approvals = self._planning_services()
        authority = ExecutionWorkerAuthority("worker-a", ("execution:operate",))
        lifecycle = RunLifecycleCommandService(
            self.factory,
            clock=lambda: "2026-07-18T00:06:00Z",
            id_factory=Ids("lifecycle"),
        )
        interpreter = ScenarioEffectInterpreter()
        deploy = Deploy(
            self.current,
            self.desired,
            PrepareDeployment(Plan(planning)),
            Approve(approvals),
            ExecuteApprovedDeployment(
                Admit(
                    ExecutionAdmissionCommandService(
                        self.factory,
                        clock=lambda: "2026-07-18T00:05:00Z",
                        id_factory=Ids("admission"),
                    )
                ),
                Claim(lifecycle),
                Execute(
                    ExecutionCoordinator(
                        self.factory,
                        lifecycle,
                        interpreter,
                        clock=lambda: datetime(2026, 7, 18, tzinfo=timezone.utc),
                        id_factory=Ids("coordinator"),
                    )
                ),
                Advance(
                    CurrentGraphAdvancementCommandService(
                        self.factory,
                        clock=lambda: "2026-07-18T00:07:00Z",
                        id_factory=Ids("advancement"),
                    )
                ),
            ),
        )
        suspended = deploy(
            DeploymentPlanRequest(
                deploy.transition,
                "workspace-a",
                "graph-current",
                "graph-current",
                "operator",
                "Admit and claim",
                "Review the transition.",
                "admit-claim",
            )
        )
        assert isinstance(suspended, ApprovalSuspension)
        foreign = Deploy(
            self.desired,
            self.current,
            deploy.preparation,
            deploy.approval,
            deploy.execution,
        )
        with self.assertRaisesRegex(ValueError, "another graph transition"):
            foreign.approve(
                suspended,
                ApprovalGrant(
                    "approver",
                    (suspended.approval_request.request.required_scope,),
                    IdempotencyKey("foreign:approve"),
                ),
            )
        self.assertIsNone(
            self.stores.activity_history.approval_decision_for_request(
                suspended.approval_request.request.request_id
            )
        )
        approved = deploy.approve(
            suspended,
            ApprovalGrant(
                "approver",
                (suspended.approval_request.request.required_scope,),
                IdempotencyKey("admit-claim:approve"),
            ),
        )
        with self.assertRaisesRegex(ValueError, "another graph transition"):
            foreign.execute_approved(
                approved,
                DeploymentExecutionGrant(
                    AdmissionGrant(
                        "operator",
                        ("plan:execute",),
                        IdempotencyKey("foreign:admit"),
                    ),
                    ClaimGrant(
                        authority,
                        "2026-07-18T01:00:00Z",
                        IdempotencyKey("foreign:claim"),
                        IdempotencyKey("foreign:start"),
                    ),
                    AdvancementGrant(IdempotencyKey("foreign:advance")),
                ),
            )
        self.assertEqual(
            self.stores.execution.runs_for_plan(
                approved.suspension.preparation.plan.plan_record.plan_id
            ),
            (),
        )
        advanced = deploy.execute_approved(
            approved,
            DeploymentExecutionGrant(
                AdmissionGrant(
                    "operator",
                    ("plan:execute",),
                    IdempotencyKey("admit-claim:admit"),
                ),
                ClaimGrant(
                    authority,
                    "2026-07-18T01:00:00Z",
                    IdempotencyKey("admit-claim:claim"),
                    IdempotencyKey("admit-claim:start"),
                ),
                AdvancementGrant(IdempotencyKey("admit-claim:advance")),
            ),
        )
        self.assertIsInstance(advanced, AdvancedDeployment)
        assert isinstance(advanced, AdvancedDeployment)
        executed = advanced.executed
        claimed = executed.claimed
        admitted = claimed.admitted

        self.assertIsInstance(claimed, ClaimedDeployment)
        self.assertIs(admitted.admission.request.status, ExecutionRequestStatus.QUEUED)
        self.assertIs(claimed.opened.event.kind, ActivityEventKind.RUN_OPENED)
        self.assertIs(claimed.opened.run.status, ActivityRunStatus.CLAIMED)
        self.assertIs(claimed.started.event.kind, ActivityEventKind.RUN_STARTED)
        self.assertIs(claimed.started.run.status, ActivityRunStatus.RUNNING)
        self.assertEqual(
            [
                event.kind
                for event in self.stores.execution.events_for_run(
                    claimed.started.run.run_id
                )[:2]
            ],
            [ActivityEventKind.RUN_OPENED, ActivityEventKind.RUN_STARTED],
        )
        self.assertGreater(len(interpreter.requests), 0)
        self.assertIs(executed.execution.run.status, ActivityRunStatus.SUCCEEDED)
        self.assertEqual(advanced.advancement.from_graph_id, "graph-current")
        self.assertEqual(
            advanced.advancement.to_graph_id,
            executed.claimed.admitted.approved.suspension.preparation.plan.plan_record.desired_graph_id,
        )
        self.assertEqual(
            self.stores.workspace.get("workspace-a").current_graph_id,
            advanced.advancement.to_graph_id,
        )

        advance = deploy.execution.advance

        for status in (
            CoordinatorStatus.PAUSED,
            CoordinatorStatus.COMPENSATION_FAILED,
            CoordinatorStatus.UNCERTAIN,
        ):
            with self.subTest(status=status):
                suspended = Execute(
                    FixedCoordinator(
                        ExecutionCoordinatorResult(status, claimed.started.run)
                    )
                )(claimed)
                self.assertIsInstance(suspended, RecoverySuspension)
                assert isinstance(suspended, RecoverySuspension)
                self.assertIs(suspended.execution.status, status)

        for status in (
            CoordinatorStatus.PROGRESSED,
            CoordinatorStatus.IN_FLIGHT,
        ):
            with self.subTest(status=status):
                continuation = Execute(
                    FixedCoordinator(
                        ExecutionCoordinatorResult(status, claimed.started.run)
                    )
                )(claimed)
                self.assertIsInstance(continuation, ExecutionContinuation)
                assert isinstance(continuation, ExecutionContinuation)
                self.assertIs(continuation.execution.status, status)

                with self.assertRaisesRegex(
                    ValueError, "another graph transition"
                ):
                    foreign.resume_execution(
                        continuation,
                        limits=ExecutionLimits(),
                        advancement=AdvancementGrant(
                            IdempotencyKey("foreign:resume")
                        ),
                    )

        with self.assertRaisesRegex(ValueError, "another graph transition"):
            foreign.resume_recovered(
                suspended,
                limits=ExecutionLimits(),
                advancement=AdvancementGrant(
                    IdempotencyKey("foreign:recovery")
                ),
            )

        with self.assertRaisesRegex(TypeError, "ExecutedDeployment"):
            advance(
                continuation,
                AdvancementGrant(IdempotencyKey("admit-claim:invalid-continuation")),
            )
        with self.assertRaisesRegex(TypeError, "ExecutedDeployment"):
            advance(
                suspended,
                AdvancementGrant(IdempotencyKey("admit-claim:invalid-recovery")),
            )

    def _planning_services(self) -> tuple[PlanningServices, ApprovalCommandService]:
        approvals = ApprovalCommandService(
            self.factory,
            clock=lambda: "2026-07-18T00:04:00Z",
            id_factory=Ids("approval"),
        )
        return (
            PlanningServices(
                OperationCommandService(
                    self.factory,
                    clock=lambda: "2026-07-18T00:01:00Z",
                    id_factory=Ids("operation"),
                ),
                DesiredGraphCommandService(
                    self.factory,
                    clock=lambda: "2026-07-18T00:02:00Z",
                    id_factory=Ids("graph"),
                ),
                ActivityPlanningCommandService(
                    self.factory,
                    clock=lambda: "2026-07-18T00:03:00Z",
                    id_factory=Ids("plan"),
                ),
                approvals,
            ),
            approvals,
        )
