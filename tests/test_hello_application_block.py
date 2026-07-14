from unittest import TestCase, main

from control_plane_kit import (
    DeploymentRecipe,
    DockerRuntime,
    Protocol,
    compile_recipe,
    hello_application_block,
)


class HelloApplicationBlockTests(TestCase):
    def test_hello_application_block_compiles_as_http_provider(self):
        graph = compile_recipe(
            DeploymentRecipe(
                "hello-demo",
                DockerRuntime(children=(hello_application_block("hello-earth", world="earth"),)),
            )
        )
        node = graph.node("hello-earth")

        self.assertEqual(node.provider_socket("internal").protocol, Protocol.HTTP)
        self.assertEqual(node.endpoint("internal").url, "plan://hello-earth/internal")
        self.assertEqual(node.metadata["display_name"], "Hello earth")
        self.assertEqual(node.metadata["hello_world"], "earth")


if __name__ == "__main__":
    main()
