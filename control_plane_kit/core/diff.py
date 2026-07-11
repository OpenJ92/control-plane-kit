"""Graph comparison primitives."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.core.graph import DeploymentGraph, Edge, Node


@dataclass(frozen=True)
class GraphDiff:
    """The structural difference between two deployment graphs."""

    added_nodes: tuple[Node, ...]
    removed_nodes: tuple[Node, ...]
    changed_nodes: tuple[tuple[Node, Node], ...]
    added_edges: tuple[Edge, ...]
    removed_edges: tuple[Edge, ...]
    changed_edges: tuple[tuple[Edge, Edge], ...]

    def is_empty(self) -> bool:
        """Return true when the graphs are structurally equal."""

        return not any(
            (
                self.added_nodes,
                self.removed_nodes,
                self.changed_nodes,
                self.added_edges,
                self.removed_edges,
                self.changed_edges,
            )
        )

    def summary(self) -> str:
        """Return a compact human-readable summary."""

        if self.is_empty():
            return "no topology changes"
        lines: list[str] = []
        if self.added_nodes:
            lines.append("added nodes: " + ", ".join(node.node_id for node in self.added_nodes))
        if self.removed_nodes:
            lines.append(
                "removed nodes: " + ", ".join(node.node_id for node in self.removed_nodes)
            )
        if self.changed_nodes:
            lines.append(
                "changed nodes: "
                + ", ".join(after.node_id for _, after in self.changed_nodes)
            )
        if self.added_edges:
            lines.append("added edges: " + ", ".join(edge.edge_id for edge in self.added_edges))
        if self.removed_edges:
            lines.append(
                "removed edges: " + ", ".join(edge.edge_id for edge in self.removed_edges)
            )
        if self.changed_edges:
            lines.append(
                "changed edges: "
                + ", ".join(after.edge_id for _, after in self.changed_edges)
            )
        return "\n".join(lines)


def diff_graphs(before: DeploymentGraph, after: DeploymentGraph) -> GraphDiff:
    """Compare two deployment graphs by node and edge identity."""

    before_node_ids = set(before.nodes)
    after_node_ids = set(after.nodes)
    before_edge_ids = set(before.edges)
    after_edge_ids = set(after.edges)

    shared_nodes = sorted(before_node_ids & after_node_ids)
    shared_edges = sorted(before_edge_ids & after_edge_ids)

    return GraphDiff(
        added_nodes=tuple(after.nodes[node_id] for node_id in sorted(after_node_ids - before_node_ids)),
        removed_nodes=tuple(before.nodes[node_id] for node_id in sorted(before_node_ids - after_node_ids)),
        changed_nodes=tuple(
            (before.nodes[node_id], after.nodes[node_id])
            for node_id in shared_nodes
            if before.nodes[node_id] != after.nodes[node_id]
        ),
        added_edges=tuple(after.edges[edge_id] for edge_id in sorted(after_edge_ids - before_edge_ids)),
        removed_edges=tuple(before.edges[edge_id] for edge_id in sorted(before_edge_ids - after_edge_ids)),
        changed_edges=tuple(
            (before.edges[edge_id], after.edges[edge_id])
            for edge_id in shared_edges
            if before.edges[edge_id] != after.edges[edge_id]
        ),
    )
