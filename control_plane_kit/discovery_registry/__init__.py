"""Independent durable implementation for one service-discovery registry."""

from control_plane_kit.discovery_registry.postgres import (
    DISCOVERY_POSTGRES_SCHEMA,
    PostgresDiscoveryStore,
)
from control_plane_kit.discovery_registry.service import (
    DiscoveryConflict,
    DiscoveryDenied,
    DiscoveryMissing,
    DiscoveryRegistryError,
    DiscoveryRegistryService,
)
from control_plane_kit.discovery_registry.unit_of_work import (
    PostgresDiscoveryUnitOfWork,
    install_discovery_schema,
)

__all__ = [
    "DISCOVERY_POSTGRES_SCHEMA",
    "DiscoveryConflict",
    "DiscoveryDenied",
    "DiscoveryMissing",
    "DiscoveryRegistryError",
    "DiscoveryRegistryService",
    "PostgresDiscoveryStore",
    "PostgresDiscoveryUnitOfWork",
    "install_discovery_schema",
]
