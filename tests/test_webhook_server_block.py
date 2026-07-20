from __future__ import annotations

import json
import unittest

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerPostgresImplementation,
    DockerRuntime,
    GraphDescriptorCodec,
    PackageServerProduct,
    Protocol,
    ProviderSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.products.servers import (
    MAX_WEBHOOK_ENDPOINT_GRANTS,
    MAX_WEBHOOK_ENDPOINT_POLICY_BYTES,
    parse_webhook_address_policy,
    render_webhook_address_policy,
    webhook_address_policy_descriptor,
    webhook_address_policy_from_descriptor,
    webhook_delivery_block,
)
from control_plane_kit.servers import ProductMaturity, package_server_contract
from control_plane_kit.domains.webhook import (
    WebhookAddressPolicy,
    WebhookEndpointGrant,
    WebhookEndpointScope,
)
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.products.servers.webhook_delivery import (
    WEBHOOK_DATABASE_ENVIRONMENT,
    WEBHOOK_ENDPOINT_POLICY_ENVIRONMENT,
    WEBHOOK_IDENTITY_ENVIRONMENT,
    WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT,
    WEBHOOK_SIGNING_SECRET_ENVIRONMENT,
)
from control_plane_kit.entrypoints.webhook_server.main import (
    create_app_from_environment,
    psycopg_connection_string,
)


class WebhookDeliveryBlockTests(unittest.TestCase):
    def test_process_factory_reports_its_entrypoint_home(self) -> None:
        self.assertEqual(
            create_app_from_environment.__module__,
            "control_plane_kit.entrypoints.webhook_server.main",
        )

    def test_block_declares_product_database_http_and_opaque_secrets(self) -> None:
        grant = WebhookEndpointGrant(
            "orders",
            "http://receiver:8080/hooks/orders",
            WebhookEndpointScope.RUNTIME_PRIVATE,
        )

        block = webhook_delivery_block(endpoint_grants=(grant,))

        self.assertIsInstance(block, ApplicationBlock)
        self.assertIs(block.spec.product, PackageServerProduct.WEBHOOK_DELIVERY)
        self.assertIs(block.spec.maturity, ProductMaturity.OPERATIONAL)
        self.assertEqual(block.spec.health_path, "/health/ready")
        self.assertEqual(
            block.spec.verification.checks[0].descriptor(),
            {
                "kind": "http",
                "check_id": "webhook-readiness",
                "provider_socket": "internal",
                "policy": {
                    "timeout_seconds": 5.0,
                    "maximum_attempts": 1,
                    "maximum_evidence_bytes": 16_384,
                },
                "path": "/health/ready",
                "expected_statuses": [200],
            },
        )
        requirement = block.sockets.requirement("database")
        self.assertIs(requirement.protocol, Protocol.POSTGRES)
        self.assertEqual(requirement.env_bindings, (WEBHOOK_DATABASE_ENVIRONMENT,))
        self.assertIs(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(
            block.implementation.command,
            (
                "python",
                "-m",
                "control_plane_kit.entrypoints.webhook_server.main",
            ),
        )
        self.assertEqual(
            {
                (item.descriptor()["kind"], item.environment_name):
                    item.reference.reference_id
                for item in block.implementation.secret_deliveries
            },
            {
                ("environment", WEBHOOK_IDENTITY_ENVIRONMENT):
                    "secret://webhook-delivery/identity-attestation",
                ("environment", WEBHOOK_SIGNING_SECRET_ENVIRONMENT):
                    "secret://webhook-delivery/signing-key",
                ("environment-reference", WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT):
                    "secret://webhook-delivery/signing-key",
            },
        )
        public_environment = {
            binding.name: binding.value
            for binding in block.implementation.environment
        }
        self.assertNotIn(WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT, public_environment)
        self.assertEqual(
            parse_webhook_address_policy(
                public_environment[WEBHOOK_ENDPOINT_POLICY_ENVIRONMENT]
            ),
            WebhookAddressPolicy((grant,)),
        )
        serialized = json.dumps(
            [binding.descriptor() for binding in block.implementation.environment]
        )
        self.assertNotIn("identity-attestation-value", serialized)
        self.assertNotIn("signing-secret-value", serialized)

    def test_graph_connection_supplies_the_only_database_url(self) -> None:
        webhook = webhook_delivery_block()
        database = DataBlock(
            BlockSpec("webhook-postgres"),
            DockerPostgresImplementation(database="webhooks"),
            BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
        )
        graph = compile_recipe(
            DeploymentRecipe(
                "webhook-stack",
                DockerRuntime(
                    runtime_id="webhook-runtime",
                    children=(
                        database,
                        webhook,
                        SocketConnection(
                            "webhook-postgres",
                            "internal",
                            "webhook-delivery",
                            "database",
                        ),
                    ),
                ),
            )
        )

        node = graph.node("webhook-delivery")
        self.assertEqual(
            node.non_secret_environment()[WEBHOOK_DATABASE_ENVIRONMENT],
            graph.node("webhook-postgres").endpoint("internal").url,
        )
        self.assertEqual(
            {binding.name for binding in node.socket_environment},
            {WEBHOOK_DATABASE_ENVIRONMENT},
        )
        self.assertEqual(
            {binding.name for binding in node.public_environment},
            {WEBHOOK_ENDPOINT_POLICY_ENVIRONMENT},
        )
        self.assertIn(
            WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT,
            {
                delivery.environment_name
                for delivery in node.secret_deliveries
                if delivery.descriptor()["kind"] == "environment-reference"
            },
        )

    def test_product_and_verification_round_trip_through_graph_codec(self) -> None:
        block = webhook_delivery_block(
            endpoint_grants=(
                WebhookEndpointGrant(
                    "receiver",
                    "http://receiver:8080/hook",
                    WebhookEndpointScope.RUNTIME_PRIVATE,
                ),
            )
        )
        graph = compile_recipe(
            DeploymentRecipe("webhook-codec", DockerRuntime(children=(block,)))
        )
        codec = GraphDescriptorCodec()

        restored = codec.decode(codec.encode(graph)).node(block.block_id)

        self.assertIs(
            restored.block_spec.product,
            PackageServerProduct.WEBHOOK_DELIVERY,
        )
        self.assertEqual(restored.block_spec.verification, block.spec.verification)
        self.assertEqual(
            restored.secret_deliveries,
            graph.node(block.block_id).secret_deliveries,
        )

    def test_catalogue_exposes_only_executable_readiness_capability(self) -> None:
        contract = package_server_contract(PackageServerProduct.WEBHOOK_DELIVERY)

        self.assertIs(contract.maturity, ProductMaturity.OPERATIONAL)
        self.assertEqual(contract.capabilities[0].path, "/health/ready")
        self.assertEqual(len(contract.capabilities), 1)

    def test_policy_codec_is_deterministic_closed_and_bounded(self) -> None:
        policy = WebhookAddressPolicy(
            (
                WebhookEndpointGrant(
                    "receiver",
                    "http://receiver:8080/hook",
                    WebhookEndpointScope.RUNTIME_PRIVATE,
                ),
            )
        )

        first = render_webhook_address_policy(policy)
        second = render_webhook_address_policy(policy)

        self.assertEqual(first, second)
        self.assertEqual(parse_webhook_address_policy(first), policy)
        with self.assertRaisesRegex(ValueError, "descriptor is malformed"):
            webhook_address_policy_from_descriptor({"grants": [], "extra": True})
        with self.assertRaisesRegex(ValueError, "grant is malformed"):
            webhook_address_policy_from_descriptor(
                {
                    "grants": [
                        {
                            "endpoint_id": "receiver",
                            "url": "http://receiver:8080/hook",
                            "scope": "runtime-private",
                            "extra": True,
                        }
                    ]
                }
            )

    def test_policy_rejects_excessive_grants_and_encoded_content(self) -> None:
        excessive = WebhookAddressPolicy(
            tuple(
                WebhookEndpointGrant(
                    f"receiver-{index}",
                    f"http://receiver-{index}:8080/hook",
                    WebhookEndpointScope.RUNTIME_PRIVATE,
                )
                for index in range(MAX_WEBHOOK_ENDPOINT_GRANTS + 1)
            )
        )
        with self.assertRaisesRegex(ValueError, "exceeds its grant bound"):
            webhook_address_policy_descriptor(excessive)

        long_path = "/" + "x" * 1_900
        encoded = WebhookAddressPolicy(
            tuple(
                WebhookEndpointGrant(
                    f"receiver-{index}",
                    f"https://receiver-{index}.example.test{long_path}",
                    WebhookEndpointScope.PUBLIC,
                )
                for index in range(MAX_WEBHOOK_ENDPOINT_GRANTS)
            )
        )
        with self.assertRaisesRegex(ValueError, "exceeds its encoded bound"):
            render_webhook_address_policy(encoded)
        with self.assertRaisesRegex(ValueError, "is malformed"):
            parse_webhook_address_policy("x" * (MAX_WEBHOOK_ENDPOINT_POLICY_BYTES + 1))

        with self.assertRaisesRegex(ValueError, "identities must be unique"):
            WebhookAddressPolicy(
                (
                    WebhookEndpointGrant(
                        "receiver",
                        "http://receiver:8080/one",
                        WebhookEndpointScope.RUNTIME_PRIVATE,
                    ),
                    WebhookEndpointGrant(
                        "receiver",
                        "http://receiver:8080/two",
                        WebhookEndpointScope.RUNTIME_PRIVATE,
                    ),
                )
            )

    def test_psycopg_interpreter_preserves_graph_identity_but_vends_driver_dsn(self) -> None:
        self.assertEqual(
            psycopg_connection_string(
                "postgresql+psycopg://postgres@runtime-db:5432/webhooks"
            ),
            "postgresql://postgres@runtime-db:5432/webhooks",
        )
        self.assertEqual(
            psycopg_connection_string("postgresql://db/webhooks"),
            "postgresql://db/webhooks",
        )


if __name__ == "__main__":
    unittest.main()
