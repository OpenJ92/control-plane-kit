"""Closed, pure activity-plan algebra."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

from control_plane_kit.core.topology.changes import DiffSubject


@dataclass(frozen=True, order=True)
class ActivityId:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("activity id must not be empty")


@dataclass(frozen=True)
class ActivityDependency:
    predecessor: ActivityId

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, ActivityId):
            raise TypeError("activity dependency predecessor must be ActivityId")


@dataclass(frozen=True)
class NodeTarget:
    node_id: str

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise ValueError("node target must not be empty")


@dataclass(frozen=True)
class RuntimeTarget:
    runtime_id: str

    def __post_init__(self) -> None:
        if not self.runtime_id.strip():
            raise ValueError("runtime target must not be empty")


@dataclass(frozen=True)
class DataResourceTarget:
    node_id: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.node_id.strip() or not self.resource_id.strip():
            raise ValueError("data resource target identities must not be empty")


@dataclass(frozen=True)
class SocketConnectionTarget:
    edge_id: str

    def __post_init__(self) -> None:
        if not self.edge_id.strip():
            raise ValueError("socket connection target must not be empty")


@dataclass(frozen=True)
class ChangeTarget:
    subject: DiffSubject

    def __post_init__(self) -> None:
        if not isinstance(self.subject, DiffSubject):
            raise TypeError("change target subject must be a typed diff subject")


@dataclass(frozen=True)
class StartNode:
    target: NodeTarget


@dataclass(frozen=True)
class StopNode:
    target: NodeTarget


@dataclass(frozen=True)
class RemoveNodeResource:
    target: NodeTarget


@dataclass(frozen=True)
class WaitForHealthy:
    target: NodeTarget


@dataclass(frozen=True)
class AddSocketConnection:
    target: SocketConnectionTarget


@dataclass(frozen=True)
class SwitchSocketConnection:
    target: SocketConnectionTarget


@dataclass(frozen=True)
class RemoveSocketConnection:
    target: SocketConnectionTarget


@dataclass(frozen=True)
class ReconcileNode:
    target: NodeTarget


@dataclass(frozen=True)
class ReconcileRuntime:
    target: RuntimeTarget


@dataclass(frozen=True)
class StartRuntime:
    target: RuntimeTarget


@dataclass(frozen=True)
class StopRuntime:
    target: RuntimeTarget


@dataclass(frozen=True)
class RemoveRuntimeResource:
    target: RuntimeTarget


@dataclass(frozen=True)
class DestroyDataResource:
    target: DataResourceTarget


class ReviewReason(StrEnum):
    UNSUPPORTED_CHANGE = "unsupported-change"
    AMBIGUOUS_CHANGE = "ambiguous-change"


@dataclass(frozen=True)
class ReviewChange:
    target: ChangeTarget
    reason: ReviewReason


ActivityOperation: TypeAlias = (
    StartNode
    | StopNode
    | RemoveNodeResource
    | WaitForHealthy
    | AddSocketConnection
    | SwitchSocketConnection
    | RemoveSocketConnection
    | ReconcileNode
    | ReconcileRuntime
    | StartRuntime
    | StopRuntime
    | RemoveRuntimeResource
    | DestroyDataResource
    | ReviewChange
)


class CompensationMaterialSource(StrEnum):
    """Which immutable plan-pinned graph defines compensation material."""

    BASE_GRAPH = "base-graph"
    DESIRED_GRAPH = "desired-graph"


class NonCompensatableReason(StrEnum):
    """Closed reasons a completed operation has no safe automatic inverse."""

    RESOURCE_REMOVAL = "resource-removal"
    DATA_DESTRUCTION = "data-destruction"


@dataclass(frozen=True)
class Compensate:
    """Pinned inverse operation and the graph from which to materialize it."""

    operation: ActivityOperation
    material_source: CompensationMaterialSource

    def __post_init__(self) -> None:
        _require_typed_operation(self.operation)
        if not isinstance(self.material_source, CompensationMaterialSource):
            raise TypeError("compensation source must be CompensationMaterialSource")


@dataclass(frozen=True)
class NoCompensationRequired:
    """The forward activity has no external state requiring an inverse."""


@dataclass(frozen=True)
class NonCompensatable:
    """The effect may complete, but automatic reversal would be unsafe."""

    reason: NonCompensatableReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, NonCompensatableReason):
            raise TypeError("non-compensatable reason must be closed")


CompensationSpec: TypeAlias = Compensate | NoCompensationRequired | NonCompensatable


class RiskLevel(StrEnum):
    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActivityImpact(StrEnum):
    NON_DESTRUCTIVE = "non-destructive"
    DISRUPTIVE = "disruptive"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class PlannedActivity:
    activity_id: ActivityId
    operation: ActivityOperation
    dependencies: tuple[ActivityDependency, ...] = ()
    risk: RiskLevel = RiskLevel.LOW
    impact: ActivityImpact = ActivityImpact.NON_DESTRUCTIVE
    compensation: CompensationSpec = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.activity_id, ActivityId):
            raise TypeError("planned activity requires ActivityId")
        if not all(
            isinstance(dependency, ActivityDependency)
            for dependency in self.dependencies
        ):
            raise TypeError("planned activity dependencies must be ActivityDependency")
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                sorted(
                    self.dependencies,
                    key=lambda dependency: dependency.predecessor.value,
                )
            ),
        )
        if not isinstance(self.risk, RiskLevel):
            raise TypeError("planned activity risk must be RiskLevel")
        if not isinstance(self.impact, ActivityImpact):
            raise TypeError("planned activity impact must be ActivityImpact")
        _require_typed_operation(self.operation)
        object.__setattr__(
            self,
            "compensation",
            compensation_for_operation(self.operation),
        )


class PlanViolationCode(StrEnum):
    DUPLICATE_ACTIVITY_ID = "duplicate-activity-id"
    MISSING_DEPENDENCY = "missing-dependency"
    SELF_DEPENDENCY = "self-dependency"
    DUPLICATE_DEPENDENCY = "duplicate-dependency"
    DEPENDENCY_CYCLE = "dependency-cycle"
    DESTRUCTIVE_RISK = "destructive-risk"
    DATA_DESTRUCTION_SAFETY = "data-destruction-safety"
    REVIEW_RISK = "review-risk"


@dataclass(frozen=True)
class PlanViolation:
    code: PlanViolationCode
    message: str
    activity_id: ActivityId | None = None


class InvalidActivityPlan(ValueError):
    def __init__(self, violations: tuple[PlanViolation, ...]) -> None:
        self.violations = violations
        summary = "; ".join(violation.message for violation in violations)
        super().__init__(summary)


@dataclass(frozen=True)
class ActivityPlan:
    """A dependency-ordered collection of typed intended activities."""

    activities: tuple[PlannedActivity, ...]

    def __post_init__(self) -> None:
        if not all(isinstance(activity, PlannedActivity) for activity in self.activities):
            raise TypeError("activity plan values must be PlannedActivity")
        violations = _validate_composition(self.activities)
        if violations:
            raise InvalidActivityPlan(violations)
        object.__setattr__(self, "activities", _topological_order(self.activities))

    @property
    def ready_for_execution(self) -> bool:
        return not any(
            isinstance(activity.operation, ReviewChange)
            for activity in self.activities
        )

    def activity(self, activity_id: ActivityId) -> PlannedActivity:
        for activity in self.activities:
            if activity.activity_id == activity_id:
                return activity
        raise KeyError(f"activity plan has no activity {activity_id.value!r}")


def _require_typed_operation(operation: object) -> None:
    match operation:
        case StartNode(target=NodeTarget()):
            return
        case StopNode(target=NodeTarget()):
            return
        case RemoveNodeResource(target=NodeTarget()):
            return
        case WaitForHealthy(target=NodeTarget()):
            return
        case AddSocketConnection(target=SocketConnectionTarget()):
            return
        case SwitchSocketConnection(target=SocketConnectionTarget()):
            return
        case RemoveSocketConnection(target=SocketConnectionTarget()):
            return
        case ReconcileNode(target=NodeTarget()):
            return
        case ReconcileRuntime(target=RuntimeTarget()):
            return
        case StartRuntime(target=RuntimeTarget()):
            return
        case StopRuntime(target=RuntimeTarget()):
            return
        case RemoveRuntimeResource(target=RuntimeTarget()):
            return
        case DestroyDataResource(target=DataResourceTarget()):
            return
        case ReviewChange(target=ChangeTarget(), reason=ReviewReason()):
            return
        case _:
            raise TypeError("planned activity operation is not a closed activity variant")


def compensation_for_operation(operation: ActivityOperation) -> CompensationSpec:
    """Return the deterministic recovery meaning of one forward operation."""

    match operation:
        case StartNode(target=target):
            return Compensate(StopNode(target), CompensationMaterialSource.DESIRED_GRAPH)
        case StopNode(target=target):
            return Compensate(StartNode(target), CompensationMaterialSource.BASE_GRAPH)
        case StartRuntime(target=target):
            return Compensate(StopRuntime(target), CompensationMaterialSource.DESIRED_GRAPH)
        case StopRuntime(target=target):
            return Compensate(StartRuntime(target), CompensationMaterialSource.BASE_GRAPH)
        case AddSocketConnection(target=target):
            return Compensate(
                RemoveSocketConnection(target),
                CompensationMaterialSource.DESIRED_GRAPH,
            )
        case RemoveSocketConnection(target=target):
            return Compensate(
                AddSocketConnection(target),
                CompensationMaterialSource.BASE_GRAPH,
            )
        case SwitchSocketConnection(target=target):
            return Compensate(
                SwitchSocketConnection(target),
                CompensationMaterialSource.BASE_GRAPH,
            )
        case ReconcileNode(target=target):
            return Compensate(ReconcileNode(target), CompensationMaterialSource.BASE_GRAPH)
        case ReconcileRuntime(target=target):
            return Compensate(
                ReconcileRuntime(target),
                CompensationMaterialSource.BASE_GRAPH,
            )
        case WaitForHealthy() | ReviewChange():
            return NoCompensationRequired()
        case RemoveNodeResource() | RemoveRuntimeResource():
            return NonCompensatable(NonCompensatableReason.RESOURCE_REMOVAL)
        case DestroyDataResource():
            return NonCompensatable(NonCompensatableReason.DATA_DESTRUCTION)


def _validate_composition(
    activities: tuple[PlannedActivity, ...],
) -> tuple[PlanViolation, ...]:
    violations: list[PlanViolation] = []
    by_id: dict[ActivityId, PlannedActivity] = {}
    for activity in activities:
        if activity.activity_id in by_id:
            violations.append(
                PlanViolation(
                    PlanViolationCode.DUPLICATE_ACTIVITY_ID,
                    f"duplicate activity id {activity.activity_id.value!r}",
                    activity.activity_id,
                )
            )
        else:
            by_id[activity.activity_id] = activity
    known = set(by_id)
    for activity in activities:
        predecessors = tuple(
            dependency.predecessor for dependency in activity.dependencies
        )
        if len(predecessors) != len(set(predecessors)):
            violations.append(
                PlanViolation(
                    PlanViolationCode.DUPLICATE_DEPENDENCY,
                    f"activity {activity.activity_id.value!r} repeats a dependency edge",
                    activity.activity_id,
                )
            )
        for dependency in activity.dependencies:
            if dependency.predecessor == activity.activity_id:
                violations.append(
                    PlanViolation(
                        PlanViolationCode.SELF_DEPENDENCY,
                        f"activity {activity.activity_id.value!r} depends on itself",
                        activity.activity_id,
                    )
                )
            elif dependency.predecessor not in known:
                violations.append(
                    PlanViolation(
                        PlanViolationCode.MISSING_DEPENDENCY,
                        (
                            f"activity {activity.activity_id.value!r} depends on missing "
                            f"activity {dependency.predecessor.value!r}"
                        ),
                        activity.activity_id,
                    )
                )
        if (
            activity.impact is ActivityImpact.DESTRUCTIVE
            and _risk_rank(activity.risk) < _risk_rank(RiskLevel.HIGH)
        ):
            violations.append(
                PlanViolation(
                    PlanViolationCode.DESTRUCTIVE_RISK,
                    (
                        f"destructive activity {activity.activity_id.value!r} "
                        "must be high or critical risk"
                    ),
                    activity.activity_id,
                )
            )
        if isinstance(activity.operation, DestroyDataResource) and (
            activity.risk is not RiskLevel.CRITICAL
            or activity.impact is not ActivityImpact.DESTRUCTIVE
        ):
            violations.append(
                PlanViolation(
                    PlanViolationCode.DATA_DESTRUCTION_SAFETY,
                    (
                        f"data destruction activity {activity.activity_id.value!r} "
                        "must be critical risk and destructive"
                    ),
                    activity.activity_id,
                )
            )
        if (
            isinstance(activity.operation, ReviewChange)
            and _risk_rank(activity.risk) < _risk_rank(RiskLevel.HIGH)
        ):
            violations.append(
                PlanViolation(
                    PlanViolationCode.REVIEW_RISK,
                    (
                        f"review activity {activity.activity_id.value!r} "
                        "must be high or critical risk"
                    ),
                    activity.activity_id,
                )
            )
    if not any(
        violation.code
        in {
            PlanViolationCode.DUPLICATE_ACTIVITY_ID,
            PlanViolationCode.MISSING_DEPENDENCY,
            PlanViolationCode.SELF_DEPENDENCY,
        }
        for violation in violations
    ) and _has_cycle(by_id):
        violations.append(
            PlanViolation(
                PlanViolationCode.DEPENDENCY_CYCLE,
                "activity dependencies contain a cycle",
            )
        )
    return tuple(
        sorted(
            violations,
            key=lambda value: (
                value.code.value,
                value.activity_id.value if value.activity_id is not None else "",
                value.message,
            ),
        )
    )


def _has_cycle(by_id: dict[ActivityId, PlannedActivity]) -> bool:
    visiting: set[ActivityId] = set()
    visited: set[ActivityId] = set()

    def visit(activity_id: ActivityId) -> bool:
        if activity_id in visiting:
            return True
        if activity_id in visited:
            return False
        visiting.add(activity_id)
        for dependency in by_id[activity_id].dependencies:
            if visit(dependency.predecessor):
                return True
        visiting.remove(activity_id)
        visited.add(activity_id)
        return False

    return any(visit(activity_id) for activity_id in sorted(by_id))


def _topological_order(
    activities: tuple[PlannedActivity, ...],
) -> tuple[PlannedActivity, ...]:
    remaining = {activity.activity_id: activity for activity in activities}
    completed: set[ActivityId] = set()
    ordered: list[PlannedActivity] = []
    while remaining:
        ready = sorted(
            (
                activity
                for activity in remaining.values()
                if all(
                    dependency.predecessor in completed
                    for dependency in activity.dependencies
                )
            ),
            key=lambda activity: activity.activity_id.value,
        )
        if not ready:
            raise RuntimeError("validated activity plan unexpectedly has no ready activity")
        for activity in ready:
            ordered.append(activity)
            completed.add(activity.activity_id)
            del remaining[activity.activity_id]
    return tuple(ordered)


def _risk_rank(risk: RiskLevel) -> int:
    return {
        RiskLevel.INFORMATIONAL: 0,
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }[risk]
