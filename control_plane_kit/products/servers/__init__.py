"""Uniform declaration language for package-owned deployable servers."""

from control_plane_kit.products.servers.catalog import (
    CapabilityImplementation,
    CapabilityResolution,
    ExecutableCapability,
    ProductCatalog,
    ProductDeclaration,
    UnsupportedCapability,
)
from control_plane_kit.products.servers.webhook_delivery import (
    MAX_WEBHOOK_ENDPOINT_GRANTS,
    MAX_WEBHOOK_ENDPOINT_POLICY_BYTES,
    WEBHOOK_DATABASE_ENVIRONMENT,
    WEBHOOK_DELIVERY_PRODUCT,
    WEBHOOK_ENDPOINT_POLICY_ENVIRONMENT,
    WEBHOOK_IDENTITY_ENVIRONMENT,
    WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT,
    WEBHOOK_SIGNING_SECRET_ENVIRONMENT,
    parse_webhook_address_policy,
    render_webhook_address_policy,
    webhook_address_policy_descriptor,
    webhook_address_policy_from_descriptor,
    webhook_delivery_block,
)

__all__ = [
    "CapabilityImplementation",
    "CapabilityResolution",
    "ExecutableCapability",
    "ProductCatalog",
    "ProductDeclaration",
    "UnsupportedCapability",
    "MAX_WEBHOOK_ENDPOINT_GRANTS",
    "MAX_WEBHOOK_ENDPOINT_POLICY_BYTES",
    "WEBHOOK_DATABASE_ENVIRONMENT",
    "WEBHOOK_DELIVERY_PRODUCT",
    "WEBHOOK_ENDPOINT_POLICY_ENVIRONMENT",
    "WEBHOOK_IDENTITY_ENVIRONMENT",
    "WEBHOOK_SIGNING_REFERENCE_ENVIRONMENT",
    "WEBHOOK_SIGNING_SECRET_ENVIRONMENT",
    "parse_webhook_address_policy",
    "render_webhook_address_policy",
    "webhook_address_policy_descriptor",
    "webhook_address_policy_from_descriptor",
    "webhook_delivery_block",
]
