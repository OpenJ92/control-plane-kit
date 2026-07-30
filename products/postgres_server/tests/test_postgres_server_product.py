from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from control_plane_kit_core.lifecycle import ResourcePersistence
from control_plane_kit_core.products import (
    ProductFamily,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    RetainedDataMount,
    instantiate_product,
)
from control_plane_kit_core.secrets import SecretEnvironmentDelivery, SecretUseIntent
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import (
    PostgresPasswordAuthentication,
    PostgresQueryCheck,
)


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "postgres_server"
DESCRIPTOR = PRODUCT / "product.cpk.json"


class PostgresServerProductTests(unittest.TestCase):
    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_official_image_contract(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "postgres-server", 1),
        )
        self.assertEqual(product.display_name, "postgres-server")
        self.assertIs(product.product_family, ProductFamily.DATA_SERVICE)
        self.assertEqual(product.image.registry, "docker.io")
        self.assertEqual(product.image.repository, "library/postgres")
        self.assertEqual(product.image.tag, "16-alpine")
        self.assertEqual(
            product.image.execution_reference,
            f"docker.io/library/postgres@{product.image.digest}",
        )
        self.assertEqual(
            dict(product.image.provenance)["source"],
            "official-postgres-image",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_declares_private_postgres_provider(self) -> None:
        product = self.decode().product
        sockets = product.runtime_contract.sockets

        self.assertEqual(sockets.provider("postgres").protocol, Protocol.POSTGRES)
        self.assertEqual(
            {
                value.provider_socket: value.container_port
                for value in product.runtime_contract.provider_ports
            },
            {"postgres": 5432},
        )
        self.assertEqual(sockets.requirement_names(), ())
        self.assertIn("private postgres socket", product.description.lower())
        self.assertNotIn("host port", product.description.lower())

    def test_descriptor_uses_secret_delivery_for_password(self) -> None:
        product = self.decode().product

        public_environment = {
            value.name: value.value for value in product.runtime_contract.public_environment
        }
        self.assertEqual(public_environment, {"POSTGRES_DB": "cpk", "POSTGRES_USER": "cpk"})
        self.assertNotIn("POSTGRES_PASSWORD", public_environment)

        (delivery,) = product.runtime_contract.secret_deliveries
        self.assertIsInstance(delivery, SecretEnvironmentDelivery)
        self.assertEqual(delivery.environment_name, "POSTGRES_PASSWORD")
        self.assertEqual(
            delivery.reference.reference_id,
            "secret://control-plane-kit/postgres/password",
        )
        self.assertIs(delivery.intent, SecretUseIntent.POSTGRES_PASSWORD)
        descriptor = DESCRIPTOR.read_text(encoding="utf-8").lower()
        self.assertIn("postgres_password", descriptor)
        self.assertNotIn("cpk-smoke-password", descriptor)

    def test_descriptor_classifies_retained_data_resource(self) -> None:
        product = self.decode().product
        lifecycle = product.runtime_contract.lifecycle

        self.assertEqual(lifecycle.compute, ResourcePersistence.EPHEMERAL)
        data = lifecycle.data_resource("postgres-data")
        self.assertEqual(data.persistence, ResourcePersistence.RETAINED)
        self.assertEqual(
            product.runtime_contract.retained_data_mounts,
            (RetainedDataMount("postgres-data", "/var/lib/postgresql/data"),),
        )

    def test_descriptor_uses_database_readiness_not_tcp_only(self) -> None:
        product = self.decode().product
        (check,) = product.runtime_contract.verification.checks

        self.assertIsInstance(check, PostgresQueryCheck)
        self.assertEqual(check.provider_socket, "postgres")
        self.assertEqual(check.operation.value, "select-one")
        self.assertEqual(check.policy.maximum_attempts, 10)
        self.assertEqual(check.policy.interval_seconds, 1.0)
        self.assertIsInstance(check.authentication, PostgresPasswordAuthentication)
        self.assertEqual(check.authentication.database, "cpk")
        self.assertEqual(check.authentication.username, "cpk")
        self.assertEqual(
            check.authentication.password_reference.reference_id,
            "secret://control-plane-kit/postgres/password",
        )

    def test_descriptor_instantiates_without_application_or_store_logic(self) -> None:
        product = self.decode().product

        block = instantiate_product(
            product,
            "db",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "db")
        self.assertEqual(block.sockets.provider("postgres").protocol, Protocol.POSTGRES)
        descriptor = DESCRIPTOR.read_text(encoding="utf-8").lower()
        self.assertNotIn("stores", descriptor)
        self.assertNotIn("create table", descriptor)
        self.assertNotIn("alembic", descriptor)

    def test_descriptor_digest_is_catalogue_ready(self) -> None:
        content = DESCRIPTOR.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        self.assertEqual(len(digest), 64)
        self.assertEqual(ProductDescriptorCodec().decode_document(content).content_digest, digest)


if __name__ == "__main__":
    unittest.main()
