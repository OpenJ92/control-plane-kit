"""Immutable-ish graph values for deployment topology.

The graph layer deliberately avoids runtime effects.  A ``DeploymentGraph`` is
just data: nodes, endpoints, and edges.  That makes it cheap to diff, render,
test, serialize, and hand to multiple interpreters.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping


@dataclass(frozen=True)
class Endpoint:
    """A named address exposed by a node.

    ``scope`` is descriptive rather than enforced.  A runtime can interpret
    ``public`` endpoints as user-facing URLs, ``private`` endpoints as internal
    addresses, and ``local`` endpoints as host-machine access.
    """

    url: str
    scope: str = "private"
    protocol: str | None = None

    def descriptor(self) -> dict[str, str]:
        """Return a JSON-serializable representation."""

        data = {"url": self.url, "scope": self.scope}
        if self.protocol is not None:
            data["protocol"] = self.protocol
        return data


@dataclass(frozen=True)
class Node:
    """A deployable, external resource, or control-plane component."""

    node_id: str
    kind: str
    endpoints: Mapping[str, Endpoint] = field(default_factory=dict)
    capabilities: frozenset[str] = field(default_factory=frozenset)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def endpoint(self, name: str = "default") -> Endpoint:
        """Return one endpoint by name with a helpful error if missing."""

        try:
            return self.endpoints[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.endpoints)) or "<none>"
            raise KeyError(
                f"node {self.node_id!r} has no endpoint {name!r}; "
                f"available endpoints: {available}"
            ) from exc

    def descriptor(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "endpoints": {
                name: endpoint.descriptor()
                for name, endpoint in sorted(self.endpoints.items())
            },
            "capabilities": sorted(self.capabilities),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Edge:
    """A directed relationship between two nodes.

    Edges are where the topology becomes visible.  ``mutable=True`` means a
    runtime may be able to change the edge target without changing application
    code, for example by reloading an HTTP router or TCP switch.
    """

    edge_id: str
    source: str
    target: str
    protocol: str
    source_endpoint: str = "default"
    target_endpoint: str = "default"
    mutable: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "protocol": self.protocol,
            "source_endpoint": self.source_endpoint,
            "target_endpoint": self.target_endpoint,
            "mutable": self.mutable,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DeploymentGraph:
    """A named deployment topology.

    Methods return new graph values.  This makes examples pleasant to read and
    avoids hidden mutation when a migration is described as ``v1 -> v2``.
    """

    name: str
    nodes: Mapping[str, Node] = field(default_factory=dict)
    edges: Mapping[str, Edge] = field(default_factory=dict)

    def add_node(self, node: Node) -> DeploymentGraph:
        """Return a graph with ``node`` inserted or replaced."""

        return replace(self, nodes={**self.nodes, node.node_id: node})

    def add_edge(self, edge: Edge) -> DeploymentGraph:
        """Return a graph with ``edge`` inserted after validating endpoints."""

        self._validate_edge(edge)
        return replace(self, edges={**self.edges, edge.edge_id: edge})

    def replace_edge(self, edge: Edge) -> DeploymentGraph:
        """Return a graph with an existing edge changed."""

        if edge.edge_id not in self.edges:
            raise KeyError(f"cannot replace missing edge {edge.edge_id!r}")
        return self.add_edge(edge)

    def without_node(self, node_id: str) -> DeploymentGraph:
        """Return a graph with a node and its incident edges removed."""

        return replace(
            self,
            nodes={key: value for key, value in self.nodes.items() if key != node_id},
            edges={
                key: edge
                for key, edge in self.edges.items()
                if edge.source != node_id and edge.target != node_id
            },
        )

    def descriptor(self) -> dict[str, object]:
        """Return a JSON-serializable graph descriptor."""

        return {
            "name": self.name,
            "nodes": {
                node_id: node.descriptor()
                for node_id, node in sorted(self.nodes.items())
            },
            "edges": {
                edge_id: edge.descriptor()
                for edge_id, edge in sorted(self.edges.items())
            },
        }

    def _validate_edge(self, edge: Edge) -> None:
        if edge.source not in self.nodes:
            raise KeyError(f"edge {edge.edge_id!r} references missing source {edge.source!r}")
        if edge.target not in self.nodes:
            raise KeyError(f"edge {edge.edge_id!r} references missing target {edge.target!r}")
        self.nodes[edge.source].endpoint(edge.source_endpoint)
        self.nodes[edge.target].endpoint(edge.target_endpoint)
