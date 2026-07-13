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
    RoleInputSocket,
    RoleOutputSocket,
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
            inputs=(RoleInputSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            outputs=(RoleOutputSocket("internal", Protocol.HTTP),),
        ),
    )
    postgres = DataBlock(
        spec=DataSpec("postgres", "Orders Postgres", database_name="orders"),
        implementation=DockerPostgresImplementation(database="orders"),
        sockets=RoleSockets(outputs=(RoleOutputSocket("internal", Protocol.POSTGRES),)),
    )
    return DeploymentRecipe(
        "app-with-postgres",
        DockerRuntime(children=(
            app,
            postgres,
            SocketConnection("postgres", "internal", "orders-api", "DATABASE_URL"),
        )),
    )
