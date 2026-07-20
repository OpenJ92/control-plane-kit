from __future__ import annotations

import unittest

from control_plane_kit import (
    BlockSockets,
    DockerImageImplementation,
    DockerRuntime,
    PublicStaticEnvironmentBinding,
    SecretEnvironmentDelivery,
    SecretReference,
)


class PublicStaticEnvironmentBindingTests(unittest.TestCase):
    def test_binding_is_closed_bounded_and_deterministic(self) -> None:
        binding = PublicStaticEnvironmentBinding("WORKER_COUNT", "4")

        self.assertEqual(
            binding.descriptor(),
            {"kind": "public-static", "name": "WORKER_COUNT", "value": "4"},
        )

        for name in ("", "lowercase", "1_INVALID", "A-B"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                PublicStaticEnvironmentBinding(name, "value")
        with self.assertRaises(TypeError):
            PublicStaticEnvironmentBinding("WORKER_COUNT", 4)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "malformed or unbounded"):
            PublicStaticEnvironmentBinding("VALUE", "contains\x00nul")
        with self.assertRaisesRegex(ValueError, "malformed or unbounded"):
            PublicStaticEnvironmentBinding("VALUE", "x" * 16_385)
        with self.assertRaisesRegex(ValueError, "SecretEnvironmentDelivery") as caught:
            PublicStaticEnvironmentBinding("API_TOKEN", "do-not-disclose")
        self.assertNotIn("do-not-disclose", str(caught.exception))

    def test_docker_implementation_rejects_open_mappings_and_duplicate_names(self) -> None:
        with self.assertRaisesRegex(TypeError, "tuple"):
            DockerImageImplementation(
                "service:latest",
                environment={"WORKER_COUNT": "4"},  # type: ignore[arg-type]
            )

        with self.assertRaisesRegex(ValueError, "must be unique"):
            DockerImageImplementation(
                "service:latest",
                environment=(
                    PublicStaticEnvironmentBinding("WORKER_COUNT", "4"),
                    PublicStaticEnvironmentBinding("WORKER_COUNT", "8"),
                ),
            )

    def test_public_and_secret_environment_names_remain_disjoint(self) -> None:
        implementation = DockerImageImplementation(
            "service:latest",
            environment=(PublicStaticEnvironmentBinding("DATABASE_URL", "public"),),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "DATABASE_URL",
                    SecretReference("secret://local/workspace/database"),
                ),
            ),
        )

        with self.assertRaisesRegex(ValueError, "overlap"):
            implementation.materialize("service", BlockSockets(), DockerRuntime())


if __name__ == "__main__":
    unittest.main()
