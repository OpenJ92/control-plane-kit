"""Control-plane capabilities advertised by deployable blocks.

Capabilities are operator-facing powers. They describe what a control plane or UI
may ask a running block to do, and they point at the control route set that
implements the power when such a route set exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from control_plane_kit_core.control_routes import ControlRouteSetName


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
    TRAFFIC_EVIDENCE_READABLE = "traffic-evidence-readable"
    FAULT_STATE_READABLE = "fault-state-readable"
    FAULT_MUTABLE = "fault-mutable"
    CACHE_STATE_READABLE = "cache-state-readable"
    CACHE_PURGEABLE = "cache-purgeable"
    LOAD_STATE_READABLE = "load-state-readable"
    LOAD_MUTABLE = "load-mutable"
    DISCOVERY_READABLE = "discovery-readable"
    DISCOVERY_MUTABLE = "discovery-mutable"


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
TRAFFIC_EVIDENCE_READABLE = Capability(
    name=CapabilityName.TRAFFIC_EVIDENCE_READABLE,
    label="Traffic evidence",
    description="Node exposes bounded paginated HTTP traffic evidence.",
    route_set=ControlRouteSetName.TRAFFIC_EVIDENCE,
)
FAULT_STATE_READABLE = Capability(
    name=CapabilityName.FAULT_STATE_READABLE,
    label="Fault state",
    description="Node exposes bounded test-only fault-injection state.",
    route_set=ControlRouteSetName.FAULTS,
)
FAULT_MUTABLE = Capability(
    name=CapabilityName.FAULT_MUTABLE,
    label="Inject fault",
    description="Node accepts strongly authenticated test-only fault activation.",
    route_set=ControlRouteSetName.FAULTS,
)
CACHE_STATE_READABLE = Capability(
    name=CapabilityName.CACHE_STATE_READABLE,
    label="Cache state",
    description="Node exposes bounded process-local HTTP cache state.",
    route_set=ControlRouteSetName.CACHE,
)
CACHE_PURGEABLE = Capability(
    name=CapabilityName.CACHE_PURGEABLE,
    label="Purge cache",
    description="Node accepts authenticated purge of ephemeral cache entries.",
    route_set=ControlRouteSetName.CACHE,
)
LOAD_STATE_READABLE = Capability(
    name=CapabilityName.LOAD_STATE_READABLE,
    label="Load runs",
    description="Node exposes bounded aggregate evidence for test-only load runs.",
    route_set=ControlRouteSetName.LOADS,
)
LOAD_MUTABLE = Capability(
    name=CapabilityName.LOAD_MUTABLE,
    label="Run load",
    description="Node can start and cancel bounded test-only load runs.",
    route_set=ControlRouteSetName.LOADS,
)
DISCOVERY_READABLE = Capability(
    name=CapabilityName.DISCOVERY_READABLE,
    label="Discovery",
    description="Node resolves bounded lease-backed service registrations.",
    route_set=ControlRouteSetName.DISCOVERY,
)
DISCOVERY_MUTABLE = Capability(
    name=CapabilityName.DISCOVERY_MUTABLE,
    label="Register service",
    description="Node accepts authenticated service registration lifecycle commands.",
    route_set=ControlRouteSetName.DISCOVERY,
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
    TRAFFIC_EVIDENCE_READABLE,
    FAULT_STATE_READABLE,
    FAULT_MUTABLE,
    CACHE_STATE_READABLE,
    CACHE_PURGEABLE,
    LOAD_STATE_READABLE,
    LOAD_MUTABLE,
    DISCOVERY_READABLE,
    DISCOVERY_MUTABLE,
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
