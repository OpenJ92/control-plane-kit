from unittest import TestCase, main

from control_plane_kit.servers import (
    hello_command,
    http_active_router_command,
    http_multiplexer_command,
    http_proxy_command,
    http_rate_limiter_command,
    http_weighted_load_balancer_command,
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
            compile(command[2], "<rendered-server-command>", "exec")

    def test_rendered_commands_include_expected_environment_names(self):
        self.assertIn("HELLO_MESSAGE", hello_command()[2])
        self.assertIn("PROXY_TARGET_URL", http_proxy_command()[2])
        self.assertIn("ACTIVE_TARGET_URL", http_active_router_command()[2])
        self.assertIn("BALANCER_TARGET_A_URL", http_weighted_load_balancer_command()[2])
        self.assertIn("MULTIPLEXER_PRIMARY_URL", http_multiplexer_command()[2])
        self.assertIn("RATE_LIMIT_TARGET_URL", http_rate_limiter_command()[2])


if __name__ == "__main__":
    main()
