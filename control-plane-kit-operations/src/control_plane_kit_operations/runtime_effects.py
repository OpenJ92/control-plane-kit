"""Translate operations-owned realization context into core runtime effects."""

from __future__ import annotations

from typing import Mapping

from control_plane_kit_core.planning.activity_plan import (
    AddSocketConnection,
    NodeTarget,
    ReconcileNode,
    RemoveNodeResource,
    RemoveSocketConnection,
    StartNode,
    StopNode,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit_core.products import (
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectSource,
    RuntimeProductMaterial,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.workflows import InvalidOperationCommand


def runtime_effect_request_for_context(
    context: ActivityRealizationContext,
) -> RuntimeEffectRequest:
    """Interpret pinned operations material as a pure runtime-effect request."""

    if not isinstance(context, ActivityRealizationContext):
        raise InvalidOperationCommand(
            "runtime effect translation requires ActivityRealizationContext"
        )
    graph = _material_graph(context)
    runtime_id = _runtime_id_for_context(context, graph)
    return RuntimeEffectRequest(
        effect_id=context.intent_event.event_id,
        kind=RuntimeEffectKind.REALIZE_ACTIVITY,
        runtime_kind=_runtime_kind_for_context(context, graph, runtime_id),
        source=RuntimeEffectSource(
            workspace_id=context.request.identity.workspace_id,
            request_id=context.request.identity.request_id,
            run_id=context.run.run_id,
            plan_id=context.plan_record.plan_id,
            base_graph_id=context.plan_record.base_graph_id,
            desired_graph_id=context.plan_record.desired_graph_id,
            intent_event_id=context.intent_event.event_id,
        ),
        activity_id=context.activity.activity_id,
        operation=context.activity.operation,
        products=_products_for_context(context, graph, runtime_id),
    )


def _material_graph(context: ActivityRealizationContext) -> DeploymentGraph:
    if isinstance(
        context.activity.operation,
        (StopNode, RemoveNodeResource, RemoveSocketConnection),
    ):
        return DEFAULT_GRAPH_CODEC.decode(context.base_graph.graph_descriptor)
    return DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)


def _runtime_id_for_context(
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
) -> str:
    node_id = _node_target(context)
    if node_id is not None:
        try:
            return graph.nodes[node_id].runtime_id
        except KeyError as error:
            raise InvalidOperationCommand("runtime effect node target is missing") from error
    operation = context.activity.operation
    target = getattr(operation, "target", None)
    runtime_id = getattr(target, "runtime_id", None)
    if isinstance(runtime_id, str) and runtime_id:
        return runtime_id
    raise InvalidOperationCommand("runtime effect target is not a runtime operation")


def _runtime_kind_for_context(
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
    runtime_id: str,
) -> RuntimeKind:
    del context
    try:
        return graph.runtimes[runtime_id].kind
    except KeyError as error:
        raise InvalidOperationCommand("runtime effect runtime target is missing") from error


def _products_for_context(
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
    runtime_id: str,
) -> tuple[RuntimeProductMaterial, ...]:
    node_id = _node_target(context)
    if node_id is None:
        return ()
    try:
        node = graph.nodes[node_id]
    except KeyError as error:
        raise InvalidOperationCommand("runtime effect node target is missing") from error
    product = _registered_product_for_node(context.registered_products, node.metadata)
    return (
        RuntimeProductMaterial(
            node_id=node_id,
            runtime_id=runtime_id,
            reference=product.reference,
            product=product.descriptor_document.product,
        ),
    )


def _node_target(context: ActivityRealizationContext) -> str | None:
    operation = context.activity.operation
    match operation:
        case (
            StartNode(target=NodeTarget(node_id=node_id))
            | StopNode(target=NodeTarget(node_id=node_id))
            | RemoveNodeResource(target=NodeTarget(node_id=node_id))
            | WaitForHealthy(target=NodeTarget(node_id=node_id))
            | ReconcileNode(target=NodeTarget(node_id=node_id))
        ):
            return node_id
        case (
            AddSocketConnection()
            | SwitchSocketConnection()
            | RemoveSocketConnection()
        ):
            return None
        case _:
            return None


def _registered_product_for_node(
    products: tuple[RegisteredProduct, ...],
    metadata: Mapping[str, object],
) -> RegisteredProduct:
    identity = _product_identity(metadata.get("product_identity"))
    digest = _descriptor_digest(metadata.get("product_descriptor_digest"))
    reference = ProductReference(identity, digest)
    for product in products:
        if product.reference == reference:
            return product
    raise InvalidOperationCommand("runtime effect product reference is not registered")


def _product_identity(value: object) -> ProductIdentity:
    if not isinstance(value, str):
        raise InvalidOperationCommand("runtime effect product identity is malformed")
    parts = value.split("/")
    if len(parts) != 3:
        raise InvalidOperationCommand("runtime effect product identity is malformed")
    try:
        revision = int(parts[2])
    except ValueError as error:
        raise InvalidOperationCommand(
            "runtime effect product identity is malformed"
        ) from error
    return ProductIdentity(parts[0], parts[1], revision)


def _descriptor_digest(value: object) -> ProductDescriptorDigest:
    if not isinstance(value, str):
        raise InvalidOperationCommand("runtime effect product descriptor is malformed")
    return ProductDescriptorDigest(value)
