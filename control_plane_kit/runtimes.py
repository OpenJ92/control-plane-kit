"""Runtime interpreters for compiled graphs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Mapping, Protocol

from control_plane_kit.graph import DeploymentGraph, Endpoint, RuntimeRecord
from control_plane_kit.types import RuntimeKind


class CleanupPolicy(StrEnum):
    """How an interpreter should treat owned runtime resources on stop."""

    REMOVE_ON_STOP = "remove-on-stop"
    PRESERVE_ON_STOP = "preserve-on-stop"


class RuntimeActivity(Protocol):
    """Inspectable runtime action planned by an interpreter."""

    def descriptor(self) -> Mapping[str, object]:
        """Return a JSON-friendly description of the planned action."""


@dataclass(frozen=True)
class RuntimePlan:
    """Effect-free plan for a runtime transition."""

    runtime_id: str
    action: str
    activities: tuple[RuntimeActivity, ...] = ()

    def descriptor(self) -> dict[str, object]:
        return {
            "runtime_id": self.runtime_id,
            "action": self.action,
            "activities": [activity.descriptor() for activity in self.activities],
        }

    def to_text(self) -> tuple[str, ...]:
        return tuple(str(activity.descriptor()) for activity in self.activities)


@dataclass(frozen=True)
class RuntimeNodeState:
    """Observed/interpreted state for one node in a runtime."""

    node_id: str
    kind: str
    runtime_id: str
    healthy: bool = False
    environment: Mapping[str, str] = field(default_factory=dict)
    endpoints: Mapping[str, Endpoint] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "runtime_id": self.runtime_id,
            "healthy": self.healthy,
            "environment": dict(sorted(self.environment.items())),
            "endpoints": {key: value.descriptor() for key, value in sorted(self.endpoints.items())},
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RuntimeState:
    """Interpreted state of one runtime after execution or observation."""

    runtime_id: str
    kind: RuntimeKind
    cleanup_policy: CleanupPolicy = CleanupPolicy.REMOVE_ON_STOP
    nodes: Mapping[str, RuntimeNodeState] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def node(self, node_id: str) -> RuntimeNodeState:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.nodes)) or "<none>"
            raise KeyError(f"runtime state has no node {node_id!r}; available: {available}") from exc

    def with_nodes(self, nodes: Mapping[str, RuntimeNodeState]) -> "RuntimeState":
        return replace(self, nodes={**self.nodes, **nodes})

    def descriptor(self) -> dict[str, object]:
        return {
            "runtime_id": self.runtime_id,
            "kind": self.kind.value,
            "cleanup_policy": self.cleanup_policy.value,
            "nodes": {key: value.descriptor() for key, value in sorted(self.nodes.items())},
            "metadata": dict(self.metadata),
        }


class RuntimeInterpreter(Protocol):
    """Interpreter boundary from pure graph data to runtime effects."""

    def plan_start(self, graph: DeploymentGraph, runtime_id: str) -> RuntimePlan:
        """Describe how this interpreter would start one runtime."""

    def up(self, graph: DeploymentGraph, runtime_id: str) -> RuntimeState:
        """Start or observe one runtime and return interpreted state."""

    def plan_stop(self, state: RuntimeState) -> RuntimePlan:
        """Describe how this interpreter would stop one runtime state."""

    def down(self, state: RuntimeState) -> RuntimeState:
        """Stop one runtime state and return its post-stop state."""


@dataclass(frozen=True)
class DryRunActivity:
    """Human-readable no-effect activity."""

    action: str
    target: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def descriptor(self) -> dict[str, object]:
        return {"action": self.action, "target": self.target, "metadata": dict(self.metadata)}


@dataclass(frozen=True)
class DryRunRuntime:
    """Effect-free runtime interpreter."""

    cleanup_policy: CleanupPolicy = CleanupPolicy.REMOVE_ON_STOP

    def plan_start(self, graph: DeploymentGraph, runtime_id: str) -> RuntimePlan:
        runtime = _runtime_record(graph, runtime_id)
        activities: list[RuntimeActivity] = [
            DryRunActivity("observe-runtime", runtime_id, {"kind": runtime.kind.value})
        ]
        for child in runtime.children:
            node = graph.node(child)
            activities.append(
                DryRunActivity(
                    "observe-node",
                    node.node_id,
                    {"kind": node.kind, "env": sorted(node.environment)},
                )
            )
        return RuntimePlan(runtime_id=runtime_id, action="start", activities=tuple(activities))

    def up(self, graph: DeploymentGraph, runtime_id: str) -> RuntimeState:
        runtime = _runtime_record(graph, runtime_id)
        nodes = {
            child: RuntimeNodeState(
                node_id=child,
                kind=graph.node(child).kind,
                runtime_id=runtime_id,
                healthy=True,
                environment=graph.node(child).environment,
                endpoints=graph.node(child).endpoints,
                metadata={"planned": True},
            )
            for child in runtime.children
        }
        return RuntimeState(
            runtime_id=runtime_id,
            kind=runtime.kind,
            cleanup_policy=self.cleanup_policy,
            nodes=nodes,
            metadata={"interpreter": "dry-run"},
        )

    def plan_stop(self, state: RuntimeState) -> RuntimePlan:
        activities: list[RuntimeActivity] = [
            DryRunActivity("stop-node", node_id, {"cleanup_policy": state.cleanup_policy.value})
            for node_id in sorted(state.nodes)
        ]
        return RuntimePlan(runtime_id=state.runtime_id, action="stop", activities=tuple(activities))

    def down(self, state: RuntimeState) -> RuntimeState:
        return replace(state, nodes={}, metadata={**state.metadata, "stopped": True})

    def describe_start(self, graph: DeploymentGraph) -> tuple[str, ...]:
        """Return human-readable startup statements for all runtimes."""

        lines: list[str] = []
        for runtime_id in sorted(graph.runtimes):
            plan = self.plan_start(graph, runtime_id)
            for activity in plan.activities:
                descriptor = activity.descriptor()
                lines.append(f"{descriptor['action']} {descriptor['target']}")
        return tuple(lines)


def _runtime_record(graph: DeploymentGraph, runtime_id: str) -> RuntimeRecord:
    try:
        return graph.runtimes[runtime_id]
    except KeyError as exc:
        available = ", ".join(sorted(graph.runtimes)) or "<none>"
        raise KeyError(f"missing runtime {runtime_id!r}; available: {available}") from exc
