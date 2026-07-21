"""Compile recipe algebra into graph data."""

from __future__ import annotations

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    DataBlock,
    DeployBlock,
    DeploymentExpr,
    DeploymentRecipe,
    ProxyBlock,
    DockerRuntime,
    RuntimeContext,
    SocketConnection,
)
from control_plane_kit_core.capabilities import capability_named
from control_plane_kit_core.environment import SocketDerivedEnvironmentBinding
from control_plane_kit_core.topology.graph import DeploymentGraph, Edge, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily


def compile_recipe(recipe: DeploymentRecipe) -> DeploymentGraph:
    """Compile a deployment recipe into a pure graph."""

    graph = DeploymentGraph(recipe.name)
    graph, connections = _compile_runtime(recipe.root, graph)
    for connection in connections:
        graph = _apply_connection(graph, connection)
    return graph


def _compile_runtime(
    runtime: RuntimeContext,
    graph: DeploymentGraph,
) -> tuple[DeploymentGraph, tuple[SocketConnection, ...]]:
    connections: list[SocketConnection] = []
    child_nodes: list[str] = []
    next_graph = graph
    for child in runtime.children:
        if isinstance(child, SocketConnection):
            connections.append(child)
            continue
        if isinstance(child, RuntimeContext):
            next_graph, child_connections = _compile_runtime(child, next_graph)
            connections.extend(child_connections)
            continue
        if isinstance(child, (ApplicationBlock, DataBlock, ProxyBlock)):
            node = _materialize_block(child, runtime)
            child_nodes.append(node.node_id)
            next_graph = next_graph.add_node(node)
            continue
        raise TypeError(f"unsupported deployment expression {child!r}")
    next_graph = next_graph.add_runtime(
        RuntimeRecord(
            runtime_id=runtime.runtime_id,
            kind=runtime.kind,
            children=tuple(child_nodes),
            metadata=_runtime_metadata(runtime),
            lifecycle=runtime.lifecycle,
        )
    )
    return next_graph, tuple(connections)


def _runtime_metadata(runtime: RuntimeContext) -> dict[str, str]:
    metadata = dict(runtime.metadata)
    if isinstance(runtime, DockerRuntime):
        metadata.setdefault("network_name", runtime.network_name)
    return metadata


def _materialize_block(block: DeployBlock, runtime: RuntimeContext) -> Node:
    materialized = block.implementation.materialize(block.block_id, block.sockets, runtime)
    metadata = _spec_metadata(block)
    metadata.update(materialized.metadata)
    return Node(
        node_id=block.block_id,
        block_family=_block_family(block),
        block_spec=block.spec,
        kind=materialized.kind,
        runtime_id=runtime.runtime_id,
        sockets=block.sockets,
        endpoints=materialized.endpoints,
        public_environment=materialized.public_environment,
        metadata=metadata,
        lifecycle=materialized.lifecycle,
        configuration_artifacts=materialized.configuration_artifacts,
        secret_deliveries=materialized.secret_deliveries,
    )


def _block_family(block: DeployBlock) -> BlockFamily:
    match block:
        case ApplicationBlock():
            return BlockFamily.APPLICATION
        case DataBlock():
            return BlockFamily.DATA
        case ProxyBlock():
            return BlockFamily.PROXY


def _spec_metadata(block: DeployBlock) -> dict[str, object]:
    spec = block.spec
    metadata: dict[str, object] = {
        "display_name": spec.display_name or block.block_id,
        "block_family": block.__class__.__name__,
    }
    if hasattr(spec, "health_path") and spec.health_path is not None:
        metadata["health_path"] = spec.health_path
    if hasattr(spec, "capabilities") and spec.capabilities:
        metadata["capabilities"] = [
            capability_named(capability).as_descriptor()
            for capability in spec.capabilities
        ]
    metadata.update(spec.metadata)
    return metadata


def _apply_connection(graph: DeploymentGraph, connection: SocketConnection) -> DeploymentGraph:
    provider = graph.node(connection.provider_role)
    consumer = graph.node(connection.consumer_role)
    provider_socket = provider.provider_socket(connection.provider_socket)
    requirement_socket = consumer.requirement_socket(connection.requirement_socket)
    protocol = connection.protocol or provider_socket.protocol
    if provider_socket.protocol != protocol:
        raise ValueError(
            f"provider {provider.node_id}.{provider_socket.name} is {provider_socket.protocol.value}, "
            f"connection requested {protocol.value}"
        )
    if requirement_socket.protocol != protocol:
        raise ValueError(
            f"consumer {consumer.node_id}.{requirement_socket.name} expects {requirement_socket.protocol.value}, "
            f"connection provides {protocol.value}"
        )
    endpoint = provider.endpoint(provider_socket.name)
    edge_id = connection.edge_id or _edge_id(connection)
    assignments = {env_var: endpoint.url for env_var in requirement_socket.env_bindings}
    edge = Edge(
        edge_id=edge_id,
        provider_role=provider.node_id,
        provider_socket=provider_socket.name,
        consumer_role=consumer.node_id,
        requirement_socket=requirement_socket.name,
        protocol=protocol,
        binding=requirement_socket.binding,
        env_assignments=assignments,
    )
    bindings = tuple(
        SocketDerivedEnvironmentBinding(name, value, edge_id)
        for name, value in sorted(assignments.items())
    )
    return graph.update_node(consumer.with_socket_environment(bindings)).add_edge(edge)


def _edge_id(connection: SocketConnection) -> str:
    return (
        f"{connection.provider_role}.{connection.provider_socket}"
        f"-to-{connection.consumer_role}.{connection.requirement_socket}"
    )
