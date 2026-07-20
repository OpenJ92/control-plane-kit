"""Named backend-swap wrapper over the generic graph-transition workflow."""

from __future__ import annotations

from control_plane_kit.core.topology.graph import DeploymentGraph
from examples.scenarios.workflow import (
    GraphTransitionPlanningResult,
    PlanningWorkflowServices,
    plan_graph_transition,
)

BackendSwapPlanningResult = GraphTransitionPlanningResult


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
    """Plan the familiar backend replacement through the generic workflow."""

    return plan_graph_transition(
        services,
        workspace_id=workspace_id,
        actor_id=actor_id,
        title="Replace API backend",
        approval_comment="Review the backend replacement plan before execution.",
        current_graph_id=current_graph_id,
        expected_desired_graph_id=expected_desired_graph_id,
        desired_graph=desired_graph,
        idempotency_prefix=idempotency_prefix,
    )
