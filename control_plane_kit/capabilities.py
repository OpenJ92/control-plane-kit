"""Control-plane capabilities advertised by deployable blocks.

Capabilities are operator-facing powers. They describe what a control plane or UI
may ask a running block to do, and they point at the control route set that
implements the power when such a route set exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from control_plane_kit.control_routes import ControlRouteSetName


class CapabilityName(StrEnum):
    """Closed capability names understood by control-plane-kit."""

    HEALTH_CHECKABLE = "health-checkable"
    LOG_READABLE = "log-readable"
    TARGET_MUTABLE = "target-mutable"
    SWITCHABLE = "switchable"
    DRAINABLE = "drainable"
    OBSERVER_MUTABLE = "observer-mutable"
    METRICS_READABLE = "metrics-readable"
    RESTARTABLE = "restartable"
    CIRCUIT_STATE_READABLE = "circuit-state-readable"
    CIRCUIT_RESETTABLE = "circuit-resettable"


@dataclass(frozen=True)
class Capability:
    """One operator/control-plane capability exposed by a graph node."""

    name: CapabilityName
    label: str
    description: str
    route_set: ControlRouteSetName | None = None
    route_path: str | None = None

    def as_descriptor(self) -> dict[str, str]:
        """Return a JSON-friendly capability descriptor."""

        descriptor = {
            "name": self.name.value,
            "label": self.label,
            "description": self.description,
        }
        if self.route_set is not None:
            descriptor["route_set"] = self.route_set.value
        if self.route_path is not None:
            descriptor["route_path"] = self.route_path
        return descriptor


HEALTH_CHECKABLE = Capability(
    name=CapabilityName.HEALTH_CHECKABLE,
    label="Health",
    description="Node exposes health and status state through the control protocol.",
    route_set=ControlRouteSetName.COMMON_STATUS,
)
LOG_READABLE = Capability(
    name=CapabilityName.LOG_READABLE,
    label="Logs",
    description="Node exposes deploy-readable logs.",
    route_set=ControlRouteSetName.LOGS,
)
TARGET_MUTABLE = Capability(
    name=CapabilityName.TARGET_MUTABLE,
    label="Targets",
    description="Node can register or replace downstream targets.",
    route_set=ControlRouteSetName.TARGETS,
)
SWITCHABLE = Capability(
    name=CapabilityName.SWITCHABLE,
    label="Switch",
    description="Node can switch one active downstream target.",
    route_set=ControlRouteSetName.TARGETS,
)
DRAINABLE = Capability(
    name=CapabilityName.DRAINABLE,
    label="Drain",
    description="Node can mark a downstream target as draining.",
    route_set=ControlRouteSetName.TARGETS,
)
OBSERVER_MUTABLE = Capability(
    name=CapabilityName.OBSERVER_MUTABLE,
    label="Observers",
    description="Node can register or replace observer side-channel targets.",
    route_set=ControlRouteSetName.OBSERVERS,
)
METRICS_READABLE = Capability(
    name=CapabilityName.METRICS_READABLE,
    label="Metrics",
    description="Node exposes deploy-readable operational counters.",
    route_set=ControlRouteSetName.METRICS,
)
RESTARTABLE = Capability(
    name=CapabilityName.RESTARTABLE,
    label="Restart",
    description="Runtime owns enough lifecycle state to restart this node.",
)
CIRCUIT_STATE_READABLE = Capability(
    name=CapabilityName.CIRCUIT_STATE_READABLE,
    label="Circuit state",
    description="Node exposes bounded circuit-breaker state.",
    route_set=ControlRouteSetName.CIRCUIT,
)
CIRCUIT_RESETTABLE = Capability(
    name=CapabilityName.CIRCUIT_RESETTABLE,
    label="Reset circuit",
    description="Node accepts an authenticated circuit reset signal.",
    route_set=ControlRouteSetName.CIRCUIT,
)

CAPABILITIES = (
    HEALTH_CHECKABLE,
    LOG_READABLE,
    TARGET_MUTABLE,
    SWITCHABLE,
    DRAINABLE,
    OBSERVER_MUTABLE,
    METRICS_READABLE,
    RESTARTABLE,
    CIRCUIT_STATE_READABLE,
    CIRCUIT_RESETTABLE,
)
CAPABILITY_BY_NAME = {capability.name: capability for capability in CAPABILITIES}


def capability_named(name: CapabilityName | str) -> Capability:
    """Return a known capability or raise a readable error."""

    allowed = ", ".join(capability.name.value for capability in CAPABILITIES)
    try:
        capability_name = CapabilityName(name)
    except ValueError as exc:
        raise KeyError(f"unknown capability {name!r}; known capabilities: {allowed}") from exc
    try:
        return CAPABILITY_BY_NAME[capability_name]
    except KeyError as exc:
        raise KeyError(f"unknown capability {name!r}; known capabilities: {allowed}") from exc
