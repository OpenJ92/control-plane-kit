"""Callable deployment stages over canonical application command services."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.application.deploy.values import (
    ApprovalGrant,
    ApprovalSuspension,
    ApprovedDeployment,
    DeploymentPlanRequest,
    DeploymentPreparation,
    DeploymentPreparationResult,
    DeploymentReviewBlocked,
    NoDeploymentChanges,
)
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ApprovalCommandService,
    ApprovalDecisionKind,
    ApprovalDecisionResult,
    ApprovalRequestResult,
    DecidePlanApproval,
    DesiredGraphCommandService,
    IdempotencyKey,
    OperationCommandService,
    RequestActivityPlan,
    RequestPlanApproval,
    SetDesiredGraph,
    StartOperationSession,
)


@dataclass(frozen=True)
class PlanningServices:
    """Canonical transactional services interpreted by `Plan`."""

    operations: OperationCommandService
    desired_graphs: DesiredGraphCommandService
    plans: ActivityPlanningCommandService
    approvals: ApprovalCommandService


@dataclass(frozen=True)
class Plan:
    """Prepare a transition and suspend before any approval decision."""

    services: PlanningServices

    def __call__(self, request: DeploymentPlanRequest) -> DeploymentPreparationResult:
        if not isinstance(request, DeploymentPlanRequest):
            raise TypeError("Plan requires DeploymentPlanRequest")
        prefix = request.idempotency_prefix
        session = self.services.operations.execute(
            StartOperationSession(
                workspace_id=request.workspace_id,
                actor_id=request.actor_id,
                title=request.title,
                idempotency_key=IdempotencyKey(f"{prefix}:session"),
            )
        )
        desired = self.services.desired_graphs.execute(
            SetDesiredGraph(
                session_id=session.session.session_id,
                workspace_id=request.workspace_id,
                actor_id=request.actor_id,
                graph=request.transition.desired,
                expected_desired_graph_id=request.expected_desired_graph_id,
                idempotency_key=IdempotencyKey(f"{prefix}:desired"),
            )
        )
        plan = self.services.plans.execute(
            RequestActivityPlan(
                session_id=session.session.session_id,
                workspace_id=request.workspace_id,
                actor_id=request.actor_id,
                expected_current_graph_id=request.current_graph_id,
                expected_desired_graph_id=desired.graph_version.graph_id,
                idempotency_key=IdempotencyKey(f"{prefix}:plan"),
            )
        )
        preparation = DeploymentPreparation(request, session, desired, plan)
        if not plan.plan_record.plan.ready_for_execution:
            return DeploymentReviewBlocked(preparation)
        if not plan.plan_record.plan.activities:
            return NoDeploymentChanges(preparation)
        approval = self.services.approvals.execute(
            RequestPlanApproval(
                session_id=session.session.session_id,
                plan_id=plan.plan_record.plan_id,
                actor_id=request.actor_id,
                actor_scopes=("plan:request",),
                idempotency_key=IdempotencyKey(f"{prefix}:approval"),
                comment=request.approval_comment,
            )
        )
        if not isinstance(approval, ApprovalRequestResult):
            raise TypeError("approval request command returned the wrong result")
        return ApprovalSuspension(preparation, approval)


@dataclass(frozen=True)
class Approve:
    """Interpret an explicit authorized approval at the suspension boundary."""

    service: ApprovalCommandService

    def __call__(
        self,
        suspension: ApprovalSuspension,
        grant: ApprovalGrant,
    ) -> ApprovedDeployment:
        if not isinstance(suspension, ApprovalSuspension):
            raise TypeError("Approve requires ApprovalSuspension")
        if not isinstance(grant, ApprovalGrant):
            raise TypeError("Approve requires ApprovalGrant")
        request = suspension.approval_request.request
        result = self.service.execute(
            DecidePlanApproval(
                session_id=request.session_id,
                request_id=request.request_id,
                actor_id=grant.actor_id,
                actor_scopes=grant.actor_scopes,
                decision=ApprovalDecisionKind.APPROVED,
                idempotency_key=grant.idempotency_key,
                comment=grant.comment,
            )
        )
        if not isinstance(result, ApprovalDecisionResult):
            raise TypeError("approval decision command returned the wrong result")
        return ApprovedDeployment(suspension, result)
