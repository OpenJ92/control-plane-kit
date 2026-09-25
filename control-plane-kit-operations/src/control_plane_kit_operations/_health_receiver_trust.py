"""Shared pinned selection and coverage; store reads are outside pure catches."""
from dataclasses import replace

from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget, workload_node_control_audience
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.planning import PlanGraphSide
from control_plane_kit_core.products import ProductReference
from control_plane_kit_operations.graph_authoring import product_reference_in_node
from control_plane_kit_operations.health_receiver_trust import (
    GatewayHealthReceiverTrust, HealthReceiverDecoders, HealthReceiverSelection, HealthReceiverTrustError,
    WorkloadHealthReceiverTrust, _INPUT_ERRORS, _PURPOSES, _require, _same,
)
from control_plane_kit_operations.products import RegisteredProduct


def _checked(call, refuse, errors=_INPUT_ERRORS):
    try:
        return call()
    except errors:
        failure = refuse()
    raise failure


def _context(plan, graphs, selected, workspace):
    operation = selected.operation
    side = operation.target.graph_side
    _require(type(side) is PlanGraphSide)
    base = side is PlanGraphSide.BASE_GRAPH
    graph = graphs[0 if base else 1].graph
    authored = plan.base_graph_id if base else plan.desired_graph_id
    projection = plan.base_realized_projection_id if base else plan.desired_realized_projection_id
    def ref(role, value):
        return NodeControlGraphReference(role, value)
    target = NodeControlTarget(ref(NodeControlGraphReferenceRole.WORKSPACE, workspace),
        ref(NodeControlGraphReferenceRole.GRAPH_REVISION, authored),
        ref(NodeControlGraphReferenceRole.NODE, selected.target_node_id),
        ref(NodeControlGraphReferenceRole.PROVIDER_SOCKET, selected.target_provider_socket_name))
    runtime = ref(NodeControlGraphReferenceRole.RUNTIME, operation.target.runtime_id)
    gateway = ref(NodeControlGraphReferenceRole.NODE, selected.gateway_node_id)
    declaration = WorkloadNodeControlSurfaceDeclaration(selected.target_surface,
        WorkloadNodeControlSurfaceDeclarationProfile.V2)
    return graph, authored, projection, side, target, runtime, gateway, declaration


def _node_reference(graph, node_id, runtime_id, socket):
    node = graph.node(node_id)
    _require(node.node_id == node_id and node.runtime_id == runtime_id
             and any(provider.name == socket for provider in node.sockets.providers))
    reference = product_reference_in_node(node)
    _require(type(reference) is ProductReference)
    return node, reference


def _slot(value):
    return value.artifact_id, value.target_path, value.media_type, value.file_mode


def _selection(registry, product, node, reference, purpose, workspace, authored, projection, side, socket):
    _require(type(product) is RegisteredProduct and product.workspace_id == workspace
             and _same(product.reference, reference))
    # No ACTIVE-status policy is introduced: this is immutable descriptor provenance.
    binding = registry.binding_for(reference, purpose)
    declared = tuple(value for value in product.descriptor_document.product.runtime_contract.configuration_artifacts
                     if _slot(value) == _slot(binding))
    actual = tuple(value for value in node.configuration_artifacts if _slot(value) == _slot(binding))
    _require(len(declared) == len(actual) == 1)
    return binding, HealthReceiverSelection(workspace, authored, projection, side,
        node.node_id, node.runtime_id, socket, reference, product.descriptor_document, actual[0])


def _coverage(decoded, purpose, signer, target, runtime, gateway, declaration):
    expected = GatewayHealthReceiverTrust if purpose is _PURPOSES[0] else WorkloadHealthReceiverTrust
    _require(type(decoded) is expected)
    _require(_same(decoded, replace(decoded)))
    _require(decoded.purpose is signer.purpose is purpose and decoded.issuer == signer.issuer
             and decoded.runtime_id == runtime)
    if purpose is _PURPOSES[0]:
        _require(decoded.workspace_id == target.workspace_id and decoded.gateway_node_id == gateway
                 and decoded.audience == f"gateway:{target.workspace_id.value}:{gateway.value}")
    else:
        _require(decoded.target == target and decoded.declaration == declaration
                 and decoded.audience == workload_node_control_audience(target))
    _require(any(_same(key, signer.public_key) for key in decoded.public_keys))


def require_health_receiver_coverage(stores, registry, *, plan, graphs, selected, workspace, keys, refuse):
    """Check both receivers without creating authority, history or external effects."""
    _checked(lambda: _require(type(registry) is HealthReceiverDecoders), refuse)
    graph, authored, projection, side, target, runtime, gateway, declaration = _checked(
        lambda: _context(plan, graphs, selected, workspace), refuse)
    for purpose, node_id, socket, key in (
            (_PURPOSES[0], selected.gateway_node_id, selected.gateway_transit_provider_socket_name, keys[0]),
            (_PURPOSES[1], selected.target_node_id, selected.target_provider_socket_name, keys[1])):
        node, reference = _checked(lambda: _node_reference(graph, node_id, runtime.value, socket), refuse)
        # Owner exceptions retain identity even if named like our contract refusal.
        product = stores.registered_products.get(workspace, reference)
        binding, selection = _checked(lambda: _selection(registry, product, node, reference,
            purpose, workspace, authored, projection, side, socket), refuse)
        # Unexpected adapter bugs are not display-safe contract refusals.
        decoded = _checked(lambda: binding.decoder.decode(selection), refuse, (HealthReceiverTrustError,))
        _checked(lambda: _coverage(decoded, purpose, key, target, runtime, gateway, declaration), refuse)
