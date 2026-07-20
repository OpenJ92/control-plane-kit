"""Hello server block factory used by examples and smoke tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from control_plane_kit.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit.contracts import EnvironmentContract, TextVariable
from control_plane_kit.environment import PublicStaticEnvironmentBinding
from control_plane_kit.implementations import DockerImageImplementation, HostPublication
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.types import Protocol


_DEPENDENCY_NAME = re.compile(r"[a-z][a-z0-9-]*")


@dataclass(frozen=True)
class HelloDependency:
    """One paired HTTP and Postgres dependency exposed by the Hello server."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _DEPENDENCY_NAME.fullmatch(self.name) is None:
            raise ValueError(
                "hello dependency name must start with a lowercase letter and "
                "contain only lowercase letters, digits, and hyphens"
            )

    @property
    def http_socket(self) -> str:
        return f"http-{self.name}"

    @property
    def database_socket(self) -> str:
        return f"database-{self.name}"

    @property
    def http_environment(self) -> str:
        return f"HELLO_HTTP_{self._environment_suffix}_URL"

    @property
    def database_environment(self) -> str:
        return f"HELLO_DATABASE_{self._environment_suffix}_URL"

    @property
    def _environment_suffix(self) -> str:
        return self.name.upper().replace("-", "_")

    def descriptor(self) -> dict[str, str]:
        return {
            "name": self.name,
            "http_environment": self.http_environment,
            "database_environment": self.database_environment,
        }


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
    dependencies: tuple[HelloDependency, ...] = (),
) -> ApplicationBlock:
    """Return a Docker-backed HTTP hello server block.

    The message is loaded through `HelloEnvironment` and passed to the server as
    startup environment. Runtime descriptors redact the actual value.
    """

    normalized = _dependencies(dependencies)
    env = HelloEnvironment.from_mapping({"message": message})
    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HELLO,
            display_name=display_name,
            health_path="/",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
        ),
        DockerImageImplementation(
            image=image,
            command=hello_command(normalized),
            ports={"internal": 8000},
            environment=(
                PublicStaticEnvironmentBinding("HELLO_MESSAGE", env.get("message")),
            ),
            host_publications=(
                {"internal": HostPublication.loopback_v4(host_port)}
                if host_port is not None
                else {}
            ),
        ),
        BlockSockets(
            requirements=tuple(
                socket
                for dependency in normalized
                for socket in (
                    RequirementSocket(
                        dependency.http_socket,
                        Protocol.HTTP,
                        (dependency.http_environment,),
                    ),
                    RequirementSocket(
                        dependency.database_socket,
                        Protocol.POSTGRES,
                        (dependency.database_environment,),
                    ),
                )
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def hello_command(
    dependencies: tuple[HelloDependency, ...] = (),
) -> tuple[str, ...]:
    """Return the tiny stdlib HTTP server command for the hello block."""

    normalized = _dependencies(dependencies)
    return render_python_command(
        "hello.py.j2",
        message_env="HELLO_MESSAGE",
        dependencies_json=json.dumps(
            [dependency.descriptor() for dependency in normalized],
            sort_keys=True,
            separators=(",", ":"),
        ),
        port=8000,
    )


def _dependencies(
    values: tuple[HelloDependency, ...],
) -> tuple[HelloDependency, ...]:
    if not isinstance(values, tuple) or not all(
        isinstance(value, HelloDependency) for value in values
    ):
        raise TypeError("hello dependencies must be a tuple of HelloDependency")
    names = tuple(value.name for value in values)
    if len(set(names)) != len(names):
        raise ValueError("hello dependency names must be unique")
    return values
