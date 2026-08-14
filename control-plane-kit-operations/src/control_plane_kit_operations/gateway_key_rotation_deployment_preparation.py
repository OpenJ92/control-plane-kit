"""Shared mechanical preparation of one approved key-rotation child run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_operations.admission import RequestPlanExecution
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
)
from control_plane_kit_operations.lifecycle import (
    ClaimAndOpenActivityRun,
    StartActivityRun,
)
from control_plane_kit_operations.planning import RequestActivityPlan
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    StartOperationSession,
)


@dataclass(frozen=True)
class GatewayKeyRotationPreparedChild:
    """Started ordinary run plus immutable phase checkpoint."""

    checkpoint: GatewayKeyRotationDeploymentCheckpoint


def prepare_gateway_key_rotation_child(
    *,
    rotation: GatewayKeyRotation,
    command: Any,
    phase: GatewayKeyRotationDeploymentPhase,
    prefix: str,
    operations: Any,
    projections: Any,
    projection_command: Callable[[str], Any],
    planning: Any,
    admission: Any,
    lifecycle: Any,
) -> GatewayKeyRotationPreparedChild:
    """Compose canonical existing services; perform no runtime effect."""

    session = operations.execute(
        StartOperationSession(
            workspace_id=rotation.workspace_id,
            actor_id=command.actor_id,
            title=f"Deploy gateway key rotation {phase.value}",
            idempotency_key=IdempotencyKey(f"{prefix}:session"),
            metadata={
                "rotation_id": rotation.rotation_id,
                "deployment_phase": phase.value,
            },
        )
    )
    publication = projections.execute(
        projection_command(session.session.session_id)
    ).publication
    plan = planning.execute(
        RequestActivityPlan(
            session_id=session.session.session_id,
            workspace_id=rotation.workspace_id,
            actor_id=command.actor_id,
            expected_current_graph_id=publication.authored_graph_id,
            expected_desired_graph_id=publication.authored_graph_id,
            expected_current_realized_projection_id=(
                publication.previous_realized_projection_id
            ),
            expected_desired_realized_projection_id=(
                publication.desired_realized_projection_id
            ),
            expected_desired_graph_revision=publication.desired_graph_revision,
            idempotency_key=IdempotencyKey(f"{prefix}:plan"),
        )
    )
    admitted = admission.execute(
        RequestPlanExecution(
            workspace_id=rotation.workspace_id,
            session_id=session.session.session_id,
            plan_id=plan.plan_record.plan_id,
            approval_request_id=rotation.approval_request_id,
            actor_id=command.actor_id,
            actor_scopes=command.actor_scopes,
            idempotency_key=IdempotencyKey(f"{prefix}:admission"),
        )
    )
    claim = lifecycle.execute(
        ClaimAndOpenActivityRun(
            request_id=admitted.request.identity.request_id,
            authority=command.worker_authority,
            lease_duration=command.lease_duration,
            idempotency_key=IdempotencyKey(f"{prefix}:claim"),
        )
    )
    if claim.request.claim is None:
        raise ValueError("claimed rotation child lacks lease evidence")
    started = lifecycle.execute(
        StartActivityRun(
            run_id=claim.run.run_id,
            authority=command.worker_authority,
            fence=claim.request.claim.fence,
            idempotency_key=IdempotencyKey(f"{prefix}:start"),
        )
    )
    decision_id = admitted.request.approval_decision_id
    if not isinstance(decision_id, str):
        raise ValueError("admitted rotation child lacks approval decision evidence")
    return GatewayKeyRotationPreparedChild(
        GatewayKeyRotationDeploymentCheckpoint(
            phase=phase,
            status=GatewayKeyRotationDeploymentStatus.PREPARED,
            session_id=session.session.session_id,
            plan_id=plan.plan_record.plan_id,
            approval_request_id=admitted.request.approval_request_id,
            approval_decision_id=decision_id,
            execution_request_id=admitted.request.identity.request_id,
            run_id=started.run.run_id,
            base_authored_graph_id=plan.plan_record.base_graph_id,
            base_realized_projection_id=(
                plan.plan_record.base_realized_projection_id
            ),
            desired_authored_graph_id=plan.plan_record.desired_graph_id,
            desired_realized_projection_id=(
                plan.plan_record.desired_realized_projection_id
            ),
            desired_revision=plan.plan_record.desired_graph_revision,
            prepared_at=started.event.occurred_at,
        )
    )


__all__ = [
    "GatewayKeyRotationPreparedChild",
    "prepare_gateway_key_rotation_child",
]
