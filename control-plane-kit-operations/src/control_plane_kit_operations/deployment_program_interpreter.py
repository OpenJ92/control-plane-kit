"""Application program for durable, effect-free deployment preparation."""

from __future__ import annotations

import hashlib
import json

from control_plane_kit_core.policies import ApprovalPolicy, InstanceAccessPolicy
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    GraphValidationError,
    validate_graph,
)
from control_plane_kit_operations.approvals import (
    ApprovalAuthorizationDenied,
    ApprovalCommandService,
    ApprovalWorkflowError,
    RequestApproval,
)
from control_plane_kit_operations.deployment_program import (
    DeploymentProgramReference,
    PrepareDeploymentProgram,
)
from control_plane_kit_operations.deployment_program_projections import (
    DeploymentApprovalRequired,
    DeploymentNoChanges,
    DeploymentReviewBlocked,
)
from control_plane_kit_operations.deployment_transitions import NoOpDeployment
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    ActivityPlanningError,
    DesiredGraphCommandError,
    DesiredGraphCommandService,
    RequestActivityPlan,
    SetDesiredGraph,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandError,
    OperationCommandService,
    StartOperationSession,
)


class DeploymentProgramError(RuntimeError):
    """Base error for deployment-program interpretation."""


class DeploymentProgramAuthorizationDenied(DeploymentProgramError):
    """Raised when current authority cannot prepare the deployment."""


class DeploymentProgramStateConflict(DeploymentProgramError):
    """Raised when durable state cannot accept deployment preparation."""


class DeploymentProgram:
    """Compose existing command services into one resumable preparation program."""

    def __init__(
        self,
        operations: OperationCommandService,
        desired_graphs: DesiredGraphCommandService,
        activity_planning: ActivityPlanningCommandService,
        approvals: ApprovalCommandService,
    ) -> None:
        self._operations = operations
        self._desired_graphs = desired_graphs
        self._activity_planning = activity_planning
        self._approvals = approvals

    def prepare(self, command: PrepareDeploymentProgram):
        _authorize(command)
        _validate_desired(command)
        keys = _child_keys(command)
        session_result = _execute_state(
            self._operations,
            StartOperationSession(
                workspace_id=command.context.workspace_id,
                actor_id=command.context.actor_id,
                title=command.title,
                idempotency_key=keys["session"],
                metadata={
                    "deployment_prepare_intent_sha256": _intent_digest(command)
                },
            ),
            OperationCommandError,
        )
        session_id = session_result.session.session_id
        expected_desired = command.expected_desired
        desired_result = _execute_state(
            self._desired_graphs,
            SetDesiredGraph(
                session_id=session_id,
                workspace_id=command.context.workspace_id,
                actor_id=command.context.actor_id,
                graph=command.desired,
                expected_desired_graph_id=(
                    None
                    if expected_desired is None
                    else expected_desired.authored_graph_id
                ),
                expected_desired_realized_projection_id=(
                    None
                    if expected_desired is None
                    else expected_desired.realized_projection_id
                ),
                expected_desired_graph_revision=(
                    command.expected_desired_graph_revision
                ),
                idempotency_key=keys["desired"],
            ),
            DesiredGraphCommandError,
        )
        planning_result = _execute_state(
            self._activity_planning,
            RequestActivityPlan(
                session_id=session_id,
                workspace_id=command.context.workspace_id,
                actor_id=command.context.actor_id,
                expected_current_graph_id=(
                    command.expected_current.authored_graph_id
                ),
                expected_current_realized_projection_id=(
                    command.expected_current.realized_projection_id
                ),
                expected_desired_graph_id=desired_result.graph_version_id,
                expected_desired_realized_projection_id=(
                    desired_result.desired_realized_projection_id
                ),
                expected_desired_graph_revision=(
                    desired_result.desired_graph_revision
                ),
                idempotency_key=keys["plan"],
            ),
            ActivityPlanningError,
        )
        reference = DeploymentProgramReference(
            command.context.workspace_id,
            planning_result.plan_record.plan_id,
        )
        if isinstance(planning_result.transition, NoOpDeployment):
            return DeploymentNoChanges(reference)
        if not planning_result.plan_record.plan.ready_for_execution:
            return DeploymentReviewBlocked(reference)
        approval_result = _execute_approval(
            self._approvals,
            RequestApproval(
                session_id=session_id,
                plan_id=planning_result.plan_record.plan_id,
                actor_id=command.context.actor_id,
                actor_scopes=command.context.granted_scopes,
                idempotency_key=keys["approval"],
                comment=command.approval_comment,
            ),
        )
        return DeploymentApprovalRequired(
            reference,
            approval_result.request.request_id,
        )


def _authorize(command: PrepareDeploymentProgram) -> None:
    scopes = command.context.granted_scopes
    workspace = InstanceAccessPolicy().can_edit_workspace(scopes)
    planning = ApprovalPolicy().can_request_plan(scopes)
    if not workspace.allowed or not planning.allowed:
        raise DeploymentProgramAuthorizationDenied(
            "deployment preparation is not authorized"
        )


def _validate_desired(command: PrepareDeploymentProgram) -> None:
    invalid = False
    try:
        validate_graph(command.desired).require_valid()
    except GraphValidationError:
        invalid = True
    if invalid:
        raise DeploymentProgramStateConflict(
            "deployment preparation state is unavailable"
        )


def _execute_state(service, command, expected_error):
    failed = False
    try:
        result = service.execute(command)
    except InvalidOperationCommand:
        raise
    except expected_error:
        failed = True
    if failed:
        raise DeploymentProgramStateConflict(
            "deployment preparation state is unavailable"
        )
    return result


def _execute_approval(service: ApprovalCommandService, command: RequestApproval):
    denied = False
    failed = False
    try:
        result = service.execute(command)
    except ApprovalAuthorizationDenied:
        denied = True
    except ApprovalWorkflowError:
        failed = True
    if denied:
        raise DeploymentProgramAuthorizationDenied(
            "deployment preparation is not authorized"
        )
    if failed:
        raise DeploymentProgramStateConflict(
            "deployment preparation state is unavailable"
        )
    return result


def _intent_digest(command: PrepareDeploymentProgram) -> str:
    return _sha256(
        {
            "profile": "deployment-program-prepare.v1",
            "workspace_id": command.context.workspace_id,
            "actor_id": command.context.actor_id,
            "desired": DEFAULT_GRAPH_CODEC.encode(command.desired),
            "expected_current": _lineage(command.expected_current),
            "expected_desired": (
                None
                if command.expected_desired is None
                else _lineage(command.expected_desired)
            ),
            "expected_desired_graph_revision": (
                command.expected_desired_graph_revision
            ),
            "title": command.title,
            "approval_comment": command.approval_comment,
        }
    )


def _child_keys(command: PrepareDeploymentProgram) -> dict[str, IdempotencyKey]:
    return {
        stage: IdempotencyKey(
            f"deployment-prepare.v1:{stage}:"
            + _sha256(
                {
                    "profile": "deployment-program-prepare-child.v1",
                    "stage": stage,
                    "workspace_id": command.context.workspace_id,
                    "parent_idempotency_key": command.idempotency_key.value,
                }
            )
        )
        for stage in ("session", "desired", "plan", "approval")
    }


def _lineage(value) -> dict[str, str]:
    return {
        "authored_graph_id": value.authored_graph_id,
        "realized_projection_id": value.realized_projection_id,
    }


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
