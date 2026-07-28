from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from control_plane_kit_core.lifecycle import ResourcePersistence
from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductFamily,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.secrets import SecretEnvironmentDelivery


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "cloudflared_connector"
DESCRIPTOR = PRODUCT / "product.cpk.json"


class CloudflaredConnectorProductTests(unittest.TestCase):
    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_connector_product(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "cloudflared-connector", 1),
        )
        self.assertEqual(product.display_name, "cloudflared-connector")
        self.assertIs(product.product_family, ProductFamily.SERVER)
        self.assertEqual(product.image.registry, "docker.io")
        self.assertEqual(product.image.repository, "cloudflare/cloudflared")
        self.assertEqual(product.image.tag, "2026.6.1")
        self.assertEqual(
            product.image.digest,
            "sha256:6d91c121b803126f7a5344005d17a9324788fc09d305b6e2560ec6040a7ae283",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_declares_no_workload_socket_or_retained_data(self) -> None:
        product = self.decode().product
        sockets = product.runtime_contract.sockets

        self.assertEqual(sockets.requirement_names(), ())
        self.assertEqual(sockets.provider_names(), ())
        self.assertEqual(product.runtime_contract.provider_ports, ())
        self.assertEqual(product.runtime_contract.public_environment, ())
        self.assertEqual(product.runtime_contract.retained_data_mounts, ())
        self.assertEqual(
            product.runtime_contract.lifecycle.compute,
            ResourcePersistence.EPHEMERAL,
        )
        self.assertEqual(product.runtime_contract.lifecycle.data, ())
        self.assertIn("not the gateway", product.description.lower())
        self.assertIn("outbound cloudflare tunnel", product.description.lower())

    def test_descriptor_delivers_tunnel_token_only_as_secret_material(self) -> None:
        product = self.decode().product

        (delivery,) = product.runtime_contract.secret_deliveries
        self.assertIsInstance(delivery, SecretEnvironmentDelivery)
        self.assertEqual(delivery.environment_name, "TUNNEL_TOKEN")
        self.assertEqual(
            delivery.reference.reference_id,
            "secret://control-plane-kit/cloudflare/tunnel-token",
        )

        descriptor = DESCRIPTOR.read_text(encoding="utf-8").lower()
        self.assertIn("tunnel_token", descriptor)
        self.assertNotIn("eyj", descriptor)
        self.assertNotIn("api_token", descriptor)
        self.assertNotIn("cloudflare_api_token", descriptor)

    def test_descriptor_instantiates_without_gateway_or_cloudflare_api_logic(self) -> None:
        product = self.decode().product

        block = instantiate_product(
            product,
            "cloudflared",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "cloudflared")
        self.assertEqual(block.sockets.provider_names(), ())
        descriptor = DESCRIPTOR.read_text(encoding="utf-8").lower()
        self.assertNotIn('"cloudflare_api_token"', descriptor)
        self.assertNotIn('"zone_id"', descriptor)
        self.assertNotIn('"dns_record_id"', descriptor)
        self.assertNotIn('"cpk_gateway_targets_json"', descriptor)

    def test_official_image_command_gap_is_explicit_handoff(self) -> None:
        product = self.decode().product
        provenance = dict(product.image.provenance)

        self.assertEqual(provenance["source"], "official-cloudflared-image")
        self.assertEqual(
            provenance["inspected-entrypoint"],
            "cloudflared --no-autoupdate",
        )
        self.assertEqual(provenance["inspected-cmd"], "version")
        self.assertEqual(
            provenance["command-handoff"],
            "OpenJ92/control-plane-kit#1037",
        )

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
            if product["product_id"] == "cloudflared-connector"
        )
        self.assertEqual(entry["descriptor_sha256"], digest)


if __name__ == "__main__":
    unittest.main()
