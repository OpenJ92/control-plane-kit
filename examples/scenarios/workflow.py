"""Generic Roadmap 0007 workflow for planning any graph transition."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.topology import DeploymentGraph
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ActivityPlanningResult,
    ApprovalCommandService,
    ApprovalRequestResult,
    DesiredGraphCommandService,
    DesiredGraphEditResult,
    IdempotencyKey,
    OperationCommandResult,
    OperationCommandService,
    RequestActivityPlan,
    RequestPlanApproval,
    SetDesiredGraph,
    StartOperationSession,
)


@dataclass(frozen=True)
class PlanningWorkflowServices:
    """Transport-neutral command services required by the planning workflow."""

    operations: OperationCommandService
    desired_graphs: DesiredGraphCommandService
    plans: ActivityPlanningCommandService
    approvals: ApprovalCommandService


@dataclass(frozen=True)
class GraphTransitionPlanningResult:
    """Durable evidence from a complete, non-executing planning session."""

    session: OperationCommandResult
    desired_graph: DesiredGraphEditResult
    plan: ActivityPlanningResult
    approval: ApprovalRequestResult | None

    def descriptor(self) -> dict[str, object]:
        return {
            "session": self.session.descriptor(),
            "desired_graph": self.desired_graph.descriptor(),
            "plan": self.plan.descriptor(),
            "approval": (
                self.approval.descriptor()
                if self.approval is not None
                else None
            ),
            "runtime_effects_executed": False,
        }


def plan_graph_transition(
    services: PlanningWorkflowServices,
    *,
    workspace_id: str,
    actor_id: str,
    title: str,
    approval_comment: str,
    current_graph_id: str,
    expected_desired_graph_id: str | None,
    desired_graph: DeploymentGraph,
    idempotency_prefix: str,
) -> GraphTransitionPlanningResult:
    """Record and compile an arbitrary desired graph.

    Approval is requested only when the compiler declares the plan ready for
    execution. Review blockers remain durable planning evidence and must be
    resolved before an approval workflow can begin.
    """

    session = services.operations.execute(
        StartOperationSession(
            workspace_id=workspace_id,
            actor_id=actor_id,
            title=title,
            idempotency_key=IdempotencyKey(f"{idempotency_prefix}:session"),
        )
    )
    desired = services.desired_graphs.execute(
        SetDesiredGraph(
            session_id=session.session.session_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            graph=desired_graph,
            expected_desired_graph_id=expected_desired_graph_id,
            idempotency_key=IdempotencyKey(f"{idempotency_prefix}:desired"),
        )
    )
    plan = services.plans.execute(
        RequestActivityPlan(
            session_id=session.session.session_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            expected_current_graph_id=current_graph_id,
            expected_desired_graph_id=desired.graph_version.graph_id,
            idempotency_key=IdempotencyKey(f"{idempotency_prefix}:plan"),
        )
    )
    approval: ApprovalRequestResult | None = None
    if plan.plan_record.plan.ready_for_execution and plan.plan_record.plan.activities:
        approval = services.approvals.execute(
            RequestPlanApproval(
                session_id=session.session.session_id,
                plan_id=plan.plan_record.plan_id,
                actor_id=actor_id,
                actor_scopes=("plan:request",),
                idempotency_key=IdempotencyKey(f"{idempotency_prefix}:approval"),
                comment=approval_comment,
            )
        )
        if not isinstance(approval, ApprovalRequestResult):
            raise TypeError("planning workflow must produce an approval request")
    return GraphTransitionPlanningResult(session, desired, plan, approval)
