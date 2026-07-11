"""Proxy node composition."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.core.graph import DeploymentGraph, Edge, Endpoint, Node
from control_plane_kit.proxies.behaviors import ProxyBehavior
from control_plane_kit.proxies.implementations import ProxyImplementation
from control_plane_kit.proxies.protocols import NetworkProtocol


@dataclass(frozen=True)
class ProxyNode:
    """A composable proxy value.

    This is the central product type:

    ``ProxyNode = NetworkProtocol x ProxyBehavior x ProxyImplementation``

    ``as_node`` records the proxy itself.  ``attach`` inserts the proxy node and
    its target edges into a deployment graph.
    """

    node_id: str
    protocol: NetworkProtocol
    behavior: ProxyBehavior
    implementation: ProxyImplementation
    endpoint_name: str = "default"
    host: str | None = None

    def as_node(self) -> Node:
        """Return this proxy as a graph node."""

        host = self.host or self.node_id
        metadata = {
            **self.protocol_metadata(),
            **self.behavior.metadata(),
            **self.implementation.metadata(),
        }
        return Node(
            self.node_id,
            kind=f"{self.protocol.name}-proxy",
            endpoints={
                self.endpoint_name: Endpoint(
                    f"{self.protocol.endpoint_scheme}://{host}:{self.protocol.default_port}",
                    protocol=self.protocol.name,
                )
            },
            capabilities=frozenset({"health", "logs", "switch-target"})
            if self.behavior.mutable_edge()
            else frozenset({"health", "logs"}),
            metadata=metadata,
        )

    def attach(self, graph: DeploymentGraph) -> DeploymentGraph:
        """Insert the proxy node and its target edges into ``graph``."""

        next_graph = graph.add_node(self.as_node())
        for index, target_id in enumerate(self.behavior.target_ids()):
            target_endpoint = "default"
            edge_id = f"{self.node_id}.target.{index}"
            if index == 0 and self.behavior.mutable_edge():
                edge_id = f"{self.node_id}.active"
            next_graph = next_graph.add_edge(
                Edge(
                    edge_id,
                    self.node_id,
                    target_id,
                    protocol=self.protocol.name,
                    source_endpoint=self.endpoint_name,
                    target_endpoint=target_endpoint,
                    mutable=self.behavior.mutable_edge() and index == 0,
                )
            )
        return next_graph

    def protocol_metadata(self) -> dict[str, object]:
        """Return protocol details for descriptor output."""

        return {
            "protocol": self.protocol.name,
            "default_port": self.protocol.default_port,
            "endpoint_scheme": self.protocol.endpoint_scheme,
        }
