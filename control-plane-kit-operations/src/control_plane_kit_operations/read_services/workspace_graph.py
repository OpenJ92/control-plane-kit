"""Workspace and graph projections over durable operations truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit_core.topology import (
    GraphDescriptorCodec,
    GraphDescriptorError,
    validate_graph,
)
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord

from ._redaction import _redact_descriptor_value
from .errors import ReadModelError
from .protocols import GraphTopologyStore, WorkspaceStore


@dataclass(frozen=True)
class WorkspaceSummary:
    """Small workspace identity and lifecycle summary."""

    workspace_id: str
    name: str
    lifecycle: str
    current_graph_id: str | None
    desired_graph_id: str | None
    current_realized_projection_id: str | None
    desired_realized_projection_id: str | None
    desired_graph_revision: int
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "lifecycle": self.lifecycle,
            "current_graph_id": self.current_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "current_realized_projection_id": self.current_realized_projection_id,
            "desired_realized_projection_id": self.desired_realized_projection_id,
            "desired_graph_revision": self.desired_graph_revision,
            "metadata": _redact_descriptor_value("metadata", self.metadata),
        }


@dataclass(frozen=True)
class GraphPointerReadModel:
    """Read model for a graph pointer that may not yet be assigned."""

    pointer: str
    assigned: bool
    graph_id: str | None = None
    authored_graph_id: str | None = None
    realized_projection_id: str | None = None
    version: int | None = None
    graph_name: str | None = None
    graph_descriptor: Mapping[str, object] | None = None
    operator_graph: Mapping[str, object] | None = None

    def descriptor(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "pointer": self.pointer,
            "assigned": self.assigned,
            "graph_id": self.graph_id,
            "authored_graph_id": self.authored_graph_id,
            "realized_projection_id": self.realized_projection_id,
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
    workspace: WorkspaceSummary
    current_graph: GraphPointerReadModel
    desired_graph: GraphPointerReadModel

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace": self.workspace.descriptor(),
            "current_graph": self.current_graph.descriptor(),
            "desired_graph": self.desired_graph.descriptor(),
        }


@dataclass(frozen=True)
class ControlSurfaceReadModel:
    workspace_id: str
    pointer: str
    assigned: bool
    graph_id: str | None = None
    graph_name: str | None = None
    nodes: tuple[Mapping[str, object], ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "pointer": self.pointer,
            "assigned": self.assigned,
            "graph_id": self.graph_id,
            "graph_name": self.graph_name,
            "nodes": [dict(node) for node in self.nodes],
        }


class _WorkspaceGraphReadProjection:
    def __init__(
        self,
        workspace_store: WorkspaceStore,
        graph_topology_store: GraphTopologyStore,
        *,
        graph_codec: GraphDescriptorCodec,
    ) -> None:
        self._workspace_store = workspace_store
        self._graph_topology_store = graph_topology_store
        self._graph_codec = graph_codec

    def workspace(self, workspace_id: str) -> WorkspaceReadModel:
        workspace = self.require_workspace(workspace_id)
        return WorkspaceReadModel(
            workspace=_workspace_summary(workspace),
            current_graph=self._graph_pointer(
                "current",
                workspace.current_graph_id,
                workspace.current_realized_projection_id,
            ),
            desired_graph=self._graph_pointer(
                "desired",
                workspace.desired_graph_id,
                workspace.desired_realized_projection_id,
            ),
        )

    def current_graph(self, workspace_id: str) -> GraphPointerReadModel:
        workspace = self.require_workspace(workspace_id)
        return self._graph_pointer(
            "current",
            workspace.current_graph_id,
            workspace.current_realized_projection_id,
        )

    def desired_graph(self, workspace_id: str) -> GraphPointerReadModel:
        workspace = self.require_workspace(workspace_id)
        return self._graph_pointer(
            "desired",
            workspace.desired_graph_id,
            workspace.desired_realized_projection_id,
        )

    def operator_graph(
        self,
        workspace_id: str,
        *,
        pointer: str = "current",
    ) -> GraphPointerReadModel:
        workspace = self.require_workspace(workspace_id)
        return self._graph_pointer(
            pointer,
            _graph_id_for_pointer(workspace, pointer),
            _projection_id_for_pointer(workspace, pointer),
            include_operator_graph=True,
        )

    def control_surface(
        self,
        workspace_id: str,
        *,
        pointer: str = "current",
    ) -> ControlSurfaceReadModel:
        workspace = self.require_workspace(workspace_id)
        graph_id = _graph_id_for_pointer(workspace, pointer)
        if graph_id is None:
            return ControlSurfaceReadModel(workspace_id, pointer, False)
        record = _graph_record(self._graph_topology_store, graph_id)
        graph = _decode_valid_graph(self._graph_codec, record.graph_descriptor)
        descriptor = _redact_graph_descriptor(self._graph_codec.encode(graph))
        nodes = _mapping(descriptor.get("nodes", {}))
        return ControlSurfaceReadModel(
            workspace_id=workspace_id,
            pointer=pointer,
            assigned=True,
            graph_id=record.graph_id,
            graph_name=str(record.graph_descriptor.get("name", record.graph_id)),
            nodes=tuple(
                _node_control_surface(str(node_id), _mapping(node_descriptor))
                for node_id, node_descriptor in sorted(nodes.items())
            ),
        )

    def require_workspace(self, workspace_id: str) -> WorkspaceRecord:
        workspace: WorkspaceRecord | None = None
        try:
            workspace = self._workspace_store.get(workspace_id)
        except KeyError:
            pass
        if workspace is None:
            raise ReadModelError(f"missing workspace {workspace_id!r}")
        return workspace

    def _graph_pointer(
        self,
        pointer: str,
        graph_id: str | None,
        realized_projection_id: str | None,
        *,
        include_operator_graph: bool = False,
    ) -> GraphPointerReadModel:
        if graph_id is None or realized_projection_id is None:
            return GraphPointerReadModel(pointer=pointer, assigned=False)
        record = _graph_record(self._graph_topology_store, graph_id)
        operator_graph: Mapping[str, object] | None = None
        if include_operator_graph:
            graph = _decode_valid_graph(self._graph_codec, record.graph_descriptor)
            operator_graph = _operator_graph_descriptor(graph)
        return _graph_pointer_read_model(
            pointer,
            record,
            realized_projection_id=realized_projection_id,
            operator_graph=operator_graph,
        )

def _decode_valid_graph(
    codec: GraphDescriptorCodec,
    descriptor: Mapping[str, object],
) -> object:
    graph: object | None = None
    result = None
    try:
        graph = codec.decode(descriptor)
        result = validate_graph(graph, codec=codec)
    except (GraphDescriptorError, KeyError, TypeError, ValueError):
        pass
    if graph is None or result is None or not result.valid:
        raise ReadModelError("invalid stored graph descriptor")
    return graph


def _graph_record(
    store: GraphTopologyStore,
    graph_id: str,
) -> GraphVersionRecord:
    record: GraphVersionRecord | None = None
    try:
        record = store.get(graph_id)
    except KeyError:
        pass
    if record is None:
        raise ReadModelError("missing graph truth")
    return record


def _workspace_summary(record: WorkspaceRecord) -> WorkspaceSummary:
    return WorkspaceSummary(
        workspace_id=record.workspace_id,
        name=record.name,
        lifecycle=record.lifecycle.value,
        current_graph_id=record.current_graph_id,
        desired_graph_id=record.desired_graph_id,
        current_realized_projection_id=record.current_realized_projection_id,
        desired_realized_projection_id=record.desired_realized_projection_id,
        desired_graph_revision=record.desired_graph_revision,
        metadata=record.metadata,
    )


def _graph_id_for_pointer(workspace: WorkspaceRecord, pointer: str) -> str | None:
    if pointer == "current":
        return workspace.current_graph_id
    if pointer == "desired":
        return workspace.desired_graph_id
    raise ReadModelError("unknown graph pointer")


def _projection_id_for_pointer(
    workspace: WorkspaceRecord,
    pointer: str,
) -> str | None:
    if pointer == "current":
        return workspace.current_realized_projection_id
    if pointer == "desired":
        return workspace.desired_realized_projection_id
    raise ReadModelError("unknown graph pointer")


def _graph_pointer_read_model(
    pointer: str,
    record: GraphVersionRecord,
    *,
    realized_projection_id: str,
    operator_graph: Mapping[str, object] | None,
) -> GraphPointerReadModel:
    return GraphPointerReadModel(
        pointer=pointer,
        assigned=True,
        graph_id=record.graph_id,
        authored_graph_id=record.graph_id,
        realized_projection_id=realized_projection_id,
        version=record.version,
        graph_name=str(record.graph_descriptor.get("name", record.graph_id)),
        graph_descriptor=_redact_graph_descriptor(record.graph_descriptor),
        operator_graph=operator_graph,
    )


def _operator_graph_descriptor(graph: object) -> dict[str, object]:
    nodes = []
    connected_requirements = {
        (edge.consumer_role, edge.requirement_socket)
        for edge in graph.edges.values()
    }
    connected_providers = {
        (edge.provider_role, edge.provider_socket)
        for edge in graph.edges.values()
    }
    for _, node in sorted(graph.nodes.items()):
        nodes.append(
            {
                "node_id": node.node_id,
                "kind": node.kind,
                "runtime_id": node.runtime_id,
                "display_name": str(node.metadata.get("display_name", node.node_id)),
                "providers": [
                    {
                        "name": socket.name,
                        "protocol": {
                            "transport": socket.protocol.transport.value,
                            "application": socket.protocol.application.value,
                        },
                        "direction": "provider",
                        "connected": (node.node_id, socket.name) in connected_providers,
                    }
                    for socket in sorted(
                        node.sockets.providers,
                        key=lambda candidate: candidate.name,
                    )
                ],
                "requirements": [
                    {
                        "name": socket.name,
                        "protocol": {
                            "transport": socket.protocol.transport.value,
                            "application": socket.protocol.application.value,
                        },
                        "direction": "requirement",
                        "binding": socket.binding.value,
                        "required": socket.required,
                        "env_bindings": list(socket.env_bindings),
                        "connected": (node.node_id, socket.name) in connected_requirements,
                    }
                    for socket in sorted(
                        node.sockets.requirements,
                        key=lambda candidate: candidate.name,
                    )
                ],
                "metadata": _redact_descriptor_value("metadata", node.metadata),
            }
        )
    return {
        "name": graph.name,
        "runtimes": [
            {
                "runtime_id": runtime.runtime_id,
                "kind": runtime.kind.value,
                "children": sorted(runtime.children),
                "metadata": _redact_descriptor_value("metadata", runtime.metadata),
            }
            for _, runtime in sorted(graph.runtimes.items())
        ],
        "nodes": nodes,
        "edges": [
            {
                "edge_id": edge.edge_id,
                "provider": {
                    "node_id": edge.provider_role,
                    "socket": edge.provider_socket,
                },
                "consumer": {
                    "node_id": edge.consumer_role,
                    "socket": edge.requirement_socket,
                },
                "protocol": {
                    "transport": edge.protocol.transport.value,
                    "application": edge.protocol.application.value,
                },
            }
            for _, edge in sorted(graph.edges.items())
        ],
    }


def _node_control_surface(
    node_id: str,
    descriptor: Mapping[str, object],
) -> dict[str, object]:
    metadata = _mapping(descriptor.get("metadata", {}))
    block_spec = _mapping(descriptor.get("block_spec", {}))
    return {
        "node_id": node_id,
        "display_name": str(metadata.get("display_name", node_id)),
        "kind": str(descriptor["kind"]),
        "runtime_id": str(descriptor["runtime_id"]),
        "capabilities": _list(metadata.get("capabilities", ())),
        "providers": dict(_mapping(descriptor.get("providers", {}))),
        "requirements": dict(_mapping(descriptor.get("requirements", {}))),
        "control_surfaces": _list(block_spec.get("control_surfaces", ())),
        "metadata": {
            str(key): value
            for key, value in sorted(metadata.items())
            if str(key) != "capabilities"
        },
        "warnings": [],
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReadModelError("expected mapping in graph descriptor")
    return value


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _redact_graph_descriptor(descriptor: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _redact_descriptor_value(str(key), value)
        for key, value in sorted(descriptor.items())
    }
