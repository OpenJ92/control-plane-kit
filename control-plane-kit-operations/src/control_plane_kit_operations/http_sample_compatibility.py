"""Pure registered compatibility over loaded owner records; never authority."""
from dataclasses import dataclass, field, replace

from control_plane_kit_core._node_control_public_wire import identifier_violation, reference_violation
from control_plane_kit_core.configuration import ConfigurationArtifactRequirement
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.http_verification_sample_installation import (
    HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT,
    HttpVerificationSampleReceiverBinding, HttpVerificationSampleReceiverConfigurationCodec,
    select_http_verification_sample_configuration_slot,
)
from control_plane_kit_core.planning import WaitForHealthy
from control_plane_kit_core.products import ProductReference, product_configuration_slots
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
    select_receiver_node_control_configuration_slot,
)
from control_plane_kit_core.receiver_identity import NodeControlReceiverTarget, NodeControlReceiverTargetCodec
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRoutesCodec
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.graph_authoring import product_reference_in_node
from control_plane_kit_operations.health_receiver_trust import _public_keys
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.records import ActivityPlanRecord, ActivityPlanStatus, RealizedGraphProjectionRecord
from control_plane_kit_operations.runtime_management_admission import _node_matches_product


class HttpSampleCompatibilityUnavailable(ValueError):
    """Fixed refusal, without candidate material or provider error text."""


def _require(condition):
    if condition is not True:
        raise ValueError


@dataclass(frozen=True, slots=True, repr=False)
class GatewayHttpSampleTrust:
    target: NodeControlReceiverTarget
    purpose: DelegationKeyPurpose
    issuer: str
    public_keys: tuple

    def __post_init__(self):
        NodeControlReceiverTargetCodec().encode(self.target)
        _require(self.purpose is DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT
            and type(self.issuer) is str and reference_violation(self.issuer) is None)
        _public_keys(self.public_keys)
        _require(all(identifier_violation(key.key_id) is None for key in self.public_keys))


@dataclass(frozen=True, slots=True, repr=False)
class GatewayHttpSampleBinding:
    product: ProductReference
    slot: ConfigurationArtifactRequirement
    # Product-specific, pure public-material decoder; not a signer or transport.
    decoder: object = field(repr=False)
    route_slot: ConfigurationArtifactRequirement = field(kw_only=True)


@dataclass(frozen=True, slots=True, repr=False)
class HttpSampleCompatibility:
    workspace_id: str
    plan_id: str
    desired_graph_id: str
    projection_id: str
    workload_product: ProductReference
    gateway_product: ProductReference
    receiver: HttpVerificationSampleReceiverBinding
    route: object
    gateway_trust: GatewayHttpSampleTrust


def _slot(value):
    return value.artifact_id, value.target_path, value.media_type, value.file_mode


def _qualified_artifact(node, product, expected):
    _require(type(expected) is ConfigurationArtifactRequirement and replace(expected) == expected)
    declared = tuple(value for value in product_configuration_slots(product.descriptor_document.product.runtime_contract)
        if _slot(value) == _slot(expected))
    actual = tuple(value for value in node.configuration_artifacts if _slot(value) == _slot(expected))
    _require(len(declared) == len(actual) == 1)
    return actual[0]


def _registered(node, products, workspace):
    reference = product_reference_in_node(node)
    _require(type(reference) is ProductReference)
    matches = tuple(product for product in products if product.reference == reference and product.workspace_id == workspace)
    _require(len(matches) == 1)
    product, = matches
    _require(replace(product) == product and _node_matches_product(node, product))
    return product


def _receiver(node, product, workspace):
    contract = product.descriptor_document.product.runtime_contract
    declared = select_receiver_node_control_configuration_slot(artifacts=contract.configuration_artifacts,
        requirements=contract.configuration_requirements, environment=contract.public_environment,
        control_surfaces=contract.control_surfaces)
    actual = select_receiver_node_control_configuration_artifact(artifacts=node.configuration_artifacts,
        environment=node.public_environment + node.socket_environment, control_surfaces=node.block_spec.control_surfaces)
    _require(_slot(declared) == _slot(actual))
    receiver = ReceiverNodeControlConfigurationCodec().decode_bytes(actual.content.encode("utf-8"))
    _require((receiver.target.workspace_id.value, receiver.target.runtime_id.value, receiver.target.node_id.value)
        == (workspace, node.runtime_id, node.node_id))
    return receiver


def bind_http_sample_compatibility(*, workspace_id, plan, current, desired, products, node_id, gateway_binding):
    """Bind supplied immutable facts only; callers own loading and current policy.

    No stores, secret resolvers, runtime clients or transport ports are accepted.
    The decoder is a pure product parser. This value grants no execution and is
    not an observation that a process loaded, listened on or served its config.
    """
    try:
        return _bind(workspace_id, plan, current, desired, products, node_id, gateway_binding)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        pass
    raise HttpSampleCompatibilityUnavailable("HTTP sample compatibility is unavailable")


def _bind(workspace, plan, current, desired, products, node_id, gateway_binding):
    _require(type(workspace) is str and type(node_id) is str and type(plan) is ActivityPlanRecord
        and plan.status is ActivityPlanStatus.PLANNED
        and plan.derivation_profile in (PlanDerivationProfile.MANAGED_HTTP_SAMPLE_BOOTSTRAP_V1,
            PlanDerivationProfile.MANAGED_SQL_HTTP_BOOTSTRAP_V1)
        and type(products) is tuple and all(type(product) is RegisteredProduct for product in products)
        and type(gateway_binding) is GatewayHttpSampleBinding)
    _require(replace(plan) == plan)
    graphs = []
    for projection, expected_id, source in ((current, plan.base_realized_projection_id, plan.base_graph_id),
            (desired, plan.desired_realized_projection_id, plan.desired_graph_id)):
        _require(type(projection) is RealizedGraphProjectionRecord and replace(projection) == projection
            and projection.workspace_id == workspace and projection.projection_id == expected_id
            and projection.source_authored_graph_id == source)
        graph = validate_graph(DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor))
        graph.require_valid()
        graphs.append(graph)
    canonical = derive_activity_plan(Deploy(*graphs), profile=plan.derivation_profile)
    _require(canonical == plan.plan and canonical.ready_for_execution
        and any(type(item.operation) is WaitForHealthy and item.operation.target.node_id == node_id
            for item in canonical.activities))
    graph = graphs[1].graph
    node = graph.node(node_id)
    gateway = graph.node(graph.runtimes[node.runtime_id].management.gateway_node_id)
    workload_product = _registered(node, products, workspace)
    gateway_product = _registered(gateway, products, workspace)
    receiver = _receiver(node, workload_product, workspace)
    own = _receiver(gateway, gateway_product, workspace)
    contract = workload_product.descriptor_document.product.runtime_contract
    # Select expected structure from the registered descriptor, never graph bytes.
    environment = tuple(value for value in contract.public_environment
        if value.name == HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT)
    _require(len(environment) == 1 and type(environment[0]) is PublicStaticEnvironmentBinding)
    slots = tuple(value for value in product_configuration_slots(contract) if value.target_path == environment[0].value)
    _require(len(slots) == 1)
    expected = ConfigurationArtifactRequirement(*_slot(slots[0]))
    select_http_verification_sample_configuration_slot(expected_slot=expected,
        artifacts=contract.configuration_artifacts, requirements=contract.configuration_requirements,
        environment=contract.public_environment)
    artifact = select_http_verification_sample_configuration_slot(expected_slot=expected,
        artifacts=node.configuration_artifacts, environment=node.public_environment + node.socket_environment)
    configured = HttpVerificationSampleReceiverConfigurationCodec().decode_canonical_bytes(artifact.content.encode("utf-8"))
    installed = HttpVerificationSampleReceiverBinding(configured, receiver, contract.verification,
        contract.sockets.providers, contract.provider_ports)
    # Relay goes to the receiver socket. Core binds each check's own local port.
    receiver_ports = tuple(port.container_port for port in contract.provider_ports
        if port.provider_socket == receiver.target.provider_socket_name.value)
    _require(len(receiver_ports) == 1)
    _require(type(gateway_binding.product) is ProductReference and gateway_binding.product == gateway_product.reference
        and callable(getattr(gateway_binding.decoder, "decode", None)))
    route_artifact = _qualified_artifact(gateway, gateway_product, gateway_binding.route_slot)
    routes = ReceiverManagementRoutesCodec().decode_bytes(route_artifact.content.encode("utf-8"))
    selected = tuple(route for route in routes.targets if route.target == receiver.target
        and route.declaration == receiver.declaration)
    _require(routes.gateway_target == own.target and len(selected) == 1
        and selected[0].origin == f"http://{node_id}:{receiver_ports[0]}"
        and reference_violation(selected[0].target_id) is None)
    trust_artifact = _qualified_artifact(gateway, gateway_product, gateway_binding.slot)
    trust = gateway_binding.decoder.decode(trust_artifact)
    _require(type(trust) is GatewayHttpSampleTrust and replace(trust) == trust and trust.target == own.target)
    return HttpSampleCompatibility(workspace, plan.plan_id, plan.desired_graph_id, desired.projection_id,
        workload_product.reference, gateway_product.reference, installed, selected[0], trust)


__all__ = ["HttpSampleCompatibilityUnavailable", "GatewayHttpSampleTrust", "GatewayHttpSampleBinding",
    "HttpSampleCompatibility", "bind_http_sample_compatibility"]
