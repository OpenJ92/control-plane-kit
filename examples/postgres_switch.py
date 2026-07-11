"""Postgres switch migration example."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane_kit import DeploymentGraph, Edge, Endpoint, Node, diff_graphs, plan_migration
from control_plane_kit.proxies import (
    ActiveTarget,
    HAProxyImplementation,
    PostgresProtocol,
    ProxyNode,
)


def graph_v1() -> DeploymentGraph:
    """Return api -> postgres-switch -> postgres-v1."""

    graph = (
        DeploymentGraph("postgres-switch-v1")
        .add_node(Node("api", "fastapi", {"default": Endpoint("http://api:8000")}, frozenset({"health", "logs"})))
        .add_node(
            Node(
                "postgres-v1",
                "postgres",
                {"default": Endpoint("postgresql://postgres-v1:5432/app")},
                frozenset({"health"}),
            )
        )
    )
    graph = ProxyNode(
        "postgres-switch",
        protocol=PostgresProtocol(),
        behavior=ActiveTarget("postgres-v1"),
        implementation=HAProxyImplementation(),
    ).attach(graph)
    return graph.add_edge(Edge("api-to-postgres", "api", "postgres-switch", "postgres"))


def graph_v2() -> DeploymentGraph:
    """Return api -> postgres-switch -> postgres-v2."""

    return (
        graph_v1()
        .add_node(
            Node(
                "postgres-v2",
                "postgres",
                {"default": Endpoint("postgresql://postgres-v2:5432/app")},
                frozenset({"health"}),
            )
        )
        .replace_edge(
            Edge(
                "postgres-switch.active",
                "postgres-switch",
                "postgres-v2",
                "postgres",
                mutable=True,
            )
        )
    )


if __name__ == "__main__":
    before = graph_v1()
    after = graph_v2()
    print(diff_graphs(before, after).summary())
    print()
    print(plan_migration(before, after).to_text())
