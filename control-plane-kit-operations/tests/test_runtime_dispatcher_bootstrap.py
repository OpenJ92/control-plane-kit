from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations import (
    RuntimeDispatcherBootstrapConfiguration,
    RuntimeDispatcherBootstrapError,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SOURCE = (
    PACKAGE_ROOT
    / "src"
    / "control_plane_kit_operations"
    / "runtime_dispatcher_bootstrap.py"
)


class RuntimeDispatcherBootstrapApiTests(unittest.TestCase):
    def test_bootstrap_config_can_represent_disabled_runtime_dispatch(self) -> None:
        config = RuntimeDispatcherBootstrapConfiguration.disabled()

        self.assertEqual(config.runtime_kinds, ())
        self.assertEqual(
            config.descriptor(),
            {
                "runtime_interpreters": [],
                "runtime_dispatch": "disabled",
            },
        )
        self.assertEqual(str(config), "none")

    def test_bootstrap_config_can_represent_allowed_runtime_kinds(self) -> None:
        config = RuntimeDispatcherBootstrapConfiguration.allow(
            (RuntimeKind.KUBERNETES, RuntimeKind.DOCKER, RuntimeKind.DOCKER)
        )

        self.assertEqual(
            config.runtime_kinds,
            (RuntimeKind.DOCKER, RuntimeKind.KUBERNETES),
        )
        self.assertEqual(
            config.descriptor(),
            {
                "runtime_interpreters": ["docker", "kubernetes"],
                "runtime_dispatch": "enabled",
            },
        )

    def test_cpk_server_process_value_is_a_bootstrap_input_not_authority(self) -> None:
        config = RuntimeDispatcherBootstrapConfiguration.from_process_value("docker")

        self.assertEqual(config.runtime_kinds, (RuntimeKind.DOCKER,))
        self.assertEqual(str(config), "docker")
        self.assertNotIn("authority", repr(config.descriptor()).lower())

    def test_unknown_empty_and_mixed_none_values_fail_closed(self) -> None:
        for value in ("", " ", "unknown", "none,docker", "docker,none"):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeDispatcherBootstrapError):
                    RuntimeDispatcherBootstrapConfiguration.from_process_value(value)

    def test_bootstrap_api_does_not_import_concrete_interpreters_or_sdks(self) -> None:
        tree = ast.parse(BOOTSTRAP_SOURCE.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])

        self.assertNotIn("control_plane_kit_interpreters", imports)
        self.assertNotIn("docker", imports)
        self.assertNotIn("boto3", imports)
        self.assertNotIn("kubernetes", imports)


if __name__ == "__main__":
    unittest.main()
