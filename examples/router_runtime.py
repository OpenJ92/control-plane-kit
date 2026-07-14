"""Active HTTP router deployment through the Docker runtime interpreter."""

from __future__ import annotations

import json

from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    BlockSockets,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    Protocol,
    ProviderSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.docker_runtime import DockerClient, DockerRuntimeInterpreter
from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.runtimes import RuntimePlan, RuntimeState
from control_plane_kit.servers import HttpActiveRouterRuntime, http_active_router_block


def router_recipe(active: str = "api-v1") -> DeploymentRecipe:
    """Return two hello backends behind a Docker-backed active router."""

    state = HttpActiveRouterRuntime.from_mapping({
        "active_target": active,
        "targets": {"api-v1": "api-v1", "api-v2": "api-v2"},
    })
    active_target = state.get("active_target")
    if active_target not in state.get("targets"):
        raise ValueError(f"unknown active target {active_target!r}")

    api_v1 = _hello_api("api-v1", "Hello from v1")
    api_v2 = _hello_api("api-v2", "Hello from v2")
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


def _hello_api(block_id: str, message: str) -> ApplicationBlock:
    return ApplicationBlock(
        BlockSpec(block_id, block_id),
        DockerImageImplementation(
            image="python:3.13-alpine",
            command=_hello_command(message),
            ports={"internal": 8000},
        ),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )


def _hello_command(message: str) -> tuple[str, ...]:
    body = json.dumps(message)
    lines = [
        "from http.server import BaseHTTPRequestHandler, HTTPServer",
        f"BODY = {body!r}.encode()",
        "class Handler(BaseHTTPRequestHandler):",
        "    def do_GET(self):",
        "        self.send_response(200)",
        "        self.send_header('content-type', 'text/plain')",
        "        self.end_headers()",
        "        self.wfile.write(BODY)",
        "    def log_message(self, format, *args): pass",
        "HTTPServer(('0.0.0.0', 8000), Handler).serve_forever()",
    ]
    return ("python", "-c", chr(10).join(lines))
