"""Built-in block factories."""

from __future__ import annotations

from control_plane_kit.algebra import (
    AppSpec,
    ApplicationBlock,
    ProxyBlock,
    ProxySpec,
    ProviderSocket,
    RoleSockets,
    RuntimeImplementation,
    RuntimeRequirementSocket,
)
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.control_routes import ControlRouteSetName
from control_plane_kit.implementations import PlanOnlyImplementation
from control_plane_kit.types import Protocol


def hello_application_block(
    role_id: str = "hello",
    *,
    world: str = "world",
    display_name: str | None = None,
    implementation: RuntimeImplementation | None = None,
    provider_socket: str = "internal",
) -> ApplicationBlock:
    """Return a tiny HTTP application block for router switch demos."""

    return ApplicationBlock(
        spec=AppSpec(
            role_id=role_id,
            display_name=display_name or f"Hello {world}",
            metadata={"hello_world": world},
        ),
        implementation=implementation or PlanOnlyImplementation(kind="hello-app"),
        sockets=RoleSockets(providers=(ProviderSocket(provider_socket, Protocol.HTTP),)),
    )


def http_active_router_block(
    role_id: str = "http-active-router",
    *,
    display_name: str | None = None,
    implementation: RuntimeImplementation | None = None,
    provider_socket: str = "internal",
) -> ProxyBlock:
    """Return a generic HTTP active-target router block.

    The block exposes one HTTP provider socket and one runtime target-registry requirement socket. Its downstream targets are
    runtime control state, mutated through the `targets` route set rather than
    encoded as fixed environment-backed requirement sockets.
    """

    return ProxyBlock(
        spec=ProxySpec(
            role_id=role_id,
            display_name=display_name or "HTTP Active Router",
            behavior="active-target",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.SWITCHABLE,
                CapabilityName.DRAINABLE,
            ),
        ),
        implementation=implementation or PlanOnlyImplementation(kind="http-active-router"),
        sockets=RoleSockets(
            requirements=(RuntimeRequirementSocket("targets", Protocol.HTTP, ControlRouteSetName.TARGETS),),
            providers=(ProviderSocket(provider_socket, Protocol.HTTP),),
        ),
    )
