"""A monolith split into API plus extracted service."""

from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerPostgresImplementation,
    DockerRuntime,
    Protocol,
    RequirementSocket,
    ProviderSocket,
    RoleSockets,
    SocketConnection,
)


def recipe() -> DeploymentRecipe:
    api = ApplicationBlock(
        spec=BlockSpec("api", "Main API"),
        implementation=DockerImageImplementation("api:latest", ports={"internal": 8000}),
        sockets=RoleSockets(
            inputs=(
                RequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),
                RequirementSocket("INVENTORY_SERVICE_URL", Protocol.HTTP, ("INVENTORY_SERVICE_URL",)),
            ),
            outputs=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    inventory = ApplicationBlock(
        spec=BlockSpec("inventory-service", "Inventory Service"),
        implementation=DockerImageImplementation("inventory:latest", ports={"internal": 8015}),
        sockets=RoleSockets(
            inputs=(RequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            outputs=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    postgres = DataBlock(
        spec=BlockSpec("postgres"),
        implementation=DockerPostgresImplementation(database="pottery"),
        sockets=RoleSockets(outputs=(ProviderSocket("internal", Protocol.POSTGRES),)),
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
