"""Durable control-plane operations package boundary."""

from __future__ import annotations

from control_plane_kit_core import DeploymentProgramStage

from .foundation import (
    OPERATIONS_PACKAGE_BOUNDARY,
    OperationsPackageBoundary,
)

__version__ = "0.1.0"

__all__ = [
    "DeploymentProgramStage",
    "OPERATIONS_PACKAGE_BOUNDARY",
    "OperationsPackageBoundary",
]
