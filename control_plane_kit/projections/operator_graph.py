"""Operator-facing graph projection.

The compiled graph is the source of truth.  This module is an interpreter from
that truth into a deterministic, redacted shape suitable for humans, CLI, MCP,
and later UI clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.topology.graph import DeploymentGraph, Edge, Node, RuntimeRecord

_SECRET_MARKERS = ("secret", "token", "password", "private_key", "credential", "api_key")
_REDACTED = "<redacted>"


@dataclass(frozen=True)
class OperatorSocket:
    """A provider or requirement socket visible to an operator."""

    name: str
    protocol: str
    direction: str
    required: bool | None = None
    env_bindings: tuple[str, ...] = ()
    connected: bool = False

    def descriptor(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "protocol": self.protocol,
            "direction": self.direction,
            "connected": self.connected,
        }
        if self.required is not None:
            payload["required"] = self.required
        if self.env_bindings:
            payload["env_bindings"] = list(self.env_bindings)
        return payload


@dataclass(frozen=True)
class OperatorNode:
    """A block node projected for operator inspection."""

    node_id: str
    kind: str
    runtime_id: str
    display_name: str
    providers: tuple[OperatorSocket, ...] = ()
    requirements: tuple[OperatorSocket, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "runtime_id": self.runtime_id,
            "display_name": self.display_name,
            "providers": [socket.descriptor() for socket in self.providers],
            "requirements": [socket.descriptor() for socket in self.requirements],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class OperatorEdge:
    """A socket connection projected as a visual/operator edge."""

    edge_id: str
    provider_node_id: str
    provider_socket: str
    consumer_node_id: str
    requirement_socket: str
    protocol: str

    def descriptor(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "provider": {"node_id": self.provider_node_id, "socket": self.provider_socket},
            "consumer": {"node_id": self.consumer_node_id, "socket": self.requirement_socket},
            "protocol": self.protocol,
        }


@dataclass(frozen=True)
class OperatorRuntime:
    """A runtime context and the block nodes interpreted within it."""

    runtime_id: str
    kind: str
    children: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "runtime_id": self.runtime_id,
            "kind": self.kind,
            "children": list(self.children),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class OperatorWarning:
    """A graph condition that should be visible but does not prevent reading."""

    code: str
    node_id: str
    socket: str
    message: str

    def descriptor(self) -> dict[str, str]:
        return {
            "code": self.code,
            "node_id": self.node_id,
            "socket": self.socket,
            "message": self.message,
        }


@dataclass(frozen=True)
class OperatorGraph:
    """The complete operator-facing graph projection."""

    name: str
    runtimes: tuple[OperatorRuntime, ...]
    nodes: tuple[OperatorNode, ...]
    edges: tuple[OperatorEdge, ...]
    warnings: tuple[OperatorWarning, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "name": self.name,
            "runtimes": [runtime.descriptor() for runtime in self.runtimes],
            "nodes": [node.descriptor() for node in self.nodes],
            "edges": [edge.descriptor() for edge in self.edges],
            "warnings": [warning.descriptor() for warning in self.warnings],
        }


def project_operator_graph(graph: DeploymentGraph) -> OperatorGraph:
    """Project a compiled graph into a deterministic operator read model."""

    connected_requirements = {
        (edge.consumer_role, edge.requirement_socket)
        for edge in graph.edges.values()
    }
    connected_providers = {
        (edge.provider_role, edge.provider_socket)
        for edge in graph.edges.values()
    }
    nodes = tuple(
        _project_node(node, connected_requirements, connected_providers)
        for _, node in sorted(graph.nodes.items())
    )
    warnings = tuple(
        warning
        for node in nodes
        for warning in _dangling_requirement_warnings(node)
    )
    return OperatorGraph(
        name=graph.name,
        runtimes=tuple(_project_runtime(runtime) for _, runtime in sorted(graph.runtimes.items())),
        nodes=nodes,
        edges=tuple(_project_edge(edge) for _, edge in sorted(graph.edges.items())),
        warnings=warnings,
    )


def _project_node(
    node: Node,
    connected_requirements: set[tuple[str, str]],
    connected_providers: set[tuple[str, str]],
) -> OperatorNode:
    return OperatorNode(
        node_id=node.node_id,
        kind=node.kind,
        runtime_id=node.runtime_id,
        display_name=str(node.metadata.get("display_name", node.node_id)),
        providers=tuple(
            OperatorSocket(
                name=socket.name,
                protocol=socket.protocol.value,
                direction="provider",
                connected=(node.node_id, socket.name) in connected_providers,
            )
            for socket in sorted(node.sockets.providers, key=lambda candidate: candidate.name)
        ),
        requirements=tuple(
            OperatorSocket(
                name=socket.name,
                protocol=socket.protocol.value,
                direction="requirement",
                required=socket.required,
                env_bindings=tuple(socket.env_bindings),
                connected=(node.node_id, socket.name) in connected_requirements,
            )
            for socket in sorted(node.sockets.requirements, key=lambda candidate: candidate.name)
        ),
        metadata=_redact_mapping(node.metadata),
    )


def _project_runtime(runtime: RuntimeRecord) -> OperatorRuntime:
    return OperatorRuntime(
        runtime_id=runtime.runtime_id,
        kind=runtime.kind.value,
        children=tuple(sorted(runtime.children)),
        metadata=_redact_mapping(runtime.metadata),
    )


def _project_edge(edge: Edge) -> OperatorEdge:
    return OperatorEdge(
        edge_id=edge.edge_id,
        provider_node_id=edge.provider_role,
        provider_socket=edge.provider_socket,
        consumer_node_id=edge.consumer_role,
        requirement_socket=edge.requirement_socket,
        protocol=edge.protocol.value,
    )


def _dangling_requirement_warnings(node: OperatorNode) -> tuple[OperatorWarning, ...]:
    return tuple(
        OperatorWarning(
            code="dangling-required-socket",
            node_id=node.node_id,
            socket=socket.name,
            message=f"required socket {node.node_id}.{socket.name} is not connected",
        )
        for socket in node.requirements
        if socket.required and not socket.connected
    )


def _redact_mapping(mapping: Mapping[str, object]) -> dict[str, object]:
    return {
        key: _redact_value(key, value)
        for key, value in sorted(mapping.items())
    }


def _redact_value(key: str, value: object) -> object:
    if _looks_secret(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            child_key: _redact_value(str(child_key), child_value)
            for child_key, child_value in sorted(value.items())
        }
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(key, item) for item in value)
    return value


def _looks_secret(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_MARKERS)
