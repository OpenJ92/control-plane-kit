"""Postgres-backed application deployment through the Docker interpreter."""

from __future__ import annotations

from control_plane_kit.docker_runtime import DockerClient, DockerRuntimeInterpreter
from control_plane_kit.runtimes import RuntimePlan, RuntimeState
from examples.app_with_postgres import recipe

from control_plane_kit import compile_recipe


def postgres_plan() -> RuntimePlan:
    """Plan the existing app-with-postgres graph through Docker."""

    return DockerRuntimeInterpreter(project_name="postgres-demo").plan_start(compile_recipe(recipe()), "docker")


def run_postgres_with_client(client: DockerClient) -> RuntimeState:
    """Run the Postgres example through an injected Docker client."""

    interpreter = DockerRuntimeInterpreter(project_name="postgres-demo", client=client)
    return interpreter.up(compile_recipe(recipe()), "docker")
