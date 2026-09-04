"""Pure graph-pair transition language for deployment programs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar, TypeAlias

from control_plane_kit_core.topology import (
    DeploymentGraph,
    GraphDiff,
    ValidatedGraph,
    diff_graphs,
)


class _TransitionForm(StrEnum):
    INITIAL = "initial"
    UPDATE = "update"
    TEARDOWN = "teardown"
    NO_OP = "no-op"


@dataclass(frozen=True, slots=True)
class _DeploymentTransitionValue:
    current: ValidatedGraph
    desired: ValidatedGraph
    diff: GraphDiff = field(init=False)

    _expected_form: ClassVar[_TransitionForm]

    def __post_init__(self) -> None:
        form, diff = _classify(self.current, self.desired)
        if form is not self._expected_form:
            raise ValueError(
                f"{self._expected_form.value} deployment transition has the wrong graph form"
            )
        object.__setattr__(self, "diff", diff)


@dataclass(frozen=True, slots=True)
class InitialDeployment(_DeploymentTransitionValue):
    """A transition from structurally empty current truth to desired topology."""

    _expected_form = _TransitionForm.INITIAL


@dataclass(frozen=True, slots=True)
class UpdateDeployment(_DeploymentTransitionValue):
    """A distinct graph pair that does not cross the empty boundary."""

    _expected_form = _TransitionForm.UPDATE


@dataclass(frozen=True, slots=True)
class TeardownDeployment(_DeploymentTransitionValue):
    """A transition from current topology to structurally empty desired truth."""

    _expected_form = _TransitionForm.TEARDOWN


@dataclass(frozen=True, slots=True)
class NoOpDeployment(_DeploymentTransitionValue):
    """A graph pair whose existing structural diff is empty."""

    _expected_form = _TransitionForm.NO_OP


DeploymentTransition: TypeAlias = (
    InitialDeployment | UpdateDeployment | TeardownDeployment | NoOpDeployment
)


def Deploy(
    current: ValidatedGraph,
    desired: ValidatedGraph,
) -> DeploymentTransition:
    """Interpret one valid graph pair as a closed deployment transition."""

    form, _ = _classify(current, desired)
    variants = {
        _TransitionForm.INITIAL: InitialDeployment,
        _TransitionForm.UPDATE: UpdateDeployment,
        _TransitionForm.TEARDOWN: TeardownDeployment,
        _TransitionForm.NO_OP: NoOpDeployment,
    }
    return variants[form](current, desired)


def _classify(
    current: ValidatedGraph,
    desired: ValidatedGraph,
) -> tuple[_TransitionForm, GraphDiff]:
    if not isinstance(current, ValidatedGraph) or not isinstance(
        desired, ValidatedGraph
    ):
        raise TypeError("deployment transition requires two ValidatedGraph values")

    current_graph = current.require_valid()
    desired_graph = desired.require_valid()
    diff = diff_graphs(current, desired)
    if diff.empty:
        return _TransitionForm.NO_OP, diff

    current_empty = _structurally_empty(current_graph)
    desired_empty = _structurally_empty(desired_graph)
    if current_empty and not desired_empty:
        return _TransitionForm.INITIAL, diff
    if not current_empty and desired_empty:
        return _TransitionForm.TEARDOWN, diff
    return _TransitionForm.UPDATE, diff


def _structurally_empty(graph: DeploymentGraph) -> bool:
    return not (
        graph.nodes
        or graph.edges
        or graph.runtimes
        or graph.public_ingresses
        or graph.delegation_authorities
    )


__all__ = [
    "Deploy",
    "DeploymentTransition",
    "InitialDeployment",
    "NoOpDeployment",
    "TeardownDeployment",
    "UpdateDeployment",
]
