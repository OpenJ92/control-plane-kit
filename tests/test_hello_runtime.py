from unittest import TestCase, main

from examples.hello_runtime import hello_graph, hello_plan, run_hello_with_client


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


class HelloRuntimeExampleTests(TestCase):
    def test_hello_graph_compiles_to_one_docker_node(self):
        graph = hello_graph("Hello, Earth!")

        self.assertEqual(tuple(graph.nodes), ("hello",))
        self.assertEqual(graph.node("hello").metadata["image"], "python:3.13-alpine")
        self.assertEqual(
            graph.node("hello").non_secret_environment(),
            {"HELLO_MESSAGE": "Hello, Earth!"},
        )
        self.assertNotIn("Hello, Earth!", " ".join(graph.node("hello").metadata["command"]))

    def test_hello_plan_uses_docker_interpreter(self):
        plan = hello_plan("Hello, Mars!")
        descriptors = [activity.descriptor() for activity in plan.activities]

        self.assertEqual(descriptors[0]["type"], "ensure-docker-network")
        self.assertEqual(descriptors[1]["type"], "start-docker-container")
        self.assertEqual(descriptors[1]["container_name"], "hello-demo-docker-hello")
        self.assertEqual(descriptors[1]["environment"], {"HELLO_MESSAGE": "<redacted>"})
        self.assertNotIn("Hello, Mars!", " ".join(descriptors[1]["command"]))

    def test_hello_can_run_through_injected_client(self):
        client = FakeDockerClient()
        state = run_hello_with_client(client, "Hello, Venus!")

        self.assertEqual(client.calls[0], ("ensure_network", "control-plane-kit-network"))
        self.assertEqual(client.calls[1][1], "hello-demo-docker-hello")
        self.assertEqual(client.calls[1][4]["HELLO_MESSAGE"], "Hello, Venus!")
        self.assertNotIn("Hello, Venus!", " ".join(client.calls[1][5]))
        self.assertEqual(state.node("hello").metadata["container_name"], "hello-demo-docker-hello")


if __name__ == "__main__":
    main()
