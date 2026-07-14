from unittest import TestCase, main

from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    BlockSockets,
    CleanupPolicy,
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


class FakeDockerClient:
    def __init__(self):
        self.calls = []

    def ensure_network(self, name):
        self.calls.append(("ensure_network", name))

    def start_container(self, *, name, image, network, environment, command):
        self.calls.append(("start_container", name, image, network, dict(environment), tuple(command)))

    def stop_container(self, name):
        self.calls.append(("stop_container", name))

    def remove_container(self, name):
        self.calls.append(("remove_container", name))

    def remove_network(self, name):
        self.calls.append(("remove_network", name))


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


class DockerRuntimeExecutorTests(TestCase):
    def test_up_executes_planned_activities_and_returns_runtime_state(self):
        graph = compile_recipe(app_recipe())
        client = FakeDockerClient()
        interpreter = DockerRuntimeInterpreter(project_name="demo", client=client)

        state = interpreter.up(graph, "docker")

        self.assertEqual(client.calls[0], ("ensure_network", "control-plane-kit-network"))
        self.assertEqual(client.calls[1][0], "start_container")
        self.assertEqual(client.calls[1][1], "demo-docker-orders-api")
        self.assertEqual(client.calls[1][4]["DATABASE_URL"], graph.node("postgres").endpoint("internal").url)
        self.assertEqual(state.node("orders-api").metadata["container_name"], "demo-docker-orders-api")
        self.assertTrue(state.node("postgres").healthy)

    def test_down_removes_owned_containers_and_network_by_default(self):
        graph = compile_recipe(app_recipe())
        client = FakeDockerClient()
        interpreter = DockerRuntimeInterpreter(project_name="demo", client=client)
        state = interpreter.up(graph, "docker")
        client.calls.clear()

        stopped = interpreter.down(state)

        self.assertIn(("stop_container", "demo-docker-orders-api"), client.calls)
        self.assertIn(("remove_container", "demo-docker-orders-api"), client.calls)
        self.assertIn(("remove_network", "control-plane-kit-network"), client.calls)
        self.assertEqual(stopped.nodes, {})
        self.assertTrue(stopped.metadata["stopped"])

    def test_down_preserves_containers_when_cleanup_policy_preserves(self):
        graph = compile_recipe(app_recipe())
        client = FakeDockerClient()
        interpreter = DockerRuntimeInterpreter(
            project_name="demo",
            cleanup_policy=CleanupPolicy.PRESERVE_ON_STOP,
            client=client,
        )
        state = interpreter.up(graph, "docker")
        client.calls.clear()

        stopped = interpreter.down(state)

        self.assertIn(("stop_container", "demo-docker-orders-api"), client.calls)
        self.assertNotIn(("remove_container", "demo-docker-orders-api"), client.calls)
        self.assertNotIn(("remove_network", "control-plane-kit-network"), client.calls)
        self.assertIn("orders-api", stopped.nodes)


if __name__ == "__main__":
    main()
