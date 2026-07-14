from unittest import TestCase, main

from examples.postgres_runtime import postgres_plan, run_postgres_with_client
from examples.router_runtime import router_graph, router_plan, run_router_with_client


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


class RuntimeCompositionExampleTests(TestCase):
    def test_postgres_plan_redacts_environment_descriptors(self):
        plan = postgres_plan()
        api_descriptor = plan.activities[1].descriptor()

        self.assertEqual(api_descriptor["environment"], {"DATABASE_URL": "<redacted>"})
        self.assertEqual(plan.activities[2].descriptor()["image"], "postgres:16-alpine")

    def test_postgres_execution_still_receives_actual_environment(self):
        client = FakeDockerClient()
        state = run_postgres_with_client(client)

        self.assertIn("postgresql+psycopg://", client.calls[1][4]["DATABASE_URL"])
        self.assertEqual(state.node("postgres").metadata["image"], "postgres:16-alpine")

    def test_router_graph_switches_active_target_by_socket_connection(self):
        v1 = router_graph("api-v1")
        v2 = router_graph("api-v2")

        self.assertEqual(v1.node("api-router").environment["ACTIVE_TARGET_URL"], v1.node("api-v1").endpoint("internal").url)
        self.assertEqual(v2.node("api-router").environment["ACTIVE_TARGET_URL"], v2.node("api-v2").endpoint("internal").url)

    def test_router_plan_runs_three_docker_containers(self):
        plan = router_plan("api-v2")
        descriptors = [activity.descriptor() for activity in plan.activities]

        self.assertEqual(descriptors[0]["type"], "ensure-docker-network")
        self.assertEqual(
            [descriptor["node_id"] for descriptor in descriptors[1:]],
            ["api-v1", "api-v2", "api-router"],
        )
        self.assertEqual(descriptors[3]["environment"], {"ACTIVE_TARGET_URL": "<redacted>"})

    def test_router_execution_receives_actual_active_target(self):
        client = FakeDockerClient()
        state = run_router_with_client(client, "api-v2")

        router_start = client.calls[3]
        self.assertEqual(router_start[1], "router-demo-docker-api-router")
        self.assertEqual(router_start[4]["ACTIVE_TARGET_URL"], state.node("api-v2").endpoints["internal"].url)


if __name__ == "__main__":
    main()
