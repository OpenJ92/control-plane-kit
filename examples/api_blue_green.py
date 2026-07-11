"""Blue/green API migration example."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane_kit import DeploymentGraph, Edge, Endpoint, Node, diff_graphs, plan_migration


def graph_v1() -> DeploymentGraph:
    """Return auth -> api-router -> api-v1."""

    return (
        DeploymentGraph("api-blue-green-v1")
        .add_node(Node("auth", "fastapi", {"default": Endpoint("http://auth:8010")}, frozenset({"health", "logs"})))
        .add_node(Node("api-v1", "fastapi", {"default": Endpoint("http://api-v1:8000")}, frozenset({"health", "logs"})))
        .add_node(
            Node(
                "api-router",
                "http-router",
                {"default": Endpoint("http://api-router:8080")},
                frozenset({"health", "logs", "switch-target"}),
            )
        )
        .add_edge(Edge("auth-to-api", "auth", "api-router", "http"))
        .add_edge(Edge("api-router-active", "api-router", "api-v1", "http", mutable=True))
    )


def graph_v2() -> DeploymentGraph:
    """Return auth -> api-router -> api-v2 with api-v1 retained for rollback."""

    return (
        graph_v1()
        .add_node(Node("api-v2", "fastapi", {"default": Endpoint("http://api-v2:8000")}, frozenset({"health", "logs"})))
        .replace_edge(Edge("api-router-active", "api-router", "api-v2", "http", mutable=True))
    )


if __name__ == "__main__":
    before = graph_v1()
    after = graph_v2()
    print(diff_graphs(before, after).summary())
    print()
    print(plan_migration(before, after).to_text())
