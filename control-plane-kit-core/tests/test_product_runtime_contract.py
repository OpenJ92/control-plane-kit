from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket, RequirementSocket
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.configuration import (
    ConfigurationArtifact,
    ConfigurationMediaType,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.lifecycle import ResourceLifecycle
from control_plane_kit_core.products import (
    ProductRuntimeContract,
    ProductRuntimeContractCodec,
    ProductRuntimeContractError,
)
from control_plane_kit_core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck, VerificationContract


class ProductRuntimeContractTests(unittest.TestCase):
    def test_composes_closed_runtime_material_into_descriptor(self) -> None:
        contract = ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=(RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),),
                providers=(ProviderSocket("http", Protocol.HTTP),),
            ),
            public_environment=(PublicStaticEnvironmentBinding("MODE", "demo"),),
            configuration_artifacts=(
                ConfigurationArtifact(
                    "router-config",
                    "/etc/cpk/router.json",
                    ConfigurationMediaType.JSON,
                    '{"mode":"demo"}',
                ),
            ),
            secret_deliveries=(
                SecretEnvironmentDelivery("API_TOKEN", SecretReference("secret://local/api/token")),
            ),
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
            verification=VerificationContract(
                (HttpCheck(check_id="ready", provider_socket="http", path="/health"),)
            ),
            lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
        )

        descriptor = contract.descriptor()

        self.assertEqual(
            descriptor["sockets"]["providers"]["http"]["protocol"],
            {"transport": "tcp", "application": "http"},
        )
        self.assertEqual(descriptor["capabilities"], ["health-checkable"])
        self.assertEqual(
            descriptor["secret_deliveries"][0],
            {
                "kind": "environment",
                "environment_name": "API_TOKEN",
                "reference_id": "secret://local/api/token",
            },
        )

    def test_codec_round_trips_strict_descriptor(self) -> None:
        contract = ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            verification=VerificationContract(
                (HttpCheck(check_id="ready", provider_socket="http", path="/health"),)
            ),
        )
        codec = ProductRuntimeContractCodec()

        descriptor = codec.encode(contract)
        restored = codec.decode(descriptor)

        self.assertEqual(restored, contract)
        self.assertEqual(codec.encode(restored), descriptor)

    def test_verification_protocol_mismatch_fails_before_runtime_effects(self) -> None:
        with self.assertRaisesRegex(ProductRuntimeContractError, "verification"):
            ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("db", Protocol.POSTGRES),)),
                verification=VerificationContract(
                    (HttpCheck(check_id="ready", provider_socket="db", path="/health"),)
                ),
            )

    def test_secret_values_cannot_enter_public_environment_or_configuration(self) -> None:
        with self.assertRaises(ValueError):
            ProductRuntimeContract(
                sockets=BlockSockets(),
                public_environment=(PublicStaticEnvironmentBinding("API_TOKEN", "do-not-disclose"),),
            )
        with self.assertRaises(ValueError):
            ProductRuntimeContract(
                sockets=BlockSockets(),
                configuration_artifacts=(
                    ConfigurationArtifact(
                        "app-config",
                        "/etc/cpk/app.json",
                        ConfigurationMediaType.JSON,
                        '{"password":"do-not-disclose"}',
                    ),
                ),
            )

    def test_descriptor_rejects_unknown_fields_and_secret_literals(self) -> None:
        codec = ProductRuntimeContractCodec()
        descriptor = codec.encode(
            ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            )
        )

        with self.assertRaises(ProductRuntimeContractError):
            codec.decode({**descriptor, "future": "unknown"})
        descriptor["public_environment"] = [
            {"kind": "public-static", "name": "API_TOKEN", "value": "do-not-disclose"}
        ]
        with self.assertRaises(ProductRuntimeContractError):
            codec.decode(descriptor)

    def test_descriptor_rejects_non_string_socket_names(self) -> None:
        codec = ProductRuntimeContractCodec()
        descriptor = codec.encode(ProductRuntimeContract(sockets=BlockSockets()))
        descriptor["sockets"]["providers"] = {
            1: {"protocol": Protocol.HTTP.descriptor()}
        }

        with self.assertRaises(ProductRuntimeContractError):
            codec.decode(descriptor)

    def test_retained_data_resource_is_distinct_from_configuration_artifact(self) -> None:
        with self.assertRaisesRegex(ProductRuntimeContractError, "retained data"):
            ProductRuntimeContract(
                sockets=BlockSockets(),
                configuration_artifacts=(
                    ConfigurationArtifact(
                        "orders-db",
                        "/etc/cpk/orders.json",
                        ConfigurationMediaType.JSON,
                        '{"mode":"readonly"}',
                    ),
                ),
                lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
            )

    def test_product_runtime_contract_module_has_no_effect_boundary(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))

        forbidden_import_roots = {
            "control_plane_kit",
            "docker",
            "fastapi",
            "httpx",
            "importlib",
            "mcp",
            "psycopg",
            "uvicorn",
        }
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])

        self.assertEqual(roots & forbidden_import_roots, set())


if __name__ == "__main__":
    unittest.main()
