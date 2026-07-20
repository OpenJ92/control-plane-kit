"""Heterogeneous service and application-infrastructure topology.

The expression is ordinary deployment algebra. It does not introduce a
combined service-stack product or hide application-owned persistence.
"""

from __future__ import annotations

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    Protocol,
    ProviderSocket,
    PublicStaticEnvironmentBinding,
    SecretEnvironmentDelivery,
    SecretReference,
    SocketConnection,
)
from control_plane_kit.servers import (
    opentelemetry_collector_block,
    service_discovery_block,
)
from control_plane_kit.products.servers import webhook_delivery_block
from control_plane_kit.domains.webhook import (
    WebhookEndpointGrant,
    WebhookEndpointScope,
)
from control_plane_kit.servers.opentelemetry_collector import COLLECTOR_IMAGE


SERVICE_RUNTIME_ID = "service-infrastructure"
DISCOVERY_IDENTITY_REFERENCE = "secret://service-acceptance/discovery-identity"
WEBHOOK_IDENTITY_REFERENCE = "secret://service-acceptance/webhook-identity"
WEBHOOK_SIGNING_REFERENCE = "secret://service-acceptance/webhook-signing"


def service_infrastructure_recipe(
    *,
    package_image: str = "control-plane-kit-live-test:service-infrastructure",
    collector_image: str = COLLECTOR_IMAGE,
) -> DeploymentRecipe:
    """Compose discovery, telemetry, and webhook delivery as graph data."""

    receiver_address = (
        f"http://{SERVICE_RUNTIME_ID}-webhook-receiver:8090/hook"
    )
    return DeploymentRecipe(
        "service-infrastructure",
        DockerRuntime(
            runtime_id=SERVICE_RUNTIME_ID,
            network_name=SERVICE_RUNTIME_ID,
            children=(
                _ephemeral_postgres("discovery-postgres"),
                _ephemeral_postgres("webhook-postgres"),
                _webhook_receiver(package_image),
                service_discovery_block(
                    image=package_image,
                    identity_secret_reference=DISCOVERY_IDENTITY_REFERENCE,
                ),
                opentelemetry_collector_block(image=collector_image),
                webhook_delivery_block(
                    image=package_image,
                    endpoint_grants=(
                        WebhookEndpointGrant(
                            "receiver",
                            receiver_address,
                            WebhookEndpointScope.RUNTIME_PRIVATE,
                        ),
                    ),
                    identity_secret_reference=WEBHOOK_IDENTITY_REFERENCE,
                    signing_secret_reference=WEBHOOK_SIGNING_REFERENCE,
                ),
                SocketConnection(
                    "discovery-postgres",
                    "internal",
                    "service-discovery",
                    "database",
                ),
                SocketConnection(
                    "webhook-postgres",
                    "internal",
                    "webhook-delivery",
                    "database",
                ),
            ),
        ),
    )


def _ephemeral_postgres(block_id: str) -> DataBlock:
    return DataBlock(
        BlockSpec(block_id, f"Ephemeral {block_id}"),
        DockerImageImplementation(
            image="postgres:16-alpine",
            ports={"internal": 5432},
            environment=(
                PublicStaticEnvironmentBinding("POSTGRES_DB", "root"),
                PublicStaticEnvironmentBinding("POSTGRES_USER", "root"),
                PublicStaticEnvironmentBinding("POSTGRES_HOST_AUTH_METHOD", "trust"),
            ),
        ),
        BlockSockets(
            providers=(ProviderSocket("internal", Protocol.POSTGRES),)
        ),
    )


def _webhook_receiver(image: str) -> ApplicationBlock:
    return ApplicationBlock(
        BlockSpec(
            "webhook-receiver",
            "Controlled webhook acceptance receiver",
            health_path="/health",
        ),
        DockerImageImplementation(
            image=image,
            command=("python", "-m", "tests.fixtures.webhook_receiver"),
            ports={"internal": 8090},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_WEBHOOK_RECEIVER_SECRET",
                    SecretReference(WEBHOOK_SIGNING_REFERENCE),
                ),
            ),
        ),
        BlockSockets(
            providers=(ProviderSocket("internal", Protocol.HTTP),)
        ),
    )
