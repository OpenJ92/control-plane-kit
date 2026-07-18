import ast
from unittest import TestCase, main

from control_plane_kit.servers import (
    HelloDependency,
    hello_command,
    http_active_router_command,
    http_multiplexer_command,
    http_proxy_command,
    http_rate_limiter_command,
    http_weighted_load_balancer_command,
)
from control_plane_kit.servers._templates import (
    GeneratedServerSyntaxError,
    validated_python_command,
)


class ServerCommandTemplateTests(TestCase):
    def test_server_commands_render_python_scripts(self):
        commands = (
            hello_command(),
            http_proxy_command(),
            http_active_router_command(),
            http_weighted_load_balancer_command(),
            http_multiplexer_command(),
            http_rate_limiter_command(),
        )

        for command in commands:
            self.assertEqual(command[:2], ("python", "-c"))
            self.assertIsInstance(ast.parse(command[2]), ast.Module)

    def test_rendered_commands_include_expected_environment_names(self):
        self.assertIn("HELLO_MESSAGE", hello_command()[2])
        dependency_source = hello_command((HelloDependency("orders"),))[2]
        self.assertIn("HELLO_HTTP_ORDERS_URL", dependency_source)
        self.assertIn("HELLO_DATABASE_ORDERS_URL", dependency_source)
        self.assertIn("PROXY_TARGET_URL", http_proxy_command()[2])
        self.assertIn("ACTIVE_TARGET_URL", http_active_router_command()[2])
        self.assertIn("BALANCER_TARGET_A_URL", http_weighted_load_balancer_command()[2])
        self.assertIn("MULTIPLEXER_PRIMARY_URL", http_multiplexer_command()[2])
        self.assertIn("RATE_LIMIT_TARGET_URL", http_rate_limiter_command()[2])

    def test_invalid_rendered_source_fails_without_retaining_source(self):
        sensitive_source = "TOKEN = 'do-not-retain'\ndef broken(:\n"

        with self.assertRaises(GeneratedServerSyntaxError) as raised:
            validated_python_command(
                sensitive_source,
                template_name="broken.py.j2",
            )

        error = raised.exception
        self.assertEqual(error.template_name, "broken.py.j2")
        self.assertEqual(error.line, 2)
        self.assertNotIn("do-not-retain", str(error))
        self.assertIsNone(error.__context__)

    def test_validated_command_preserves_valid_source_exactly(self):
        source = "print('hello')\n"

        self.assertEqual(
            validated_python_command(source, template_name="hello.py.j2"),
            ("python", "-c", source),
        )


if __name__ == "__main__":
    main()
