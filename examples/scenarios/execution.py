"""Provider-neutral execution expectations over canonical planning scenarios.

This module extends the Roadmap 0007 scenario language with expected execution
evidence. It does not execute scenarios, persist records, or duplicate any
planning, scheduling, recovery, or coordination rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from control_plane_kit.execution import (
    ActivityEventKind,
    ActivityEventScope,
    ActivityRunStatus,
    FailureCategory,
    ObservationStatus,
    ProbeKind,
    ProbeOutcome,
    activity_event_scope,
)
from control_plane_kit.core.planning import WaitForHealthy
from control_plane_kit.workflows import CoordinatorStatus
from examples.scenarios.catalog import planning_scenarios
from examples.scenarios.model import OperationExpectation, PlanningScenario


class ApprovalExpectation(StrEnum):
    """Expected durable approval state before execution admission."""

    NOT_REQUESTED = "not-requested"
    REQUESTED = "requested"
    APPROVED = "approved"


class AdmissionExpectation(StrEnum):
    """Whether canonical execution admission is expected to succeed."""

    NOT_ADMITTED = "not-admitted"
    ADMITTED = "admitted"


class GraphAdvancementExpectation(StrEnum):
    """Expected current-graph projection behavior after execution."""

    UNCHANGED = "unchanged"
    ADVANCED_TO_DESIRED = "advanced-to-desired"


class ExternalReadinessRequirement(StrEnum):
    """Closed evidence that must originate outside the scenario runner."""

    DATABASE_ENDPOINT_CUTOVER = "database-endpoint-cutover"


class ReviewBlockReason(StrEnum):
    """Closed reasons planning evidence must not proceed to execution."""

    UNSUPPORTED_CHANGE = "unsupported-change"


@dataclass(frozen=True)
class ExecutableScenario:
    """The canonical scenario is safe for typed-effect interpretation."""


@dataclass(frozen=True)
class NoChanges:
    """Planning proved that desired and current topology are identical."""


@dataclass(frozen=True)
class ExternalReadinessGated:
    """Execution requires explicit provider/operator readiness evidence."""

    requirement: ExternalReadinessRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, ExternalReadinessRequirement):
            raise TypeError("external readiness requirement must be typed")


@dataclass(frozen=True)
class ReviewBlocked:
    """The planning result intentionally admits no execution workflow."""

    reason: ReviewBlockReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, ReviewBlockReason):
            raise TypeError("review block reason must be typed")


ExecutionEligibility: TypeAlias = (
    ExecutableScenario | NoChanges | ExternalReadinessGated | ReviewBlocked
)


@dataclass(frozen=True)
class NoRunExpected:
    """No ActivityRun may be created for this expectation."""


@dataclass(frozen=True)
class RunExpected:
    """Expected canonical run and bounded coordinator projection."""

    status: ActivityRunStatus
    coordinator_status: CoordinatorStatus

    def __post_init__(self) -> None:
        if not isinstance(self.status, ActivityRunStatus):
            raise TypeError("expected run status must be ActivityRunStatus")
        if not isinstance(self.coordinator_status, CoordinatorStatus):
            raise TypeError("expected coordinator status must be CoordinatorStatus")


RunProjectionExpectation: TypeAlias = NoRunExpected | RunExpected


@dataclass(frozen=True)
class EventExpectation:
    """One semantic event, optionally attached to a stable plan operation."""

    kind: ActivityEventKind
    operation: OperationExpectation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActivityEventKind):
            raise TypeError("expected event kind must be ActivityEventKind")
        if self.operation is not None and not isinstance(
            self.operation, OperationExpectation
        ):
            raise TypeError("expected event operation must be typed")
        scope = activity_event_scope(self.kind)
        if scope is ActivityEventScope.ACTIVITY and self.operation is None:
            raise ValueError("activity event expectation requires an operation")
        if scope is ActivityEventScope.RUN and self.operation is not None:
            raise ValueError("run event expectation cannot reference an operation")


@dataclass(frozen=True)
class EventOrderExpectation:
    """One required semantic precedence relation between durable events."""

    predecessor: EventExpectation
    successor: EventExpectation

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, EventExpectation) or not isinstance(
            self.successor, EventExpectation
        ):
            raise TypeError("event order endpoints must be EventExpectation")
        if self.predecessor == self.successor:
            raise ValueError("event order cannot require an event before itself")


@dataclass(frozen=True)
class ObservationExpectation:
    """Provider-neutral runtime evidence expected for one topology subject."""

    subject_id: str
    status: ObservationStatus
    probe_kind: ProbeKind
    probe_outcome: ProbeOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise ValueError("observation subject id must be non-empty text")
        if not isinstance(self.status, ObservationStatus):
            raise TypeError("expected observation status must be typed")
        if not isinstance(self.probe_kind, ProbeKind):
            raise TypeError("expected observation probe kind must be typed")
        if not isinstance(self.probe_outcome, ProbeOutcome):
            raise TypeError("expected observation probe outcome must be typed")


class FailurePhase(StrEnum):
    """Whether failure belongs to forward execution or compensation."""

    FORWARD = "forward"
    COMPENSATION = "compensation"


class UncertaintyResolution(StrEnum):
    """Independent evidence supplied for one uncertain effect."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ResolveScenarioUncertainty:
    """Resolve an uncertain semantic operation without generated activity ids."""

    operation: OperationExpectation
    resolution: UncertaintyResolution

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationExpectation):
            raise TypeError("uncertainty resolution operation must be typed")
        if not isinstance(self.resolution, UncertaintyResolution):
            raise TypeError("uncertainty resolution outcome must be typed")


@dataclass(frozen=True)
class PauseScenarioExecution:
    """Pause a running uncertain run before an operator recovery decision."""


@dataclass(frozen=True)
class ResumeScenarioExecution:
    """Resume the unchanged admitted intent after uncertainty is resolved."""


@dataclass(frozen=True)
class BeginScenarioCompensation:
    """Admit the plan-pinned compensation program after forward failure."""


ScenarioRecoveryStep: TypeAlias = (
    PauseScenarioExecution
    | ResolveScenarioUncertainty
    | ResumeScenarioExecution
    | BeginScenarioCompensation
)


@dataclass(frozen=True)
class ScenarioRecoveryProgram:
    """Pure semantic recovery choices interpreted by the scenario runner."""

    steps: tuple[ScenarioRecoveryStep, ...] = ()

    def __post_init__(self) -> None:
        variants = (
            PauseScenarioExecution,
            ResolveScenarioUncertainty,
            ResumeScenarioExecution,
            BeginScenarioCompensation,
        )
        if not all(isinstance(value, variants) for value in self.steps):
            raise TypeError("scenario recovery steps must be closed typed values")


@dataclass(frozen=True)
class FailureExpectation:
    """Expected failure classification attached to a stable operation."""

    operation: OperationExpectation
    category: FailureCategory
    phase: FailurePhase = FailurePhase.FORWARD

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationExpectation):
            raise TypeError("failure operation must be typed")
        if not isinstance(self.category, FailureCategory):
            raise TypeError("failure category must be FailureCategory")
        if not isinstance(self.phase, FailurePhase):
            raise TypeError("failure phase must be FailurePhase")


@dataclass(frozen=True)
class NoCompensationExpected:
    """No compensation phase should be admitted."""


@dataclass(frozen=True)
class CompensationExpected:
    """Expected compensation order expressed through stable operations."""

    reverse_completion_order: tuple[OperationExpectation, ...]

    def __post_init__(self) -> None:
        if not self.reverse_completion_order:
            raise ValueError("expected compensation order cannot be empty")
        if not all(
            isinstance(value, OperationExpectation)
            for value in self.reverse_completion_order
        ):
            raise TypeError("expected compensation operations must be typed")
        if len(set(self.reverse_completion_order)) != len(
            self.reverse_completion_order
        ):
            raise ValueError("expected compensation order cannot repeat operations")


CompensationExpectation: TypeAlias = (
    NoCompensationExpected | CompensationExpected
)


@dataclass(frozen=True)
class ExecutionScenarioExpectation:
    """Closed execution evidence expected around one planning scenario."""

    eligibility: ExecutionEligibility
    approval: ApprovalExpectation
    admission: AdmissionExpectation
    run: RunProjectionExpectation
    events: tuple[EventExpectation, ...] = ()
    event_order: tuple[EventOrderExpectation, ...] = ()
    observations: tuple[ObservationExpectation, ...] = ()
    failures: tuple[FailureExpectation, ...] = ()
    uncertain: tuple[OperationExpectation, ...] = ()
    compensation: CompensationExpectation = NoCompensationExpected()
    graph_advancement: GraphAdvancementExpectation = (
        GraphAdvancementExpectation.UNCHANGED
    )

    def __post_init__(self) -> None:
        if not isinstance(
            self.eligibility,
            (ExecutableScenario, NoChanges, ExternalReadinessGated, ReviewBlocked),
        ):
            raise TypeError("execution eligibility must be a closed typed value")
        if not isinstance(self.approval, ApprovalExpectation):
            raise TypeError("approval expectation must be typed")
        if not isinstance(self.admission, AdmissionExpectation):
            raise TypeError("admission expectation must be typed")
        if not isinstance(self.run, (NoRunExpected, RunExpected)):
            raise TypeError("run expectation must be a closed typed value")
        if not all(isinstance(value, EventExpectation) for value in self.events):
            raise TypeError("event expectations must be typed")
        if len(set(self.events)) != len(self.events):
            raise ValueError("event expectations cannot repeat semantic events")
        if not all(
            isinstance(value, EventOrderExpectation) for value in self.event_order
        ):
            raise TypeError("event order expectations must be typed")
        event_set = set(self.events)
        if any(
            order.predecessor not in event_set or order.successor not in event_set
            for order in self.event_order
        ):
            raise ValueError("event order must reference declared semantic events")
        if not all(
            isinstance(value, ObservationExpectation) for value in self.observations
        ):
            raise TypeError("observation expectations must be typed")
        if not all(isinstance(value, FailureExpectation) for value in self.failures):
            raise TypeError("failure expectations must be typed")
        if not all(isinstance(value, OperationExpectation) for value in self.uncertain):
            raise TypeError("uncertain operation expectations must be typed")
        if not isinstance(
            self.compensation,
            (NoCompensationExpected, CompensationExpected),
        ):
            raise TypeError("compensation expectation must be a closed typed value")
        if not isinstance(self.graph_advancement, GraphAdvancementExpectation):
            raise TypeError("graph advancement expectation must be typed")
        if isinstance(self.run, NoRunExpected):
            if (
                self.events
                or self.event_order
                or self.observations
                or self.failures
                or self.uncertain
                or isinstance(self.compensation, CompensationExpected)
            ):
                raise ValueError("a no-run expectation cannot contain runtime evidence")
            if self.graph_advancement is not GraphAdvancementExpectation.UNCHANGED:
                raise ValueError("a no-run expectation cannot advance graph truth")
        if self.graph_advancement is GraphAdvancementExpectation.ADVANCED_TO_DESIRED:
            if not isinstance(self.run, RunExpected) or (
                self.run.status is not ActivityRunStatus.SUCCEEDED
            ):
                raise ValueError("graph advancement requires a succeeded run")
        if self.admission is AdmissionExpectation.ADMITTED and (
            self.approval is not ApprovalExpectation.APPROVED
        ):
            raise ValueError("execution admission requires approved expectation")


@dataclass(frozen=True)
class ExecutionScenario:
    """One unchanged planning scenario paired with execution expectations."""

    planning: PlanningScenario
    expectation: ExecutionScenarioExpectation

    def __post_init__(self) -> None:
        if not isinstance(self.planning, PlanningScenario):
            raise TypeError("execution scenario must wrap PlanningScenario")
        if not isinstance(self.expectation, ExecutionScenarioExpectation):
            raise TypeError("execution scenario expectation must be typed")
        operations = set(self.planning.expectation.operations)
        referenced = {
            event.operation
            for event in self.expectation.events
            if event.operation is not None
        }
        referenced.update(failure.operation for failure in self.expectation.failures)
        referenced.update(self.expectation.uncertain)
        if isinstance(self.expectation.compensation, CompensationExpected):
            referenced.update(self.expectation.compensation.reverse_completion_order)
        if not referenced.issubset(operations):
            raise ValueError(
                "execution expectations must reference canonical planning operations"
            )
        if isinstance(self.expectation.eligibility, ReviewBlocked):
            if self.planning.expectation.ready_for_execution:
                raise ValueError("review-blocked execution requires blocked planning")
            if self.expectation.approval is not ApprovalExpectation.NOT_REQUESTED:
                raise ValueError("review-blocked execution cannot expect approval")
        elif not self.planning.expectation.ready_for_execution:
            raise ValueError("execution eligibility requires ready planning truth")
        if isinstance(self.expectation.eligibility, NoChanges):
            if self.planning.expectation.operations:
                raise ValueError("no-change execution cannot contain operations")
            if self.expectation.approval is not ApprovalExpectation.NOT_REQUESTED:
                raise ValueError("no-change execution cannot expect approval")
            if self.expectation.admission is not AdmissionExpectation.NOT_ADMITTED:
                raise ValueError("no-change execution cannot expect admission")
        if isinstance(self.expectation.eligibility, ExternalReadinessGated) and (
            self.expectation.admission is not AdmissionExpectation.NOT_ADMITTED
        ):
            raise ValueError("external readiness gate cannot expect admission")

    @property
    def scenario_id(self) -> str:
        return self.planning.scenario_id


def execution_scenarios() -> tuple[ExecutionScenario, ...]:
    """Pair every canonical planning scenario with one execution contract."""

    expectations = {
        "no-change": _no_change_expectation(),
        "switch-database-endpoint": _database_readiness_expectation(),
        "unsupported-implementation-transition": _review_blocked_expectation(),
    }
    scenarios = planning_scenarios()
    return tuple(
        ExecutionScenario(
            scenario,
            expectations.get(scenario.scenario_id, _successful_expectation(scenario)),
        )
        for scenario in scenarios
    )


def _successful_expectation(
    scenario: PlanningScenario,
) -> ExecutionScenarioExpectation:
    opened = EventExpectation(ActivityEventKind.RUN_OPENED)
    started = EventExpectation(ActivityEventKind.RUN_STARTED)
    succeeded = EventExpectation(ActivityEventKind.RUN_SUCCEEDED)
    advanced = EventExpectation(ActivityEventKind.CURRENT_GRAPH_ADVANCED)
    step_events = tuple(
        event
        for operation in scenario.expectation.operations
        for event in (
            EventExpectation(ActivityEventKind.STEP_STARTED, operation),
            EventExpectation(ActivityEventKind.STEP_SUCCEEDED, operation),
        )
    )
    events = (opened, started, *step_events, succeeded, advanced)
    event_order = [
        EventOrderExpectation(opened, started),
        EventOrderExpectation(succeeded, advanced),
    ]
    for operation in scenario.expectation.operations:
        step_started = EventExpectation(ActivityEventKind.STEP_STARTED, operation)
        step_succeeded = EventExpectation(ActivityEventKind.STEP_SUCCEEDED, operation)
        event_order.extend(
            (
                EventOrderExpectation(started, step_started),
                EventOrderExpectation(step_started, step_succeeded),
                EventOrderExpectation(step_succeeded, succeeded),
            )
        )
    for dependency in scenario.expectation.required_dependencies:
        event_order.append(
            EventOrderExpectation(
                EventExpectation(
                    ActivityEventKind.STEP_SUCCEEDED,
                    dependency.predecessor,
                ),
                EventExpectation(
                    ActivityEventKind.STEP_STARTED,
                    dependency.successor,
                ),
            )
        )
    observations = tuple(
        ObservationExpectation(
            operation.target_id,
            ObservationStatus.HEALTHY,
            ProbeKind.APPLICATION_HEALTH,
            ProbeOutcome.HEALTHY,
        )
        for operation in scenario.expectation.operations
        if operation.operation_type is WaitForHealthy
    )
    return ExecutionScenarioExpectation(
        eligibility=ExecutableScenario(),
        approval=ApprovalExpectation.APPROVED,
        admission=AdmissionExpectation.ADMITTED,
        run=RunExpected(ActivityRunStatus.SUCCEEDED, CoordinatorStatus.COMPLETED),
        events=events,
        event_order=tuple(event_order),
        observations=observations,
        graph_advancement=GraphAdvancementExpectation.ADVANCED_TO_DESIRED,
    )


def _database_readiness_expectation() -> ExecutionScenarioExpectation:
    return ExecutionScenarioExpectation(
        eligibility=ExternalReadinessGated(
            ExternalReadinessRequirement.DATABASE_ENDPOINT_CUTOVER
        ),
        approval=ApprovalExpectation.APPROVED,
        admission=AdmissionExpectation.NOT_ADMITTED,
        run=NoRunExpected(),
    )


def _no_change_expectation() -> ExecutionScenarioExpectation:
    return ExecutionScenarioExpectation(
        eligibility=NoChanges(),
        approval=ApprovalExpectation.NOT_REQUESTED,
        admission=AdmissionExpectation.NOT_ADMITTED,
        run=NoRunExpected(),
    )


def _review_blocked_expectation() -> ExecutionScenarioExpectation:
    return ExecutionScenarioExpectation(
        eligibility=ReviewBlocked(ReviewBlockReason.UNSUPPORTED_CHANGE),
        approval=ApprovalExpectation.NOT_REQUESTED,
        admission=AdmissionExpectation.NOT_ADMITTED,
        run=NoRunExpected(),
    )
