from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sys
import unittest

from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.products import (
    ProductFamily,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.types import Protocol


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "hello_server"
PRODUCT_SRC = PRODUCT / "src"
DESCRIPTOR = PRODUCT / "product.cpk.json"


class HelloServerProductTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))

    def tearDown(self) -> None:
        sys.path.remove(str(PRODUCT_SRC))
        for name in list(sys.modules):
            if name == "control_plane_kit_servers_hello_server" or name.startswith(
                "control_plane_kit_servers_hello_server."
            ):
                sys.modules.pop(name, None)

    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_product_contract(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "hello-server", 1),
        )
        self.assertEqual(product.display_name, "hello-server")
        self.assertIs(product.product_family, ProductFamily.SERVER)
        self.assertEqual(product.image.registry, "ghcr.io")
        self.assertEqual(
            product.image.repository,
            "openj92/control-plane-kit-servers/hello-server",
        )
        self.assertEqual(
            product.image.execution_reference,
            f"ghcr.io/openj92/control-plane-kit-servers/hello-server@{product.image.digest}",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_declares_http_provider_and_message_contract(self) -> None:
        product = self.decode().product
        sockets = product.runtime_contract.sockets

        self.assertEqual(sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(
            {
                value.provider_socket: value.container_port
                for value in product.runtime_contract.provider_ports
            },
            {"internal": 8000},
        )
        self.assertEqual(sockets.requirement_names(), ())
        self.assertEqual(
            product.runtime_contract.public_environment,
            (
                PublicStaticEnvironmentBinding("HELLO_DEPENDENCIES_JSON", "[]"),
                PublicStaticEnvironmentBinding("HELLO_MESSAGE", "Hello, world!"),
            ),
        )
        self.assertEqual(product.runtime_contract.secret_deliveries, ())
        for check in product.runtime_contract.verification.checks:
            self.assertEqual(check.policy.maximum_attempts, 5)
        self.assertIn(
            "dynamic per-instance dependency sockets",
            product.description.lower(),
        )

    def test_descriptor_instantiates_without_importing_process_code(self) -> None:
        product = self.decode().product

        block = instantiate_product(
            product,
            "hello",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "hello")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertNotIn("control_plane_kit_servers_hello_server.server", sys.modules)

    def test_process_dependency_names_are_closed_and_unique(self) -> None:
        from control_plane_kit_servers_hello_server import (
            HelloConfigurationError,
            dependency_environment_names,
            load_dependencies,
        )

        self.assertEqual(
            dependency_environment_names("inventory-api"),
            (
                "HELLO_HTTP_INVENTORY_API_URL",
                "HELLO_DATABASE_INVENTORY_API_URL",
            ),
        )
        for name in ("", "Orders", "orders_api", "1-orders", "orders/api"):
            with self.subTest(name=name):
                with self.assertRaises(HelloConfigurationError):
                    load_dependencies(json.dumps([{"name": name}]))

        with self.assertRaisesRegex(HelloConfigurationError, "unique"):
            load_dependencies(json.dumps([{"name": "orders"}, {"name": "orders"}]))

    def test_process_dependency_descriptor_contains_env_names_not_endpoints(self) -> None:
        from control_plane_kit_servers_hello_server import load_dependencies

        dependencies = load_dependencies(json.dumps([{"name": "orders"}]))
        descriptor = dependencies[0].descriptor()

        self.assertEqual(
            descriptor,
            {
                "name": "orders",
                "http_environment": "HELLO_HTTP_ORDERS_URL",
                "database_environment": "HELLO_DATABASE_ORDERS_URL",
            },
        )
        self.assertNotIn("http://orders", json.dumps(descriptor))
        self.assertNotIn("postgresql://orders", json.dumps(descriptor))

    def test_entrypoint_source_preserves_bounded_dependency_checks(self) -> None:
        source = (PRODUCT_SRC / "control_plane_kit_servers_hello_server" / "server.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("_MAX_RESPONSE_BYTES", source)
        self.assertIn("NoRedirects", source)
        self.assertIn('parsed.scheme not in {"postgresql", "postgresql+psycopg"}', source)
        self.assertNotIn('startswith("postgresql")', source)
        self.assertNotIn("Hello, block!", source)

    def test_process_observer_receipt_evidence_is_bounded_and_redacted(self) -> None:
        from control_plane_kit_servers_hello_server.server import (
            _OBSERVED_REQUEST_LIMIT,
            _clear_observed_requests,
            _observed_requests_payload,
            _record_observed_request,
        )

        _clear_observed_requests()
        for index in range(_OBSERVED_REQUEST_LIMIT + 3):
            _record_observed_request("GET", f"/?token=secret-{index}")

        payload = _observed_requests_payload()

        self.assertEqual(payload["count"], _OBSERVED_REQUEST_LIMIT)
        self.assertEqual(payload["retained_limit"], _OBSERVED_REQUEST_LIMIT)
        self.assertEqual(
            payload["requests"],
            tuple({"method": "GET", "path": "/"} for _ in range(_OBSERVED_REQUEST_LIMIT)),
        )
        self.assertNotIn("secret", json.dumps(payload))
        self.assertNotIn("headers", json.dumps(payload).lower())
        self.assertNotIn("body", json.dumps(payload).lower())

    def test_dockerfile_uses_product_entrypoint_and_not_embedded_message(self) -> None:
        dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("USER hello", dockerfile)
        self.assertIn("control_plane_kit_servers_hello_server.server", dockerfile)
        self.assertIn("EXPOSE 8000", dockerfile)
        self.assertNotIn("Hello, block!", dockerfile)
        self.assertNotIn("docker system prune", dockerfile)

    def test_descriptor_digest_is_catalogue_ready(self) -> None:
        content = DESCRIPTOR.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        self.assertEqual(len(digest), 64)
        self.assertEqual(ProductDescriptorCodec().decode_document(content).content_digest, digest)


if __name__ == "__main__":
    unittest.main()
