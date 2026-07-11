"""Control-plane capability descriptors."""

from control_plane_kit.control_plane.capabilities import (
    Capability,
    HEALTH,
    LOGS,
    RESTART,
    SCALE,
    SWITCH_TARGET,
)
from control_plane_kit.control_plane.protocol import (
    CONTROL_PLANE_PREFIX,
    CONTROL_PLANE_PROTOCOL,
    ControlRoute,
    default_control_routes,
)

__all__ = [
    "CONTROL_PLANE_PREFIX",
    "CONTROL_PLANE_PROTOCOL",
    "Capability",
    "ControlRoute",
    "HEALTH",
    "LOGS",
    "RESTART",
    "SCALE",
    "SWITCH_TARGET",
    "default_control_routes",
]
