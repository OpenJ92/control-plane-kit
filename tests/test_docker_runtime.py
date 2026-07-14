from unittest import TestCase, main

from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    BlockSockets,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    ExternalHttpImplementation,
    ExternalRuntime,
    Protocol,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.docker_runtime import DockerRuntimeInterpreter, UnsupportedDockerRuntimeFeature
from examples.app_with_postgres import recipe as app_recipe


class DockerRuntimePlanningTests(TestCase):
    def test_plan_start_creates_network_and_container_activities(self):
        graph = compile_recipe(app_recipe())
        plan = DockerRuntimeInterpreter(project_name="demo").plan_start(graph, "docker")
        descriptors = [activity.descriptor() for activity in plan.activities]

        self.assertEqual(plan.action, "start")
        self.assertEqual(descriptors[0]["type"], "ensure-docker-network")
        self.assertEqual(descriptors[1]["type"], "start-docker-container")
        self.assertEqual(descriptors[1]["container_name"], "demo-docker-orders-api")
        self.assertIn("DATABASE_URL", descriptors[1]["environment"])
        self.assertEqual(descriptors[2]["image"], "postgres:16-alpine")

    def test_non_docker_runtime_is_rejected_before_planning(self):
        graph = compile_recipe(
            DeploymentRecipe(
                "external-only",
                ExternalRuntime(
                    children=(
                        ApplicationBlock(
                            BlockSpec("external-api"),
                            ExternalHttpImplementation("https://example.invalid"),
                            BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
                        ),
                    )
                ),
            )
        )

        with self.assertRaises(UnsupportedDockerRuntimeFeature):
            DockerRuntimeInterpreter().plan_start(graph, "external")

    def test_cross_runtime_edges_are_preserved_but_not_realized(self):
        consumer = ApplicationBlock(
            BlockSpec("consumer"),
            DockerImageImplementation("consumer:latest", ports={"internal": 8000}),
            BlockSockets(
                requirements=(RequirementSocket("UPSTREAM_URL", Protocol.HTTP, ("UPSTREAM_URL",)),),
                providers=(ProviderSocket("internal", Protocol.HTTP),),
            ),
        )
        provider = ApplicationBlock(
            BlockSpec("provider"),
            ExternalHttpImplementation("https://example.invalid"),
            BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
        )
        graph = compile_recipe(
            DeploymentRecipe(
                "cross-runtime",
                DockerRuntime(
                    children=(
                        consumer,
                        ExternalRuntime(children=(provider,)),
                        SocketConnection("provider", "internal", "consumer", "UPSTREAM_URL"),
                    )
                ),
            )
        )

        self.assertEqual(len(graph.edges), 1)
        with self.assertRaises(UnsupportedDockerRuntimeFeature):
            DockerRuntimeInterpreter().plan_start(graph, "docker")


if __name__ == "__main__":
    main()
