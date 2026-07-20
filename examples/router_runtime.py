"""Active HTTP router deployment through the Docker runtime interpreter."""

from __future__ import annotations

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.docker_runtime import DockerClient, DockerRuntimeInterpreter
from control_plane_kit.core.topology.graph import DeploymentGraph
from control_plane_kit.runtimes import RuntimePlan, RuntimeState
from control_plane_kit.servers import HttpActiveRouterRuntime, hello_server_block, http_active_router_block


def router_recipe(active: str = "api-v1") -> DeploymentRecipe:
    """Return two hello backends behind a Docker-backed active router."""

    state = HttpActiveRouterRuntime.from_mapping({
        "active_target": active,
        "targets": {"api-v1": "api-v1", "api-v2": "api-v2"},
    })
    active_target = state.get("active_target")
    if active_target not in state.get("targets"):
        raise ValueError(f"unknown active target {active_target!r}")

    api_v1 = hello_server_block("api-v1", message="Hello from v1")
    api_v2 = hello_server_block("api-v2", message="Hello from v2")
    router = http_active_router_block("api-router", display_name="API Router")
    return DeploymentRecipe(
        f"router-runtime-{active}",
        DockerRuntime(
            children=(
                api_v1,
                api_v2,
                router,
                SocketConnection(active_target, "internal", "api-router", "active", edge_id="api-router.active"),
            )
        ),
    )


def router_graph(active: str = "api-v1") -> DeploymentGraph:
    """Compile the router runtime recipe."""

    return compile_recipe(router_recipe(active))


def router_plan(active: str = "api-v1") -> RuntimePlan:
    """Plan the router runtime recipe through Docker."""

    return DockerRuntimeInterpreter(project_name="router-demo").plan_start(router_graph(active), "docker")


def run_router_with_client(client: DockerClient, active: str = "api-v1") -> RuntimeState:
    """Run the router runtime recipe through an injected Docker client."""

    interpreter = DockerRuntimeInterpreter(project_name="router-demo", client=client)
    return interpreter.up(router_graph(active), "docker")
