"""Composable HTTP block examples.

These examples stop at topology. They demonstrate how package-provided blocks
can be placed under the same runtime and wired with provider/requirement socket
connections.
"""

from __future__ import annotations

from control_plane_kit import DeploymentRecipe, DockerRuntime, SocketConnection, compile_recipe
from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.servers import (
    hello_server_block,
    http_active_router_block,
    http_multiplexer_block,
    http_proxy_block,
    http_rate_limiter_block,
    http_weighted_load_balancer_block,
)


def proxy_recipe() -> DeploymentRecipe:
    """Return a hello app behind a one-target HTTP proxy."""

    return DeploymentRecipe(
        "http-proxy-composition",
        DockerRuntime(children=(
            hello_server_block("app", message="Hello through proxy"),
            http_proxy_block("proxy"),
            SocketConnection("app", "internal", "proxy", "target"),
        )),
    )


def active_router_recipe(active: str = "app-v1") -> DeploymentRecipe:
    """Return two hello apps behind an active HTTP router."""

    if active not in {"app-v1", "app-v2"}:
        raise ValueError(f"unknown active app {active!r}")
    return DeploymentRecipe(
        f"http-active-router-composition-{active}",
        DockerRuntime(children=(
            hello_server_block("app-v1", message="Hello from v1"),
            hello_server_block("app-v2", message="Hello from v2"),
            http_active_router_block("router"),
            SocketConnection(active, "internal", "router", "active"),
        )),
    )


def weighted_balancer_recipe() -> DeploymentRecipe:
    """Return two hello apps behind a weighted HTTP load balancer."""

    return DeploymentRecipe(
        "http-weighted-balancer-composition",
        DockerRuntime(children=(
            hello_server_block("app-a", message="Hello from A"),
            hello_server_block("app-b", message="Hello from B"),
            http_weighted_load_balancer_block("balancer"),
            SocketConnection("app-a", "internal", "balancer", "target-a"),
            SocketConnection("app-b", "internal", "balancer", "target-b"),
        )),
    )


def multiplexer_recipe() -> DeploymentRecipe:
    """Return one primary app and one observer behind an HTTP multiplexer."""

    return DeploymentRecipe(
        "http-multiplexer-composition",
        DockerRuntime(children=(
            hello_server_block("primary", message="Primary response"),
            hello_server_block("observer", message="Observer response"),
            http_multiplexer_block("multiplexer"),
            SocketConnection("primary", "internal", "multiplexer", "primary"),
            SocketConnection("observer", "internal", "multiplexer", "observer-a"),
        )),
    )


def rate_limiter_recipe() -> DeploymentRecipe:
    """Return a hello app behind an HTTP rate limiter."""

    return DeploymentRecipe(
        "http-rate-limiter-composition",
        DockerRuntime(children=(
            hello_server_block("app", message="Allowed response"),
            http_rate_limiter_block("limiter"),
            SocketConnection("app", "internal", "limiter", "target"),
        )),
    )


def compile_all_http_block_graphs() -> tuple[DeploymentGraph, ...]:
    """Compile every HTTP block composition example."""

    return (
        compile_recipe(proxy_recipe()),
        compile_recipe(active_router_recipe()),
        compile_recipe(weighted_balancer_recipe()),
        compile_recipe(multiplexer_recipe()),
        compile_recipe(rate_limiter_recipe()),
    )
