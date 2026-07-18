from unittest import TestCase, main

from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerPostgresImplementation,
    DockerRuntime,
    ProviderSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.servers import (
    HelloDependency,
    HelloEnvironment,
    hello_command,
    hello_server_block,
)
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

    def test_dependencies_expand_to_paired_http_and_postgres_requirements(self):
        block = hello_server_block(
            "gateway",
            dependencies=(
                HelloDependency("orders"),
                HelloDependency("inventory-api"),
            ),
        )

        self.assertEqual(
            block.sockets.requirement_names(),
            (
                "http-orders",
                "database-orders",
                "http-inventory-api",
                "database-inventory-api",
            ),
        )
        self.assertEqual(
            block.sockets.requirement("http-orders").env_bindings,
            ("HELLO_HTTP_ORDERS_URL",),
        )
        self.assertEqual(
            block.sockets.requirement("database-inventory-api").env_bindings,
            ("HELLO_DATABASE_INVENTORY_API_URL",),
        )

    def test_dependency_names_are_closed_unique_identifiers(self):
        for name in ("", "Orders", "orders_api", "1-orders", "orders/api"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    HelloDependency(name)

        with self.assertRaisesRegex(ValueError, "must be unique"):
            hello_server_block(
                dependencies=(HelloDependency("orders"), HelloDependency("orders"))
            )
        with self.assertRaises(TypeError):
            hello_server_block(dependencies=[HelloDependency("orders")])

    def test_dependency_command_contains_names_not_endpoint_values(self):
        source = hello_command((HelloDependency("orders"),))[2]

        self.assertIn("HELLO_HTTP_ORDERS_URL", source)
        self.assertIn("HELLO_DATABASE_ORDERS_URL", source)
        self.assertIn("MAX_RESPONSE_BYTES", source)
        self.assertIn("NoRedirects", source)
        self.assertNotIn("http://orders", source)
        self.assertNotIn("postgresql://orders", source)

    def test_paired_dependencies_compile_to_socket_derived_environment(self):
        dependency = HelloDependency("orders")
        upstream = hello_server_block("orders", message="Hello, orders!")
        database = DataBlock(
            BlockSpec("orders-db", "Orders database"),
            DockerPostgresImplementation(database="orders"),
            BlockSockets(
                providers=(ProviderSocket("internal", Protocol.POSTGRES),)
            ),
        )
        gateway = hello_server_block("gateway", dependencies=(dependency,))
        recipe = DeploymentRecipe(
            "paired-hello",
            DockerRuntime(
                children=(
                    gateway,
                    upstream,
                    database,
                    SocketConnection(
                        "orders",
                        "internal",
                        "gateway",
                        dependency.http_socket,
                    ),
                    SocketConnection(
                        "orders-db",
                        "internal",
                        "gateway",
                        dependency.database_socket,
                    ),
                )
            ),
        )

        graph = compile_recipe(recipe)
        environment = graph.node("gateway").environment

        self.assertEqual(
            environment[dependency.http_environment],
            graph.node("orders").endpoint("internal").url,
        )
        self.assertEqual(
            environment[dependency.database_environment],
            graph.node("orders-db").endpoint("internal").url,
        )


if __name__ == "__main__":
    main()
