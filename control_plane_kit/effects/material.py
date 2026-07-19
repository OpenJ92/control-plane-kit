"""Pure materialization of planned effects from plan-pinned graph truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address, ip_address
import json
from typing import Mapping, TypeAlias
from urllib.parse import urlsplit

from control_plane_kit.lifecycle import OWNED_EPHEMERAL, ResourceLifecycle
from control_plane_kit.configuration import ConfigurationArtifact
from control_plane_kit.secrets import (
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretFileMode,
    SecretReference,
)

from control_plane_kit.effects.values import EffectPurpose, EffectRequest
from control_plane_kit.planning import (
    ActivityOperation,
    AddSocketConnection,
    Compensate,
    CompensationMaterialSource,
    DestroyDataResource,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit.topology import (
    DeploymentGraph,
    Edge,
    Endpoint,
    LiteralAddress,
    Node,
    SecretReferenceAddress,
)
from control_plane_kit.types import EndpointScope, Protocol, RuntimeKind
from control_plane_kit.verification import (
    VerificationCheck,
    VerificationContract,
    expected_protocols,
)


_SECRET_MARKERS = ("secret", "token", "password", "credential", "private_key", "api_key")


class MaterializationCode(StrEnum):
    """Closed reasons why approved work cannot become adapter input."""

    GRAPH_IDENTITY = "graph-identity"
    TARGET_NOT_FOUND = "target-not-found"
    INVALID_RUNTIME_MEMBERSHIP = "invalid-runtime-membership"
    INVALID_SOCKET_CONNECTION = "invalid-socket-connection"
    MALFORMED_IMPLEMENTATION = "malformed-implementation"
    SECRET_VALUE = "secret-value"
    UNSUPPORTED_OPERATION = "unsupported-operation"
    INVALID_VERIFICATION_TARGET = "invalid-verification-target"


class EffectMaterializationError(ValueError):
    """Typed pre-intent rejection; its message must never contain secret values."""

    def __init__(self, code: MaterializationCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PinnedGraphSet:
    """The immutable graph coordinates owned by one approved plan."""

    workspace_id: str
    plan_id: str
    base_graph_id: str
    desired_graph_id: str


@dataclass(frozen=True)
class LiteralMaterialValue:
    value: str


@dataclass(frozen=True, order=True)
class SecretReferenceMaterialValue:
    reference_id: str

    def __post_init__(self) -> None:
        SecretReference(self.reference_id)


EnvironmentMaterialValue: TypeAlias = LiteralMaterialValue | SecretReferenceMaterialValue


@dataclass(frozen=True, order=True)
class EnvironmentBindingMaterial:
    name: str
    value: EnvironmentMaterialValue


@dataclass(frozen=True, order=True)
class DataMountMaterial:
    """One named lifecycle resource attached at an implementation path."""

    resource_id: str
    target_path: str

    def __post_init__(self) -> None:
        if not self.resource_id.strip():
            raise EffectMaterializationError(
                MaterializationCode.MALFORMED_IMPLEMENTATION,
                "data mount resource identity must not be empty",
            )
        if not self.target_path.startswith("/"):
            raise EffectMaterializationError(
                MaterializationCode.MALFORMED_IMPLEMENTATION,
                "data mount target path must be absolute",
            )


@dataclass(frozen=True, order=True)
class SecretFileMaterial:
    reference_id: str
    target_path: str
    file_mode: SecretFileMode

    def __post_init__(self) -> None:
        SecretFileDelivery(
            self.target_path,
            SecretReference(self.reference_id),
            self.file_mode,
        )


@dataclass(frozen=True)
class HostPublicationMaterial:
    """One explicit host binding derived from a provider socket."""

    socket_name: str
    protocol: Protocol
    container_port: int
    bind_address: IPv4Address | IPv6Address
    host_port: int | None = None

    def __post_init__(self) -> None:
        if not self.socket_name.strip():
            raise _malformed("host_publications")
        if not isinstance(self.protocol, Protocol):
            raise TypeError("host publication protocol must be Protocol")
        if (
            type(self.container_port) is not int
            or self.container_port < 1
            or self.container_port > 65_535
        ):
            raise _malformed("host_publications")
        if not isinstance(self.bind_address, (IPv4Address, IPv6Address)):
            raise TypeError("host publication bind address must be an IP address")
        if self.host_port is not None and (
            type(self.host_port) is not int
            or self.host_port < 1
            or self.host_port > 65_535
        ):
            raise _malformed("host_publications")


@dataclass(frozen=True)
class LiteralEndpointMaterial:
    value: str


@dataclass(frozen=True)
class SecretEndpointMaterial:
    reference_id: str


EndpointAddressMaterial: TypeAlias = LiteralEndpointMaterial | SecretEndpointMaterial


@dataclass(frozen=True)
class EndpointMaterial:
    socket_name: str
    protocol: Protocol
    scope: EndpointScope
    address: EndpointAddressMaterial


@dataclass(frozen=True)
class ImplementationMaterial:
    """Closed provider-neutral process material retained from a graph node."""

    kind: str
    image: str | None = None
    command: tuple[str, ...] = ()
    environment: tuple[EnvironmentBindingMaterial, ...] = ()
    database: str | None = None
    data_mounts: tuple[DataMountMaterial, ...] = ()
    host_publications: tuple[HostPublicationMaterial, ...] = ()
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    secret_files: tuple[SecretFileMaterial, ...] = ()


@dataclass(frozen=True)
class RuntimeMaterial:
    runtime_id: str
    kind: RuntimeKind
    children: tuple[str, ...]
    network_name: str | None
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL


@dataclass(frozen=True)
class NodeMaterial:
    node_id: str
    runtime: RuntimeMaterial
    implementation: ImplementationMaterial
    endpoints: tuple[EndpointMaterial, ...]
    environment: tuple[EnvironmentBindingMaterial, ...]
    health_path: str | None
    lifecycle: ResourceLifecycle = OWNED_EPHEMERAL
    verification: VerificationContract = field(default_factory=VerificationContract)

    def __post_init__(self) -> None:
        if not isinstance(self.verification, VerificationContract):
            raise TypeError("node material verification must be VerificationContract")


@dataclass(frozen=True)
class VerificationCheckMaterial:
    """One semantic check paired with its graph-pinned provider endpoint."""

    node_id: str
    graph_id: str
    check: VerificationCheck
    endpoint: EndpointMaterial

    def __post_init__(self) -> None:
        VerificationContract((self.check,))
        if not self.node_id.strip() or not self.graph_id.strip():
            raise EffectMaterializationError(
                MaterializationCode.INVALID_VERIFICATION_TARGET,
                "verification material node and graph identities must not be empty",
            )
        if self.endpoint.socket_name != self.check.provider_socket:
            raise EffectMaterializationError(
                MaterializationCode.INVALID_VERIFICATION_TARGET,
                "verification endpoint does not match its declared provider socket",
            )
        if self.endpoint.protocol not in expected_protocols(self.check):
            raise EffectMaterializationError(
                MaterializationCode.INVALID_VERIFICATION_TARGET,
                "verification endpoint protocol is incompatible with its check",
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "type": "verification-check",
            "node_id": self.node_id,
            "graph_id": self.graph_id,
            "check": self.check.descriptor(),
            "endpoint": _descriptor(self.endpoint),
        }


@dataclass(frozen=True)
class SocketConnectionMaterial:
    edge_id: str
    protocol: Protocol
    provider_node_id: str
    provider_socket: str
    provider_endpoint: EndpointMaterial
    consumer_node_id: str
    requirement_socket: str
    environment: tuple[EnvironmentBindingMaterial, ...]


EffectMaterial: TypeAlias = NodeMaterial | RuntimeMaterial | SocketConnectionMaterial


@dataclass(frozen=True)
class MaterializedEffectRequest:
    """An abstract request paired with immutable, graph-pinned adapter input."""

    request: EffectRequest
    graphs: PinnedGraphSet
    material_graph_id: str
    material: EffectMaterial

    def __post_init__(self) -> None:
        if not isinstance(self.request, EffectRequest):
            raise TypeError("materialized effect requires EffectRequest")
        if not isinstance(self.graphs, PinnedGraphSet):
            raise TypeError("materialized effect requires PinnedGraphSet")
        if self.material_graph_id not in (
            self.graphs.base_graph_id,
            self.graphs.desired_graph_id,
        ):
            raise EffectMaterializationError(
                MaterializationCode.GRAPH_IDENTITY,
                "material graph identity is not pinned by the approved plan",
            )
        if not isinstance(
            self.material,
            (NodeMaterial, RuntimeMaterial, SocketConnectionMaterial),
        ):
            raise TypeError("materialized effect requires typed effect material")

    @property
    def identity(self):
        return self.request.identity

    @property
    def action(self):
        return self.request.action

    @property
    def timeout(self):
        return self.request.timeout

    @property
    def capability(self):
        return self.request.capability

    @property
    def purpose(self):
        return self.request.purpose

    @property
    def material_secret_references(self) -> tuple[SecretReferenceMaterialValue, ...]:
        references = {
            value.reference_id
            for binding in _all_environment(self.material)
            if isinstance((value := binding.value), SecretReferenceMaterialValue)
        }
        references.update(
            endpoint.address.reference_id
            for endpoint in _all_endpoints(self.material)
            if isinstance(endpoint.address, SecretEndpointMaterial)
        )
        return tuple(SecretReferenceMaterialValue(value) for value in sorted(references))

    def descriptor(self) -> dict[str, object]:
        """Return deterministic execution identity without secret values."""

        return {
            "run_id": self.identity.run_id,
            "activity_id": self.identity.activity_id.value,
            "attempt": self.identity.attempt,
            "purpose": self.purpose.value,
            "plan_id": self.graphs.plan_id,
            "workspace_id": self.graphs.workspace_id,
            "base_graph_id": self.graphs.base_graph_id,
            "desired_graph_id": self.graphs.desired_graph_id,
            "material_graph_id": self.material_graph_id,
            "material": _descriptor(self.material),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.descriptor(), sort_keys=True, separators=(",", ":"))


def materialize_effect_request(
    request: EffectRequest,
    activity: PlannedActivity,
    graphs: PinnedGraphSet,
    *,
    base_graph_id: str,
    base_graph: DeploymentGraph,
    desired_graph_id: str,
    desired_graph: DeploymentGraph,
) -> MaterializedEffectRequest:
    """Interpret one operation against only the graph versions pinned by its plan."""

    if request.purpose is not EffectPurpose.FORWARD:
        raise EffectMaterializationError(
            MaterializationCode.GRAPH_IDENTITY,
            "forward materialization requires a forward effect request",
        )
    if request.action != activity.operation:
        raise EffectMaterializationError(
            MaterializationCode.GRAPH_IDENTITY,
            "effect request action does not match the planned activity",
        )
    if base_graph_id != graphs.base_graph_id or desired_graph_id != graphs.desired_graph_id:
        raise EffectMaterializationError(
            MaterializationCode.GRAPH_IDENTITY,
            "loaded graph identity does not match the approved plan",
        )

    graph_id, graph = _forward_material_graph(
        activity.operation,
        base_graph_id=base_graph_id,
        base_graph=base_graph,
        desired_graph_id=desired_graph_id,
        desired_graph=desired_graph,
    )
    material = _material_for_operation(graph, activity.operation)
    return MaterializedEffectRequest(request, graphs, graph_id, material)


def materialize_compensation_effect_request(
    request: EffectRequest,
    activity: PlannedActivity,
    graphs: PinnedGraphSet,
    *,
    base_graph_id: str,
    base_graph: DeploymentGraph,
    desired_graph_id: str,
    desired_graph: DeploymentGraph,
) -> MaterializedEffectRequest:
    """Materialize the canonical inverse from its explicitly pinned graph."""

    if request.purpose is not EffectPurpose.COMPENSATION:
        raise EffectMaterializationError(
            MaterializationCode.GRAPH_IDENTITY,
            "compensation materialization requires a compensation effect request",
        )
    compensation = activity.compensation
    if not isinstance(compensation, Compensate):
        raise EffectMaterializationError(
            MaterializationCode.UNSUPPORTED_OPERATION,
            "planned activity has no executable compensation",
        )
    if request.action != compensation.operation:
        raise EffectMaterializationError(
            MaterializationCode.GRAPH_IDENTITY,
            "effect request action does not match planned compensation",
        )
    if base_graph_id != graphs.base_graph_id or desired_graph_id != graphs.desired_graph_id:
        raise EffectMaterializationError(
            MaterializationCode.GRAPH_IDENTITY,
            "loaded graph identity does not match the approved plan",
        )
    match compensation.material_source:
        case CompensationMaterialSource.BASE_GRAPH:
            graph_id, graph = base_graph_id, base_graph
        case CompensationMaterialSource.DESIRED_GRAPH:
            graph_id, graph = desired_graph_id, desired_graph
    return MaterializedEffectRequest(
        request,
        graphs,
        graph_id,
        _material_for_operation(graph, compensation.operation),
    )


def _forward_material_graph(
    operation: ActivityOperation,
    *,
    base_graph_id: str,
    base_graph: DeploymentGraph,
    desired_graph_id: str,
    desired_graph: DeploymentGraph,
) -> tuple[str, DeploymentGraph]:
    match operation:
        case StopNode() | RemoveNodeResource() | StopRuntime() | RemoveRuntimeResource() | DestroyDataResource() | RemoveSocketConnection():
            return base_graph_id, base_graph
        case StartNode() | ReconcileNode() | WaitForHealthy() | StartRuntime() | ReconcileRuntime() | AddSocketConnection() | SwitchSocketConnection():
            return desired_graph_id, desired_graph
        case _:
            raise EffectMaterializationError(
                MaterializationCode.UNSUPPORTED_OPERATION,
                f"operation {type(operation).__name__} has no effect materializer",
            )


def _material_for_operation(
    graph: DeploymentGraph,
    operation: ActivityOperation,
) -> EffectMaterial:
    match operation:
        case StartNode(target=target) | StopNode(target=target) | RemoveNodeResource(target=target) | ReconcileNode(target=target) | WaitForHealthy(target=target):
            return _node_material(graph, target.node_id)
        case StartRuntime(target=target) | StopRuntime(target=target) | RemoveRuntimeResource(target=target) | ReconcileRuntime(target=target):
            return _runtime_material(graph, target.runtime_id)
        case DestroyDataResource(target=target):
            material = _node_material(graph, target.node_id)
            try:
                material.lifecycle.data_resource(target.resource_id)
            except KeyError as error:
                raise EffectMaterializationError(
                    MaterializationCode.TARGET_NOT_FOUND,
                    "pinned graph has no matching data resource",
                ) from error
            return material
        case AddSocketConnection(target=target) | SwitchSocketConnection(target=target) | RemoveSocketConnection(target=target):
            return _edge_material(graph, target.edge_id)
        case _:
            raise EffectMaterializationError(
                MaterializationCode.UNSUPPORTED_OPERATION,
                f"operation {type(operation).__name__} has no effect materializer",
            )


def _runtime_material(graph: DeploymentGraph, runtime_id: str) -> RuntimeMaterial:
    try:
        runtime = graph.runtimes[runtime_id]
    except KeyError as error:
        raise EffectMaterializationError(
            MaterializationCode.TARGET_NOT_FOUND,
            f"pinned graph has no runtime {runtime_id!r}",
        ) from error
    missing = tuple(child for child in runtime.children if child not in graph.nodes)
    if missing:
        raise EffectMaterializationError(
            MaterializationCode.INVALID_RUNTIME_MEMBERSHIP,
            f"runtime {runtime_id!r} contains missing node identities",
        )
    return RuntimeMaterial(
        runtime.runtime_id,
        runtime.kind,
        tuple(runtime.children),
        runtime.metadata.get("network_name"),
        runtime.lifecycle,
    )


def _node_material(graph: DeploymentGraph, node_id: str) -> NodeMaterial:
    try:
        node = graph.nodes[node_id]
    except KeyError as error:
        raise EffectMaterializationError(
            MaterializationCode.TARGET_NOT_FOUND,
            f"pinned graph has no node {node_id!r}",
        ) from error
    runtime = _runtime_material(graph, node.runtime_id)
    if node_id not in runtime.children:
        raise EffectMaterializationError(
            MaterializationCode.INVALID_RUNTIME_MEMBERSHIP,
            f"node {node_id!r} is not a child of runtime {node.runtime_id!r}",
        )
    return NodeMaterial(
        node.node_id,
        runtime,
        _implementation_material(node, graph),
        tuple(_endpoint_material(name, endpoint) for name, endpoint in sorted(node.endpoints.items())),
        _environment_material(node.environment, node=node, graph=graph),
        node.block_spec.health_path,
        node.lifecycle,
        node.block_spec.verification,
    )


def materialize_verification_contract(
    request: MaterializedEffectRequest,
) -> tuple[VerificationCheckMaterial, ...]:
    """Resolve a contract only against endpoints already pinned in node material."""

    if not isinstance(request, MaterializedEffectRequest):
        raise TypeError("verification materialization requires MaterializedEffectRequest")
    if not isinstance(request.material, NodeMaterial):
        raise TypeError("verification materialization requires node effect material")
    node = request.material
    endpoints = {value.socket_name: value for value in node.endpoints}
    material: list[VerificationCheckMaterial] = []
    for check in node.verification.checks:
        try:
            endpoint = endpoints[check.provider_socket]
        except KeyError as error:
            raise EffectMaterializationError(
                MaterializationCode.INVALID_VERIFICATION_TARGET,
                f"verification check {check.check_id!r} has no pinned provider endpoint",
            ) from error
        material.append(
            VerificationCheckMaterial(
                node.node_id,
                request.material_graph_id,
                check,
                endpoint,
            )
        )
    return tuple(material)


def _edge_material(graph: DeploymentGraph, edge_id: str) -> SocketConnectionMaterial:
    try:
        edge = graph.edges[edge_id]
        provider = graph.node(edge.provider_role)
        consumer = graph.node(edge.consumer_role)
        endpoint = provider.endpoint(edge.provider_socket)
        provider.provider_socket(edge.provider_socket)
        requirement = consumer.requirement_socket(edge.requirement_socket)
    except KeyError as error:
        raise EffectMaterializationError(
            MaterializationCode.TARGET_NOT_FOUND,
            f"pinned graph cannot resolve socket connection {edge_id!r}",
        ) from error
    if endpoint.protocol != edge.protocol or requirement.protocol != edge.protocol:
        raise EffectMaterializationError(
            MaterializationCode.INVALID_SOCKET_CONNECTION,
            f"socket connection {edge_id!r} has incompatible protocols",
        )
    return SocketConnectionMaterial(
        edge.edge_id,
        edge.protocol,
        provider.node_id,
        edge.provider_socket,
        _endpoint_material(edge.provider_socket, endpoint),
        consumer.node_id,
        edge.requirement_socket,
        _environment_material(edge.env_assignments, endpoint=endpoint),
    )


def _implementation_material(node: Node, graph: DeploymentGraph) -> ImplementationMaterial:
    metadata = node.metadata
    image = _optional_text(metadata.get("image"), "image")
    database = _optional_text(metadata.get("database"), "database")
    command_value = metadata.get("command", ())
    if not isinstance(command_value, (list, tuple)) or not all(isinstance(value, str) for value in command_value):
        raise _malformed("command")
    static_environment = metadata.get("environment", {})
    if not isinstance(static_environment, Mapping) or not all(
        isinstance(key, str) for key in static_environment
    ):
        raise _malformed("environment")
    environment = {**dict(static_environment), **dict(node.environment)}
    mounts_value = metadata.get("data_mounts", ())
    if not isinstance(mounts_value, (list, tuple)):
        raise _malformed("data_mounts")
    mounts: list[DataMountMaterial] = []
    for value in mounts_value:
        if not isinstance(value, Mapping):
            raise _malformed("data_mounts")
        resource_id = value.get("resource_id")
        target_path = value.get("target_path")
        if not isinstance(resource_id, str) or not isinstance(target_path, str):
            raise _malformed("data_mounts")
        try:
            node.lifecycle.data_resource(resource_id)
        except KeyError as error:
            raise EffectMaterializationError(
                MaterializationCode.MALFORMED_IMPLEMENTATION,
                "data mount references an undeclared lifecycle resource",
            ) from error
        mounts.append(DataMountMaterial(resource_id, target_path))
    if len({mount.resource_id for mount in mounts}) != len(mounts):
        raise _malformed("data_mounts")
    publications_value = metadata.get("host_publications", ())
    if not isinstance(publications_value, (list, tuple)):
        raise _malformed("host_publications")
    publications: list[HostPublicationMaterial] = []
    for value in publications_value:
        if not isinstance(value, Mapping):
            raise _malformed("host_publications")
        socket_name = value.get("socket_name")
        bind_address = value.get("bind_address")
        host_port = value.get("host_port")
        if not isinstance(socket_name, str) or not isinstance(bind_address, str):
            raise _malformed("host_publications")
        try:
            endpoint = node.endpoint(socket_name)
            parsed = urlsplit(endpoint.url)
            container_port = parsed.port
            address = ip_address(bind_address)
        except (KeyError, ValueError) as error:
            raise _malformed("host_publications") from error
        if container_port is None:
            raise _malformed("host_publications")
        publications.append(
            HostPublicationMaterial(
                socket_name,
                endpoint.protocol,
                container_port,
                address,
                host_port,
            )
        )
    if len({value.socket_name for value in publications}) != len(publications):
        raise _malformed("host_publications")
    fixed_bindings = {
        (value.bind_address, value.host_port, value.protocol.transport)
        for value in publications
        if value.host_port is not None
    }
    if len(fixed_bindings) != len(
        [value for value in publications if value.host_port is not None]
    ):
        raise _malformed("host_publications")
    environment = list(_environment_material(environment, node=node, graph=graph))
    secret_files: list[SecretFileMaterial] = []
    for delivery in node.secret_deliveries:
        match delivery:
            case SecretEnvironmentDelivery(environment_name=name, reference=reference):
                environment.append(
                    EnvironmentBindingMaterial(
                        name,
                        SecretReferenceMaterialValue(reference.reference_id),
                    )
                )
            case SecretFileDelivery(
                target_path=target_path,
                reference=reference,
                file_mode=file_mode,
            ):
                secret_files.append(
                    SecretFileMaterial(reference.reference_id, target_path, file_mode)
                )
    if len({value.name for value in environment}) != len(environment):
        raise _malformed("environment")
    return ImplementationMaterial(
        node.kind,
        image,
        tuple(command_value),
        tuple(sorted(environment)),
        database,
        tuple(sorted(mounts)),
        tuple(sorted(publications, key=lambda value: value.socket_name)),
        tuple(sorted(node.configuration_artifacts)),
        tuple(sorted(secret_files)),
    )


def _environment_material(
    values: Mapping[str, object],
    *,
    node: Node | None = None,
    graph: DeploymentGraph | None = None,
    endpoint: Endpoint | None = None,
) -> tuple[EnvironmentBindingMaterial, ...]:
    bindings: list[EnvironmentBindingMaterial] = []
    for name, literal in sorted(values.items()):
        explicit_reference = _secret_reference(literal)
        source = endpoint or _environment_endpoint(name, node, graph)
        if explicit_reference is not None:
            value: EnvironmentMaterialValue = SecretReferenceMaterialValue(
                explicit_reference
            )
        elif source is not None and isinstance(source.address, SecretReferenceAddress):
            value: EnvironmentMaterialValue = SecretReferenceMaterialValue(source.address.secret_ref)
        else:
            if not isinstance(literal, str):
                raise _malformed("environment")
            if any(marker in name.lower() for marker in _SECRET_MARKERS):
                raise EffectMaterializationError(
                    MaterializationCode.SECRET_VALUE,
                    f"environment field {name!r} must use an opaque secret reference",
                )
            value = LiteralMaterialValue(literal)
        bindings.append(EnvironmentBindingMaterial(name, value))
    return tuple(bindings)


def _secret_reference(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if set(value) != {"kind", "reference_id"} or value.get("kind") != "secret-reference":
        raise _malformed("environment")
    reference_id = value.get("reference_id")
    if not isinstance(reference_id, str) or not reference_id.strip():
        raise _malformed("environment")
    return reference_id


def _environment_endpoint(name: str, node: Node | None, graph: DeploymentGraph | None) -> Endpoint | None:
    if node is None or graph is None:
        return None
    for edge in graph.edges.values():
        if edge.consumer_role == node.node_id and name in edge.env_assignments:
            return graph.node(edge.provider_role).endpoint(edge.provider_socket)
    return None


def _endpoint_material(name: str, endpoint: Endpoint) -> EndpointMaterial:
    match endpoint.address:
        case LiteralAddress(value=value):
            address: EndpointAddressMaterial = LiteralEndpointMaterial(value)
        case SecretReferenceAddress(secret_ref=reference):
            address = SecretEndpointMaterial(reference)
    return EndpointMaterial(name, endpoint.protocol, endpoint.scope, address)


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _malformed(name)
    return value


def _malformed(name: str) -> EffectMaterializationError:
    return EffectMaterializationError(
        MaterializationCode.MALFORMED_IMPLEMENTATION,
        f"implementation field {name!r} has an invalid shape",
    )


def _all_environment(material: EffectMaterial) -> tuple[EnvironmentBindingMaterial, ...]:
    match material:
        case NodeMaterial():
            return material.implementation.environment
        case SocketConnectionMaterial():
            return material.environment
        case RuntimeMaterial():
            return ()


def _all_endpoints(material: EffectMaterial) -> tuple[EndpointMaterial, ...]:
    match material:
        case NodeMaterial():
            return material.endpoints
        case SocketConnectionMaterial():
            return (material.provider_endpoint,)
        case RuntimeMaterial():
            return ()


def _descriptor(value: object) -> object:
    match value:
        case RuntimeMaterial():
            return {
                "type": "runtime",
                "runtime_id": value.runtime_id,
                "kind": value.kind.value,
                "children": list(value.children),
                "network_name": value.network_name,
                "lifecycle": value.lifecycle.descriptor(),
            }
        case NodeMaterial():
            return {
                "type": "node",
                "node_id": value.node_id,
                "runtime": _descriptor(value.runtime),
                "implementation": _descriptor(value.implementation),
                "endpoints": [_descriptor(item) for item in value.endpoints],
                "environment": [_descriptor(item) for item in value.environment],
                "health_path": value.health_path,
                "lifecycle": value.lifecycle.descriptor(),
                "verification": value.verification.descriptor(),
            }
        case VerificationCheckMaterial():
            return value.descriptor()
        case ImplementationMaterial():
            return {
                "kind": value.kind,
                "image": value.image,
                "command": list(value.command),
                "environment": [_descriptor(item) for item in value.environment],
                "database": value.database,
                "data_mounts": [_descriptor(item) for item in value.data_mounts],
                "host_publications": [
                    _descriptor(item) for item in value.host_publications
                ],
                "configuration_artifacts": [
                    _descriptor(item) for item in value.configuration_artifacts
                ],
                "secret_files": [_descriptor(item) for item in value.secret_files],
            }
        case ConfigurationArtifact():
            return value.descriptor()
        case DataMountMaterial():
            return {
                "resource_id": value.resource_id,
                "target_path": value.target_path,
            }
        case SecretFileMaterial():
            return {
                "reference_id": value.reference_id,
                "target_path": value.target_path,
                "file_mode": value.file_mode.value,
            }
        case HostPublicationMaterial():
            return {
                "socket_name": value.socket_name,
                "protocol": value.protocol.descriptor(),
                "container_port": value.container_port,
                "bind_address": str(value.bind_address),
                "host_port": value.host_port,
            }
        case SocketConnectionMaterial():
            return {
                "type": "socket-connection",
                "edge_id": value.edge_id,
                "protocol": value.protocol.descriptor(),
                "provider_node_id": value.provider_node_id,
                "provider_socket": value.provider_socket,
                "provider_endpoint": _descriptor(value.provider_endpoint),
                "consumer_node_id": value.consumer_node_id,
                "requirement_socket": value.requirement_socket,
                "environment": [_descriptor(item) for item in value.environment],
            }
        case EndpointMaterial():
            return {
                "socket_name": value.socket_name,
                "protocol": value.protocol.descriptor(),
                "scope": value.scope.value,
                "address": _descriptor(value.address),
            }
        case LiteralEndpointMaterial():
            return {"kind": "literal", "value": value.value}
        case SecretEndpointMaterial():
            return {"kind": "secret-reference", "reference_id": value.reference_id}
        case EnvironmentBindingMaterial():
            return {"name": value.name, "value": _descriptor(value.value)}
        case LiteralMaterialValue():
            return {"kind": "literal", "value": "<redacted>"}
        case SecretReferenceMaterialValue():
            return {"kind": "secret-reference", "reference_id": value.reference_id}
    raise TypeError(f"unsupported effect material descriptor {value!r}")
