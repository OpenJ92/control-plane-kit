"""Callable deployment stages over canonical application command services."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.application.deploy.values import (
    AdvancedDeployment,
    AdvancementGrant,
    AdmissionGrant,
    AdmittedDeployment,
    ApprovalGrant,
    ApprovalSuspension,
    ApprovedDeployment,
    ClaimGrant,
    ClaimedDeployment,
    DeploymentPlanRequest,
    DeploymentPreparation,
    DeploymentPreparationResult,
    DeploymentReviewBlocked,
    DeploymentExecutionResult,
    ExecutedDeployment,
    ExecutionContinuation,
    ExecutionLimits,
    NoDeploymentChanges,
    RecoverySuspension,
)
from control_plane_kit.workflows import (
    AdvanceCurrentGraph,
    ActivityPlanningCommandService,
    ApprovalCommandService,
    ApprovalDecisionKind,
    ApprovalDecisionResult,
    ApprovalRequestResult,
    ClaimAndOpenActivityRun,
    DecidePlanApproval,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionAdmissionResult,
    CoordinatorStatus,
    CurrentGraphAdvancementCommandService,
    ExecuteActivityRun,
    ExecutionCoordinator,
    IdempotencyKey,
    OperationCommandService,
    RequestActivityPlan,
    RequestPlanApproval,
    RequestPlanExecution,
    RunLifecycleCommandService,
    SetDesiredGraph,
    StartOperationSession,
    StartActivityRun,
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


@dataclass(frozen=True)
class Admit:
    """Record durable execution intent for one explicitly approved plan."""

    service: ExecutionAdmissionCommandService

    def __call__(
        self,
        approved: ApprovedDeployment,
        grant: AdmissionGrant,
    ) -> AdmittedDeployment:
        if not isinstance(approved, ApprovedDeployment):
            raise TypeError("Admit requires ApprovedDeployment")
        if not isinstance(grant, AdmissionGrant):
            raise TypeError("Admit requires AdmissionGrant")
        preparation = approved.suspension.preparation
        approval_request = approved.suspension.approval_request.request
        result = self.service.execute(
            RequestPlanExecution(
                workspace_id=preparation.request.workspace_id,
                session_id=preparation.session.session.session_id,
                plan_id=preparation.plan.plan_record.plan_id,
                approval_request_id=approval_request.request_id,
                actor_id=grant.actor_id,
                actor_scopes=grant.actor_scopes,
                idempotency_key=grant.idempotency_key,
                readiness=grant.readiness,
            )
        )
        if not isinstance(result, ExecutionAdmissionResult):
            raise TypeError("execution admission command returned the wrong result")
        return AdmittedDeployment(approved, result)


@dataclass(frozen=True)
class Claim:
    """Claim/open then start one admitted run through canonical transitions."""

    service: RunLifecycleCommandService

    def __call__(
        self,
        admitted: AdmittedDeployment,
        grant: ClaimGrant,
    ) -> ClaimedDeployment:
        if not isinstance(admitted, AdmittedDeployment):
            raise TypeError("Claim requires AdmittedDeployment")
        if not isinstance(grant, ClaimGrant):
            raise TypeError("Claim requires ClaimGrant")
        opened = self.service.execute(
            ClaimAndOpenActivityRun(
                request_id=admitted.admission.request.identity.request_id,
                authority=grant.authority,
                lease_expires_at=grant.lease_expires_at,
                idempotency_key=grant.claim_idempotency_key,
            )
        )
        started = self.service.execute(
            StartActivityRun(
                run_id=opened.run.run_id,
                authority=grant.authority,
                idempotency_key=grant.start_idempotency_key,
            )
        )
        return ClaimedDeployment(admitted, opened, started, grant.authority)


@dataclass(frozen=True)
class Execute:
    """Request bounded coordinator progress and classify its durable outcome."""

    coordinator: ExecutionCoordinator

    def __call__(
        self,
        claimed: ClaimedDeployment | ExecutionContinuation,
        limits: ExecutionLimits = ExecutionLimits(),
    ) -> DeploymentExecutionResult:
        match claimed:
            case ClaimedDeployment() as deployment:
                pass
            case ExecutionContinuation(claimed=deployment):
                pass
            case _:
                raise TypeError("Execute requires claimed work or a continuation")
        if not isinstance(limits, ExecutionLimits):
            raise TypeError("Execute requires ExecutionLimits")
        result = self.coordinator.execute(
            ExecuteActivityRun(
                run_id=deployment.started.run.run_id,
                authority=deployment.authority,
                timeout=limits.timeout,
                max_effects=limits.max_effects,
            )
        )
        match result.status:
            case CoordinatorStatus.COMPLETED:
                return ExecutedDeployment(deployment, result)
            case CoordinatorStatus.PROGRESSED | CoordinatorStatus.IN_FLIGHT:
                return ExecutionContinuation(deployment, result)
            case _:
                return RecoverySuspension(deployment, result)


@dataclass(frozen=True)
class Advance:
    """Publish the plan-pinned desired graph after terminal execution success."""

    service: CurrentGraphAdvancementCommandService

    def __call__(
        self,
        executed: ExecutedDeployment,
        grant: AdvancementGrant,
    ) -> AdvancedDeployment:
        if not isinstance(executed, ExecutedDeployment):
            raise TypeError("Advance requires ExecutedDeployment")
        if not isinstance(grant, AdvancementGrant):
            raise TypeError("Advance requires AdvancementGrant")
        preparation = executed.claimed.admitted.approved.suspension.preparation
        result = self.service.execute(
            AdvanceCurrentGraph(
                workspace_id=preparation.request.workspace_id,
                run_id=executed.execution.run.run_id,
                plan_id=preparation.plan.plan_record.plan_id,
                expected_current_graph_id=preparation.request.current_graph_id,
                desired_graph_id=preparation.plan.plan_record.desired_graph_id,
                authority=executed.claimed.authority,
                idempotency_key=grant.idempotency_key,
            )
        )
        return AdvancedDeployment(executed, result)
