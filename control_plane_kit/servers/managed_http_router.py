"""HTTP active router with a shared data path and authenticated control plane."""

from dataclasses import dataclass

from control_plane_kit.algebra import (
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProviderSocket,
    ProxyBlock,
    RequirementSocket,
)
from control_plane_kit.adapters.http_forwarding import forward_http_request
from control_plane_kit.capabilities import CapabilityName
from control_plane_kit.contracts import EnvironmentContract, TextVariable
from control_plane_kit.implementations import (
    DockerImageImplementation,
    HostPublication,
)
from control_plane_kit.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit.servers.block_control import BlockControlState, create_block_control_app
from control_plane_kit.types import Protocol, SocketBinding


class ManagedRouterEnvironment(EnvironmentContract):
    """Startup contract for the package-managed two-target router."""

    block_id = TextVariable("block_id", metadata={"env": "CPK_ROUTER_BLOCK_ID"})
    blue_url = TextVariable("blue_url", metadata={"env": "CPK_ROUTER_BLUE_URL"})
    green_url = TextVariable("green_url", metadata={"env": "CPK_ROUTER_GREEN_URL"})
    active_target = TextVariable(
        "active_target", metadata={"env": "CPK_ROUTER_ACTIVE_TARGET"}
    )
    control_token = TextVariable(
        "control_token", metadata={"env": "CPK_CONTROL_TOKEN"}
    )


@dataclass(frozen=True)
class ManagedRouterSettings:
    """Validated values used to construct one managed router process."""

    block_id: str
    targets: dict[str, str]
    active_target: str
    control_token: str

    @classmethod
    def from_process(cls) -> "ManagedRouterSettings":
        contract = ManagedRouterEnvironment.from_process()
        return cls(
            block_id=contract.get("block_id"),
            targets={
                "hello-blue": contract.get("blue_url"),
                "hello-green": contract.get("green_url"),
            },
            active_target=contract.get("active_target"),
            control_token=contract.get("control_token"),
        )


def create_managed_http_router_app(settings: ManagedRouterSettings):
    """Create one FastAPI app serving both forwarding and control routes."""

    state = BlockControlState(
        block_id=settings.block_id,
        capabilities=(
            CapabilityName.HEALTH_CHECKABLE,
            CapabilityName.TARGET_MUTABLE,
            CapabilityName.SWITCHABLE,
            CapabilityName.DRAINABLE,
        ),
        targets=dict(settings.targets),
        active_target=settings.active_target,
    )
    app = create_block_control_app(
        state,
        token=settings.control_token,
        execution_mode=True,
    )

    from fastapi import HTTPException, Request
    from fastapi.responses import Response
    @app.api_route(
        "/{path:path}",
        methods=("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"),
    )
    async def forward(path: str, request: Request) -> Response:
        targets = state.runtime.get("targets") or {}
        active = str(state.runtime.get("active_target") or "")
        target = targets.get(active)
        if target is None:
            raise HTTPException(status_code=503, detail="router has no active target")
        url = f"{target.rstrip('/')}/{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length", "authorization", "x-control-plane-token"}
        }
        try:
            response = await forward_http_request(
                request.method,
                url,
                headers=headers,
                body=await request.body(),
            )
        except Exception as error:
            raise HTTPException(status_code=502, detail="active target request failed") from error
        return Response(
            content=response.body,
            status_code=response.status_code,
            media_type=response.content_type,
        )

    return app


def create_managed_http_router_app_from_environment():
    """Uvicorn factory for a process configured solely through its contract."""

    return create_managed_http_router_app(ManagedRouterSettings.from_process())


def managed_http_router_block(
    block_id: str = "managed-router",
    *,
    image: str = "control-plane-kit-live-test:local",
    host_port: int | None = None,
    control_secret_reference: str = "secret://gate-d/router-control",
) -> ProxyBlock:
    """Return a graph-wired, switchable two-target router block."""

    return ProxyBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.MANAGED_HTTP_ROUTER,
            display_name="Managed HTTP Active Router",
            health_path="/",
            capabilities=(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.SWITCHABLE,
                CapabilityName.DRAINABLE,
            ),
            metadata={"behavior": "managed-http-active-router"},
        ),
        DockerImageImplementation(
            image=image,
            command=(
                "uvicorn",
                "control_plane_kit.servers.managed_http_router:create_managed_http_router_app_from_environment",
                "--factory",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
            ),
            ports={"internal": 8080},
            environment={
                "CPK_ROUTER_BLOCK_ID": block_id,
                "CPK_ROUTER_ACTIVE_TARGET": "hello-blue",
            },
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_CONTROL_TOKEN",
                    SecretReference(control_secret_reference),
                ),
            ),
            host_publications={
                "internal": HostPublication.loopback_v4(host_port)
            },
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("target-blue", Protocol.HTTP, ("CPK_ROUTER_BLUE_URL",)),
                RequirementSocket("target-green", Protocol.HTTP, ("CPK_ROUTER_GREEN_URL",)),
                RequirementSocket(
                    "active",
                    Protocol.HTTP,
                    (),
                    binding=SocketBinding.RUNTIME_CONTROL,
                ),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
