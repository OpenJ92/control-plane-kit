"""Runtime interpreters for compiled graphs."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.graph import DeploymentGraph


@dataclass(frozen=True)
class DryRunRuntime:
    """Effect-free runtime interpreter."""

    def describe_start(self, graph: DeploymentGraph) -> tuple[str, ...]:
        """Return human-readable startup statements."""

        lines: list[str] = []
        for runtime_id, runtime in sorted(graph.runtimes.items()):
            lines.append(f"runtime {runtime_id} ({runtime.kind.value})")
            for child in runtime.children:
                node = graph.node(child)
                lines.append(f"  node {node.node_id}: {node.kind}")
                for key, value in sorted(node.environment.items()):
                    lines.append(f"    env {key}={value}")
        return tuple(lines)
