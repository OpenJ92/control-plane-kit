"""Retained rotation review and child associations; no transition authority."""

from control_plane_kit_core.approval_subjects import GatewayKeyRotationApprovalSubject
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import LifecycleOperationKind
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.records import ApprovalDecisionKind, OperationActionRecord, RealizedGraphProjectionKind


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
        and action.session_id == approval.session_id
        and approval.intent_fingerprint is not None
        and action.intent_fingerprint == approval.intent_fingerprint
        and action.payload.get("request_id") == approval.request_id
        and action.payload.get("subject_kind") == subject.kind.value
        and action.payload.get("subject_id") == subject.subject_id
        and action.payload.get("review_digest") == subject.review_digest
        and action.payload.get("required_scope") == approval.required_scope.value
        and action.payload.get("max_risk") == approval.max_risk.value
        and action.payload.get("destructive") is approval.destructive)


def _require_rotation_review_approval(history, approval, decision):
    subject = approval.subject
    _require(type(subject) is GatewayKeyRotationApprovalSubject
        and approval.required_scope is PolicyScope.DELEGATION_KEY_ROTATE_APPROVE
        and approval.max_risk is RiskLevel.HIGH and approval.destructive is True
        and decision.request_id == approval.request_id
        and decision.decision is ApprovalDecisionKind.APPROVED
        and decision.scope is PolicyScope.DELEGATION_KEY_ROTATE_APPROVE
        and history.get_session(approval.session_id).workspace_id == subject.workspace_id)
    _require_rotation_approval_action(history, approval, subject)
    return subject


def _require_rotation_approval(history, rotation, approval, decision):
    subject = _require_rotation_review_approval(history, approval, decision)
    _require(subject == _rotation_review_subject(rotation)
        and rotation.approval_request_id == approval.request_id
        and rotation.approval_decision_id == decision.decision_id)


def _rotation_publication_version(history, rotation, plan):
    _require(history.get_session(plan.session_id).workspace_id == rotation.workspace_id)
    actions = history._projection_publication_actions(
        plan.session_id, plan.desired_realized_projection_id)
    _require(len(actions) == 1)
    _require(type(actions[0]) is OperationActionRecord
        and actions[0].session_id == plan.session_id
        and actions[0].action_type is OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION
        and actions[0].payload.get("desired_realized_projection_id") == plan.desired_realized_projection_id)
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
    """Prove original admission; later rotation checkpoints are not authority."""
    history = stores.activity_history
    subject = _require_rotation_review_approval(history, approval, decision)
    _require(approval.request_id == request.approval_request_id
        and decision.decision_id == request.approval_decision_id)
    plan = history.get_plan(request.identity.plan_id)
    base = stores.realized_graphs.get(plan.base_realized_projection_id)
    desired = stores.realized_graphs.get(plan.desired_realized_projection_id)
    _require(subject.workspace_id == request.identity.workspace_id
        and history.get_session(request.identity.session_id).workspace_id == subject.workspace_id
        and plan.plan_id == request.identity.plan_id
        and plan.session_id == request.identity.session_id
        and plan.base_graph_id == plan.desired_graph_id
        and base.workspace_id == desired.workspace_id == subject.workspace_id
        and base.source_authored_graph_id == desired.source_authored_graph_id == plan.base_graph_id
        and desired.projection_kind is RealizedGraphProjectionKind.DELEGATION_VERIFIER)
    phases = tuple(phase for phase in ("overlap", "retirement")
        if desired.projection_id == f"gateway-rotation-{subject.rotation_id}-{phase}"
        and desired.projection_key == f"gateway-rotation:{subject.rotation_id}:{phase}")
    _require(len(phases) == 1)
    action = history.action_for_idempotency(request.identity.session_id, request.idempotency.key)
    expected = dict(execution_request_id=request.identity.request_id, plan_id=plan.plan_id,
        approval_request_id=approval.request_id, approval_decision_id=decision.decision_id,
        base_graph_id=plan.base_graph_id, desired_graph_id=plan.desired_graph_id,
        base_realized_projection_id=plan.base_realized_projection_id,
        desired_realized_projection_id=plan.desired_realized_projection_id,
        desired_graph_revision=plan.desired_graph_revision)
    _require(action is not None and action.action_type is LifecycleOperationKind.ADMIT_EXECUTION
        and action.session_id == request.identity.session_id
        and action.intent_fingerprint == request.idempotency.intent_fingerprint
        and all(action.payload.get(key) == value for key, value in expected.items()))
    _rotation_publication_version(history, subject, plan)
