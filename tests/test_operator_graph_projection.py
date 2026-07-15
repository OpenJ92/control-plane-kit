from unittest import TestCase, main

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DockerPostgresImplementation,
    DockerRuntime,
    LocalSourceImplementation,
    Protocol,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.algebra import DeploymentRecipe
from control_plane_kit.projections import project_operator_graph


def _recipe_with_dangling_requirement() -> DeploymentRecipe:
    postgres = DataBlock(
        BlockSpec("postgres", display_name="Postgres", metadata={"admin_token": "secret"}),
        DockerPostgresImplementation(password="not-for-output"),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    api = ApplicationBlock(
        BlockSpec(
            "api",
            display_name="API",
            metadata={"api_key": "hidden", "public_note": "visible"},
        ),
        LocalSourceImplementation(
            repo_path="/tmp/api",
            run_command=("python", "-m", "api"),
            ports={"internal": 8000},
        ),
        BlockSockets(
            requirements=(
                RequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),
                RequirementSocket("POLICY_URL", Protocol.HTTP, ("POLICY_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    root = DockerRuntime(
        runtime_id="local",
        children=(
            postgres,
            api,
            SocketConnection("postgres", "internal", "api", "DATABASE_URL"),
        ),
        metadata={"runtime_secret": "hidden", "owner": "dev"},
    )
    return DeploymentRecipe("operator-projection", root)


class OperatorGraphProjectionTests(TestCase):
    def test_projection_includes_runtimes_nodes_sockets_and_edges(self):
        graph = compile_recipe(_recipe_with_dangling_requirement())

        projection = project_operator_graph(graph).descriptor()

        self.assertEqual(projection["name"], "operator-projection")
        self.assertEqual(projection["runtimes"][0]["runtime_id"], "local")
        self.assertEqual(projection["runtimes"][0]["children"], ["api", "postgres"])
        self.assertEqual([node["node_id"] for node in projection["nodes"]], ["api", "postgres"])
        self.assertEqual(
            projection["edges"][0],
            {
                "edge_id": "postgres.internal-to-api.DATABASE_URL",
                "provider": {"node_id": "postgres", "socket": "internal"},
                "consumer": {"node_id": "api", "socket": "DATABASE_URL"},
                "protocol": "postgres",
            },
        )

    def test_projection_marks_connected_and_dangling_requirement_sockets(self):
        graph = compile_recipe(_recipe_with_dangling_requirement())

        api = project_operator_graph(graph).descriptor()["nodes"][0]
        requirements = {socket["name"]: socket for socket in api["requirements"]}

        self.assertTrue(requirements["DATABASE_URL"]["connected"])
        self.assertFalse(requirements["POLICY_URL"]["connected"])
        warnings = project_operator_graph(graph).descriptor()["warnings"]
        self.assertEqual(
            warnings,
            [
                {
                    "code": "dangling-required-socket",
                    "node_id": "api",
                    "socket": "POLICY_URL",
                    "message": "required socket api.POLICY_URL is not connected",
                }
            ],
        )

    def test_projection_redacts_secret_like_metadata(self):
        graph = compile_recipe(_recipe_with_dangling_requirement())

        projection = project_operator_graph(graph).descriptor()
        api = projection["nodes"][0]
        postgres = projection["nodes"][1]
        runtime = projection["runtimes"][0]

        self.assertEqual(api["metadata"]["api_key"], "<redacted>")
        self.assertEqual(api["metadata"]["public_note"], "visible")
        self.assertEqual(postgres["metadata"]["admin_token"], "<redacted>")
        self.assertEqual(runtime["metadata"]["runtime_secret"], "<redacted>")
        self.assertEqual(runtime["metadata"]["owner"], "dev")

    def test_projection_is_deterministic(self):
        graph = compile_recipe(_recipe_with_dangling_requirement())

        first = project_operator_graph(graph).descriptor()
        second = project_operator_graph(graph).descriptor()

        self.assertEqual(first, second)


if __name__ == "__main__":
    main()
