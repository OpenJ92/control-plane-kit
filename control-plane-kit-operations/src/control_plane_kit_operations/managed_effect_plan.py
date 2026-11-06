"""Read the original accepted management plan; this grants no new authority."""

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.planning import resolve_management_observation
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from control_plane_kit_operations.records import ActivityPlanStatus, ApprovalDecisionKind
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
from control_plane_kit_operations.runtime_management_targets import (
    is_native_connection_operation, is_signed_management_health_operation,
)


class ManagedEffectPlanConflict(ValueError):
    """The management intent does not match the accepted durable plan."""


def require_managed_effect_plan(stores, intent, request):
    """Return the graph-pinned runtime, without current policy, locks or writes."""
    runtime = None
    try:
        plan = stores.activity_history.get_plan(request.identity.plan_id)
        source = intent.source
        if (plan.derivation_profile not in (
                    PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1,
                    PlanDerivationProfile.MANAGED_UPDATE_V1,
                    PlanDerivationProfile.RETAINED_IMAGE_INGRESS_V1,
                )
                or plan.status is not ActivityPlanStatus.PLANNED
                or plan.session_id != request.identity.session_id
                or source.plan_id != plan.plan_id
                or source.base_graph_id != plan.base_graph_id
                or source.desired_graph_id != plan.desired_graph_id
                or not (is_native_connection_operation(intent.operation)
                    or is_signed_management_health_operation(intent.operation))):
            raise ValueError
        graphs = []
        for projection_id, authored_id in ((plan.base_realized_projection_id, plan.base_graph_id),
                (plan.desired_realized_projection_id, plan.desired_graph_id)):
            projection = stores.realized_graphs.get(projection_id)
            authored = stores.graphs.get(authored_id)
            if (projection.workspace_id != request.identity.workspace_id
                    or projection.projection_id != projection_id
                    or projection.source_authored_graph_id != authored_id
                    or authored.workspace_id != request.identity.workspace_id
                    or authored.graph_id != authored_id):
                raise ValueError
            graph = validate_graph(DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor))
            graph.require_valid()
            graphs.append(graph)
        if runtime_management_execution_is_unsupported(graphs[0].graph, graphs[1].graph,
                plan.plan,
                registered_products=stores.registered_products.list_active(request.identity.workspace_id),
                derivation_profile=plan.derivation_profile):
            raise ValueError
        approval = stores.activity_history.get_approval_request(request.approval_request_id)
        decision = stores.activity_history.approval_decision_for_request(approval.request_id)
        requirement = ApprovalPolicy().requirement_for(plan.plan)
        if (type(approval.subject) is not ActivityPlanApprovalSubject
                or approval.request_id != request.approval_request_id
                or approval.session_id != request.identity.session_id
                or approval.subject.plan_id != plan.plan_id
                or decision.decision_id != request.approval_decision_id
                or decision.request_id != approval.request_id
                or decision.decision is not ApprovalDecisionKind.APPROVED
                or decision.scope is not approval.required_scope
                or (approval.required_scope, approval.destructive, approval.max_risk)
                    != (requirement.required_scope, requirement.destructive, requirement.max_risk)):
            raise ValueError
        resolved = resolve_management_observation(intent.operation, *graphs,
            expected_operation=plan.plan.activity(intent.activity_id).operation)
        selected = resolved.selected_graph.graph.runtimes[intent.operation.target.runtime_id]
        if selected.authority_ref == intent.authority_ref and selected.kind is intent.runtime_kind:
            runtime = selected
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    if runtime is None:
        raise ManagedEffectPlanConflict("management effect does not match pinned plan")
    return runtime
