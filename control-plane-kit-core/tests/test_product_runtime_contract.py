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
    ProductFamily,
    ProviderRuntimePort,
    ProductRuntimeContract,
    ProductRuntimeContractCodec,
    ProductRuntimeContractError,
    RetainedDataMount,
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
            provider_ports=(ProviderRuntimePort("http", 8000),),
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
            retained_data_mounts=(RetainedDataMount("orders-db", "/var/lib/postgresql/data"),),
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
            descriptor["provider_ports"],
            [{"provider_socket": "http", "container_port": 8000}],
        )
        self.assertEqual(
            descriptor["retained_data_mounts"],
            [
                {
                    "resource_id": "orders-db",
                    "target_path": "/var/lib/postgresql/data",
                }
            ],
        )
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
            provider_ports=(ProviderRuntimePort("http", 8000),),
            verification=VerificationContract(
                (HttpCheck(check_id="ready", provider_socket="http", path="/health"),)
            ),
        )
        codec = ProductRuntimeContractCodec()

        descriptor = codec.encode(contract)
        restored = codec.decode(descriptor)

        self.assertEqual(restored, contract)
        self.assertEqual(codec.encode(restored), descriptor)

    def test_provider_runtime_ports_are_closed_descriptor_material(self) -> None:
        contract = ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            provider_ports=(ProviderRuntimePort("http", 8000),),
        )
        descriptor = ProductRuntimeContractCodec().encode(contract)

        self.assertEqual(
            descriptor["provider_ports"],
            [{"provider_socket": "http", "container_port": 8000}],
        )
        self.assertEqual(
            ProductRuntimeContractCodec().decode(descriptor).provider_ports,
            (ProviderRuntimePort("http", 8000),),
        )

    def test_provider_runtime_ports_reject_unknown_socket_and_bad_port(self) -> None:
        with self.assertRaisesRegex(ProductRuntimeContractError, "provider runtime port"):
            ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
                provider_ports=(ProviderRuntimePort("admin", 8000),),
            )
        for port in (0, 65536, True):
            with self.subTest(port=port):
                with self.assertRaises(ProductRuntimeContractError):
                    ProviderRuntimePort("http", port)  # type: ignore[arg-type]

    def test_retained_data_mounts_are_closed_descriptor_material(self) -> None:
        mount = RetainedDataMount("orders-db", "/var/lib/postgresql/data")
        contract = ProductRuntimeContract(
            retained_data_mounts=(mount,),
            lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
        )
        descriptor = ProductRuntimeContractCodec().encode(contract)

        self.assertEqual(
            descriptor["retained_data_mounts"],
            [{"resource_id": "orders-db", "target_path": "/var/lib/postgresql/data"}],
        )
        self.assertEqual(
            ProductRuntimeContractCodec().decode(descriptor).retained_data_mounts,
            (mount,),
        )

    def test_retained_data_mounts_reject_host_paths_and_unknown_resources(self) -> None:
        for target_path in (
            "var/lib/postgresql/data",
            "/var/run/docker.sock",
            "/proc/self",
            "/sys/kernel",
            "/var/lib/../postgresql/data",
        ):
            with self.subTest(target_path=target_path):
                with self.assertRaises(ProductRuntimeContractError):
                    ProductRuntimeContract(
                        retained_data_mounts=(
                            RetainedDataMount("orders-db", target_path),
                        ),
                        lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
                    )

        with self.assertRaisesRegex(ProductRuntimeContractError, "retained data mount"):
            ProductRuntimeContract(
                retained_data_mounts=(
                    RetainedDataMount("unknown", "/var/lib/postgresql/data"),
                ),
                lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
            )

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

    def test_product_family_is_a_closed_descriptor_vocabulary(self) -> None:
        self.assertEqual(ProductFamily.SERVER.value, "server")
        self.assertEqual(ProductFamily.DATA_SERVICE.value, "data-service")


if __name__ == "__main__":
    unittest.main()
