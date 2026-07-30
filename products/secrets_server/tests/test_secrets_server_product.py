from __future__ import annotations

import json
from pathlib import Path
import unittest

from control_plane_kit_core.lifecycle import ResourcePersistence
from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductFamily,
    ProductIdentity,
    ProductInstanceConfiguration,
    RetainedDataMount,
    instantiate_product,
)
from control_plane_kit_core.types import Protocol


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "secrets_server"
DESCRIPTOR = PRODUCT / "product.cpk.json"
BOOTSTRAP = PRODUCT / "bootstrap.contract.json"
DOCKERFILE = PRODUCT / "Dockerfile"
ENTRYPOINT = PRODUCT / "entrypoint.sh"
SMOKE = ROOT / "scripts" / "secrets_server_image_smoke.sh"


class SecretsServerProductTests(unittest.TestCase):
    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_product_contract(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "secrets-server", 1),
        )
        self.assertEqual(product.display_name, "secrets-server")
        self.assertIs(product.product_family, ProductFamily.DATA_SERVICE)
        self.assertEqual(product.image.registry, "ghcr.io")
        self.assertEqual(
            product.image.repository,
            "openj92/control-plane-kit-servers/secrets-server",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_exposes_private_http_and_retained_custody(self) -> None:
        product = self.decode().product
        contract = product.runtime_contract

        self.assertEqual(contract.sockets.provider("control").protocol, Protocol.HTTP)
        self.assertEqual(
            {
                port.provider_socket: port.container_port
                for port in contract.provider_ports
            },
            {"control": 8081},
        )
        self.assertEqual(contract.sockets.requirement_names(), ())
        self.assertEqual(
            contract.retained_data_mounts,
            (RetainedDataMount("provider-data", "/var/lib/cpk-secrets"),),
        )
        self.assertIs(
            contract.lifecycle.data_resource("provider-data").persistence,
            ResourcePersistence.RETAINED,
        )

    def test_root_bootstrap_is_explicit_and_non_recursive(self) -> None:
        product = self.decode().product
        bootstrap = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
        descriptor = DESCRIPTOR.read_text(encoding="utf-8").lower()

        self.assertEqual(product.runtime_contract.secret_deliveries, ())
        self.assertEqual(product.runtime_contract.configuration_artifacts, ())
        self.assertEqual(
            {
                item["environment_name"] for item in bootstrap["bootstrap_files"]
            },
            {"CPK_SECRETS_MASTER_KEY_FILE", "CPK_SECRETS_CREDENTIALS_FILE"},
        )
        self.assertTrue(
            all(item["mode"] == "0400" for item in bootstrap["bootstrap_files"])
        )
        self.assertIn("not recursively resolved", " ".join(bootstrap["runtime_inputs"]))
        self.assertNotIn("fernet", descriptor)
        self.assertNotIn("\"token\":", descriptor)

    def test_image_is_non_root_and_pins_provider_source(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("USER secrets", dockerfile)
        self.assertIn(
            "control-plane-kit-secrets/archive/"
            "d1c923ca476a87f74af628b7e2fb9b7c0712debe.zip",
            dockerfile,
        )
        self.assertNotIn("CPK_SECRETS_DEVELOPMENT_CREDENTIALS_JSON", dockerfile)
        self.assertNotIn("ENV CPK_SECRETS_MASTER_KEY_FILE", dockerfile)
        entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("/run/secrets/cpk-secrets/master.key", entrypoint)
        self.assertIn('exec "$@"', entrypoint)

    def test_image_smoke_recreates_provider_against_retained_data(self) -> None:
        smoke = SMOKE.read_text(encoding="utf-8")

        self.assertIn('docker rm -f "$CONTAINER"', smoke)
        self.assertGreaterEqual(smoke.count("start_provider"), 3)
        self.assertIn("resolve-after-restart", smoke)
        self.assertIn("cpk.test-run", smoke)
        self.assertIn("docker volume create", smoke)

    def test_descriptor_instantiates_without_provider_implementation(self) -> None:
        product = self.decode().product
        block = instantiate_product(
            product,
            "secrets",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "secrets")
        self.assertEqual(block.sockets.provider("control").protocol, Protocol.HTTP)


if __name__ == "__main__":
    unittest.main()
