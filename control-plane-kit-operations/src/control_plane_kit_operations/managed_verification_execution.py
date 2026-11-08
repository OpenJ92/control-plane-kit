"""Reference-only managed verification composition and prepared SQL handoff.

Construction proves congruence, never current authority, byte custody, provider
identity or a successful SQL read. Only the original reached-check owner may
issue this value after committing its current guards and authorization uses.
"""
from dataclasses import dataclass, replace

from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.configuration import ConfigurationArtifactRequirement
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.planning import PlanGraphSide, WaitForHealthy
from control_plane_kit_core.postgres_readiness import PostgresReadinessBinding, PostgresReadinessObserver
from control_plane_kit_core.products import ProductReference
from control_plane_kit_core.public_ingress import NamedPublicIngress, PublicIngressExposure
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfiguration
from control_plane_kit_core.receiver_identity import NodeControlReceiverTarget, receiver_node_control_audience
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRouteBinding, ReceiverManagementRoutesCodec
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.receiver_health_reads import DelegatedWorkloadReceiverHealthReadGrant
from control_plane_kit_core.receiver_health_transit import DelegatedGatewayReceiverHealthReadTransitGrant
from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant
from control_plane_kit_operations.health_effect_preparations import health_effect_attempt_wire_id
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders, HealthReceiverDecoderBinding, HealthReceiverSelection, GatewayHealthReceiverTrust
from control_plane_kit_operations.health_signing_authority import (
    GatewayNodeHealthReadTransitSigningAuthority, WorkloadNodeHealthReadSigningAuthority,
)
from control_plane_kit_operations.http_sample_execution import HttpSampleExecutionSupport
from control_plane_kit_operations.http_sample_compatibility import _registered, _receiver, _qualified_artifact
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.records import ActivityPlanRecord, RealizedGraphProjectionRecord


class ManagedVerificationSupportUnavailable(ValueError):
    """Fixed refusal without values or protected authorization references."""


def _require(condition):
    if condition is not True:
        raise ValueError


def _checked(action):
    try:
        return action()
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, OverflowError, RecursionError):
        pass
    raise ManagedVerificationSupportUnavailable("managed verification support is unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class ManagedVerificationExecutionSupport:
    """Trusted composition's exact observer table; not a graph-supplied claim."""
    http_samples: HttpSampleExecutionSupport
    postgres_observers: tuple[PostgresReadinessObserver, ...]
    health_receiver_decoders: HealthReceiverDecoders

    def __post_init__(self):
        def validate():
            _require(type(self.http_samples) is HttpSampleExecutionSupport
                and type(self.health_receiver_decoders) is HealthReceiverDecoders
                and type(self.postgres_observers) is tuple and 1 <= len(self.postgres_observers) <= 32)
            self.http_samples.__post_init__()
            self.health_receiver_decoders.__post_init__()
            for observer in self.postgres_observers:
                _require(type(observer) is PostgresReadinessObserver)
                observer.descriptor()
            _require(len({value.product for value in self.postgres_observers}) == len(self.postgres_observers))
        _checked(validate)

    def observer_for(self, reference):
        def select():
            _require(type(reference) is ProductReference)
            self.__post_init__()
            matches = tuple(value for value in self.postgres_observers if value.product == reference)
            _require(len(matches) == 1)
            return matches[0]
        return _checked(select)


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPostgresReadinessCheck:
    """One original check's complete public grant/route inputs, never a permit."""
    binding: PostgresReadinessBinding
    password_grant: SecretVersionResolutionGrant
    transit: GatewayNodeHealthReadTransitSigningAuthority
    workload: WorkloadNodeHealthReadSigningAuthority
    transit_grant: DelegatedGatewayReceiverHealthReadTransitGrant
    workload_grant: DelegatedWorkloadReceiverHealthReadGrant
    route: "PreparedPostgresReadinessRoute"

    def __post_init__(self):
        _checked(lambda: _validate_prepared(self))


def _validate_prepared(value):
    _require(type(value.binding) is PostgresReadinessBinding
        and type(value.password_grant) is SecretVersionResolutionGrant
        and type(value.transit) is GatewayNodeHealthReadTransitSigningAuthority
        and type(value.workload) is WorkloadNodeHealthReadSigningAuthority
        and type(value.transit_grant) is DelegatedGatewayReceiverHealthReadTransitGrant
        and type(value.workload_grant) is DelegatedWorkloadReceiverHealthReadGrant
        and type(value.route) is PreparedPostgresReadinessRoute)
    for item in (value.binding, value.password_grant, value.transit, value.workload,
            value.transit_grant, value.workload_grant, value.route):
        type(item).__post_init__(item)
    binding = value.binding
    document = binding.descriptor()
    request = binding.request()
    password = value.password_grant
    _require(password.selection.descriptor() == document["credential_selection"]
        and password.request_fingerprint == document["request_fingerprint"]
        and password.selection_fingerprint == binding.receipt.selection_fingerprint)
    roles = (password, value.transit.resolution_grant, value.workload.resolution_grant)
    _require((password.operation_id, password.effect_id)
        == (binding.wait.source.request_id, binding.wait.effect_id))
    for role in roles:
        _require((role.workspace_id, role.run_id, role.activity_id)
            == (binding.wait.source.workspace_id, binding.attempt.run_id.value, binding.attempt.activity_id))
    for role in roles[1:]:
        _require(role.operation_id == health_effect_attempt_wire_id(binding.attempt)
            and role.effect_id is None and role.probe_id is None)
    _require(len({role.actor_subject for role in roles}) == 1
        and password.session_id is not None and len({role.session_id for role in roles}) == 1
        and len({role.authorization_id for role in roles}) == 3
        and value.transit.resolution_grant.correlation_id != value.workload.resolution_grant.correlation_id)
    _require(value.transit.public_key.fingerprint_sha256 != value.workload.public_key.fingerprint_sha256
        and value.transit.resolution_grant.reference != value.workload.resolution_grant.reference)
    for grant, authority in ((value.transit_grant, value.transit), (value.workload_grant, value.workload)):
        _require(grant.target == request.target and grant.authority_context == request.authority_context
            and grant.kind is NodeHealthReadKind.READINESS and grant.declaration_identity == request.declaration_identity
            and grant.request_id == request.request_id and grant.request_digest == request.canonical_digest()
            and grant.key_id == authority.public_key.key_id
            and grant.issued_at == grant.not_before == binding.started_at_epoch_ms // 1000
            and grant.expires_at == binding.deadline_epoch_ms // 1000)
    gateway = value.transit_grant.gateway_target
    route = value.route
    trust = route.gateway_trust
    _require(value.transit_grant.attempt_id == health_effect_attempt_wire_id(binding.attempt)
        and gateway.workspace_id == request.target.workspace_id and gateway.runtime_id == request.target.runtime_id
        and gateway == route.gateway_target and route.receiver == binding.receiver
        and route.plan_id == binding.wait.source.plan_id
        and route.selection.authored_graph_id == binding.wait.source.desired_graph_id
        and route.selection.realized_projection_id == binding.desired_projection_id
        and value.workload_grant.audience == receiver_node_control_audience(request.target))
    families = tuple(item for item in binding.receiver.verifiers
        if item.purpose is DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
    _require(len(families) == 1 and families[0].issuer == value.workload_grant.issuer
        and value.workload.public_key in families[0].public_keys)
    _require(trust.purpose is DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT
        and trust.issuer == value.transit_grant.issuer and trust.audience == value.transit_grant.audience
        and (trust.workspace_id, trust.runtime_id, trust.gateway_node_id)
            == (gateway.workspace_id, gateway.runtime_id, gateway.node_id)
        and value.transit.public_key in trust.public_keys)


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPostgresReadinessRoute:
    """Pinned configured route facts; no daemon observation or execution permit."""
    plan_id: str
    selection: HealthReceiverSelection
    transit_selection: HealthReceiverSelection
    transit_decoder: HealthReceiverDecoderBinding
    gateway_trust: GatewayHealthReceiverTrust
    gateway_target: NodeControlReceiverTarget
    receiver: ReceiverNodeControlConfiguration
    binding: ReceiverManagementRouteBinding
    ingress: NamedPublicIngress
    transit_provider_socket_name: str

    def __post_init__(self):
        def validate():
            _require(type(self.plan_id) is str and bool(self.plan_id)
                and type(self.selection) is HealthReceiverSelection
                and type(self.transit_selection) is HealthReceiverSelection
                and type(self.transit_decoder) is HealthReceiverDecoderBinding
                and type(self.gateway_trust) is GatewayHealthReceiverTrust
                and type(self.gateway_target) is NodeControlReceiverTarget
                and type(self.receiver) is ReceiverNodeControlConfiguration
                and type(self.binding) is ReceiverManagementRouteBinding
                and type(self.ingress) is NamedPublicIngress)
            for item in (self.selection, self.transit_selection, self.transit_decoder, self.gateway_trust,
                    self.gateway_target, self.receiver, self.binding, self.ingress):
                type(item).__post_init__(item)
            routes = ReceiverManagementRoutesCodec().decode_bytes(self.selection.artifact.content.encode("utf-8"))
            _require(routes.gateway_target == self.gateway_target and self.binding in routes.targets
                and self.gateway_trust.gateway_target == self.gateway_target
                and self.binding.target == self.receiver.target and self.binding.declaration == self.receiver.declaration
                and self.selection.graph_side is PlanGraphSide.DESIRED_GRAPH
                and (self.selection.workspace_id, self.selection.runtime_id, self.selection.receiver_node_id,
                    self.selection.provider_socket_name) == (self.gateway_target.workspace_id.value,
                    self.gateway_target.runtime_id.value, self.gateway_target.node_id.value,
                    self.transit_provider_socket_name)
                and self.ingress.exposure is PublicIngressExposure.HTTPS
                and self.ingress.target.node_id == self.gateway_target.node_id.value
                and self.ingress.target.provider_socket == self.transit_provider_socket_name)
            for name in ("workspace_id", "authored_graph_id", "realized_projection_id", "graph_side",
                    "receiver_node_id", "runtime_id", "provider_socket_name", "product_reference", "descriptor_document"):
                _require(getattr(self.selection, name) == getattr(self.transit_selection, name))
            decoder = self.transit_decoder
            _require(decoder.product_reference == self.selection.product_reference
                and decoder.purpose is DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT)
            slot = ConfigurationArtifactRequirement(decoder.artifact_id, decoder.target_path, decoder.media_type, decoder.file_mode)
            actual = self.transit_selection.artifact
            _require((actual.artifact_id, actual.target_path, actual.media_type, actual.file_mode)
                == (slot.artifact_id, slot.target_path, slot.media_type, slot.file_mode))
            _require(decoder.decoder.decode(self.transit_selection) == self.gateway_trust)
        _checked(validate)


def select_postgres_readiness_route(*, workspace_id, plan, current, desired, products, node_id, support):
    """Reuse the registered HTTP route-slot owner, retaining SQL health purpose."""
    def select():
        _require(type(support) is ManagedVerificationExecutionSupport and type(plan) is ActivityPlanRecord
            and plan.derivation_profile is PlanDerivationProfile.MANAGED_SQL_HTTP_BOOTSTRAP_V1)
        support.__post_init__()
        graphs = []
        for projection, identity, authored in ((current, plan.base_realized_projection_id, plan.base_graph_id),
                (desired, plan.desired_realized_projection_id, plan.desired_graph_id)):
            _require(type(projection) is RealizedGraphProjectionRecord
                and projection.workspace_id == workspace_id and projection.projection_id == identity
                and projection.source_authored_graph_id == authored)
            graph = validate_graph(DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor))
            graph.require_valid()
            graphs.append(graph)
        _require(derive_activity_plan(Deploy(*graphs), profile=plan.derivation_profile) == plan.plan
            and any(type(item.operation) is WaitForHealthy and item.operation.target.node_id == node_id
                for item in plan.plan.activities))
        graph = graphs[1].graph
        node = graph.node(node_id)
        runtime = graph.runtimes[node.runtime_id]
        _require(runtime.kind is RuntimeKind.DOCKER and runtime.authority_ref is not None)
        gateway = graph.node(runtime.management.gateway_node_id)
        product = _registered(node, products, workspace_id)
        gateway_product = _registered(gateway, products, workspace_id)
        receiver = _receiver(node, product, workspace_id)
        own = _receiver(gateway, gateway_product, workspace_id)
        qualified = support.http_samples.gateway_binding
        _require(gateway_product.reference == qualified.product)
        artifact = _qualified_artifact(gateway, gateway_product, qualified.route_slot)
        routes = ReceiverManagementRoutesCodec().decode_bytes(artifact.content.encode("utf-8"))
        selected = tuple(item for item in routes.targets
            if item.target == receiver.target and item.declaration == receiver.declaration)
        ports = tuple(port.container_port for port in product.descriptor_document.product.runtime_contract.provider_ports
            if port.provider_socket == receiver.target.provider_socket_name.value)
        _require(routes.gateway_target == own.target and len(selected) == len(ports) == 1
            and selected[0].origin == f"http://{node_id}:{ports[0]}")
        ingresses = tuple(item for item in graph.public_ingresses
            if item.ingress_id == runtime.management.management_ingress_id)
        _require(len(ingresses) == 1)
        selection = HealthReceiverSelection(workspace_id, plan.desired_graph_id, desired.projection_id,
            PlanGraphSide.DESIRED_GRAPH, gateway.node_id, gateway.runtime_id,
            support.http_samples.transit_provider_socket_name, gateway_product.reference,
            gateway_product.descriptor_document, artifact)
        decoder = support.health_receiver_decoders.binding_for(gateway_product.reference,
            DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT)
        transit_artifact = _qualified_artifact(gateway, gateway_product, ConfigurationArtifactRequirement(
            decoder.artifact_id, decoder.target_path, decoder.media_type, decoder.file_mode))
        transit_selection = replace(selection, artifact=transit_artifact)
        trust = decoder.decoder.decode(transit_selection)
        _require(type(trust) is GatewayHealthReceiverTrust and trust.gateway_target == own.target)
        return PreparedPostgresReadinessRoute(plan.plan_id, selection, transit_selection, decoder, trust, own.target, receiver, selected[0],
            ingresses[0], support.http_samples.transit_provider_socket_name)
    return _checked(select)


__all__ = ["ManagedVerificationSupportUnavailable", "ManagedVerificationExecutionSupport", "PreparedPostgresReadinessCheck",
    "PreparedPostgresReadinessRoute", "select_postgres_readiness_route"]
