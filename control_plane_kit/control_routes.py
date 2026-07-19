"""Control-plane protocol route descriptors for deployable blocks.

These values describe the private control surface a control plane can call on a
running block. They are data, not a web-framework implementation. FastAPI,
ASGI, Docker, Kubernetes, or another interpreter can all consume the same route
sets.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


DEFAULT_CONTROL_PREFIX = "/__deploy"


class ControlRouteMethod(StrEnum):
    """Closed HTTP methods used by control-plane protocol routes."""

    GET = "GET"
    POST = "POST"


class ControlRouteScope(StrEnum):
    """Authorization scopes required by control-plane protocol routes."""

    READ_STATE = "control:read"
    READ_LOGS = "logs:read"
    SEND_SIGNAL = "signal:send"
    INJECT_FAULT = "fault:inject"
    PURGE_CACHE = "cache:purge"


class ControlRouteSetName(StrEnum):
    """Closed route-set names exposed by controllable blocks."""

    COMMON_STATUS = "common-status"
    LOGS = "logs"
    TARGETS = "targets"
    OBSERVERS = "observers"
    METRICS = "metrics"
    CIRCUIT = "circuit"
    TRAFFIC_EVIDENCE = "traffic-evidence"
    FAULTS = "faults"
    CACHE = "cache"


@dataclass(frozen=True)
class ControlRoute:
    """One route exposed by a block control protocol surface."""

    name: str
    method: ControlRouteMethod
    path: str
    scope: ControlRouteScope
    description: str

    def as_descriptor(self) -> dict[str, str]:
        """Return a JSON-friendly route descriptor."""

        return {
            "name": self.name,
            "method": self.method.value,
            "path": self.path,
            "scope": self.scope.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class ControlRouteSet:
    """Named group of related control-plane protocol routes."""

    name: ControlRouteSetName
    routes: tuple[ControlRoute, ...]

    def as_descriptor(self) -> dict[str, object]:
        """Return a JSON-friendly route-set descriptor."""

        return {
            "name": self.name.value,
            "routes": [route.as_descriptor() for route in self.routes],
        }


def control_path(path: str, *, prefix: str = DEFAULT_CONTROL_PREFIX) -> str:
    """Return ``path`` under the configured deploy-control prefix."""

    prefix = prefix.rstrip("/")
    path = path if path.startswith("/") else f"/{path}"
    return f"{prefix}{path}"


COMMON_STATUS_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.COMMON_STATUS,
    routes=(
        ControlRoute(
            name="capabilities",
            method=ControlRouteMethod.GET,
            path=control_path("capabilities"),
            scope=ControlRouteScope.READ_STATE,
            description="List block capabilities and protocol route surfaces.",
        ),
        ControlRoute(
            name="health",
            method=ControlRouteMethod.GET,
            path=control_path("health"),
            scope=ControlRouteScope.READ_STATE,
            description="Read block health through the control-plane path.",
        ),
        ControlRoute(
            name="status",
            method=ControlRouteMethod.GET,
            path=control_path("status"),
            scope=ControlRouteScope.READ_STATE,
            description="Read block runtime status through the control-plane path.",
        ),
    ),
)

LOG_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.LOGS,
    routes=(
        ControlRoute(
            name="logs",
            method=ControlRouteMethod.GET,
            path=control_path("logs"),
            scope=ControlRouteScope.READ_LOGS,
            description="Read block logs through the control-plane path.",
        ),
    ),
)

TARGET_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.TARGETS,
    routes=(
        ControlRoute(
            name="targets",
            method=ControlRouteMethod.GET,
            path=control_path("targets"),
            scope=ControlRouteScope.READ_STATE,
            description="List downstream targets known to this block.",
        ),
        ControlRoute(
            name="targets",
            method=ControlRouteMethod.POST,
            path=control_path("targets"),
            scope=ControlRouteScope.SEND_SIGNAL,
            description="Register or replace downstream targets known to this block.",
        ),
        ControlRoute(
            name="active-target",
            method=ControlRouteMethod.POST,
            path=control_path("active-target"),
            scope=ControlRouteScope.SEND_SIGNAL,
            description="Switch the active downstream target.",
        ),
        ControlRoute(
            name="drain-target",
            method=ControlRouteMethod.POST,
            path=control_path("drain-target"),
            scope=ControlRouteScope.SEND_SIGNAL,
            description="Mark a downstream target as draining.",
        ),
    ),
)

OBSERVER_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.OBSERVERS,
    routes=(
        ControlRoute(
            name="observers",
            method=ControlRouteMethod.GET,
            path=control_path("observers"),
            scope=ControlRouteScope.READ_STATE,
            description="List observer side-channel targets known to this block.",
        ),
        ControlRoute(
            name="observers",
            method=ControlRouteMethod.POST,
            path=control_path("observers"),
            scope=ControlRouteScope.SEND_SIGNAL,
            description="Register or replace observer side-channel targets.",
        ),
    ),
)

METRIC_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.METRICS,
    routes=(
        ControlRoute(
            name="metrics",
            method=ControlRouteMethod.GET,
            path=control_path("metrics"),
            scope=ControlRouteScope.READ_STATE,
            description="Read bounded operational counters from this block.",
        ),
    ),
)

CIRCUIT_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.CIRCUIT,
    routes=(
        ControlRoute(
            name="circuit-state",
            method=ControlRouteMethod.GET,
            path=control_path("circuit"),
            scope=ControlRouteScope.READ_STATE,
            description="Read bounded circuit-breaker state.",
        ),
        ControlRoute(
            name="circuit-reset",
            method=ControlRouteMethod.POST,
            path=control_path("circuit/reset"),
            scope=ControlRouteScope.SEND_SIGNAL,
            description="Reset a circuit breaker to its closed state.",
        ),
    ),
)

TRAFFIC_EVIDENCE_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.TRAFFIC_EVIDENCE,
    routes=(
        ControlRoute(
            name="traffic-evidence",
            method=ControlRouteMethod.GET,
            path=control_path("traffic-evidence"),
            scope=ControlRouteScope.READ_LOGS,
            description="Read bounded paginated traffic evidence from this block.",
        ),
    ),
)

FAULT_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.FAULTS,
    routes=(
        ControlRoute(
            name="fault-state",
            method=ControlRouteMethod.GET,
            path=control_path("fault"),
            scope=ControlRouteScope.READ_STATE,
            description="Read bounded test-only fault-injection state.",
        ),
        ControlRoute(
            name="fault-replace",
            method=ControlRouteMethod.POST,
            path=control_path("fault"),
            scope=ControlRouteScope.INJECT_FAULT,
            description="Replace test-only fault activation with closed policy data.",
        ),
    ),
)

CACHE_ROUTES = ControlRouteSet(
    name=ControlRouteSetName.CACHE,
    routes=(
        ControlRoute(
            name="cache-state",
            method=ControlRouteMethod.GET,
            path=control_path("cache"),
            scope=ControlRouteScope.READ_STATE,
            description="Read bounded HTTP cache state and counters.",
        ),
        ControlRoute(
            name="cache-purge",
            method=ControlRouteMethod.POST,
            path=control_path("cache/purge"),
            scope=ControlRouteScope.PURGE_CACHE,
            description="Purge ephemeral process-local HTTP cache entries.",
        ),
    ),
)

CONTROL_ROUTE_SETS = (
    COMMON_STATUS_ROUTES,
    LOG_ROUTES,
    TARGET_ROUTES,
    OBSERVER_ROUTES,
    METRIC_ROUTES,
    CIRCUIT_ROUTES,
    TRAFFIC_EVIDENCE_ROUTES,
    FAULT_ROUTES,
    CACHE_ROUTES,
)


def route_set_named(name: ControlRouteSetName | str) -> ControlRouteSet:
    """Return a known route set or raise a readable error."""

    allowed = ", ".join(route_set.name.value for route_set in CONTROL_ROUTE_SETS)
    try:
        route_set_name = ControlRouteSetName(name)
    except ValueError as exc:
        raise KeyError(
            f"unknown control route set {name!r}; known route sets: {allowed}"
        ) from exc
    for route_set in CONTROL_ROUTE_SETS:
        if route_set.name == route_set_name:
            return route_set
    raise KeyError(f"unknown control route set {name!r}; known route sets: {allowed}")
