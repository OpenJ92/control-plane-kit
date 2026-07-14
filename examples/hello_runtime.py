"""Hello HTTP deployment through the Docker runtime interpreter."""

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
    compile_recipe,
)
from control_plane_kit.docker_runtime import DockerClient, DockerRuntimeInterpreter
from control_plane_kit.graph import DeploymentGraph
from control_plane_kit.runtimes import RuntimePlan, RuntimeState


def hello_recipe(message: str = "Hello, world!") -> DeploymentRecipe:
    """Return a tiny Docker-backed HTTP deployment recipe."""

    return DeploymentRecipe(
        "hello-runtime",
        DockerRuntime(
            children=(
                ApplicationBlock(
                    BlockSpec("hello", "Hello HTTP", health_path="/"),
                    DockerImageImplementation(
                        image="python:3.13-alpine",
                        command=_hello_command(message),
                        ports={"internal": 8000},
                    ),
                    BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
                ),
            )
        ),
    )


def hello_graph(message: str = "Hello, world!") -> DeploymentGraph:
    """Compile the hello recipe into a pure graph."""

    return compile_recipe(hello_recipe(message))


def hello_plan(message: str = "Hello, world!") -> RuntimePlan:
    """Plan the hello deployment without starting Docker."""

    return DockerRuntimeInterpreter(project_name="hello-demo").plan_start(hello_graph(message), "docker")


def run_hello_with_client(client: DockerClient, message: str = "Hello, world!") -> RuntimeState:
    """Run the hello deployment through an injected Docker client."""

    interpreter = DockerRuntimeInterpreter(project_name="hello-demo", client=client)
    return interpreter.up(hello_graph(message), "docker")


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
