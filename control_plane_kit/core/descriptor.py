"""Descriptor helpers for graph values."""

from __future__ import annotations

import json
from pathlib import Path

from control_plane_kit.core.graph import DeploymentGraph


def write_descriptor(graph: DeploymentGraph, path: Path) -> None:
    """Write a deployment graph descriptor as pretty JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph.descriptor(), indent=2, sort_keys=True) + "\n")
