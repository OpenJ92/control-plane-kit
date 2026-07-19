"""HTTP proxy server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.algebra import BlockSockets, PackageServerProduct, PackageServerSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeValueVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.servers.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.types import Protocol


class HttpProxyRuntime(RuntimeContract):
    """Runtime contract for one-target HTTP proxy state."""

    target = RuntimeValueVariable("target", required=True)


@dataclass
class HttpProxyServer:
    """In-memory HTTP proxy behavior used by tests and examples.

    The proxy forwards method, path, query, headers, and body unchanged to the
    active target handler and returns the target response.
    """

    targets: Mapping[str, HttpHandler]
    target: str
    runtime: HttpProxyRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.runtime = HttpProxyRuntime.from_mapping({"target": self.target})
        self._require_target(self.target)

    def set_target(self, target: str) -> None:
        self._require_target(target)
        self.runtime.apply_patch({"target": target})

    def handle(self, request: HttpRequest) -> HttpResponse:
        target = str(self.runtime.get("target"))
        self._require_target(target)
        return self.targets[target](request)

    def _require_target(self, target: str) -> None:
        if target not in self.targets:
            raise KeyError(f"unknown target {target!r}")


def http_proxy_block(
    block_id: str = "http-proxy",
    *,
    display_name: str = "HTTP Proxy",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP proxy block.

    The target URL is supplied by a socket connection through `PROXY_TARGET_URL`.
    """

    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_PROXY,
            display_name=display_name,
            health_path="/",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
            metadata={"behavior": "http-proxy"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_proxy_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(RequirementSocket("target", Protocol.HTTP, ("PROXY_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_proxy_command() -> tuple[str, ...]:
    """Return a tiny stdlib HTTP proxy command for Docker examples."""

    return render_python_command("http_forwarder.py.j2", target_env="PROXY_TARGET_URL", port=8080)
