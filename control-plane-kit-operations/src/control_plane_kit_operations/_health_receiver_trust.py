"""Shared pinned selection and coverage; store reads are outside pure catches."""
from dataclasses import replace

from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.planning import PlanGraphSide
from control_plane_kit_core.products import ProductReference
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext
from control_plane_kit_operations.graph_authoring import product_reference_in_node
from control_plane_kit_operations.health_receiver_trust import (
    GatewayHealthReceiverTrust, HealthReceiverDecoders, HealthReceiverSelection, HealthReceiverTrustError,
    _INPUT_ERRORS, _PURPOSES, _require, _same,
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
    runtime = ref(NodeControlGraphReferenceRole.RUNTIME, operation.target.runtime_id)
    declaration = WorkloadNodeControlSurfaceDeclaration(selected.target_surface,
        WorkloadNodeControlSurfaceDeclarationProfile.V2)
    return graph, authored, projection, side, runtime, declaration


def original_receiver_health_configurations(graph, selected, workspace):
    """Pure original-byte composition; no current membership, keys or registry."""
    configurations = []
    for node_id in (selected.target_node_id, selected.gateway_node_id):
        node = graph.node(node_id)
        artifact = select_receiver_node_control_configuration_artifact(
            artifacts=node.configuration_artifacts,
            environment=node.public_environment + node.socket_environment,
            control_surfaces=node.block_spec.control_surfaces)
        configured = ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode("utf-8"))
        target = configured.target
        _require((target.workspace_id.value, target.runtime_id.value, target.node_id.value) ==
            (workspace, selected.operation.target.runtime_id, node_id))
        _require(node.runtime_id == target.runtime_id.value
            and target.provider_socket_name == configured.declaration.surface.provider_socket_name
            and any(socket.name == target.provider_socket_name.value for socket in node.sockets.providers))
        configurations.append(configured)
    workload, gateway = configurations
    _require(workload.target.provider_socket_name.value == selected.target_provider_socket_name
        and workload.declaration == WorkloadNodeControlSurfaceDeclaration(selected.target_surface,
            WorkloadNodeControlSurfaceDeclarationProfile.V2))
    return workload, gateway


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
    contract = product.descriptor_document.product.runtime_contract
    if purpose is _PURPOSES[1]:
        # Each input must select the same declared slot. Independently valid
        # configurations cannot redirect descriptor A to selected artifact B.
        declared = select_receiver_node_control_configuration_artifact(
            artifacts=contract.configuration_artifacts, environment=contract.public_environment,
            control_surfaces=contract.control_surfaces)
        actual = select_receiver_node_control_configuration_artifact(
            artifacts=node.configuration_artifacts,
            environment=node.public_environment + node.socket_environment,
            control_surfaces=node.block_spec.control_surfaces)
        _require(_slot(declared) == _slot(actual))
        binding = None
    else:
        binding = registry.binding_for(reference, purpose)
        declared = tuple(value for value in contract.configuration_artifacts if _slot(value) == _slot(binding))
        selected = tuple(value for value in node.configuration_artifacts if _slot(value) == _slot(binding))
        _require(len(declared) == len(selected) == 1)
        actual = selected[0]
    return binding, HealthReceiverSelection(workspace, authored, projection, side,
        node.node_id, node.runtime_id, socket, reference, product.descriptor_document, actual)


def _workload_coverage(configured, signer):
    families = tuple(value for value in configured.verifiers if value.purpose is signer.purpose)
    _require(len(families) == 1)
    family, = families
    _require(family.purpose is _PURPOSES[1] and family.issuer == signer.issuer
        and any(_same(key, signer.public_key) for key in family.public_keys))


def _transit_coverage(decoded, signer, gateway):
    _require(type(decoded) is GatewayHealthReceiverTrust)
    _require(_same(decoded, replace(decoded)))
    _require(decoded.purpose is signer.purpose is _PURPOSES[0] and decoded.issuer == signer.issuer
        and decoded.runtime_id == gateway.runtime_id and decoded.workspace_id == gateway.workspace_id
        and decoded.gateway_node_id == gateway.node_id
        and decoded.audience == f"gateway:{gateway.workspace_id.value}:{gateway.node_id.value}")
    _require(any(_same(key, signer.public_key) for key in decoded.public_keys))


def require_health_receiver_coverage(stores, registry, *, plan, graphs, selected, workspace, keys, refuse):
    """Check both receivers without creating authority, history or external effects."""
    _checked(lambda: _require(type(registry) is HealthReceiverDecoders), refuse)
    graph, authored, projection, side, runtime, declaration = _checked(
        lambda: _context(plan, graphs, selected, workspace), refuse)
    workload, gateway = _checked(lambda: original_receiver_health_configurations(graph, selected, workspace), refuse)
    # Both own-control identities have independent declared/selected slots.
    # Gateway transit retains its separate product-specific slot below.
    for configured in (workload, gateway):
        target = configured.target
        node, reference = _checked(lambda: _node_reference(graph, target.node_id.value,
            runtime.value, target.provider_socket_name.value), refuse)
        product = stores.registered_products.get(workspace, reference)
        _checked(lambda: _selection(registry, product, node, reference, _PURPOSES[1],
            workspace, authored, projection, side, target.provider_socket_name.value), refuse)
    for purpose, node_id, socket, key in (
            (_PURPOSES[0], selected.gateway_node_id, selected.gateway_transit_provider_socket_name, keys[0]),
            (_PURPOSES[1], selected.target_node_id, selected.target_provider_socket_name, keys[1])):
        node, reference = _checked(lambda: _node_reference(graph, node_id, runtime.value, socket), refuse)
        # Owner exceptions retain identity even if named like our contract refusal.
        product = stores.registered_products.get(workspace, reference)
        binding, selection = _checked(lambda: _selection(registry, product, node, reference,
            purpose, workspace, authored, projection, side, socket), refuse)
        if purpose is _PURPOSES[1]:
            _checked(lambda: _workload_coverage(workload, key), refuse)
        else:
            # Unexpected transit adapter bugs are not display-safe contract refusals.
            decoded = _checked(lambda: binding.decoder.decode(selection), refuse, (HealthReceiverTrustError,))
            _checked(lambda: _transit_coverage(decoded, key, gateway.target), refuse)
    return workload.target, gateway.target, NodeControlAuthorityContext(authored, projection)
