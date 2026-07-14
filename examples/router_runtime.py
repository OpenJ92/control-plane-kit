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
    ProxyBlock,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.docker_runtime import DockerClient, DockerRuntimeInterpreter
from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.runtimes import RuntimePlan, RuntimeState


def router_recipe(active: str = "api-v1") -> DeploymentRecipe:
    """Return two hello backends behind a Docker-backed active router."""

    api_v1 = _hello_api("api-v1", "Hello from v1")
    api_v2 = _hello_api("api-v2", "Hello from v2")
    router = ProxyBlock(
        BlockSpec("api-router", "API Router", metadata={"behavior": "active-target"}),
        DockerImageImplementation(
            image="python:3.13-alpine",
            command=_router_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(RequirementSocket("active", Protocol.HTTP, ("ACTIVE_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    return DeploymentRecipe(
        f"router-runtime-{active}",
        DockerRuntime(
            children=(
                api_v1,
                api_v2,
                router,
                SocketConnection(active, "internal", "api-router", "active", edge_id="api-router.active"),
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
    script = (
        "from http.server import BaseHTTPRequestHandler, HTTPServer; "
        f"BODY = {body!r}.encode(); "
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.send_header('content-type', 'text/plain')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(BODY)\n"
        "    def log_message(self, format, *args):\n"
        "        pass\n"
        "HTTPServer(('0.0.0.0', 8000), Handler).serve_forever()"
    )
    return ("python", "-c", script)


def _router_command() -> tuple[str, ...]:
    script = (
        "import os, urllib.request; "
        "from http.server import BaseHTTPRequestHandler, HTTPServer; "
        "TARGET = os.environ['ACTIVE_TARGET_URL']; "
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        with urllib.request.urlopen(TARGET + self.path) as response:\n"
        "            body = response.read()\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n"
        "    def log_message(self, format, *args):\n"
        "        pass\n"
        "HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()"
    )
    return ("python", "-c", script)
