"""Hello HTTP deployment through the Docker runtime interpreter."""

from __future__ import annotations

from control_plane_kit import DeploymentRecipe, DockerRuntime, compile_recipe
from control_plane_kit.docker_runtime import DockerClient, DockerRuntimeInterpreter
from control_plane_kit.core.topology.graph import DeploymentGraph
from control_plane_kit.runtimes import RuntimePlan, RuntimeState
from control_plane_kit.servers import hello_server_block


def hello_recipe(message: str = "Hello, world!") -> DeploymentRecipe:
    """Return a tiny Docker-backed HTTP deployment recipe."""

    return DeploymentRecipe(
        "hello-runtime",
        DockerRuntime(children=(hello_server_block("hello", message=message),)),
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
