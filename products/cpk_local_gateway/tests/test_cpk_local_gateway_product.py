from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductFamily,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit_core.types import Protocol, SocketBinding


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "cpk_local_gateway"
PRODUCT_SRC = PRODUCT / "src"
DESCRIPTOR = PRODUCT / "product.cpk.json"


class CpkLocalGatewayProductTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))

    def tearDown(self) -> None:
        sys.path.remove(str(PRODUCT_SRC))
        for name in list(sys.modules):
            if name == "control_plane_kit_servers_cpk_local_gateway" or name.startswith(
                "control_plane_kit_servers_cpk_local_gateway."
            ):
                sys.modules.pop(name, None)

    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_product_contract(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "cpk-local-gateway", 1),
        )
        self.assertEqual(product.display_name, "cpk-local-gateway")
        self.assertIs(product.product_family, ProductFamily.SERVER)
        self.assertEqual(product.image.registry, "ghcr.io")
        self.assertEqual(
            product.image.repository,
            "openj92/control-plane-kit-servers/cpk-local-gateway",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_declares_control_provider_and_target_map_config(self) -> None:
        product = self.decode().product
        sockets = product.runtime_contract.sockets

        self.assertEqual(sockets.provider("control").protocol, Protocol.HTTP)
        self.assertEqual(
            {
                value.provider_socket: value.container_port
                for value in product.runtime_contract.provider_ports
            },
            {"control": 8000},
        )
        self.assertEqual(
            sockets.requirement_names(),
            ("target-http", "target-postgres"),
        )
        self.assertEqual(sockets.requirement("target-http").protocol, Protocol.HTTP)
        self.assertEqual(
            sockets.requirement("target-http").env_bindings,
            (),
        )
        self.assertIs(
            sockets.requirement("target-http").binding,
            SocketBinding.RUNTIME_CONTROL,
        )
        self.assertEqual(
            sockets.requirement("target-postgres").protocol,
            Protocol.POSTGRES,
        )
        self.assertEqual(
            sockets.requirement("target-postgres").env_bindings,
            (),
        )
        self.assertIs(
            sockets.requirement("target-postgres").binding,
            SocketBinding.RUNTIME_CONTROL,
        )
        self.assertEqual(
            product.runtime_contract.public_environment,
            (
                PublicStaticEnvironmentBinding("CPK_GATEWAY_TARGETS_JSON", "{}"),
            ),
        )
        self.assertEqual(
            product.runtime_contract.secret_deliveries,
            (
                SecretEnvironmentDelivery(
                    "POSTGRES_PASSWORD",
                    SecretReference("secret://control-plane-kit/postgres/password"),
                ),
            ),
        )
        self.assertEqual(product.runtime_contract.retained_data_mounts, ())
        self.assertIn("closed semantic probe", product.description.lower())

    def test_descriptor_instantiates_without_importing_process_code(self) -> None:
        product = self.decode().product

        block = instantiate_product(
            product,
            "gateway",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "gateway")
        self.assertEqual(block.sockets.provider("control").protocol, Protocol.HTTP)
        self.assertNotIn(
            "control_plane_kit_servers_cpk_local_gateway.server",
            sys.modules,
        )

    def test_probe_request_contract_rejects_unknown_kind_and_target(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            GatewayConfiguration,
            GatewayConfigurationError,
            execute_probe,
        )

        gateway = GatewayConfiguration.from_target_map(
            {
                "hello.internal": {
                    "protocol": "http",
                    "url": "http://hello:8000",
                }
            }
        )

        with self.assertRaisesRegex(GatewayConfigurationError, "unsupported probe kind"):
            execute_probe(gateway, {"kind": "tcp-open", "target_id": "hello.internal"})
        with self.assertRaisesRegex(GatewayConfigurationError, "unknown target"):
            execute_probe(gateway, {"kind": "http-status", "target_id": "db.postgres"})

    def test_http_probe_uses_declared_target_without_forwarding_arbitrary_url(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import (
            GatewayConfiguration,
            execute_probe,
        )

        gateway = GatewayConfiguration.from_target_map(
            {
                "hello.internal": {
                    "protocol": "http",
                    "url": "http://hello:8000",
                }
            }
        )

        with patch(
            "control_plane_kit_servers_cpk_local_gateway.server._http_status",
            return_value={"status": 200, "body_size": 5},
        ) as transport:
            result = execute_probe(
                gateway,
                {
                    "kind": "http-status",
                    "target_id": "hello.internal",
                    "path": "/health/ready",
                },
            )

        self.assertEqual(result["outcome"], "passed")
        transport.assert_called_once_with("http://hello:8000/health/ready")
        self.assertNotIn("http://evil", json.dumps(result))

    def test_postgres_probe_contract_is_secret_free(self) -> None:
        from control_plane_kit_servers_cpk_local_gateway import GatewayConfiguration

        gateway = GatewayConfiguration.from_target_map(
            {
                "postgres.postgres": {
                    "protocol": "postgres",
                    "host": "postgres",
                    "port": 5432,
                }
            }
        )
        descriptor = gateway.targets["postgres.postgres"].descriptor()

        self.assertEqual(descriptor["protocol"], "postgres")
        self.assertNotIn("password", json.dumps(descriptor).lower())
        self.assertNotIn("secret", json.dumps(descriptor).lower())
        self.assertNotIn("postgres://", json.dumps(descriptor).lower())

    def test_entrypoint_source_preserves_closed_gateway_boundary(self) -> None:
        source = (
            PRODUCT_SRC / "control_plane_kit_servers_cpk_local_gateway" / "server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("CPK_GATEWAY_TARGETS_JSON", source)
        self.assertIn("http-status", source)
        self.assertIn("postgres-select-one", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("docker", source.lower())
        self.assertNotIn("graph_store", source)

    def test_dockerfile_uses_product_entrypoint_and_non_root_user(self) -> None:
        dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("USER gateway", dockerfile)
        self.assertIn("control_plane_kit_servers_cpk_local_gateway.server", dockerfile)
        self.assertIn("EXPOSE 8000", dockerfile)
        self.assertNotIn("docker system prune", dockerfile)

    def test_descriptor_digest_is_catalogue_ready(self) -> None:
        content = DESCRIPTOR.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        self.assertEqual(len(digest), 64)
        self.assertEqual(
            ProductDescriptorCodec().decode_document(content).content_digest,
            digest,
        )
        catalogue = json.loads((ROOT / "catalogue" / "products.json").read_text())
        entry = next(
            product
            for product in catalogue["products"]
            if product["product_id"] == "cpk-local-gateway"
        )
        self.assertEqual(entry["descriptor_sha256"], digest)

    def test_private_probe_smoke_uses_gateway_as_only_public_probe_surface(self) -> None:
        smoke = (
            ROOT / "scripts" / "cpk_local_gateway_private_probe_smoke.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("products/cpk_local_gateway/product.cpk.json", smoke)
        self.assertIn("products/hello_server/product.cpk.json", smoke)
        self.assertIn("products/postgres_server/product.cpk.json", smoke)
        self.assertIn("docker pull \"$GATEWAY_IMAGE\"", smoke)
        self.assertIn("127.0.0.1:$GATEWAY_PORT:8000", smoke)
        self.assertIn('"kind":"http-status"', smoke)
        self.assertIn('"kind":"postgres-select-one"', smoke)
        self.assertIn('"target_id":"missing.http"', smoke)
        self.assertIn('"kind":"tcp-open"', smoke)
        self.assertIn("password_environment", smoke)
        self.assertNotIn("sync_runtime_networks", smoke)
        self.assertNotIn("-p 5432", smoke)
        self.assertNotIn("docker system prune", smoke)


if __name__ == "__main__":
    unittest.main()
