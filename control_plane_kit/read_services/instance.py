"""Read-only service for one control-plane instance workspace."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, ProviderSocket, RequirementSocket
from control_plane_kit.graph import DeploymentGraph, Edge, Endpoint, Node, RuntimeRecord
from control_plane_kit.projections import project_operator_graph
from control_plane_kit.stores.protocols import GraphTopologyStore, WorkspaceStore
from control_plane_kit.stores.records import GraphVersionRecord, WorkspaceRecord
from control_plane_kit.types import EndpointScope, Protocol, RuntimeKind

_REDACTED = "<redacted>"
_SECRET_MARKERS = ("secret", "token", "password", "private_key", "credential", "api_key")
_ADDRESS_KEYS = ("url", "environment", "env_assignments")


class ReadModelError(ValueError):
    """Raised when durable truth cannot support a requested read model."""


@dataclass(frozen=True)
class WorkspaceSummary:
    """Small workspace identity and lifecycle summary."""

    workspace_id: str
    name: str
    lifecycle: str
    current_graph_id: str | None
    desired_graph_id: str | None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "lifecycle": self.lifecycle,
            "current_graph_id": self.current_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "metadata": dict(sorted(self.metadata.items())),
        }


@dataclass(frozen=True)
class GraphPointerReadModel:
    """Read model for a graph pointer that may not yet be assigned."""

    pointer: str
    assigned: bool
    graph_id: str | None = None
    version: int | None = None
    graph_name: str | None = None
    graph_descriptor: Mapping[str, object] | None = None
    operator_graph: Mapping[str, object] | None = None

    def descriptor(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "pointer": self.pointer,
            "assigned": self.assigned,
            "graph_id": self.graph_id,
            "version": self.version,
            "graph_name": self.graph_name,
        }
        if self.graph_descriptor is not None:
            payload["graph_descriptor"] = dict(self.graph_descriptor)
        if self.operator_graph is not None:
            payload["operator_graph"] = dict(self.operator_graph)
        return payload


@dataclass(frozen=True)
class WorkspaceReadModel:
    """Top-level workspace read model for one control-plane instance."""

    workspace: WorkspaceSummary
    current_graph: GraphPointerReadModel
    desired_graph: GraphPointerReadModel

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace": self.workspace.descriptor(),
            "current_graph": self.current_graph.descriptor(),
            "desired_graph": self.desired_graph.descriptor(),
        }


class InstanceReadService:
    """Composes source-of-truth stores into read-only instance views."""

    def __init__(
        self,
        *,
        workspace_store: WorkspaceStore,
        graph_topology_store: GraphTopologyStore,
    ) -> None:
        self._workspace_store = workspace_store
        self._graph_topology_store = graph_topology_store

    def workspace(self, workspace_id: str) -> WorkspaceReadModel:
        """Return the workspace summary and graph pointer read models."""

        workspace = self._workspace(workspace_id)
        return WorkspaceReadModel(
            workspace=_workspace_summary(workspace),
            current_graph=self.current_graph(workspace_id),
            desired_graph=self.desired_graph(workspace_id),
        )

    def current_graph(self, workspace_id: str) -> GraphPointerReadModel:
        """Return the current graph pointer read model."""

        workspace = self._workspace(workspace_id)
        return self._graph_pointer("current", workspace.current_graph_id)

    def desired_graph(self, workspace_id: str) -> GraphPointerReadModel:
        """Return the desired graph pointer read model."""

        workspace = self._workspace(workspace_id)
        return self._graph_pointer("desired", workspace.desired_graph_id)

    def operator_graph(self, workspace_id: str, *, pointer: str = "current") -> GraphPointerReadModel:
        """Return a graph pointer read model with operator projection included."""

        if pointer == "current":
            workspace = self._workspace(workspace_id)
            return self._graph_pointer("current", workspace.current_graph_id, include_operator_graph=True)
        if pointer == "desired":
            workspace = self._workspace(workspace_id)
            return self._graph_pointer("desired", workspace.desired_graph_id, include_operator_graph=True)
        raise ReadModelError(f"unknown graph pointer {pointer!r}")

    def _workspace(self, workspace_id: str) -> WorkspaceRecord:
        try:
            return self._workspace_store.get(workspace_id)
        except KeyError as exc:
            raise ReadModelError(f"missing workspace {workspace_id!r}") from exc

    def _graph_pointer(
        self,
        pointer: str,
        graph_id: str | None,
        *,
        include_operator_graph: bool = False,
    ) -> GraphPointerReadModel:
        if graph_id is None:
            return GraphPointerReadModel(pointer=pointer, assigned=False)
        record = self._graph_topology_store.get(graph_id)
        operator_graph: Mapping[str, object] | None = None
        if include_operator_graph:
            operator_graph = project_operator_graph(_graph_from_descriptor(record.graph_descriptor)).descriptor()
        return _graph_pointer_read_model(pointer, record, operator_graph=operator_graph)


def _workspace_summary(record: WorkspaceRecord) -> WorkspaceSummary:
    return WorkspaceSummary(
        workspace_id=record.workspace_id,
        name=record.name,
        lifecycle=record.lifecycle.value,
        current_graph_id=record.current_graph_id,
        desired_graph_id=record.desired_graph_id,
        metadata=record.metadata,
    )


def _graph_pointer_read_model(
    pointer: str,
    record: GraphVersionRecord,
    *,
    operator_graph: Mapping[str, object] | None,
) -> GraphPointerReadModel:
    return GraphPointerReadModel(
        pointer=pointer,
        assigned=True,
        graph_id=record.graph_id,
        version=record.version,
        graph_name=str(record.graph_descriptor.get("name", record.graph_id)),
        graph_descriptor=_redact_graph_descriptor(record.graph_descriptor),
        operator_graph=operator_graph,
    )


def _graph_from_descriptor(descriptor: Mapping[str, object]) -> DeploymentGraph:
    """Rehydrate a stored graph descriptor for pure projection interpreters."""

    graph = DeploymentGraph(str(descriptor["name"]))
    runtimes = descriptor.get("runtimes", {})
    nodes = descriptor.get("nodes", {})
    edges = descriptor.get("edges", {})
    if not isinstance(runtimes, Mapping) or not isinstance(nodes, Mapping) or not isinstance(edges, Mapping):
        raise ReadModelError("graph descriptor has invalid top-level shape")
    for runtime_id, runtime_descriptor in sorted(runtimes.items()):
        graph = graph.add_runtime(_runtime_from_descriptor(str(runtime_id), _mapping(runtime_descriptor)))
    for node_id, node_descriptor in sorted(nodes.items()):
        graph = graph.add_node(_node_from_descriptor(str(node_id), _mapping(node_descriptor)))
    for edge_id, edge_descriptor in sorted(edges.items()):
        graph = graph.add_edge(_edge_from_descriptor(str(edge_id), _mapping(edge_descriptor)))
    return graph


def _runtime_from_descriptor(runtime_id: str, descriptor: Mapping[str, object]) -> RuntimeRecord:
    return RuntimeRecord(
        runtime_id=runtime_id,
        kind=RuntimeKind(str(descriptor["kind"])),
        children=tuple(str(child) for child in descriptor.get("children", ())),
        metadata=_string_mapping(descriptor.get("metadata", {})),
    )


def _node_from_descriptor(node_id: str, descriptor: Mapping[str, object]) -> Node:
    requirements = tuple(
        RequirementSocket(
            name=str(name),
            protocol=Protocol(str(socket_descriptor["protocol"])),
            env_bindings=tuple(str(value) for value in socket_descriptor.get("env_bindings", ())),
            required=bool(socket_descriptor.get("required", True)),
        )
        for name, socket_descriptor in sorted(_mapping(descriptor.get("requirements", {})).items())
        if isinstance(socket_descriptor, Mapping)
    )
    providers = tuple(
        ProviderSocket(name=str(name), protocol=Protocol(str(socket_descriptor["protocol"])))
        for name, socket_descriptor in sorted(_mapping(descriptor.get("providers", {})).items())
        if isinstance(socket_descriptor, Mapping)
    )
    endpoints = {
        str(name): Endpoint(
            url=str(endpoint_descriptor["url"]),
            protocol=Protocol(str(endpoint_descriptor["protocol"])),
            scope=EndpointScope(str(endpoint_descriptor.get("scope", EndpointScope.PRIVATE.value))),
        )
        for name, endpoint_descriptor in sorted(_mapping(descriptor.get("endpoints", {})).items())
        if isinstance(endpoint_descriptor, Mapping)
    }
    return Node(
        node_id=node_id,
        kind=str(descriptor["kind"]),
        runtime_id=str(descriptor["runtime_id"]),
        sockets=BlockSockets(requirements=requirements, providers=providers),
        endpoints=endpoints,
        environment=_string_mapping(descriptor.get("environment", {})),
        metadata=_metadata_mapping(descriptor.get("metadata", {})),
    )


def _edge_from_descriptor(edge_id: str, descriptor: Mapping[str, object]) -> Edge:
    provider = _mapping(descriptor["provider"])
    consumer = _mapping(descriptor["consumer"])
    return Edge(
        edge_id=edge_id,
        provider_role=str(provider["role"]),
        provider_socket=str(provider["socket"]),
        consumer_role=str(consumer["role"]),
        requirement_socket=str(consumer["requirement"]),
        protocol=Protocol(str(descriptor["protocol"])),
        env_assignments=_string_mapping(descriptor.get("env_assignments", {})),
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReadModelError("expected mapping in graph descriptor")
    return value


def _string_mapping(value: object) -> dict[str, str]:
    return {str(key): str(child) for key, child in _mapping(value).items()}


def _metadata_mapping(value: object) -> dict[str, object]:
    return {str(key): child for key, child in _mapping(value).items()}


def _redact_graph_descriptor(descriptor: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _redact_descriptor_value(str(key), value)
        for key, value in sorted(descriptor.items())
    }


def _redact_descriptor_value(key: str, value: object) -> object:
    if _looks_sensitive_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_descriptor_value(str(child_key), child_value)
            for child_key, child_value in sorted(value.items())
        }
    if isinstance(value, list):
        return [_redact_descriptor_value(key, child) for child in value]
    if isinstance(value, tuple):
        return tuple(_redact_descriptor_value(key, child) for child in value)
    return value


def _looks_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _ADDRESS_KEYS or any(marker in normalized for marker in _SECRET_MARKERS)
