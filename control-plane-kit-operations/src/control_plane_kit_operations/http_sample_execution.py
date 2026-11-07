"""Reference-only sample execution composition and original route selection."""
from dataclasses import dataclass, replace

from control_plane_kit_core._node_control_public_wire import reference_violation
from control_plane_kit_core.public_ingress import NamedPublicIngress, PublicIngressExposure
from control_plane_kit_core.planning import NodeTarget, WaitForHealthy
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.graph_authoring import product_reference_in_node
from control_plane_kit_operations.http_sample_compatibility import (
    GatewayHttpSampleBinding, HttpSampleCompatibility, bind_http_sample_compatibility,
)
from control_plane_kit_operations.http_sample_sender_input import HttpSampleSenderInput


@dataclass(frozen=True, slots=True, repr=False)
class HttpSampleExecutionSupport:
    gateway_binding: GatewayHttpSampleBinding
    transit_provider_socket_name: str

    @property
    def profile(self):
        return "cpk.http-sample-execution.v1"

    def __post_init__(self):
        if (type(self.gateway_binding) is not GatewayHttpSampleBinding
                or not callable(getattr(self.gateway_binding.decoder, "decode", None))
                or type(self.transit_provider_socket_name) is not str
                or reference_violation(self.transit_provider_socket_name) is not None):
            raise ValueError("HTTP sample execution support is unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class PreparedHttpSampleRoute:
    compatibility: HttpSampleCompatibility
    ingress: NamedPublicIngress
    transit_provider_socket_name: str


@dataclass(frozen=True, slots=True, repr=False)
class PreparedHttpSampleCheck:
    sender_input: HttpSampleSenderInput
    ingress: NamedPublicIngress
    transit_provider_socket_name: str


def select_http_sample_route(*, workspace_id, plan, current, desired, products, node_id, support):
    """Select only from independently loaded original registered owner values."""
    graph = DEFAULT_GRAPH_CODEC.decode(desired.graph_descriptor)
    node = graph.node(node_id)
    runtime = graph.runtimes[node.runtime_id]
    if runtime.kind is not RuntimeKind.DOCKER or runtime.authority_ref is None:
        raise ValueError("HTTP sample execution placement is unavailable")
    gateway = graph.node(runtime.management.gateway_node_id)
    compatibility = bind_http_sample_compatibility(workspace_id=workspace_id, plan=plan,
        current=current, desired=desired, products=products, node_id=node_id,
        gateway_binding=support.gateway_binding)
    ingresses = tuple(value for value in graph.public_ingresses
        if value.ingress_id == runtime.management.management_ingress_id)
    if (len(ingresses) != 1 or type(ingresses[0]) is not NamedPublicIngress
            or replace(ingresses[0]) != ingresses[0]
            or ingresses[0].exposure is not PublicIngressExposure.HTTPS
            or ingresses[0].target.node_id != gateway.node_id
            or ingresses[0].target.provider_socket != support.transit_provider_socket_name):
        raise ValueError("HTTP sample execution route is unavailable")
    return PreparedHttpSampleRoute(compatibility, ingresses[0], support.transit_provider_socket_name)


def load_http_sample_route(stores, *, workspace_id, plan, node_id, support):
    """Read the pinned registered route; no current authority or signing uses."""
    current = stores.realized_graphs.get(plan.base_realized_projection_id)
    desired = stores.realized_graphs.get(plan.desired_realized_projection_id)
    graph = DEFAULT_GRAPH_CODEC.decode(desired.graph_descriptor)
    node = graph.node(node_id)
    gateway = graph.node(graph.runtimes[node.runtime_id].management.gateway_node_id)
    products = tuple(stores.registered_products.get(workspace_id, reference)
        for reference in dict.fromkeys(product_reference_in_node(value) for value in (node, gateway)))
    return select_http_sample_route(workspace_id=workspace_id, plan=plan, current=current,
        desired=desired, products=products, node_id=node_id, support=support)


def require_http_sample_plan_support(*, workspace_id, plan, current, desired, products,
        runtime_authorities, support):
    """Complete plan compatibility; caller separately qualifies its actual consumer."""
    if type(support) is not HttpSampleExecutionSupport:
        raise ValueError("HTTP sample execution support is unavailable")
    support.__post_init__()
    graph = DEFAULT_GRAPH_CODEC.decode(desired.graph_descriptor)
    waits = tuple(activity for activity in plan.plan.activities if type(activity.operation) is WaitForHealthy)
    if not waits:
        raise ValueError("HTTP sample execution checks are unavailable")
    for activity in waits:
        if type(activity.operation.target) is not NodeTarget:
            raise ValueError("HTTP sample execution check target is unavailable")
        node = graph.node(activity.operation.target.node_id)
        runtime = graph.runtimes[node.runtime_id]
        authorities = tuple(value for value in runtime_authorities
            if value.workspace_id == workspace_id and value.authority_ref == runtime.authority_ref
            and value.runtime_kind is RuntimeKind.DOCKER and value.status.value == "active")
        if len(authorities) != 1:
            raise ValueError("HTTP sample execution runtime is unavailable")
        route = select_http_sample_route(workspace_id=workspace_id, plan=plan, current=current,
            desired=desired, products=products, node_id=node.node_id, support=support)
        receiver = route.compatibility.receiver
        original = receiver.verification_contract.checks
        selected = tuple(entry.check for entry in receiver.checks)
        if len(selected) != len(original) or any(check not in selected for check in original):
            raise ValueError("HTTP sample execution check coverage is unavailable")


__all__ = ["HttpSampleExecutionSupport", "PreparedHttpSampleRoute", "PreparedHttpSampleCheck",
    "load_http_sample_route", "select_http_sample_route", "require_http_sample_plan_support"]
