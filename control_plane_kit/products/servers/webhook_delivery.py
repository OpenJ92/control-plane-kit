"""Package-owned durable webhook delivery block."""

from __future__ import annotations

import json
from collections.abc import Mapping

from control_plane_kit.core.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.core.environment import PublicStaticEnvironmentBinding
from control_plane_kit.implementations import DockerImageImplementation
from control_plane_kit.core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
    SecretReferenceEnvironmentDelivery,
)
from control_plane_kit.core.types import Protocol
from control_plane_kit.core.verification import HttpCheck, VerificationContract
from control_plane_kit.domains.webhook import (
    WebhookAddressPolicy,
    WebhookEndpointGrant,
    WebhookEndpointScope,
)
from control_plane_kit.products.servers.catalog import (
    CapabilityImplementation,
    ExecutableCapability,
    ProductDeclaration,
)


WEBHOOK_DATABASE_ENVIRONMENT = "WEBHOOK_DATABASE_URL"
WEBHOOK_ENDPOINT_POLICY_ENVIRONMENT = "CPK_WEBHOOK_ENDPOINT_POLICY"
WEBHOOK_IDENTITY_ENVIRONMENT = "CPK_WEBHOOK_IDENTITY_TOKEN"
WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT = "CPK_WEBHOOK_SIGNING_REFERENCE"
WEBHOOK_SIGNING_SECRET_ENVIRONMENT = "CPK_WEBHOOK_SIGNING_SECRET"
MAX_WEBHOOK_ENDPOINT_GRANTS = 256
MAX_WEBHOOK_ENDPOINT_POLICY_BYTES = 131_072


def webhook_address_policy_descriptor(
    policy: WebhookAddressPolicy,
) -> dict[str, object]:
    """Encode process bootstrap authority without secret values."""

    if not isinstance(policy, WebhookAddressPolicy):
        raise TypeError("webhook bootstrap policy must be typed")
    if len(policy.grants) > MAX_WEBHOOK_ENDPOINT_GRANTS:
        raise ValueError("webhook bootstrap policy exceeds its grant bound")
    return {
        "grants": [
            {
                "endpoint_id": grant.endpoint_id,
                "url": grant.url,
                "scope": grant.scope.value,
            }
            for grant in policy.grants
        ]
    }


def webhook_address_policy_from_descriptor(value: object) -> WebhookAddressPolicy:
    """Decode the exact closed bootstrap-policy descriptor."""

    if not isinstance(value, Mapping) or set(value) != {"grants"}:
        raise ValueError("webhook bootstrap policy descriptor is malformed")
    grants = value["grants"]
    if not isinstance(grants, list) or len(grants) > MAX_WEBHOOK_ENDPOINT_GRANTS:
        raise ValueError("webhook bootstrap policy grants are malformed")
    decoded: list[WebhookEndpointGrant] = []
    for item in grants:
        if not isinstance(item, Mapping) or set(item) != {
            "endpoint_id",
            "url",
            "scope",
        }:
            raise ValueError("webhook bootstrap policy grant is malformed")
        endpoint_id = item["endpoint_id"]
        url = item["url"]
        scope = item["scope"]
        if not all(isinstance(part, str) for part in (endpoint_id, url, scope)):
            raise ValueError("webhook bootstrap policy grant is malformed")
        try:
            decoded.append(
                WebhookEndpointGrant(
                    endpoint_id,
                    url,
                    WebhookEndpointScope(scope),
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("webhook bootstrap policy grant is malformed") from error
    return WebhookAddressPolicy(tuple(decoded))


def render_webhook_address_policy(policy: WebhookAddressPolicy) -> str:
    """Render deterministic bounded JSON for process bootstrap."""

    rendered = json.dumps(
        webhook_address_policy_descriptor(policy),
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(rendered.encode()) > MAX_WEBHOOK_ENDPOINT_POLICY_BYTES:
        raise ValueError("webhook bootstrap policy exceeds its encoded bound")
    return rendered


def parse_webhook_address_policy(value: str) -> WebhookAddressPolicy:
    """Parse bounded bootstrap JSON into the closed policy language."""

    if not isinstance(value, str) or len(value.encode()) > MAX_WEBHOOK_ENDPOINT_POLICY_BYTES:
        raise ValueError("webhook bootstrap policy is malformed")
    try:
        descriptor = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("webhook bootstrap policy is malformed") from error
    return webhook_address_policy_from_descriptor(descriptor)


def webhook_delivery_block(
    block_id: str = "webhook-delivery",
    *,
    endpoint_grants: tuple[WebhookEndpointGrant, ...] = (),
    display_name: str = "Durable Webhook Delivery",
    image: str = "control-plane-kit:local",
    identity_secret_reference: str = "secret://webhook-delivery/identity-attestation",
    signing_secret_reference: str = "secret://webhook-delivery/signing-key",
) -> ApplicationBlock:
    """Return the package-owned Docker webhook ApplicationBlock."""

    policy = WebhookAddressPolicy(endpoint_grants)
    signing_reference = SecretReference(signing_secret_reference)
    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.WEBHOOK_DELIVERY,
            maturity=ProductMaturity.OPERATIONAL,
            display_name=display_name,
            health_path="/health/ready",
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
            verification=VerificationContract(
                (
                    HttpCheck(
                        check_id="webhook-readiness",
                        provider_socket="internal",
                        path="/health/ready",
                    ),
                )
            ),
        ),
        DockerImageImplementation(
            image=image,
            command=(
                "python",
                "-m",
                "control_plane_kit.entrypoints.webhook_server.main",
            ),
            ports={"internal": 8080},
            environment=(
                PublicStaticEnvironmentBinding(
                    WEBHOOK_ENDPOINT_POLICY_ENVIRONMENT,
                    render_webhook_address_policy(policy),
                ),
            ),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    WEBHOOK_IDENTITY_ENVIRONMENT,
                    SecretReference(identity_secret_reference),
                ),
                SecretEnvironmentDelivery(
                    WEBHOOK_SIGNING_SECRET_ENVIRONMENT,
                    signing_reference,
                ),
                SecretReferenceEnvironmentDelivery(
                    WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT,
                    signing_reference,
                ),
            ),
        ),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "database",
                    Protocol.POSTGRES,
                    (WEBHOOK_DATABASE_ENVIRONMENT,),
                ),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )


WEBHOOK_DELIVERY_PRODUCT = ProductDeclaration(
    PackageServerProduct.WEBHOOK_DELIVERY,
    ProductMaturity.OPERATIONAL,
    webhook_delivery_block(),
    (
        ExecutableCapability(
            CapabilityName.HEALTH_CHECKABLE,
            CapabilityImplementation.APPLICATION_PROBE,
            path="/health/ready",
        ),
    ),
)
