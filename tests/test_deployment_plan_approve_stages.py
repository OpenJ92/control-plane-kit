from __future__ import annotations

import os

import psycopg

from control_plane_kit.application.deploy import (
    ApprovalGrant,
    ApprovalSuspension,
    ApprovedDeployment,
    Approve,
    DeploymentPlanRequest,
    NoDeploymentChanges,
    Plan,
    PlanningServices,
    classify_transition,
)
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    PostgresUnitOfWork,
    WorkspaceRecord,
)
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ApprovalAuthorizationDenied,
    ApprovalCommandService,
    DesiredGraphCommandService,
    IdempotencyKey,
    OperationCommandService,
)
from examples.router_runtime import router_graph
from tests.postgres_case import PostgresStoreTestCase


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class DeploymentPlanApproveStageTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.current = router_graph("api-v1")
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

    def test_plan_suspends_then_approve_records_explicit_authorized_decision(self) -> None:
        services = self._services()
        request = self._request(router_graph("api-v2"), "backend-switch")

        suspended = Plan(services)(request)

        self.assertIsInstance(suspended, ApprovalSuspension)
        assert isinstance(suspended, ApprovalSuspension)
        self.assertEqual(
            [
                value.action_type
                for value in self.stores.activity_history.actions_for_session("session-a")
            ],
            [
                OperationActionKind.SESSION_STARTED,
                OperationActionKind.SET_DESIRED_GRAPH,
                OperationActionKind.PLAN_REQUESTED,
                OperationActionKind.APPROVAL_REQUESTED,
            ],
        )
        self.assertEqual(self.stores.execution.runs_for_plan("plan-a"), ())

        approved = Approve(services.approvals)(
            suspended,
            ApprovalGrant(
                "approver",
                (suspended.approval_request.request.required_scope,),
                IdempotencyKey("backend-switch:approval-decision"),
                "Approved in the application-stage test.",
            ),
        )

        self.assertIsInstance(approved, ApprovedDeployment)
        self.assertEqual(approved.approval.decision.actor_id, "approver")
        self.assertEqual(
            self.stores.activity_history.approval_decision_for_request(
                "approval-a"
            ).decision_id,
            "decision-a",
        )
        self.assertEqual(self.stores.execution.runs_for_plan("plan-a"), ())

    def test_no_op_preserves_plan_evidence_without_requesting_approval(self) -> None:
        services = self._services()

        result = Plan(services)(self._request(self.current, "no-op"))

        self.assertIsInstance(result, NoDeploymentChanges)
        assert isinstance(result, NoDeploymentChanges)
        self.assertEqual(result.preparation.plan.plan_record.plan.activities, ())
        self.assertEqual(
            [
                value.action_type
                for value in self.stores.activity_history.actions_for_session("session-a")
            ],
            [
                OperationActionKind.SESSION_STARTED,
                OperationActionKind.SET_DESIRED_GRAPH,
                OperationActionKind.PLAN_REQUESTED,
            ],
        )
        self.assertEqual(self.stores.execution.runs_for_plan("plan-a"), ())

    def test_approve_preserves_canonical_authorization_failure(self) -> None:
        services = self._services()
        suspended = Plan(services)(
            self._request(router_graph("api-v2"), "backend-switch")
        )
        assert isinstance(suspended, ApprovalSuspension)

        with self.assertRaises(ApprovalAuthorizationDenied):
            Approve(services.approvals)(
                suspended,
                ApprovalGrant(
                    "unauthorized-actor",
                    ("plan:request",),
                    IdempotencyKey("backend-switch:unauthorized-decision"),
                ),
            )

        self.assertIsNone(
            self.stores.activity_history.approval_decision_for_request("approval-a")
        )

    def _request(self, desired, prefix: str) -> DeploymentPlanRequest:
        return DeploymentPlanRequest(
            transition=classify_transition(self.current, desired),
            workspace_id="workspace-a",
            current_graph_id="graph-current",
            expected_desired_graph_id="graph-current",
            actor_id="operator",
            title="Deploy application stage test",
            approval_comment="Review the graph transition.",
            idempotency_prefix=prefix,
        )

    def _services(self) -> PlanningServices:
        factory = lambda: PostgresUnitOfWork(
            lambda: psycopg.connect(os.environ["CPK_TEST_DATABASE_URL"])
        )
        approvals = ApprovalCommandService(
            factory,
            clock=lambda: "2026-07-18T00:04:00Z",
            id_factory=Sequence(
                "approval-a",
                "approval-action",
                "decision-a",
                "decision-action",
            ),
        )
        return PlanningServices(
            OperationCommandService(
                factory,
                clock=lambda: "2026-07-18T00:01:00Z",
                id_factory=Sequence("session-a", "session-action"),
            ),
            DesiredGraphCommandService(
                factory,
                clock=lambda: "2026-07-18T00:02:00Z",
                id_factory=Sequence("graph-desired", "desired-action"),
            ),
            ActivityPlanningCommandService(
                factory,
                clock=lambda: "2026-07-18T00:03:00Z",
                id_factory=Sequence("plan-a", "plan-action"),
            ),
            approvals,
        )
