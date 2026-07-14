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
    EnvironmentRequirementSocket,
    ProviderSocket,
    RoleSockets,
    SocketConnection,
)


def recipe() -> DeploymentRecipe:
    api = ApplicationBlock(
        spec=AppSpec("api", "Main API"),
        implementation=DockerImageImplementation("api:latest", ports={"internal": 8000}),
        sockets=RoleSockets(
            requirements=(
                EnvironmentRequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),
                EnvironmentRequirementSocket("INVENTORY_SERVICE_URL", Protocol.HTTP, ("INVENTORY_SERVICE_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    inventory = ApplicationBlock(
        spec=AppSpec("inventory-service", "Inventory Service"),
        implementation=DockerImageImplementation("inventory:latest", ports={"internal": 8015}),
        sockets=RoleSockets(
            requirements=(EnvironmentRequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    postgres = DataBlock(
        spec=DataSpec("postgres", database_name="pottery"),
        implementation=DockerPostgresImplementation(database="pottery"),
        sockets=RoleSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
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
