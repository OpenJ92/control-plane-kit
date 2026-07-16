"""HTTP multiplexer server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, BlockSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeMapVariable, RuntimeValueVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


class HttpMultiplexerRuntime(RuntimeContract):
    """Runtime contract for one-primary, many-observer HTTP multiplexing."""

    primary_target = RuntimeValueVariable("primary_target", required=True)
    observers = RuntimeMapVariable("observers", required=False)


@dataclass
class HttpMultiplexerServer:
    """In-memory multiplexer behavior used by tests and examples.

    The primary target receives the request and owns the response. Observers
    receive the same immutable request value as side effects. Observer failures
    are recorded but do not fail the primary response path.
    """

    targets: Mapping[str, HttpHandler]
    primary_target: str
    observers: Mapping[str, HttpHandler] = field(default_factory=dict)
    runtime: HttpMultiplexerRuntime = field(init=False)
    observer_errors: list[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.runtime = HttpMultiplexerRuntime.from_mapping({
            "primary_target": self.primary_target,
            "observers": {key: key for key in self.observers},
        })
        self._require_primary(self.primary_target)

    def set_primary_target(self, target: str) -> None:
        self._require_primary(target)
        self.runtime.apply_patch({"primary_target": target})

    def replace_observers(self, observers: Mapping[str, HttpHandler]) -> None:
        self.observers = dict(observers)
        self.runtime.apply_patch({"observers": {key: key for key in self.observers}})

    def handle(self, request: HttpRequest) -> HttpResponse:
        primary_target = str(self.runtime.get("primary_target"))
        self._require_primary(primary_target)
        response = self.targets[primary_target](request)
        for observer_id, observer in self.observers.items():
            try:
                observer(request)
            except Exception as exc:  # noqa: BLE001 - observers fail open by design here.
                self.observer_errors.append(f"{observer_id}: {exc}")
        return response

    def _require_primary(self, target: str) -> None:
        if target not in self.targets:
            raise KeyError(f"unknown primary target {target!r}")


def http_multiplexer_block(
    block_id: str = "http-multiplexer",
    *,
    display_name: str = "HTTP Multiplexer",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP multiplexer block.

    The demo Docker command accepts one primary URL and up to two observer URLs.
    Observers receive copied request data; the client receives only the primary
    target response.
    """

    return ProxyBlock(
        BlockSpec(
            block_id,
            display_name,
            health_path="/",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.OBSERVER_MUTABLE,
            ),
            metadata={"behavior": "http-multiplexer"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_multiplexer_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("primary", Protocol.HTTP, ("MULTIPLEXER_PRIMARY_URL",)),
                RequirementSocket(
                    "observer-a",
                    Protocol.HTTP,
                    ("MULTIPLEXER_OBSERVER_A_URL",),
                    required=False,
                ),
                RequirementSocket(
                    "observer-b",
                    Protocol.HTTP,
                    ("MULTIPLEXER_OBSERVER_B_URL",),
                    required=False,
                ),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_multiplexer_command() -> tuple[str, ...]:
    """Return a tiny stdlib HTTP multiplexer command for Docker examples."""

    return render_python_command(
        "http_multiplexer.py.j2",
        primary_env="MULTIPLEXER_PRIMARY_URL",
        observer_a_env="MULTIPLEXER_OBSERVER_A_URL",
        observer_b_env="MULTIPLEXER_OBSERVER_B_URL",
        port=8080,
    )
