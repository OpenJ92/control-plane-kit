"""Two API versions behind a plan-only router."""

from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    PlanOnlyImplementation,
    Protocol,
    ProxyBlock,
    RoleInputSocket,
    RoleOutputSocket,
    RoleSockets,
    SocketConnection,
)


def recipe(active: str = "api-v1") -> DeploymentRecipe:
    api_v1 = ApplicationBlock(
        BlockSpec("api-v1"),
        DockerImageImplementation("api:v1", ports={"internal": 8000}),
        RoleSockets(outputs=(RoleOutputSocket("internal", Protocol.HTTP),)),
    )
    api_v2 = ApplicationBlock(
        BlockSpec("api-v2"),
        DockerImageImplementation("api:v2", ports={"internal": 8000}),
        RoleSockets(outputs=(RoleOutputSocket("internal", Protocol.HTTP),)),
    )
    router = ProxyBlock(
        BlockSpec("api-router", metadata={"behavior": "active-target"}),
        PlanOnlyImplementation("http-router", {"internal": "http://api-router:8080"}),
        RoleSockets(
            inputs=(RoleInputSocket("active", Protocol.HTTP, ("ACTIVE_TARGET_URL",)),),
            outputs=(RoleOutputSocket("internal", Protocol.HTTP),),
        ),
    )
    return DeploymentRecipe(
        f"router-swap-{active}",
        DockerRuntime(children=(
            api_v1,
            api_v2,
            router,
            SocketConnection(active, "internal", "api-router", "active", edge_id="api-router.active"),
        )),
    )
