"""Single application wired to one Postgres provider."""

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
    BlockSockets,
    SocketConnection,
)


def recipe() -> DeploymentRecipe:
    app = ApplicationBlock(
        spec=BlockSpec("orders-api", "Orders API"),
        implementation=DockerImageImplementation(
            image="orders-api:latest",
            command=("python", "-m", "orders_api"),
            ports={"internal": 8000},
        ),
        sockets=BlockSockets(
            requirements=(RequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    postgres = DataBlock(
        spec=BlockSpec("postgres", "Orders Postgres"),
        implementation=DockerPostgresImplementation(database="orders"),
        sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    return DeploymentRecipe(
        "app-with-postgres",
        DockerRuntime(children=(
            app,
            postgres,
            SocketConnection("postgres", "internal", "orders-api", "DATABASE_URL"),
        )),
    )
