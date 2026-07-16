"""Compose the complete Roadmap 0007 planning workflow without effects."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.topology.graph import DeploymentGraph
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
class BackendSwapPlanningResult:
    """Durable evidence from a complete, non-executing planning session."""

    session: OperationCommandResult
    desired_graph: DesiredGraphEditResult
    plan: ActivityPlanningResult
    approval: ApprovalRequestResult

    def descriptor(self) -> dict[str, object]:
        return {
            "session": self.session.descriptor(),
            "desired_graph": self.desired_graph.descriptor(),
            "plan": self.plan.descriptor(),
            "approval": self.approval.descriptor(),
            "runtime_effects_executed": False,
        }


def plan_backend_swap(
    services: PlanningWorkflowServices,
    *,
    workspace_id: str,
    actor_id: str,
    current_graph_id: str,
    expected_desired_graph_id: str | None,
    desired_graph: DeploymentGraph,
    idempotency_prefix: str,
) -> BackendSwapPlanningResult:
    """Record intent, compile a plan, and request approval without executing it."""

    session = services.operations.execute(
        StartOperationSession(
            workspace_id=workspace_id,
            actor_id=actor_id,
            title="Replace API backend",
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
    approval = services.approvals.execute(
        RequestPlanApproval(
            session_id=session.session.session_id,
            plan_id=plan.plan_record.plan_id,
            actor_id=actor_id,
            actor_scopes=("plan:request",),
            idempotency_key=IdempotencyKey(f"{idempotency_prefix}:approval"),
            comment="Review the backend replacement plan before execution.",
        )
    )
    if not isinstance(approval, ApprovalRequestResult):
        raise TypeError("planning workflow must produce an approval request")
    return BackendSwapPlanningResult(session, desired, plan, approval)
