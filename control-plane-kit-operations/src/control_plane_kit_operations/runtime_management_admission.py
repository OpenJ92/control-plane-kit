"""Distinct pure policies for management planning and execution admission."""

from dataclasses import replace

from control_plane_kit_core.lifecycle import (
    ResourceLifecycle,
    ResourceOwnership,
)
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.planning import (
    ActivityPlan,
    ManagementBootstrapStage,
    ObserveManagementBootstrap,
    StartRuntime,
    compile_activity_plan,
)
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
    InitialDeployment,
    TeardownDeployment,
    UpdateDeployment,
)
from control_plane_kit_operations.graph_authoring import (
    product_reference_in_node,
    product_references_in_graph,
)
from control_plane_kit_operations.plan_derivation import (
    PlanDerivationProfile,
    derive_activity_plan,
)
from control_plane_kit_operations.products import (
    RegisteredProduct,
    RegisteredProductStatus,
)


_GATEWAY_ARTIFACT_IDS = frozenset({
    "gateway-health-transit",
    "gateway-health-targets",
    "gateway-control",
})
_GATEWAY_TARGETS_PATH = "/etc/cpk/gateway/health-targets.json"


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


def runtime_management_plan_profile(
    transition: DeploymentTransition,
    *,
    registered_products: tuple[RegisteredProduct, ...] = (),
) -> PlanDerivationProfile | None:
    """Select the one derivation profile admitted for a graph transition.

    ``None`` is a refusal for a management update, not a legacy profile.
    Initial, teardown, no-op, and non-management updates retain their existing
    management graph-pair derivation.
    """

    if type(transition) is not UpdateDeployment or not any(
        _has_management_material(graph)
        for graph in (transition.current.graph, transition.desired.graph)
    ):
        return PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1
    candidate = derive_activity_plan(
        transition, profile=PlanDerivationProfile.MANAGED_UPDATE_V1,
    )
    if (
        candidate.ready_for_execution
        and _managed_update_shape(
            transition.current.graph,
            transition.desired.graph,
            registered_products=registered_products,
        )
        and not runtime_management_planning_is_unsupported(
            transition, registered_products=registered_products,
        )
    ):
        return PlanDerivationProfile.MANAGED_UPDATE_V1
    return None


def runtime_management_execution_is_unsupported(
    current: DeploymentGraph,
    desired: DeploymentGraph,
    plan: ActivityPlan | None = None,
    *,
    codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
    registered_products: tuple[RegisteredProduct, ...] = (),
    derivation_profile: PlanDerivationProfile | None = None,
) -> bool:
    """Support no-ops and exact recorded-profile fresh-owned or teardown shapes.

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
        transition = Deploy(validated_current, validated_desired)
        if derivation_profile is PlanDerivationProfile.MANAGED_UPDATE_V1:
            canonical_plan = derive_activity_plan(
                transition, profile=derivation_profile,
            )
            return (
                type(transition) is not UpdateDeployment
                or not canonical_plan.ready_for_execution
                or plan != canonical_plan
                or not _managed_update_shape(
                    current,
                    desired,
                    registered_products=registered_products,
                )
            )
        if derivation_profile is not PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1:
            return True
        if type(transition) not in (InitialDeployment, TeardownDeployment):
            return True
        canonical_plan = derive_activity_plan(transition, profile=derivation_profile)
        return (
            not canonical_plan.ready_for_execution
            or plan != canonical_plan
            or (
                type(transition) is InitialDeployment
                and not _fresh_owned_shape(desired, canonical_plan)
            )
            or runtime_management_planning_is_unsupported(
                transition, registered_products=registered_products,
            )
        )
    canonical_plan = compile_activity_plan(
        diff_graphs(validated_current, validated_desired)
    )
    return bool(canonical_plan.activities)


def _fresh_owned_shape(desired: DeploymentGraph, plan: ActivityPlan) -> bool:
    # InitialDeployment alone includes attached/external runtimes. Every managed
    # relation must have the compiler's owned-start/ingress-bootstrap branch.
    if any(
        value.lifecycle.ownership is not ResourceOwnership.OWNED
        for value in (*desired.runtimes.values(), *desired.nodes.values())
    ):
        return False
    starts = {
        value.operation.target.runtime_id for value in plan.activities
        if type(value.operation) is StartRuntime
    }
    managed = {
        runtime_id for runtime_id, runtime in desired.runtimes.items()
        if runtime.management is not None
    }
    if not managed or not managed <= starts:
        return False
    expected = {
        ManagementBootstrapStage.CONNECTOR_CONNECTED,
        ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH,
        ManagementBootstrapStage.GATEWAY_INGRESS_READY,
    }
    return all(
        {
            value.operation.stage for value in plan.activities
            if type(value.operation) is ObserveManagementBootstrap
            and value.operation.target.runtime_id == runtime_id
        } == expected
        for runtime_id in managed
    )


def _has_management_material(graph: DeploymentGraph) -> bool:
    return any(runtime.management is not None for runtime in graph.runtimes.values()) or any(
        node.block_spec.gateway_transit is not None or node.block_spec.control_surfaces
        for node in graph.nodes.values()
    )


def _managed_update_shape(
    current: DeploymentGraph,
    desired: DeploymentGraph,
    *,
    registered_products: tuple[RegisteredProduct, ...],
) -> bool:
    """Recognize only the selected A/B/C product and artifact family.

    Core owns the generic graph delta and operation ordering. This Operations
    policy binds that algebra to active registered products and the one named
    configuration slot that the application selected for managed routing.
    """

    if set(current.runtimes) != set(desired.runtimes) or len(current.runtimes) != 1:
        return False
    runtime_id = next(iter(current.runtimes))
    before_runtime = current.runtimes[runtime_id]
    after_runtime = desired.runtimes[runtime_id]
    if (
        before_runtime.management is None
        or before_runtime.management != after_runtime.management
        or replace(before_runtime, children=()) != replace(after_runtime, children=())
    ):
        return False
    gateway_id = before_runtime.management.gateway_node_id
    if gateway_id not in current.nodes or gateway_id not in desired.nodes:
        return False

    before_ids = set(current.nodes)
    after_ids = set(desired.nodes)
    introduced = after_ids - before_ids
    removed = before_ids - after_ids
    if (len(introduced), len(removed)) not in ((1, 0), (0, 1)):
        return False
    changed_id = next(iter(introduced or removed))
    before_gateway = current.nodes[gateway_id]
    after_gateway = desired.nodes[gateway_id]
    if replace(before_gateway, configuration_artifacts=()) != replace(
        after_gateway, configuration_artifacts=(),
    ):
        return False
    if not _gateway_artifact_change_is_exact(before_gateway, after_gateway):
        return False

    products = {}
    try:
        for graph in (current, desired):
            for node in graph.nodes.values():
                product = _selected_product(node, registered_products)
                if product is None or not _node_matches_product(node, product):
                    return False
                products[(graph is desired, node.node_id)] = product
    except (TypeError, ValueError):
        return False
    if any(
        node.runtime_authority_deliveries
        for graph in (current, desired)
        for node in graph.nodes.values()
    ):
        return False

    before_workloads = {
        node.node_id for node in current.nodes.values()
        if node.node_id != gateway_id and _has_readiness(node)
    }
    after_workloads = {
        node.node_id for node in desired.nodes.values()
        if node.node_id != gateway_id and _has_readiness(node)
    }
    if (
        len(before_workloads & after_workloads) != 1
        or before_workloads ^ after_workloads != {changed_id}
        or len(before_workloads | after_workloads) != 2
    ):
        return False
    retained_id = next(iter(before_workloads & after_workloads))
    changed_node = (desired if introduced else current).nodes[changed_id]
    if changed_node.lifecycle != ResourceLifecycle.owned_ephemeral():
        return False
    retained_product = products[(False, retained_id)]
    changed_product = products[(bool(introduced), changed_id)]
    if retained_product.reference != changed_product.reference:
        return False
    return products[(False, gateway_id)].reference == products[(True, gateway_id)].reference


def _gateway_artifact_change_is_exact(before, after) -> bool:
    before_artifacts = {value.artifact_id: value for value in before.configuration_artifacts}
    after_artifacts = {value.artifact_id: value for value in after.configuration_artifacts}
    if set(before_artifacts) != _GATEWAY_ARTIFACT_IDS or set(after_artifacts) != _GATEWAY_ARTIFACT_IDS:
        return False
    target_before = before_artifacts["gateway-health-targets"]
    target_after = after_artifacts["gateway-health-targets"]
    if (
        target_before.target_path != _GATEWAY_TARGETS_PATH
        or target_after.target_path != _GATEWAY_TARGETS_PATH
        or target_before == target_after
    ):
        return False
    return all(
        before_artifacts[artifact_id] == after_artifacts[artifact_id]
        for artifact_id in _GATEWAY_ARTIFACT_IDS - {"gateway-health-targets"}
    )


def _selected_product(node, registered_products):
    reference = product_reference_in_node(node)
    matches = tuple(
        product for product in registered_products
        if product.reference == reference
        and product.status is RegisteredProductStatus.ACTIVE
    )
    return matches[0] if reference is not None and len(matches) == 1 else None


def _node_matches_product(node, product: RegisteredProduct) -> bool:
    contract = product.descriptor_document.product.runtime_contract
    return (
        node.sockets == contract.sockets
        and node.public_environment == contract.public_environment
        and node.lifecycle == contract.lifecycle
        and set(node.block_spec.capabilities) == set(contract.capabilities)
        and node.block_spec.verification == contract.verification
        and node.block_spec.control_surfaces == contract.control_surfaces
        and node.block_spec.gateway_transit == contract.gateway_transit
        and _configuration_slots(node.configuration_artifacts)
            == _configuration_slots(contract.configuration_artifacts)
        and node.secret_deliveries == contract.secret_deliveries
    )


def _configuration_slots(artifacts):
    return tuple(sorted(
        (value.artifact_id, value.target_path, value.media_type, value.file_mode)
        for value in artifacts
    ))


def _has_readiness(node) -> bool:
    return any(
        NodeHealthReadKind.READINESS in surface.health_reads
        for surface in node.block_spec.control_surfaces
    )
