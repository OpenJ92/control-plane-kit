"""Reusable graph-transition acceptance scenarios.

Scenarios are compositions over the topology and planning algebras. They are
not new core primitives: each expectation names existing typed operations and
dependency relations that later roadmap interpreters must preserve.
"""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.planning import (
    ActivityOperation,
    AddSocketConnection,
    ChangeTarget,
    NodeTarget,
    ReconcileNode,
    ReconcileRuntime,
    RemoveSocketConnection,
    ReviewChange,
    RiskLevel,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit.topology import DeploymentGraph
from control_plane_kit.topology.changes import FieldSubject
from control_plane_kit.topology.validation import (
    EdgeSubject,
    GraphSubject,
    NodeSubject,
    RuntimeSubject,
)


@dataclass(frozen=True)
class OperationExpectation:
    """One typed operation expected to target one topology identity."""

    operation_type: type[object]
    target_id: str


@dataclass(frozen=True)
class DependencyExpectation:
    """A required ordering edge between two expected operations."""

    predecessor: OperationExpectation
    successor: OperationExpectation


@dataclass(frozen=True)
class ScenarioExpectation:
    """Stable planning semantics shared by future roadmap interpreters."""

    operations: tuple[OperationExpectation, ...]
    required_dependencies: tuple[DependencyExpectation, ...] = ()
    max_risk: RiskLevel = RiskLevel.LOW
    ready_for_execution: bool = True


@dataclass(frozen=True)
class PlanningScenario:
    """A named desired-state transition and its semantic acceptance contract."""

    scenario_id: str
    title: str
    approval_comment: str
    current_graph: DeploymentGraph
    desired_graph: DeploymentGraph
    expectation: ScenarioExpectation


def operation_expectation(operation: ActivityOperation) -> OperationExpectation:
    """Project a typed operation to the stable identity used by scenarios."""

    match operation:
        case (
            StartNode(target=NodeTarget(node_id=target_id))
            | StopNode(target=NodeTarget(node_id=target_id))
            | WaitForHealthy(target=NodeTarget(node_id=target_id))
            | ReconcileNode(target=NodeTarget(node_id=target_id))
        ):
            return OperationExpectation(type(operation), target_id)
        case (
            StartRuntime(target=RuntimeTarget(runtime_id=target_id))
            | StopRuntime(target=RuntimeTarget(runtime_id=target_id))
            | ReconcileRuntime(target=RuntimeTarget(runtime_id=target_id))
        ):
            return OperationExpectation(type(operation), target_id)
        case (
            AddSocketConnection(target=SocketConnectionTarget(edge_id=target_id))
            | SwitchSocketConnection(target=SocketConnectionTarget(edge_id=target_id))
            | RemoveSocketConnection(target=SocketConnectionTarget(edge_id=target_id))
        ):
            return OperationExpectation(type(operation), target_id)
        case ReviewChange(target=ChangeTarget(subject=subject)):
            return OperationExpectation(type(operation), _subject_identity(subject))
        case _:
            raise TypeError(f"unsupported scenario operation {operation!r}")


def _subject_identity(subject: object) -> str:
    match subject:
        case NodeSubject(node_id=node_id):
            return node_id
        case RuntimeSubject(runtime_id=runtime_id):
            return runtime_id
        case EdgeSubject(edge_id=edge_id):
            return edge_id
        case FieldSubject(owner=owner):
            return _subject_identity(owner)
        case GraphSubject():
            return "graph"
        case _:
            raise TypeError(f"unsupported scenario change subject {subject!r}")
