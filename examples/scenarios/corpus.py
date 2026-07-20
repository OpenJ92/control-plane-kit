"""Typed acceptance cases over the canonical execution scenario catalog."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.execution import (
    ActivityEventKind,
    ActivityRunStatus,
    FailureCategory,
)
from control_plane_kit.core.planning import (
    StartNode,
    StartRuntime,
    StopNode,
    WaitForHealthy,
)
from control_plane_kit.workflows import CoordinatorStatus
from examples.scenarios.execution import (
    AdmissionExpectation,
    ApprovalExpectation,
    BeginScenarioCompensation,
    CompensationExpected,
    CompensationExpectation,
    EventExpectation,
    EventOrderExpectation,
    ExecutableScenario,
    ExecutionScenario,
    ExecutionScenarioExpectation,
    FailureExpectation,
    FailurePhase,
    GraphAdvancementExpectation,
    NoCompensationExpected,
    PauseScenarioExecution,
    ResolveScenarioUncertainty,
    ResumeScenarioExecution,
    RunExpected,
    ScenarioRecoveryProgram,
    UncertaintyResolution,
    execution_scenarios,
)
from examples.scenarios.model import OperationExpectation
from examples.scenarios.runner import (
    ScenarioEffectDirective,
    ScenarioEffectDisposition,
    ScenarioEffectProgram,
)


@dataclass(frozen=True)
class ExecutionScenarioCase:
    """One semantic expectation and its deterministic provider/recovery inputs."""

    case_id: str
    scenario: ExecutionScenario
    effects: ScenarioEffectProgram = ScenarioEffectProgram()
    recovery: ScenarioRecoveryProgram = ScenarioRecoveryProgram()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("execution scenario case id must be non-empty text")
        if not isinstance(self.scenario, ExecutionScenario):
            raise TypeError("execution scenario case requires ExecutionScenario")
        if not isinstance(self.effects, ScenarioEffectProgram):
            raise TypeError("execution scenario effects must be typed")
        if not isinstance(self.recovery, ScenarioRecoveryProgram):
            raise TypeError("execution scenario recovery must be typed")


def execution_scenario_cases() -> tuple[ExecutionScenarioCase, ...]:
    """Return canonical cases plus failure, uncertainty, and recovery variants."""

    canonical = execution_scenarios()
    by_id = {scenario.scenario_id: scenario for scenario in canonical}
    scale_out = by_id["scale-out-load-balancer"]
    backend = by_id["backend-switch"]
    fresh = by_id["fresh-deployment"]

    independent = _operation(scale_out, StartNode, "app-b")
    shared = _operation(scale_out, StartNode, "balancer")
    backend_switch = backend.planning.expectation.operations[0]
    start_runtime = _operation(fresh, StartRuntime, "docker")
    start_api = _operation(fresh, StartNode, "api")
    wait_api = _operation(fresh, WaitForHealthy, "api")

    variants = (
        _forward_failure_case("independent-leaf-failure", scale_out, independent),
        _forward_failure_case("shared-leaf-failure", scale_out, shared),
        _uncertain_case("uncertain-paused", backend, backend_switch),
        _resolved_uncertainty_case(
            "uncertainty-resolved-and-resumed",
            backend,
            backend_switch,
        ),
        _compensation_case(
            "reverse-order-compensation",
            fresh,
            wait_api,
            (start_api, start_runtime),
        ),
        _compensation_failure_case(
            "compensation-failure",
            fresh,
            wait_api,
            start_api,
        ),
    )
    cases = tuple(
        ExecutionScenarioCase(f"canonical:{scenario.scenario_id}", scenario)
        for scenario in canonical
    ) + variants
    if len({case.case_id for case in cases}) != len(cases):
        raise AssertionError("execution scenario case ids must be unique")
    return cases


def _forward_failure_case(
    case_id: str,
    scenario: ExecutionScenario,
    failed: OperationExpectation,
) -> ExecutionScenarioCase:
    return ExecutionScenarioCase(
        case_id,
        _with_expectation(scenario, _failure_expectation(failed)),
        ScenarioEffectProgram(
            (
                ScenarioEffectDirective(
                    failed,
                    ScenarioEffectDisposition.FAIL,
                ),
            )
        ),
    )


def _uncertain_case(
    case_id: str,
    scenario: ExecutionScenario,
    uncertain: OperationExpectation,
) -> ExecutionScenarioCase:
    opened = EventExpectation(ActivityEventKind.RUN_OPENED)
    started = EventExpectation(ActivityEventKind.RUN_STARTED)
    step_started = EventExpectation(ActivityEventKind.STEP_STARTED, uncertain)
    step_uncertain = EventExpectation(ActivityEventKind.STEP_UNCERTAIN, uncertain)
    expectation = _runtime_expectation(
        ActivityRunStatus.RUNNING,
        CoordinatorStatus.UNCERTAIN,
        events=(opened, started, step_started, step_uncertain),
        event_order=(
            EventOrderExpectation(opened, started),
            EventOrderExpectation(started, step_started),
            EventOrderExpectation(step_started, step_uncertain),
        ),
        uncertain=(uncertain,),
    )
    return ExecutionScenarioCase(
        case_id,
        _with_expectation(scenario, expectation),
        ScenarioEffectProgram(
            (
                ScenarioEffectDirective(
                    uncertain,
                    ScenarioEffectDisposition.UNCERTAIN,
                ),
            )
        ),
    )


def _resolved_uncertainty_case(
    case_id: str,
    scenario: ExecutionScenario,
    uncertain: OperationExpectation,
) -> ExecutionScenarioCase:
    opened = EventExpectation(ActivityEventKind.RUN_OPENED)
    started = EventExpectation(ActivityEventKind.RUN_STARTED)
    step_started = EventExpectation(ActivityEventKind.STEP_STARTED, uncertain)
    step_uncertain = EventExpectation(ActivityEventKind.STEP_UNCERTAIN, uncertain)
    paused = EventExpectation(ActivityEventKind.RUN_PAUSED)
    resolved = EventExpectation(
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
        uncertain,
    )
    resumed = EventExpectation(ActivityEventKind.RUN_RESUMED)
    succeeded = EventExpectation(ActivityEventKind.RUN_SUCCEEDED)
    advanced = EventExpectation(ActivityEventKind.CURRENT_GRAPH_ADVANCED)
    events = (
        opened,
        started,
        step_started,
        step_uncertain,
        paused,
        resolved,
        resumed,
        succeeded,
        advanced,
    )
    expectation = _runtime_expectation(
        ActivityRunStatus.SUCCEEDED,
        CoordinatorStatus.COMPLETED,
        events=events,
        event_order=tuple(
            EventOrderExpectation(before, after)
            for before, after in zip(events, events[1:])
        ),
        graph_advancement=GraphAdvancementExpectation.ADVANCED_TO_DESIRED,
    )
    return ExecutionScenarioCase(
        case_id,
        _with_expectation(scenario, expectation),
        ScenarioEffectProgram(
            (
                ScenarioEffectDirective(
                    uncertain,
                    ScenarioEffectDisposition.UNCERTAIN,
                ),
            )
        ),
        ScenarioRecoveryProgram(
            (
                PauseScenarioExecution(),
                ResolveScenarioUncertainty(
                    uncertain,
                    UncertaintyResolution.SUCCEEDED,
                ),
                ResumeScenarioExecution(),
            )
        ),
    )


def _compensation_case(
    case_id: str,
    scenario: ExecutionScenario,
    failed: OperationExpectation,
    compensated: tuple[OperationExpectation, ...],
) -> ExecutionScenarioCase:
    events = _compensation_events(failed, compensated, succeeded=True)
    expectation = _runtime_expectation(
        ActivityRunStatus.COMPENSATED,
        CoordinatorStatus.COMPENSATED,
        events=events,
        event_order=tuple(
            EventOrderExpectation(before, after)
            for before, after in zip(events, events[1:])
        ),
        failures=(FailureExpectation(failed, FailureCategory.RETRYABLE),),
        compensation=CompensationExpected(compensated),
    )
    return ExecutionScenarioCase(
        case_id,
        _with_expectation(scenario, expectation),
        ScenarioEffectProgram(
            (ScenarioEffectDirective(failed, ScenarioEffectDisposition.FAIL),)
        ),
        ScenarioRecoveryProgram((BeginScenarioCompensation(),)),
    )


def _compensation_failure_case(
    case_id: str,
    scenario: ExecutionScenario,
    failed: OperationExpectation,
    compensation_failed: OperationExpectation,
) -> ExecutionScenarioCase:
    events = _compensation_events(
        failed,
        (compensation_failed,),
        succeeded=False,
    )
    inverse = OperationExpectation(StopNode, compensation_failed.target_id)
    expectation = _runtime_expectation(
        ActivityRunStatus.PARTIALLY_FAILED,
        CoordinatorStatus.COMPENSATION_FAILED,
        events=events,
        event_order=tuple(
            EventOrderExpectation(before, after)
            for before, after in zip(events, events[1:])
        ),
        failures=(
            FailureExpectation(failed, FailureCategory.RETRYABLE),
            FailureExpectation(
                compensation_failed,
                FailureCategory.RETRYABLE,
                FailurePhase.COMPENSATION,
            ),
        ),
        compensation=CompensationExpected((compensation_failed,)),
    )
    return ExecutionScenarioCase(
        case_id,
        _with_expectation(scenario, expectation),
        ScenarioEffectProgram(
            (
                ScenarioEffectDirective(failed, ScenarioEffectDisposition.FAIL),
                ScenarioEffectDirective(
                    inverse,
                    ScenarioEffectDisposition.FAIL,
                    FailurePhase.COMPENSATION,
                ),
            )
        ),
        ScenarioRecoveryProgram((BeginScenarioCompensation(),)),
    )


def _failure_expectation(failed: OperationExpectation) -> ExecutionScenarioExpectation:
    opened = EventExpectation(ActivityEventKind.RUN_OPENED)
    started = EventExpectation(ActivityEventKind.RUN_STARTED)
    step_started = EventExpectation(ActivityEventKind.STEP_STARTED, failed)
    step_failed = EventExpectation(ActivityEventKind.STEP_FAILED, failed)
    run_failed = EventExpectation(ActivityEventKind.RUN_FAILED)
    events = (opened, started, step_started, step_failed, run_failed)
    return _runtime_expectation(
        ActivityRunStatus.FAILED,
        CoordinatorStatus.FAILED,
        events=events,
        event_order=tuple(
            EventOrderExpectation(before, after)
            for before, after in zip(events, events[1:])
        ),
        failures=(FailureExpectation(failed, FailureCategory.RETRYABLE),),
    )


def _compensation_events(
    failed: OperationExpectation,
    compensated: tuple[OperationExpectation, ...],
    *,
    succeeded: bool,
) -> tuple[EventExpectation, ...]:
    events = [
        EventExpectation(ActivityEventKind.RUN_OPENED),
        EventExpectation(ActivityEventKind.RUN_STARTED),
        EventExpectation(ActivityEventKind.STEP_STARTED, failed),
        EventExpectation(ActivityEventKind.STEP_FAILED, failed),
        EventExpectation(ActivityEventKind.RUN_FAILED),
        EventExpectation(ActivityEventKind.RUN_COMPENSATION_STARTED),
    ]
    for ordinal, operation in enumerate(compensated):
        events.append(
            EventExpectation(ActivityEventKind.STEP_COMPENSATION_STARTED, operation)
        )
        terminal = (
            ActivityEventKind.STEP_COMPENSATION_SUCCEEDED
            if succeeded or ordinal < len(compensated) - 1
            else ActivityEventKind.STEP_COMPENSATION_FAILED
        )
        events.append(EventExpectation(terminal, operation))
    events.append(
        EventExpectation(
            ActivityEventKind.RUN_COMPENSATION_SUCCEEDED
            if succeeded
            else ActivityEventKind.RUN_COMPENSATION_FAILED
        )
    )
    return tuple(events)


def _runtime_expectation(
    status: ActivityRunStatus,
    coordinator_status: CoordinatorStatus,
    *,
    events: tuple[EventExpectation, ...],
    event_order: tuple[EventOrderExpectation, ...],
    failures: tuple[FailureExpectation, ...] = (),
    uncertain: tuple[OperationExpectation, ...] = (),
    compensation: CompensationExpectation = NoCompensationExpected(),
    graph_advancement: GraphAdvancementExpectation = GraphAdvancementExpectation.UNCHANGED,
) -> ExecutionScenarioExpectation:
    return ExecutionScenarioExpectation(
        eligibility=ExecutableScenario(),
        approval=ApprovalExpectation.APPROVED,
        admission=AdmissionExpectation.ADMITTED,
        run=RunExpected(status, coordinator_status),
        events=events,
        event_order=event_order,
        failures=failures,
        uncertain=uncertain,
        compensation=compensation,
        graph_advancement=graph_advancement,
    )


def _with_expectation(
    scenario: ExecutionScenario,
    expectation: ExecutionScenarioExpectation,
) -> ExecutionScenario:
    return ExecutionScenario(scenario.planning, expectation)


def _operation(
    scenario: ExecutionScenario,
    operation_type: type[object],
    target_id: str,
) -> OperationExpectation:
    return next(
        operation
        for operation in scenario.planning.expectation.operations
        if operation.operation_type is operation_type and operation.target_id == target_id
    )
