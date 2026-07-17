"""Hello server block factory used by examples and smoke tests."""

from __future__ import annotations

from control_plane_kit.algebra import ApplicationBlock, BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit.contracts import EnvironmentContract, TextVariable
from control_plane_kit.implementations import DockerImageImplementation, HostPublication
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.types import Protocol


class HelloEnvironment(EnvironmentContract):
    """Startup environment contract for the tiny hello server."""

    message = TextVariable("message", metadata={"env": "HELLO_MESSAGE"})


def hello_server_block(
    block_id: str = "hello",
    *,
    message: str = "Hello, world!",
    display_name: str = "Hello HTTP",
    image: str = "python:3.13-alpine",
    host_port: int | None = None,
) -> ApplicationBlock:
    """Return a Docker-backed HTTP hello server block.

    The message is loaded through `HelloEnvironment` and passed to the server as
    startup environment. Runtime descriptors redact the actual value.
    """

    env = HelloEnvironment.from_mapping({"message": message})
    return ApplicationBlock(
        BlockSpec(block_id, display_name, health_path="/"),
        DockerImageImplementation(
            image=image,
            command=hello_command(),
            ports={"internal": 8000},
            environment={"HELLO_MESSAGE": env.get("message")},
            host_publications=(
                {"internal": HostPublication.loopback_v4(host_port)}
                if host_port is not None
                else {}
            ),
        ),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )


def hello_command() -> tuple[str, ...]:
    """Return the tiny stdlib HTTP server command for the hello block."""

    return render_python_command("hello.py.j2", message_env="HELLO_MESSAGE", port=8000)
