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
        self.assertEqual(product.image.registry, "ghcr.io")
        self.assertEqual(
            product.image.repository,
            "openj92/control-plane-kit-servers/cloudflared-connector",
        )
        self.assertEqual(product.image.tag, "seeded-ingress-1048-cloudflared")
        self.assertEqual(
            product.image.digest,
            "sha256:b5db8bec60f852c1cb488fe6ea79efa38d1016878acd05d2a83089c50b94909e",
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

    def test_descriptor_waits_for_runtime_generated_tunnel_token_delivery(self) -> None:
        product = self.decode().product

        self.assertEqual(product.runtime_contract.secret_deliveries, ())

        descriptor = DESCRIPTOR.read_text(encoding="utf-8").lower()
        self.assertIn("runtime-delivered tunnel token", descriptor)
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

    def test_owned_wrapper_closes_official_image_command_gap(self) -> None:
        product = self.decode().product
        provenance = dict(product.image.provenance)

        self.assertEqual(provenance["source"], "owned-cloudflared-wrapper")
        self.assertEqual(provenance["publish"], "ghcr")
        self.assertEqual(
            provenance["dockerfile"],
            "products/cloudflared_connector/Dockerfile",
        )
        self.assertEqual(
            provenance["base-image"],
            (
                "docker.io/cloudflare/cloudflared:2026.6.1@sha256:"
                "6d91c121b803126f7a5344005d17a9324788fc09d305b6e2560ec6040a7ae283"
            ),
        )
        self.assertEqual(
            provenance["inspected-entrypoint"],
            "cloudflared --no-autoupdate",
        )
        self.assertEqual(provenance["runtime-cmd"], "tunnel run")
        dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('CMD ["tunnel", "run"]', dockerfile)
        self.assertNotIn("$TUNNEL_TOKEN", dockerfile)

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
