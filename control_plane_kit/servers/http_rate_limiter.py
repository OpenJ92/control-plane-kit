"""HTTP rate-limiter server block and in-memory behavior model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.core.algebra import BlockSockets, PackageServerProduct, PackageServerSpec, ProviderSocket, ProxyBlock, RequirementSocket
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.contracts import RuntimeContract, RuntimeValueVariable
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.products.servers.support.command_rendering import render_python_command
from control_plane_kit.products.servers.support.http_messages import HttpHandler, HttpRequest, HttpResponse
from control_plane_kit.core.types import Protocol


class HttpRateLimiterRuntime(RuntimeContract):
    """Runtime contract for quota-gated HTTP forwarding."""

    target = RuntimeValueVariable("target", required=True)
    limit = RuntimeValueVariable("limit", required=True)
    remaining = RuntimeValueVariable("remaining", required=True)


@dataclass
class HttpRateLimiterServer:
    """In-memory rate limiter behavior used by tests and examples.

    This first limiter is deliberately not time-based. It is a deterministic
    gate: allowed requests decrement remaining quota and forward to the target;
    exhausted quota returns a generated 429 response.
    """

    targets: Mapping[str, HttpHandler]
    target: str
    limit: int
    runtime: HttpRateLimiterRuntime = field(init=False)

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("rate limit must be non-negative")
        self._require_target(self.target)
        self.runtime = HttpRateLimiterRuntime.from_mapping({
            "target": self.target,
            "limit": self.limit,
            "remaining": self.limit,
        })

    def reset(self, *, limit: int | None = None) -> None:
        next_limit = self.limit if limit is None else limit
        if next_limit < 0:
            raise ValueError("rate limit must be non-negative")
        self.limit = next_limit
        self.runtime.apply_patch({"limit": next_limit, "remaining": next_limit})

    def handle(self, request: HttpRequest) -> HttpResponse:
        remaining = int(self.runtime.get("remaining"))
        if remaining <= 0:
            return HttpResponse.text("Too Many Requests", status_code=429)
        self.runtime.apply_patch({"remaining": remaining - 1})
        target = str(self.runtime.get("target"))
        self._require_target(target)
        return self.targets[target](request)

    def _require_target(self, target: str) -> None:
        if target not in self.targets:
            raise KeyError(f"unknown target {target!r}")


def http_rate_limiter_block(
    block_id: str = "http-rate-limiter",
    *,
    display_name: str = "HTTP Rate Limiter",
    image: str = "python:3.13-alpine",
) -> ProxyBlock:
    """Return a Docker-backed HTTP rate-limiter block."""

    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.HTTP_RATE_LIMITER,
            display_name=display_name,
            health_path="/",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
            metadata={"behavior": "http-rate-limiter"},
        ),
        DockerImageImplementation(
            image=image,
            command=http_rate_limiter_command(),
            ports={"internal": 8080},
        ),
        BlockSockets(
            requirements=(RequirementSocket("target", Protocol.HTTP, ("RATE_LIMIT_TARGET_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


def http_rate_limiter_command(*, port: int = 8080) -> tuple[str, ...]:
    """Return a tiny stdlib quota-gated HTTP forwarding command."""

    if type(port) is not int or port < 1 or port > 65_535:
        raise ValueError("rate limiter port must be between 1 and 65535")

    return render_python_command(
        "http_rate_limiter.py.j2",
        target_env="RATE_LIMIT_TARGET_URL",
        limit_env="RATE_LIMIT_REQUESTS",
        default_limit=60,
        port=port,
    )
