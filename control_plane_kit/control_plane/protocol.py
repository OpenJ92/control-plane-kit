"""HTTP route contract for control-plane-capable services."""

from __future__ import annotations

from dataclasses import dataclass


CONTROL_PLANE_PROTOCOL = "control-plane-kit.v1"
CONTROL_PLANE_PREFIX = "/__control"


@dataclass(frozen=True)
class ControlRoute:
    """One route a control-plane-capable service may expose."""

    method: str
    path: str
    description: str


def default_control_routes(prefix: str = CONTROL_PLANE_PREFIX) -> tuple[ControlRoute, ...]:
    """Return the minimal route vocabulary for mutable infrastructure nodes."""

    return (
        ControlRoute("GET", f"{prefix}/capabilities", "list supported capabilities"),
        ControlRoute("GET", f"{prefix}/health", "return health details"),
        ControlRoute("GET", f"{prefix}/logs", "return recent logs"),
        ControlRoute("POST", f"{prefix}/targets", "register or replace route targets"),
        ControlRoute("POST", f"{prefix}/active-target", "switch active target"),
    )
