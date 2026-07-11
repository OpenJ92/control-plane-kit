"""Cloudflare -> Auth -> API -> Postgres topology example.

This is a graph-only example.  It does not start Cloudflare or Docker.  The
point is to show how an application-specific deployment can be represented
without putting application behavior inside the graph library.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane_kit import DeploymentGraph, Edge, Endpoint, Node


def graph() -> DeploymentGraph:
    """Return a small public-edge auth deployment."""

    return (
        DeploymentGraph("local-cloudflare-auth")
        .add_node(
            Node(
                "cloudflare",
                "cloudflare-tunnel",
                {"public": Endpoint("https://auth.example.com", scope="public")},
                frozenset({"health", "logs"}),
            )
        )
        .add_node(
            Node(
                "auth",
                "fastapi",
                {"default": Endpoint("http://auth:8010")},
                frozenset({"health", "logs"}),
            )
        )
        .add_node(
            Node(
                "api",
                "fastapi",
                {"default": Endpoint("http://api:8000")},
                frozenset({"health", "logs"}),
            )
        )
        .add_node(
            Node(
                "postgres",
                "postgres",
                {"default": Endpoint("postgresql://postgres:5432/app")},
                frozenset({"health"}),
            )
        )
        .add_edge(Edge("cloudflare-to-auth", "cloudflare", "auth", "http", "public"))
        .add_edge(Edge("auth-to-api", "auth", "api", "http"))
        .add_edge(Edge("api-to-postgres", "api", "postgres", "postgres"))
    )


if __name__ == "__main__":
    import json

    print(json.dumps(graph().descriptor(), indent=2, sort_keys=True))
