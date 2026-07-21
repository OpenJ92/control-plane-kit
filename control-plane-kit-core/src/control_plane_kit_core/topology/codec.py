"""Authoritative typed codec for durable deployment-graph descriptors."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol as TypingProtocol

from control_plane_kit_core.algebra import (
    BlockSockets,
    BlockSpec,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.configuration import (
    ConfigurationArtifact,
    ConfigurationArtifactError,
)
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
    environment_binding_from_descriptor,
)
from control_plane_kit_core.secrets import (
    SecretDelivery,
    SecretResolutionError,
    secret_delivery_from_descriptor,
)
from control_plane_kit_core.lifecycle import (
    DataResourceSpec,
    ResourceLifecycle,
    ResourceOwnership,
    ResourcePersistence,
)
from control_plane_kit_core.topology.graph import (
    DeploymentGraph,
    Edge,
    Endpoint,
    LiteralAddress,
    Node,
    RuntimeRecord,
    SecretReferenceAddress,
)
from control_plane_kit_core.types import (
    BlockFamily,
    EndpointScope,
    Protocol,
    RuntimeKind,
    SocketBinding,
)
from control_plane_kit_core.verification import VerificationContract


class GraphDescriptorError(ValueError):
    """Base error for graph descriptor boundaries."""


class MalformedGraphDescriptor(GraphDescriptorError):
    """Raised when descriptor data has the wrong shape or primitive value."""


class UnknownGraphVariant(GraphDescriptorError):
    """Raised when a closed descriptor variant is unknown to this codec."""


class InvalidGraphReference(GraphDescriptorError):
    """Raised when typed graph members refer to absent or incompatible members."""


class LossyGraphDescriptor(GraphDescriptorError):
    """Raised when decoding and encoding cannot preserve descriptor identity."""


class BlockSpecVariantCodec(TypingProtocol):
    """Extension point for package-defined BlockSpec subclasses."""

    variant: str
    spec_type: type[BlockSpec]

    def encode(self, spec: BlockSpec) -> Mapping[str, object]: ...

    def decode(self, descriptor: Mapping[str, object]) -> BlockSpec: ...


class GenericBlockSpecCodec:
    """Codec for the package's base BlockSpec product."""

    variant = "block"
    spec_type = BlockSpec

    def encode(self, spec: BlockSpec) -> Mapping[str, object]:
        return {
            "variant": self.variant,
            "role_id": spec.role_id,
            "display_name": spec.display_name,
            "health_path": spec.health_path,
            "capabilities": [value.value for value in spec.capabilities],
            "verification": spec.verification.descriptor(),
            "metadata": dict(sorted(spec.metadata.items())),
        }

    def decode(self, descriptor: Mapping[str, object]) -> BlockSpec:
        try:
            capabilities = tuple(
                CapabilityName(str(value)) for value in _list(descriptor.get("capabilities", []))
            )
        except ValueError as error:
            raise UnknownGraphVariant(f"unknown capability: {error}") from error
        return BlockSpec(
            role_id=_text(descriptor, "role_id"),
            display_name=_optional_text(descriptor, "display_name"),
            health_path=_optional_text(descriptor, "health_path"),
            capabilities=capabilities,
            verification=VerificationContract.from_descriptor(
                descriptor.get("verification")
            ),
            metadata=_string_mapping(descriptor.get("metadata", {}), "block_spec.metadata"),
        )


class GraphDescriptorCodec:
    """Encode and decode one deterministic, typed graph descriptor language."""

    def __init__(
        self,
        spec_codecs: Iterable[BlockSpecVariantCodec] = (),
    ) -> None:
        codecs = (
            GenericBlockSpecCodec(),
            *tuple(spec_codecs),
        )
        self._by_variant: dict[str, BlockSpecVariantCodec] = {}
        self._by_type: dict[type[BlockSpec], BlockSpecVariantCodec] = {}
        for codec in codecs:
            if codec.variant in self._by_variant:
                raise ValueError(f"duplicate block spec variant {codec.variant!r}")
            if codec.spec_type in self._by_type:
                raise ValueError(f"duplicate block spec type {codec.spec_type.__name__}")
            self._by_variant[codec.variant] = codec
            self._by_type[codec.spec_type] = codec

    def encode(self, graph: DeploymentGraph) -> dict[str, object]:
        if not isinstance(graph, DeploymentGraph):
            raise MalformedGraphDescriptor("encode requires DeploymentGraph")
        descriptor = {
            "name": graph.name,
            "runtimes": {
                key: value.descriptor() for key, value in sorted(graph.runtimes.items())
            },
            "nodes": {
                key: self._encode_node(value) for key, value in sorted(graph.nodes.items())
            },
            "edges": {key: value.descriptor() for key, value in sorted(graph.edges.items())},
        }
        self._validate(graph)
        return descriptor

    def encode_block_spec(self, spec: BlockSpec) -> dict[str, object]:
        """Return the registered closed descriptor for one block specification."""

        try:
            codec = self._by_type[type(spec)]
        except KeyError as error:
            raise UnknownGraphVariant(
                f"unregistered block spec type {type(spec).__name__}"
            ) from error
        return dict(codec.encode(spec))

    def supports_same_block_specs_as(self, other: GraphDescriptorCodec) -> bool:
        """Return whether two codecs admit the same closed spec variants and types."""

        return {
            variant: (codec.spec_type, type(codec))
            for variant, codec in self._by_variant.items()
        } == {
            variant: (codec.spec_type, type(codec))
            for variant, codec in other._by_variant.items()
        }

    def decode(self, descriptor: Mapping[str, object]) -> DeploymentGraph:
        top = _mapping(descriptor, "graph")
        graph = DeploymentGraph(_text(top, "name"))
        for runtime_id, value in sorted(_mapping(top.get("runtimes", {}), "runtimes").items()):
            graph = graph.add_runtime(self._decode_runtime(str(runtime_id), _mapping(value, "runtime")))
        for node_id, value in sorted(_mapping(top.get("nodes", {}), "nodes").items()):
            graph = graph.add_node(self._decode_node(str(node_id), _mapping(value, "node")))
        for edge_id, value in sorted(_mapping(top.get("edges", {}), "edges").items()):
            graph = graph.add_edge(self._decode_edge(str(edge_id), _mapping(value, "edge")))
        self._validate(graph)
        encoded = self.encode(graph)
        if encoded != _json_value(descriptor):
            raise LossyGraphDescriptor("descriptor does not round-trip through the typed graph codec")
        return graph

    def _encode_node(self, node: Node) -> dict[str, object]:
        descriptor = node.descriptor()
        descriptor["block_spec"] = self.encode_block_spec(node.block_spec)
        return descriptor

    def _decode_runtime(self, runtime_id: str, descriptor: Mapping[str, object]) -> RuntimeRecord:
        try:
            kind = RuntimeKind(_text(descriptor, "kind"))
        except ValueError as error:
            raise UnknownGraphVariant(f"unknown runtime kind: {error}") from error
        return RuntimeRecord(
            runtime_id=runtime_id,
            kind=kind,
            children=tuple(str(child) for child in _list(descriptor.get("children", []))),
            metadata=_string_mapping(descriptor.get("metadata", {}), "runtime.metadata"),
            lifecycle=_lifecycle(descriptor.get("lifecycle"), "runtime.lifecycle"),
        )

    def _decode_node(self, node_id: str, descriptor: Mapping[str, object]) -> Node:
        try:
            family = BlockFamily(_text(descriptor, "block_family"))
        except ValueError as error:
            raise UnknownGraphVariant(f"unknown block family: {error}") from error
        spec_descriptor = _mapping(descriptor.get("block_spec"), "block_spec")
        variant = _text(spec_descriptor, "variant")
        try:
            spec_codec = self._by_variant[variant]
        except KeyError as error:
            raise UnknownGraphVariant(f"unknown block spec variant {variant!r}") from error
        spec = spec_codec.decode(spec_descriptor)
        if spec.role_id != node_id:
            raise InvalidGraphReference(
                f"node key {node_id!r} does not match block spec role {spec.role_id!r}"
            )
        requirements = tuple(
            RequirementSocket(
                name=str(name),
                protocol=_protocol(value, "requirement"),
                env_bindings=tuple(
                    str(binding)
                    for binding in _list(_mapping(value, "requirement").get("env_bindings", []))
                ),
                required=_boolean(_mapping(value, "requirement"), "required", default=True),
                binding=_socket_binding(value),
            )
            for name, value in sorted(
                _mapping(descriptor.get("requirements", {}), "requirements").items()
            )
        )
        providers = tuple(
            ProviderSocket(name=str(name), protocol=_protocol(value, "provider"))
            for name, value in sorted(
                _mapping(descriptor.get("providers", {}), "providers").items()
            )
        )
        endpoints = {
            str(name): Endpoint(
                address=_endpoint_address(value),
                protocol=_protocol(value, "endpoint"),
                scope=_endpoint_scope(value),
            )
            for name, value in sorted(
                _mapping(descriptor.get("endpoints", {}), "endpoints").items()
            )
        }
        environment_bindings = _environment_bindings(
            descriptor.get("environment_bindings", [])
        )
        return Node(
            node_id=node_id,
            block_family=family,
            block_spec=spec,
            kind=_text(descriptor, "kind"),
            runtime_id=_text(descriptor, "runtime_id"),
            sockets=BlockSockets(requirements=requirements, providers=providers),
            endpoints=endpoints,
            public_environment=tuple(
                value
                for value in environment_bindings
                if isinstance(value, PublicStaticEnvironmentBinding)
            ),
            socket_environment=tuple(
                value
                for value in environment_bindings
                if isinstance(value, SocketDerivedEnvironmentBinding)
            ),
            metadata=_object_mapping(descriptor.get("metadata", {}), "node.metadata"),
            lifecycle=_lifecycle(descriptor.get("lifecycle"), "node.lifecycle"),
            configuration_artifacts=_configuration_artifacts(
                descriptor.get("configuration_artifacts", [])
            ),
            secret_deliveries=_secret_deliveries(
                descriptor.get("secret_deliveries", [])
            ),
        )

    def _decode_edge(self, edge_id: str, descriptor: Mapping[str, object]) -> Edge:
        provider = _mapping(descriptor.get("provider"), "edge.provider")
        consumer = _mapping(descriptor.get("consumer"), "edge.consumer")
        return Edge(
            edge_id=edge_id,
            provider_role=_text(provider, "role"),
            provider_socket=_text(provider, "socket"),
            consumer_role=_text(consumer, "role"),
            requirement_socket=_text(consumer, "requirement"),
            protocol=_protocol(descriptor, "edge"),
            binding=_edge_socket_binding(descriptor),
            env_assignments=_string_mapping(
                descriptor.get("env_assignments", {}), "edge.env_assignments"
            ),
        )

    def _validate(self, graph: DeploymentGraph) -> None:
        if not graph.name.strip():
            raise MalformedGraphDescriptor("graph name must not be empty")
        child_owners: dict[str, str] = {}
        for runtime_id, runtime in graph.runtimes.items():
            if runtime_id != runtime.runtime_id:
                raise InvalidGraphReference("runtime map key does not match runtime_id")
            for child in runtime.children:
                if child not in graph.nodes:
                    raise InvalidGraphReference(
                        f"runtime {runtime_id!r} references missing node {child!r}"
                    )
                if child in child_owners:
                    raise InvalidGraphReference(f"node {child!r} belongs to multiple runtimes")
                child_owners[child] = runtime_id
        for node_id, node in graph.nodes.items():
            if node_id != node.node_id or node.block_spec.role_id != node_id:
                raise InvalidGraphReference(f"node identity mismatch for {node_id!r}")
            if node.runtime_id not in graph.runtimes:
                raise InvalidGraphReference(
                    f"node {node_id!r} references missing runtime {node.runtime_id!r}"
                )
            if child_owners.get(node_id) != node.runtime_id:
                raise InvalidGraphReference(
                    f"node {node_id!r} is not owned by runtime {node.runtime_id!r}"
                )
            for name, endpoint in node.endpoints.items():
                provider = node.provider_socket(name)
                if provider.protocol != endpoint.protocol:
                    raise InvalidGraphReference(
                        f"endpoint {node_id}.{name} protocol does not match provider"
                    )
        for edge_id, edge in graph.edges.items():
            if edge_id != edge.edge_id:
                raise InvalidGraphReference(f"edge key does not match edge_id {edge_id!r}")
            try:
                provider = graph.node(edge.provider_role).provider_socket(edge.provider_socket)
                requirement = graph.node(edge.consumer_role).requirement_socket(
                    edge.requirement_socket
                )
            except KeyError as error:
                raise InvalidGraphReference(str(error)) from error
            if provider.protocol != edge.protocol or requirement.protocol != edge.protocol:
                raise InvalidGraphReference(f"edge {edge_id!r} has incompatible protocol")


DEFAULT_GRAPH_CODEC = GraphDescriptorCodec()


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MalformedGraphDescriptor(f"{path} must be a mapping")
    return value


def _object_mapping(value: object, path: str) -> dict[str, object]:
    return {str(key): child for key, child in _mapping(value, path).items()}


def _string_mapping(value: object, path: str) -> dict[str, str]:
    result = _object_mapping(value, path)
    if not all(isinstance(child, str) for child in result.values()):
        raise MalformedGraphDescriptor(f"{path} values must be strings")
    return {key: str(child) for key, child in result.items()}


def _list(value: object) -> list[object]:
    if not isinstance(value, (list, tuple)):
        raise MalformedGraphDescriptor("expected a list")
    return list(value)


def _text(descriptor: Mapping[str, object], key: str) -> str:
    value = descriptor.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MalformedGraphDescriptor(f"{key} must be a non-empty string")
    return value


def _optional_text(descriptor: Mapping[str, object], key: str) -> str | None:
    value = descriptor.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedGraphDescriptor(f"{key} must be a string or null")
    return value


def _boolean(descriptor: Mapping[str, object], key: str, *, default: bool) -> bool:
    value = descriptor.get(key, default)
    if not isinstance(value, bool):
        raise MalformedGraphDescriptor(f"{key} must be boolean")
    return value


def _protocol(value: object, path: str) -> Protocol:
    descriptor = _mapping(value, path)
    try:
        return Protocol.from_descriptor(
            _mapping(descriptor.get("protocol"), f"{path}.protocol")
        )
    except ValueError as error:
        raise UnknownGraphVariant(f"unknown protocol: {error}") from error


def _socket_binding(value: object) -> SocketBinding:
    descriptor = _mapping(value, "requirement")
    try:
        return SocketBinding(
            str(descriptor.get("binding", SocketBinding.ENVIRONMENT.value))
        )
    except ValueError as error:
        raise UnknownGraphVariant(f"unknown socket binding: {error}") from error


def _edge_socket_binding(value: object) -> SocketBinding:
    descriptor = _mapping(value, "edge")
    try:
        return SocketBinding(_text(descriptor, "binding"))
    except ValueError as error:
        raise UnknownGraphVariant(f"unknown edge socket binding: {error}") from error


def _endpoint_scope(value: object) -> EndpointScope:
    descriptor = _mapping(value, "endpoint")
    try:
        return EndpointScope(str(descriptor.get("scope", EndpointScope.PRIVATE.value)))
    except ValueError as error:
        raise UnknownGraphVariant(f"unknown endpoint scope: {error}") from error


def _lifecycle(value: object, path: str) -> ResourceLifecycle:
    descriptor = _mapping(value, path)
    try:
        ownership = ResourceOwnership(_text(descriptor, "ownership"))
        compute = ResourcePersistence(_text(descriptor, "compute"))
        data = tuple(
            DataResourceSpec(
                _text(resource_descriptor, "resource_id"),
                ResourcePersistence(_text(resource_descriptor, "persistence")),
            )
            for resource_descriptor in (
                _mapping(resource, f"{path}.data")
                for resource in _list(descriptor.get("data", []))
            )
        )
    except ValueError as error:
        raise UnknownGraphVariant(f"unknown resource lifecycle variant: {error}") from error
    return ResourceLifecycle(ownership, compute, data)


def _endpoint_address(value: object) -> LiteralAddress | SecretReferenceAddress:
    endpoint = _mapping(value, "endpoint")
    address = _mapping(endpoint.get("address"), "endpoint.address")
    match _text(address, "kind"):
        case "literal":
            try:
                return LiteralAddress(_text(address, "value"))
            except ValueError as error:
                raise MalformedGraphDescriptor(str(error)) from error
        case "secret-reference":
            try:
                return SecretReferenceAddress(_text(address, "secret_ref"))
            except ValueError as error:
                raise MalformedGraphDescriptor(str(error)) from error
        case unknown:
            raise UnknownGraphVariant(f"unknown endpoint address variant {unknown!r}")


def _configuration_artifacts(value: object) -> tuple[ConfigurationArtifact, ...]:
    try:
        return tuple(
            ConfigurationArtifact.from_descriptor(
                _mapping(item, "configuration_artifact")
            )
            for item in _list(value)
        )
    except ConfigurationArtifactError as error:
        raise MalformedGraphDescriptor(str(error)) from error


def _secret_deliveries(value: object) -> tuple[SecretDelivery, ...]:
    try:
        return tuple(
            secret_delivery_from_descriptor(_mapping(item, "secret_delivery"))
            for item in _list(value)
        )
    except SecretResolutionError as error:
        raise MalformedGraphDescriptor(str(error)) from error


def _environment_bindings(
    value: object,
) -> tuple[PublicStaticEnvironmentBinding | SocketDerivedEnvironmentBinding, ...]:
    try:
        return tuple(
            environment_binding_from_descriptor(_mapping(item, "environment_binding"))
            for item in _list(value)
        )
    except ValueError as error:
        raise UnknownGraphVariant(str(error)) from error


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value
