"""Process composition package for the service-discovery server."""

from control_plane_kit.discovery_server.app import (
    MAX_DISCOVERY_REQUEST_BYTES,
    MAX_DISCOVERY_RESPONSE_BYTES,
    create_service_discovery_app,
)

__all__ = [
    "MAX_DISCOVERY_REQUEST_BYTES",
    "MAX_DISCOVERY_RESPONSE_BYTES",
    "create_service_discovery_app",
]
