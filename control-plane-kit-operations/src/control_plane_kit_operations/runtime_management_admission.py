"""Distinct pure policies for management planning and execution admission."""

from control_plane_kit_core.planning import ActivityPlan, compile_activity_plan
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    GraphDescriptorCodec,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_operations.deployment_transitions import (
    Deploy,
    DeploymentTransition,
    TeardownDeployment,
)
from control_plane_kit_operations.graph_authoring import (
    product_reference_in_node,
    product_references_in_graph,
)
from control_plane_kit_operations.plan_derivation import (
    PlanDerivationProfile,
    derive_activity_plan,
)
from control_plane_kit_operations.products import RegisteredProduct


def runtime_management_planning_is_unsupported(
    transition: DeploymentTransition,
    *,
    registered_products: tuple[RegisteredProduct, ...] = (),
) -> bool:
    """Require faithful management declarations and same-side runtime selection.

    Complete selected pairs may produce ready or review-blocked Core plans.
    Admission here grants no authority to execute those plans.
    """

    # Normalize every node before considering canonical emptiness, even when the
    # catalog is empty. Keep candidate-bearing parser errors inside this call.
    try:
        selected_nodes = tuple(
            (graph, node, product_reference_in_node(node))
            for graph in (transition.current.graph, transition.desired.graph)
            for node in graph.nodes.values()
        )
    except ValueError:
        return True
    canonical_plan = derive_activity_plan(
        transition, profile=PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1,
    )
    if not canonical_plan.activities:
        return False
    for graph, node, reference in selected_nodes:
        declared = (node.block_spec.gateway_transit, node.block_spec.control_surfaces)
        for product in registered_products:
            if product.reference != reference:
                continue
            contract = product.descriptor_document.product.runtime_contract
            if declared != (contract.gateway_transit, contract.control_surfaces):
                return True
        if (declared[0] is not None or declared[1]) and (
            graph.runtimes[node.runtime_id].management is None
        ):
            return True
    return False


def runtime_management_execution_is_unsupported(
    current: DeploymentGraph,
    desired: DeploymentGraph,
    plan: ActivityPlan | None = None,
    *,
    codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
    registered_products: tuple[RegisteredProduct, ...] = (),
    derivation_profile: PlanDerivationProfile | None = None,
) -> bool:
    """Support proven no-ops and complete, recorded-profile managed teardown.

    Shape support grants no execution authority. A direct activity must belong
    to the supplied pinned plan; callers with no such plan receive no exception.
    """

    # Parse independently of catalog contents. A malformed selected reference
    # cannot acquire a no-op exemption or leak its parser error into a receipt.
    try:
        references = set(product_references_in_graph(current)) | set(
            product_references_in_graph(desired)
        )
    except ValueError:
        return True
    registered_management = any(
        product.reference in references
        and (
            product.descriptor_document.product.runtime_contract.gateway_transit is not None
            or product.descriptor_document.product.runtime_contract.control_surfaces
        )
        for product in registered_products
    )
    if not registered_management and not any(
        _has_management_material(graph) for graph in (current, desired)
    ):
        return False
    if plan is None:
        return True
    validated_current = validate_graph(current, codec=codec)
    validated_desired = validate_graph(desired, codec=codec)
    if not validated_current.valid or not validated_desired.valid:
        return True
    if plan.activities:
        if derivation_profile is not PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1:
            return True
        transition = Deploy(validated_current, validated_desired)
        if type(transition) is not TeardownDeployment:
            return True
        canonical_plan = derive_activity_plan(transition, profile=derivation_profile)
        return (
            not canonical_plan.ready_for_execution
            or plan != canonical_plan
            or runtime_management_planning_is_unsupported(
                transition, registered_products=registered_products,
            )
        )
    canonical_plan = compile_activity_plan(
        diff_graphs(validated_current, validated_desired)
    )
    return bool(canonical_plan.activities)


def _has_management_material(graph: DeploymentGraph) -> bool:
    return any(runtime.management is not None for runtime in graph.runtimes.values()) or any(
        node.block_spec.gateway_transit is not None or node.block_spec.control_surfaces
        for node in graph.nodes.values()
    )
