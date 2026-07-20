"""Pure typed validation for compiled deployment topology."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from control_plane_kit.core.algebra import PackageServerSpec, ProductMaturity
from control_plane_kit.core.capabilities import capability_named
from control_plane_kit.core.control_routes import route_set_named
from control_plane_kit.core.topology.graph import DeploymentGraph
from control_plane_kit.core.topology.codec import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorCodec,
    GraphDescriptorError,
)
from control_plane_kit.core.verification import expected_protocols


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationCode(StrEnum):
    INVALID_DESCRIPTOR = "invalid-descriptor"
    MISSING_RUNTIME = "missing-runtime"
    RUNTIME_OWNERSHIP = "runtime-ownership"
    MISSING_PROVIDER_ENDPOINT = "missing-provider-endpoint"
    UNDECLARED_PROVIDER_ENDPOINT = "undeclared-provider-endpoint"
    ENDPOINT_PROTOCOL = "endpoint-protocol"
    MISSING_REQUIRED_CONNECTION = "missing-required-connection"
    UNCONNECTED_OPTIONAL_SOCKET = "unconnected-optional-socket"
    MULTIPLE_REQUIREMENT_CONNECTIONS = "multiple-requirement-connections"
    EDGE_REFERENCE = "edge-reference"
    EDGE_PROTOCOL = "edge-protocol"
    EDGE_BINDING = "edge-binding"
    CONTROL_ROUTE_SET = "control-route-set"
    DUPLICATE_PROVIDER_SOCKET = "duplicate-provider-socket"
    DUPLICATE_REQUIREMENT_SOCKET = "duplicate-requirement-socket"
    EDGE_ENV_BINDINGS = "edge-env-bindings"
    CONSUMER_ENVIRONMENT = "consumer-environment"
    VERIFICATION_PROVIDER = "verification-provider"
    VERIFICATION_PROTOCOL = "verification-protocol"
    PACKAGE_MATURITY = "package-maturity"
    SELF_CONNECTION = "self-connection"


class SocketDirection(StrEnum):
    PROVIDER = "provider"
    REQUIREMENT = "requirement"


@dataclass(frozen=True)
class GraphSubject:
    def descriptor(self) -> dict[str, str]:
        return {"kind": "graph"}


@dataclass(frozen=True)
class RuntimeSubject:
    runtime_id: str

    def descriptor(self) -> dict[str, str]:
        return {"kind": "runtime", "runtime_id": self.runtime_id}


@dataclass(frozen=True)
class NodeSubject:
    node_id: str

    def descriptor(self) -> dict[str, str]:
        return {"kind": "node", "node_id": self.node_id}


@dataclass(frozen=True)
class EdgeSubject:
    edge_id: str

    def descriptor(self) -> dict[str, str]:
        return {"kind": "edge", "edge_id": self.edge_id}


@dataclass(frozen=True)
class SocketSubject:
    node_id: str
    socket_name: str
    direction: SocketDirection

    def descriptor(self) -> dict[str, str]:
        return {
            "kind": "socket",
            "node_id": self.node_id,
            "socket_name": self.socket_name,
            "direction": self.direction.value,
        }


ValidationSubject = GraphSubject | RuntimeSubject | NodeSubject | EdgeSubject | SocketSubject


@dataclass(frozen=True)
class ValidationFinding:
    code: ValidationCode
    severity: ValidationSeverity
    subject: ValidationSubject
    message: str

    def descriptor(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "subject": self.subject.descriptor(),
            "message": self.message,
        }


@dataclass(frozen=True)
class ValidatedGraph:
    graph: DeploymentGraph
    findings: tuple[ValidationFinding, ...]
    codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC

    @property
    def valid(self) -> bool:
        return not any(
            finding.severity is ValidationSeverity.ERROR for finding in self.findings
        )

    @property
    def errors(self) -> tuple[ValidationFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity is ValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[ValidationFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity is ValidationSeverity.WARNING
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "graph_name": self.graph.name,
            "valid": self.valid,
            "findings": [finding.descriptor() for finding in self.findings],
        }

    def require_valid(self) -> DeploymentGraph:
        if not self.valid:
            raise GraphValidationError(self)
        return self.graph


class GraphValidationError(ValueError):
    def __init__(self, result: ValidatedGraph) -> None:
        self.result = result
        super().__init__(f"graph {result.graph.name!r} has {len(result.errors)} validation errors")


@dataclass(frozen=True)
class GraphValidationPolicy:
    """Closed package-maturity policy applied at pure graph validation."""

    allowed_package_maturities: tuple[ProductMaturity, ...] = tuple(ProductMaturity)

    def __post_init__(self) -> None:
        if not self.allowed_package_maturities:
            raise ValueError("graph validation policy must allow at least one maturity")
        if any(
            not isinstance(value, ProductMaturity)
            for value in self.allowed_package_maturities
        ):
            raise TypeError("allowed package maturities must be typed")
        if len(set(self.allowed_package_maturities)) != len(
            self.allowed_package_maturities
        ):
            raise ValueError("allowed package maturities must be unique")

    @classmethod
    def production(cls) -> "GraphValidationPolicy":
        return cls((ProductMaturity.OPERATIONAL,))


def validate_graph(
    graph: DeploymentGraph,
    *,
    codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
    policy: GraphValidationPolicy = GraphValidationPolicy(),
) -> ValidatedGraph:
    """Return deterministic findings without mutating topology or external state."""

    findings: list[ValidationFinding] = []
    owners: dict[str, list[str]] = {}
    for runtime_id, runtime in sorted(graph.runtimes.items()):
        for child in runtime.children:
            owners.setdefault(child, []).append(runtime_id)
            if child not in graph.nodes:
                findings.append(
                    _error(
                        ValidationCode.RUNTIME_OWNERSHIP,
                        RuntimeSubject(runtime_id),
                        f"runtime references missing node {child!r}",
                    )
                )

    requirement_edges: dict[tuple[str, str], list[str]] = {}
    for edge_id, edge in sorted(graph.edges.items()):
        requirement_edges.setdefault(
            (edge.consumer_role, edge.requirement_socket), []
        ).append(edge_id)
        try:
            provider_node = graph.node(edge.provider_role)
            consumer_node = graph.node(edge.consumer_role)
            provider = provider_node.provider_socket(edge.provider_socket)
            requirement = consumer_node.requirement_socket(
                edge.requirement_socket
            )
        except KeyError as error:
            findings.append(
                _error(ValidationCode.EDGE_REFERENCE, EdgeSubject(edge_id), str(error))
            )
            continue
        if edge.provider_role == edge.consumer_role:
            findings.append(
                _error(
                    ValidationCode.SELF_CONNECTION,
                    EdgeSubject(edge_id),
                    "a node cannot satisfy its own requirement socket",
                )
            )
        if provider.protocol != edge.protocol or requirement.protocol != edge.protocol:
            findings.append(
                _error(
                    ValidationCode.EDGE_PROTOCOL,
                    EdgeSubject(edge_id),
                    "edge protocol does not match both connected sockets",
                )
            )
        if requirement.binding is not edge.binding:
            findings.append(
                _error(
                    ValidationCode.EDGE_BINDING,
                    EdgeSubject(edge_id),
                    "edge binding does not match the consumer requirement contract",
                )
            )
        expected_keys = set(requirement.env_bindings)
        if set(edge.env_assignments) != expected_keys:
            findings.append(
                _error(
                    ValidationCode.EDGE_ENV_BINDINGS,
                    EdgeSubject(edge_id),
                    "edge assignments must exactly cover requirement environment bindings",
                )
            )
        endpoint = provider_node.endpoints.get(provider.name)
        if endpoint is not None and any(
            value != endpoint.url for value in edge.env_assignments.values()
        ):
            findings.append(
                _error(
                    ValidationCode.EDGE_ENV_BINDINGS,
                    EdgeSubject(edge_id),
                    "edge assignment values must equal the provider endpoint address",
                )
            )
        if any(
            {
                binding.name: binding.value
                for binding in consumer_node.socket_environment
            }.get(name) != value
            for name, value in edge.env_assignments.items()
        ):
            findings.append(
                _error(
                    ValidationCode.CONSUMER_ENVIRONMENT,
                    EdgeSubject(edge_id),
                    "consumer environment must contain the edge assignments",
                )
            )

        if any(
            binding.edge_id != edge_id
            for binding in consumer_node.socket_environment
            if binding.name in edge.env_assignments
        ):
            findings.append(
                _error(
                    ValidationCode.CONSUMER_ENVIRONMENT,
                    EdgeSubject(edge_id),
                    "consumer socket environment must retain its producing edge",
                )
            )

    for node_id, node in sorted(graph.nodes.items()):
        for binding in node.socket_environment:
            source = graph.edges.get(binding.edge_id)
            if (
                source is None
                or source.consumer_role != node_id
                or binding.name not in source.env_assignments
                or source.env_assignments[binding.name] != binding.value
            ):
                findings.append(
                    _error(
                        ValidationCode.CONSUMER_ENVIRONMENT,
                        NodeSubject(node_id),
                        "socket environment binding must identify its exact producing edge",
                    )
                )
        if (
            isinstance(node.block_spec, PackageServerSpec)
            and node.block_spec.maturity not in policy.allowed_package_maturities
        ):
            findings.append(
                _error(
                    ValidationCode.PACKAGE_MATURITY,
                    NodeSubject(node_id),
                    f"package maturity {node.block_spec.maturity.value!r} is not allowed by validation policy",
                )
            )
        if node.runtime_id not in graph.runtimes:
            findings.append(
                _error(
                    ValidationCode.MISSING_RUNTIME,
                    NodeSubject(node_id),
                    f"node references missing runtime {node.runtime_id!r}",
                )
            )
        if owners.get(node_id, []) != [node.runtime_id]:
            findings.append(
                _error(
                    ValidationCode.RUNTIME_OWNERSHIP,
                    NodeSubject(node_id),
                    "node must belong to exactly its declared runtime",
                )
            )
        provider_name_counts = Counter(node.sockets.provider_names())
        requirement_name_counts = Counter(node.sockets.requirement_names())
        for socket_name in sorted(
            name for name, count in provider_name_counts.items() if count > 1
        ):
            findings.append(
                _error(
                    ValidationCode.DUPLICATE_PROVIDER_SOCKET,
                    SocketSubject(node_id, socket_name, SocketDirection.PROVIDER),
                    "provider socket name must be unique within its node",
                )
            )
        for socket_name in sorted(
            name for name, count in requirement_name_counts.items() if count > 1
        ):
            findings.append(
                _error(
                    ValidationCode.DUPLICATE_REQUIREMENT_SOCKET,
                    SocketSubject(node_id, socket_name, SocketDirection.REQUIREMENT),
                    "requirement socket name must be unique within its node",
                )
            )
        provider_names = set(provider_name_counts)
        for endpoint_name in sorted(set(node.endpoints) - provider_names):
            findings.append(
                _error(
                    ValidationCode.UNDECLARED_PROVIDER_ENDPOINT,
                    SocketSubject(
                        node_id,
                        endpoint_name,
                        SocketDirection.PROVIDER,
                    ),
                    "compiled endpoint has no declared provider socket",
                )
            )
        for provider in node.sockets.providers:
            endpoint = node.endpoints.get(provider.name)
            subject = SocketSubject(node_id, provider.name, SocketDirection.PROVIDER)
            if endpoint is None:
                findings.append(
                    _error(
                        ValidationCode.MISSING_PROVIDER_ENDPOINT,
                        subject,
                        "provider socket has no compiled endpoint",
                    )
                )
            elif endpoint.protocol != provider.protocol:
                findings.append(
                    _error(
                        ValidationCode.ENDPOINT_PROTOCOL,
                        subject,
                        "endpoint protocol does not match provider socket",
                    )
                )
        for requirement in node.sockets.requirements:
            subject = SocketSubject(
                node_id, requirement.name, SocketDirection.REQUIREMENT
            )
            connected = requirement_edges.get((node_id, requirement.name), [])
            if len(connected) > 1:
                findings.append(
                    _error(
                        ValidationCode.MULTIPLE_REQUIREMENT_CONNECTIONS,
                        subject,
                        "requirement socket has multiple provider connections",
                    )
                )
            elif not connected and requirement.required:
                findings.append(
                    _error(
                        ValidationCode.MISSING_REQUIRED_CONNECTION,
                        subject,
                        "required socket is not connected",
                    )
                )
            elif not connected:
                findings.append(
                    ValidationFinding(
                        ValidationCode.UNCONNECTED_OPTIONAL_SOCKET,
                        ValidationSeverity.WARNING,
                        subject,
                        "optional socket is not connected",
                    )
                )
        for capability_name in node.block_spec.capabilities:
            capability = capability_named(capability_name)
            if capability.route_set is None:
                continue
            try:
                route_set_named(capability.route_set)
            except KeyError:
                findings.append(
                    _error(
                        ValidationCode.CONTROL_ROUTE_SET,
                        NodeSubject(node_id),
                        f"capability references unknown route set {capability.route_set.value!r}",
                    )
                )
        for check in node.block_spec.verification.checks:
            subject = SocketSubject(
                node_id,
                check.provider_socket,
                SocketDirection.PROVIDER,
            )
            try:
                provider = node.provider_socket(check.provider_socket)
            except KeyError:
                findings.append(
                    _error(
                        ValidationCode.VERIFICATION_PROVIDER,
                        subject,
                        f"verification check {check.check_id!r} references a missing provider socket",
                    )
                )
                continue
            if provider.protocol not in expected_protocols(check):
                findings.append(
                    _error(
                        ValidationCode.VERIFICATION_PROTOCOL,
                        subject,
                        f"verification check {check.check_id!r} is incompatible with the provider protocol",
                    )
                )

    try:
        codec.decode(codec.encode(graph))
    except (GraphDescriptorError, KeyError) as error:
        findings.append(
            _error(
                ValidationCode.INVALID_DESCRIPTOR,
                GraphSubject(),
                str(error),
            )
        )
    return ValidatedGraph(
        graph,
        tuple(sorted(findings, key=_finding_key)),
        codec,
    )


def _error(
    code: ValidationCode,
    subject: ValidationSubject,
    message: str,
) -> ValidationFinding:
    return ValidationFinding(code, ValidationSeverity.ERROR, subject, message)


def _finding_key(finding: ValidationFinding) -> tuple[str, str, str, str]:
    subject = finding.subject.descriptor()
    return (
        finding.severity.value,
        finding.code.value,
        str(sorted(subject.items())),
        finding.message,
    )
