"""Closed values for the deployment application program.

These values classify intent and suspension points. They do not replace the
canonical graph, approval, run, event, or recovery records owned elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from control_plane_kit.topology import DeploymentGraph
from control_plane_kit.workflows import (
    ApprovalRequestResult,
    ExecutionCoordinatorResult,
)


@dataclass(frozen=True)
class InitialDeployment:
    """Construct a non-empty desired topology from an empty current graph."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_empty("current", self.current)
        _require_non_empty("desired", self.desired)


@dataclass(frozen=True)
class UpdateDeployment:
    """Replace one distinct topology without crossing the empty boundary."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_graph("current", self.current)
        _require_graph("desired", self.desired)
        if self.current == self.desired:
            raise ValueError("update deployment requires distinct graphs")
        if _is_empty(self.current) != _is_empty(self.desired):
            raise ValueError(
                "update deployment cannot cross the empty-topology boundary"
            )


@dataclass(frozen=True)
class TeardownDeployment:
    """Move from a non-empty topology to an empty desired topology."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_non_empty("current", self.current)
        _require_empty("desired", self.desired)


@dataclass(frozen=True)
class NoOpDeployment:
    """Represent an identical current and desired topology."""

    current: DeploymentGraph
    desired: DeploymentGraph

    def __post_init__(self) -> None:
        _require_graph("current", self.current)
        _require_graph("desired", self.desired)
        if self.current != self.desired:
            raise ValueError("no-op deployment requires identical graphs")


DeploymentTransition: TypeAlias = (
    InitialDeployment | UpdateDeployment | TeardownDeployment | NoOpDeployment
)


@dataclass(frozen=True)
class ApprovalSuspension:
    """Durable boundary where deployment waits for an authorization decision."""

    transition: DeploymentTransition
    approval_request: ApprovalRequestResult

    def __post_init__(self) -> None:
        _require_transition(self.transition)
        if not isinstance(self.approval_request, ApprovalRequestResult):
            raise TypeError("approval_request must be ApprovalRequestResult")


@dataclass(frozen=True)
class RecoverySuspension:
    """Durable boundary where execution waits for an operator recovery decision."""

    transition: DeploymentTransition
    execution: ExecutionCoordinatorResult

    def __post_init__(self) -> None:
        _require_transition(self.transition)
        if not isinstance(self.execution, ExecutionCoordinatorResult):
            raise TypeError("execution must be ExecutionCoordinatorResult")


DeploymentSuspension: TypeAlias = ApprovalSuspension | RecoverySuspension


def classify_transition(
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> DeploymentTransition:
    """Interpret two graph values as one closed deployment-transition form."""

    _require_graph("current", current)
    _require_graph("desired", desired)
    match (_is_empty(current), _is_empty(desired), current == desired):
        case (_, _, True):
            return NoOpDeployment(current, desired)
        case (True, False, False):
            return InitialDeployment(current, desired)
        case (False, True, False):
            return TeardownDeployment(current, desired)
        case (False, False, False):
            return UpdateDeployment(current, desired)
        case (True, True, False):
            # Empty graphs with different names still have a graph-name diff.
            return UpdateDeployment(current, desired)


def _require_transition(value: object) -> None:
    if not isinstance(
        value,
        InitialDeployment | UpdateDeployment | TeardownDeployment | NoOpDeployment,
    ):
        raise TypeError("transition must be a DeploymentTransition")


def _require_graph(name: str, value: object) -> None:
    if not isinstance(value, DeploymentGraph):
        raise TypeError(f"{name} must be DeploymentGraph")


def _require_empty(name: str, graph: DeploymentGraph) -> None:
    _require_graph(name, graph)
    if not _is_empty(graph):
        raise ValueError(f"{name} must be an empty deployment graph")


def _require_non_empty(name: str, graph: DeploymentGraph) -> None:
    _require_graph(name, graph)
    if _is_empty(graph):
        raise ValueError(f"{name} must be a non-empty deployment graph")


def _is_empty(graph: DeploymentGraph) -> bool:
    return not graph.nodes and not graph.edges and not graph.runtimes
