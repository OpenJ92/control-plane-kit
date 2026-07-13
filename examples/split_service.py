"""A monolith split into API plus extracted service."""

from control_plane_kit import (
    AppSpec,
    ApplicationBlock,
    DataSpec,
    DataBlock,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerPostgresImplementation,
    DockerRuntime,
    Protocol,
    RoleInputSocket,
    RoleOutputSocket,
    RoleSockets,
    SocketConnection,
)


def recipe() -> DeploymentRecipe:
    api = ApplicationBlock(
        spec=AppSpec("api", "Main API"),
        implementation=DockerImageImplementation("api:latest", ports={"internal": 8000}),
        sockets=RoleSockets(
            inputs=(
                RoleInputSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),
                RoleInputSocket("INVENTORY_SERVICE_URL", Protocol.HTTP, ("INVENTORY_SERVICE_URL",)),
            ),
            outputs=(RoleOutputSocket("internal", Protocol.HTTP),),
        ),
    )
    inventory = ApplicationBlock(
        spec=AppSpec("inventory-service", "Inventory Service"),
        implementation=DockerImageImplementation("inventory:latest", ports={"internal": 8015}),
        sockets=RoleSockets(
            inputs=(RoleInputSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            outputs=(RoleOutputSocket("internal", Protocol.HTTP),),
        ),
    )
    postgres = DataBlock(
        spec=DataSpec("postgres", database_name="pottery"),
        implementation=DockerPostgresImplementation(database="pottery"),
        sockets=RoleSockets(outputs=(RoleOutputSocket("internal", Protocol.POSTGRES),)),
    )
    return DeploymentRecipe(
        "split-service",
        DockerRuntime(children=(
            api,
            inventory,
            postgres,
            SocketConnection("postgres", "internal", "api", "DATABASE_URL"),
            SocketConnection("postgres", "internal", "inventory-service", "DATABASE_URL"),
            SocketConnection("inventory-service", "internal", "api", "INVENTORY_SERVICE_URL"),
        )),
    )
