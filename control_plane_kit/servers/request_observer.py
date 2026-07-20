"""Bounded terminal HTTP request-observer block."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.core.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProviderSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers._templates import render_python_command
from control_plane_kit.servers.http_messages import HttpRequest, HttpResponse
from control_plane_kit.core.types import Protocol


@dataclass(frozen=True)
class RequestObservation:
    """The complete non-sensitive evidence retained by one observer."""

    count: int
    latest_correlation_id: str | None

    def descriptor(self) -> dict[str, object]:
        return {
            "count": self.count,
            "latest_correlation_id": self.latest_correlation_id,
        }


@dataclass
class RequestObserverServer:
    """In-memory behavior model that never retains request-controlled values."""

    max_request_bytes: int = 65_536
    _count: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.max_request_bytes) is not int
            or self.max_request_bytes < 1
            or self.max_request_bytes > 1_048_576
        ):
            raise ValueError("request observer byte limit must be between 1 and 1048576")

    def handle(self, request: HttpRequest) -> HttpResponse:
        if len(request.body) > self.max_request_bytes:
            return HttpResponse.text("Request Entity Too Large", status_code=413)
        self._count += 1
        correlation_id = _correlation_id(self._count)
        return HttpResponse(
            status_code=202,
            headers={"x-cpk-correlation-id": correlation_id},
        )

    def observation(self) -> RequestObservation:
        return RequestObservation(
            self._count,
            None if self._count == 0 else _correlation_id(self._count),
        )


def request_observer_block(
    block_id: str = "request-observer",
    *,
    display_name: str = "HTTP Request Observer",
    image: str = "python:3.14-alpine",
    max_request_bytes: int = 65_536,
    control_secret_reference: str = "secret://request-observer/control-token",
) -> ApplicationBlock:
    """Return a package-owned terminal observer for copied HTTP traffic."""

    command = request_observer_command(max_request_bytes=max_request_bytes)
    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.REQUEST_OBSERVER,
            display_name=display_name,
            health_path="/health",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.METRICS_READABLE,
            ),
            metadata={"behavior": "bounded-request-observer"},
        ),
        DockerImageImplementation(
            image=image,
            command=command,
            ports={"internal": 8080},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_CONTROL_TOKEN",
                    SecretReference(control_secret_reference),
                ),
            ),
        ),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )


def request_observer_command(
    *,
    max_request_bytes: int = 65_536,
    port: int = 8080,
) -> tuple[str, ...]:
    """Render the bounded observer command through the validated Jinja boundary."""

    if (
        type(max_request_bytes) is not int
        or max_request_bytes < 1
        or max_request_bytes > 1_048_576
    ):
        raise ValueError("request observer byte limit must be between 1 and 1048576")
    if type(port) is not int or port < 1 or port > 65_535:
        raise ValueError("request observer port must be between 1 and 65535")
    return render_python_command(
        "request_observer.py.j2",
        max_request_bytes=max_request_bytes,
        port=port,
    )


def _correlation_id(count: int) -> str:
    return f"observation-{count:020d}"
