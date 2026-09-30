"""Retained rotation review and child associations; no transition authority."""

from control_plane_kit_core.approval_subjects import GatewayKeyRotationApprovalSubject
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import LifecycleOperationKind
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.records import ApprovalDecisionKind, RealizedGraphProjectionKind


def _require(condition):
    if not condition:
        raise ValueError("gateway child association is unavailable")


def _rotation_review_subject(rotation):
    return GatewayKeyRotationApprovalSubject(
        rotation_id=rotation.rotation_id, workspace_id=rotation.workspace_id,
        gateway_node_id=rotation.gateway_node_id, purpose=rotation.purpose,
        issuer=rotation.issuer, old_key_id=rotation.old_key_id,
        maximum_grant_lifetime_seconds=rotation.maximum_grant_lifetime_seconds,
        clock_skew_seconds=rotation.clock_skew_seconds,
        rotation_intent_digest=rotation.intent_fingerprint)


def _require_rotation_approval_action(history, approval, subject):
    _require(approval.idempotency_key is not None)
    action = history.action_for_idempotency(approval.session_id, approval.idempotency_key)
    _require(action is not None and action.action_type is OperatorCommandKind.REQUEST_APPROVAL
        and action.payload.get("request_id") == approval.request_id
        and action.payload.get("subject_kind") == subject.kind.value
        and action.payload.get("subject_id") == subject.subject_id
        and action.payload.get("review_digest") == subject.review_digest
        and action.payload.get("required_scope") == approval.required_scope.value
        and action.payload.get("max_risk") == approval.max_risk.value
        and action.payload.get("destructive") is approval.destructive)


def _require_rotation_approval(history, rotation, approval, decision):
    subject = approval.subject
    _require(type(subject) is GatewayKeyRotationApprovalSubject
        and subject == _rotation_review_subject(rotation)
        and rotation.approval_request_id == approval.request_id
        and rotation.approval_decision_id == decision.decision_id
        and approval.required_scope is PolicyScope.DELEGATION_KEY_ROTATE_APPROVE
        and approval.max_risk is RiskLevel.HIGH and approval.destructive is True
        and decision.request_id == approval.request_id
        and decision.decision is ApprovalDecisionKind.APPROVED
        and decision.scope is PolicyScope.DELEGATION_KEY_ROTATE_APPROVE
        and history.get_session(approval.session_id).workspace_id == rotation.workspace_id)
    _require_rotation_approval_action(history, approval, subject)


def _rotation_publication_version(history, rotation, plan):
    actions = tuple(action for action in history.actions_for_session(plan.session_id)
        if action.action_type is OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION
        and action.payload.get("desired_realized_projection_id") == plan.desired_realized_projection_id)
    _require(len(actions) == 1)
    evidence = actions[0].payload
    version = evidence.get("source_operation_version")
    _require(evidence.get("workspace_id") == rotation.workspace_id
        and evidence.get("authored_graph_id") == plan.base_graph_id
        and evidence.get("previous_realized_projection_id") == plan.base_realized_projection_id
        and evidence.get("desired_graph_revision") == plan.desired_graph_revision
        and evidence.get("source_operation_id") == rotation.rotation_id
        and type(version) is int and version > 0)
    return version


def _require_retained_gateway_child(stores, request, approval, decision):
    """Read an already admitted association, without locking/progress policy."""
    rotation = stores.gateway_key_rotations.get(approval.subject.rotation_id)
    history = stores.activity_history
    _require_rotation_approval(history, rotation, approval, decision)
    plan = history.get_plan(request.identity.plan_id)
    base = stores.realized_graphs.get(plan.base_realized_projection_id)
    desired = stores.realized_graphs.get(plan.desired_realized_projection_id)
    _require(rotation.workspace_id == request.identity.workspace_id
        and plan.session_id == request.identity.session_id
        and plan.base_graph_id == plan.desired_graph_id
        and base.workspace_id == desired.workspace_id == rotation.workspace_id
        and base.source_authored_graph_id == desired.source_authored_graph_id == plan.base_graph_id
        and desired.projection_kind is RealizedGraphProjectionKind.DELEGATION_VERIFIER)
    phases = tuple(phase for phase in ("overlap", "retirement")
        if desired.projection_id == f"gateway-rotation-{rotation.rotation_id}-{phase}"
        and desired.projection_key == f"gateway-rotation:{rotation.rotation_id}:{phase}")
    _require(len(phases) == 1)
    action = history.action_for_idempotency(request.identity.session_id, request.idempotency.key)
    expected = dict(execution_request_id=request.identity.request_id, plan_id=plan.plan_id,
        approval_request_id=approval.request_id, approval_decision_id=decision.decision_id,
        base_graph_id=plan.base_graph_id, desired_graph_id=plan.desired_graph_id,
        base_realized_projection_id=plan.base_realized_projection_id,
        desired_realized_projection_id=plan.desired_realized_projection_id,
        desired_graph_revision=plan.desired_graph_revision)
    _require(action is not None and action.action_type is LifecycleOperationKind.ADMIT_EXECUTION
        and action.intent_fingerprint == request.idempotency.intent_fingerprint
        and all(action.payload.get(key) == value for key, value in expected.items()))
    _rotation_publication_version(history, rotation, plan)
    checkpoint = getattr(rotation, phases[0] + "_deployment")
    if checkpoint is not None:
        _require(checkpoint.phase.value == phases[0]
            and checkpoint.session_id == request.identity.session_id
            and checkpoint.plan_id == plan.plan_id
            and checkpoint.execution_request_id == request.identity.request_id
            and checkpoint.approval_request_id == approval.request_id
            and checkpoint.approval_decision_id == decision.decision_id
            and checkpoint.base_authored_graph_id == plan.base_graph_id
            and checkpoint.desired_authored_graph_id == plan.desired_graph_id
            and checkpoint.base_realized_projection_id == plan.base_realized_projection_id
            and checkpoint.desired_realized_projection_id == plan.desired_realized_projection_id
            and checkpoint.desired_revision == plan.desired_graph_revision)
