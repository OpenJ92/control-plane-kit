"""Single application wired to one Postgres provider."""

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
    app = ApplicationBlock(
        spec=AppSpec("orders-api", "Orders API"),
        implementation=DockerImageImplementation(
            image="orders-api:latest",
            command=("python", "-m", "orders_api"),
            ports={"internal": 8000},
        ),
        sockets=RoleSockets(
            requirements=(EnvironmentRequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    postgres = DataBlock(
        spec=DataSpec("postgres", "Orders Postgres", database_name="orders"),
        implementation=DockerPostgresImplementation(database="orders"),
        sockets=RoleSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    return DeploymentRecipe(
        "app-with-postgres",
        DockerRuntime(children=(
            app,
            postgres,
            SocketConnection("postgres", "internal", "orders-api", "DATABASE_URL"),
        )),
    )
