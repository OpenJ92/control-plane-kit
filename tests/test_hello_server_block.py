from unittest import TestCase, main

from control_plane_kit import DockerRuntime, DeploymentRecipe, compile_recipe
from control_plane_kit.servers import HelloEnvironment, hello_server_block
from control_plane_kit.types import Protocol


class HelloServerBlockTests(TestCase):
    def test_hello_environment_loads_message_contract(self):
        env = HelloEnvironment.from_mapping({"HELLO_MESSAGE": "Hello, contract!"})

        self.assertEqual(env.get("message"), "Hello, contract!")
        self.assertEqual(env.descriptor()["variables"]["message"]["value"], {"present": True, "redacted": True})

    def test_hello_server_block_advertises_http_provider(self):
        block = hello_server_block("hello", message="Hello, block!")

        self.assertEqual(block.block_id, "hello")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.implementation.environment, {"HELLO_MESSAGE": "Hello, block!"})
        self.assertNotIn("Hello, block!", " ".join(block.implementation.command))

    def test_hello_server_block_compiles_under_docker_runtime(self):
        recipe = DeploymentRecipe(
            "hello-block-demo",
            DockerRuntime(children=(hello_server_block("hello", message="Hello, graph!"),)),
        )

        graph = compile_recipe(recipe)

        self.assertEqual(graph.node("hello").metadata["environment"], {"HELLO_MESSAGE": "Hello, graph!"})
        self.assertEqual(graph.node("hello").endpoint("internal").protocol, Protocol.HTTP)


if __name__ == "__main__":
    main()
