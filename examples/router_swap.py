"""Two API versions behind a runtime-switchable router."""

from control_plane_kit import (
    AppSpec,
    ApplicationBlock,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    Protocol,
    ProviderSocket,
    RoleSockets,
    SocketConnection,
    http_active_router_block,
)


def recipe(active: str = "api-v1") -> DeploymentRecipe:
    api_v1 = ApplicationBlock(
        AppSpec("api-v1"),
        DockerImageImplementation("api:v1", ports={"internal": 8000}),
        RoleSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    api_v2 = ApplicationBlock(
        AppSpec("api-v2"),
        DockerImageImplementation("api:v2", ports={"internal": 8000}),
        RoleSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    return DeploymentRecipe(
        f"router-swap-{active}",
        DockerRuntime(children=(
            api_v1,
            api_v2,
            http_active_router_block("api-router"),
            SocketConnection(active, "internal", "api-router", "targets", edge_id="api-router.active"),
        )),
    )
