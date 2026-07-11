"""Capability names shared by control-plane-aware nodes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    """A named control-plane affordance."""

    name: str
    description: str


HEALTH = Capability("health", "node can report health")
LOGS = Capability("logs", "node can expose recent logs")
RESTART = Capability("restart", "node can restart without being recreated")
SWITCH_TARGET = Capability("switch-target", "node can change its active target")
SCALE = Capability("scale", "node can change replica count")
