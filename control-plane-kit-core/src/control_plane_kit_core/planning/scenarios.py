"""Pure graph-transition scenario catalogue and execution expectations.

The scenario catalogue is acceptance data over the extracted core topology and
planning languages. It does not execute effects, persist workflow records, or
define a user-authored workflow language.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeAlias

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
    RuntimeContext,
    SocketConnection,
)
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.planning.activity_plan import (
    ActivityOperation,
    AddSocketConnection,
    ChangeTarget,
    NodeTarget,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
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
from control_plane_kit_core.probe_intents import ProbeKind, ProbeOutcome
from control_plane_kit_core.topology import (
    DeploymentGraph,
    EdgeSubject,
    FieldSubject,
    GraphSubject,
    LiteralAddress,
    NodeSubject,
    RuntimeSubject,
    compile_topology,
)
from control_plane_kit_core.topology.graph import Endpoint
from control_plane_kit_core.types import Protocol, RuntimeKind, SocketBinding


@dataclass(frozen=True)
class _MaterializedBlock:
    kind: str
    endpoints: dict[str, Endpoint]
    lifecycle: object = OWNED_EPHEMERAL
    metadata: dict[str, object] | None = None
    public_environment: tuple[object, ...] = ()
    configuration_artifacts: tuple[object, ...] = ()
    secret_deliveries: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class _ScenarioImplementation:
    kind: str
    endpoints: dict[str, str]

    def materialize(
        self,
        block_id: str,
        sockets: BlockSockets,
        runtime: RuntimeContext,
    ) -> _MaterializedBlock:
        return _MaterializedBlock(
            kind=self.kind,
            endpoints={
                name: Endpoint(
                    LiteralAddress(address),
                    sockets.provider(name).protocol,
                )
                for name, address in self.endpoints.items()
            },
        )


@dataclass(frozen=True)
class OperationExpectation:
    """One typed operation expected to target one topology identity."""

    operation_type: type[object]
    target_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation_type, type):
            raise TypeError("operation expectation type must be a type")
        if not isinstance(self.target_id, str) or not self.target_id.strip():
            raise ValueError("operation expectation target must be non-empty text")


@dataclass(frozen=True)
class DependencyExpectation:
    """A required ordering edge between two expected operations."""

    predecessor: OperationExpectation
    successor: OperationExpectation

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, OperationExpectation):
            raise TypeError("dependency predecessor must be OperationExpectation")
        if not isinstance(self.successor, OperationExpectation):
            raise TypeError("dependency successor must be OperationExpectation")
        if self.predecessor == self.successor:
            raise ValueError("scenario dependency cannot reference itself")


@dataclass(frozen=True)
class ScenarioExpectation:
    """Stable planning semantics shared by future interpreters."""

    operations: tuple[OperationExpectation, ...]
    required_dependencies: tuple[DependencyExpectation, ...] = ()
    max_risk: RiskLevel = RiskLevel.LOW
    ready_for_execution: bool = True

    def __post_init__(self) -> None:
        if not all(isinstance(value, OperationExpectation) for value in self.operations):
            raise TypeError("scenario operations must be OperationExpectation values")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("scenario operations must be unique")
        if not all(
            isinstance(value, DependencyExpectation)
            for value in self.required_dependencies
        ):
            raise TypeError("scenario dependencies must be DependencyExpectation values")
        operation_set = set(self.operations)
        for dependency in self.required_dependencies:
            if (
                dependency.predecessor not in operation_set
                or dependency.successor not in operation_set
            ):
                raise ValueError("scenario dependency must reference declared operations")
        if not isinstance(self.max_risk, RiskLevel):
            raise TypeError("scenario max risk must be RiskLevel")
        if type(self.ready_for_execution) is not bool:
            raise TypeError("scenario readiness must be bool")


@dataclass(frozen=True)
class PlanningScenario:
    """A named desired-state transition and semantic planning contract."""

    scenario_id: str
    title: str
    approval_comment: str
    current_graph: DeploymentGraph
    desired_graph: DeploymentGraph
    expectation: ScenarioExpectation

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise ValueError("scenario id must be non-empty text")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("scenario title must be non-empty text")
        if not isinstance(self.approval_comment, str) or not self.approval_comment.strip():
            raise ValueError("approval comment must be non-empty text")
        if not isinstance(self.current_graph, DeploymentGraph):
            raise TypeError("scenario current graph must be DeploymentGraph")
        if not isinstance(self.desired_graph, DeploymentGraph):
            raise TypeError("scenario desired graph must be DeploymentGraph")
        if not isinstance(self.expectation, ScenarioExpectation):
            raise TypeError("scenario expectation must be ScenarioExpectation")


def operation_expectation(operation: ActivityOperation) -> OperationExpectation:
    """Project a typed activity operation to stable scenario identity."""

    match operation:
        case (
            StartNode(target=NodeTarget(node_id=target_id))
            | StopNode(target=NodeTarget(node_id=target_id))
            | RemoveNodeResource(target=NodeTarget(node_id=target_id))
            | WaitForHealthy(target=NodeTarget(node_id=target_id))
            | ReconcileNode(target=NodeTarget(node_id=target_id))
        ):
            return OperationExpectation(type(operation), target_id)
        case (
            StartRuntime(target=RuntimeTarget(runtime_id=target_id))
            | StopRuntime(target=RuntimeTarget(runtime_id=target_id))
            | RemoveRuntimeResource(target=RuntimeTarget(runtime_id=target_id))
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


def planning_scenarios() -> tuple[PlanningScenario, ...]:
    """Return the canonical extracted-core planning scenario corpus."""

    return (
        fresh_deployment(),
        backend_switch(),
        scale_out_behind_load_balancer(),
        insert_rate_limiter(),
        add_request_observer(),
        move_service_between_runtimes(),
        switch_database_endpoint(),
        partial_scale_in(),
        full_teardown(),
        no_change(),
        unsupported_implementation_transition(),
    )


def fresh_deployment() -> PlanningScenario:
    desired = _docker_graph(
        "fresh-deployment",
        _http_app("api", provider="public"),
    )
    start_runtime = _op(StartRuntime, "docker")
    start_api = _op(StartNode, "api")
    healthy_api = _op(WaitForHealthy, "api")
    return _scenario(
        "fresh-deployment",
        "Start a fresh application",
        DeploymentGraph("empty"),
        desired,
        operations=(start_runtime, start_api, healthy_api),
        dependencies=(
            _dependency(start_runtime, start_api),
            _dependency(start_api, healthy_api),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def backend_switch() -> PlanningScenario:
    return _scenario(
        "backend-switch",
        "Switch an active backend",
        _router_graph("api-v1"),
        _router_graph("api-v2"),
        operations=(_op(SwitchSocketConnection, "api-router.active"),),
        max_risk=RiskLevel.HIGH,
    )


def scale_out_behind_load_balancer() -> PlanningScenario:
    desired = _balanced_graph()
    current = _retain(desired, "scale-out-current", nodes=("app-a",))
    start_app = _op(StartNode, "app-b")
    healthy_app = _op(WaitForHealthy, "app-b")
    start_balancer = _op(StartNode, "balancer")
    healthy_balancer = _op(WaitForHealthy, "balancer")
    add_existing = _op(AddSocketConnection, "app-a.internal-to-balancer.app-a")
    add_new = _op(AddSocketConnection, "app-b.internal-to-balancer.app-b")
    return _scenario(
        "scale-out-load-balancer",
        "Scale an application behind a load balancer",
        current,
        desired,
        operations=(
            start_app,
            healthy_app,
            start_balancer,
            healthy_balancer,
            add_existing,
            add_new,
            _op(ReconcileRuntime, "docker"),
        ),
        dependencies=(
            _dependency(start_app, healthy_app),
            _dependency(start_balancer, healthy_balancer),
            _dependency(healthy_balancer, add_existing),
            _dependency(healthy_balancer, add_new),
            _dependency(healthy_app, add_new),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def insert_rate_limiter() -> PlanningScenario:
    desired = _rate_limiter_graph()
    current = _retain(desired, "rate-limiter-current", nodes=("app",))
    start = _op(StartNode, "limiter")
    healthy = _op(WaitForHealthy, "limiter")
    return _scenario(
        "insert-rate-limiter",
        "Insert a rate limiter",
        current,
        desired,
        operations=(start, healthy, _op(ReconcileRuntime, "docker")),
        dependencies=(_dependency(start, healthy),),
        max_risk=RiskLevel.MEDIUM,
    )


def add_request_observer() -> PlanningScenario:
    desired = _multiplexer_graph()
    current = _retain(desired, "observer-current", nodes=("primary",))
    start_observer = _op(StartNode, "observer")
    healthy_observer = _op(WaitForHealthy, "observer")
    start_mux = _op(StartNode, "multiplexer")
    healthy_mux = _op(WaitForHealthy, "multiplexer")
    return _scenario(
        "add-request-observer",
        "Add an HTTP request observer",
        current,
        desired,
        operations=(
            start_observer,
            healthy_observer,
            start_mux,
            healthy_mux,
            _op(ReconcileRuntime, "docker"),
        ),
        dependencies=(
            _dependency(start_observer, healthy_observer),
            _dependency(start_mux, healthy_mux),
            _dependency(healthy_observer, start_mux),
        ),
        max_risk=RiskLevel.MEDIUM,
    )


def move_service_between_runtimes() -> PlanningScenario:
    current = _runtime_move_graph("runtime-move-current", source="runtime-a")
    desired = _runtime_move_graph("runtime-move-desired", source="runtime-b")
    reconcile = _op(ReconcileNode, "worker")
    healthy = _op(WaitForHealthy, "worker")
    return _scenario(
        "move-service-runtime",
        "Move a service between runtimes",
        current,
        desired,
        operations=(
            reconcile,
            healthy,
            _op(ReconcileRuntime, "runtime-a"),
            _op(ReconcileRuntime, "runtime-b"),
        ),
        dependencies=(_dependency(reconcile, healthy),),
        max_risk=RiskLevel.MEDIUM,
    )


def switch_database_endpoint() -> PlanningScenario:
    current = _database_graph("database-current", active_database_id="postgres-a")
    desired = _database_graph("database-desired", active_database_id="postgres-b")
    reconcile = _op(ReconcileNode, "api")
    healthy = _op(WaitForHealthy, "api")
    return _scenario(
        "switch-database-endpoint",
        "Switch between pre-provisioned database endpoints",
        current,
        desired,
        operations=(reconcile, healthy),
        dependencies=(_dependency(reconcile, healthy),),
        max_risk=RiskLevel.MEDIUM,
    )


def partial_scale_in() -> PlanningScenario:
    current = _router_graph("api-v2")
    desired = _retain(
        current,
        "partial-scale-in",
        nodes=("api-v2", "api-router"),
        edges=("api-router.active",),
    )
    stop = _op(StopNode, "api-v1")
    remove = _op(RemoveNodeResource, "api-v1")
    return _scenario(
        "partial-scale-in",
        "Remove an inactive backend",
        current,
        desired,
        operations=(stop, remove, _op(ReconcileRuntime, "docker")),
        dependencies=(_dependency(stop, remove),),
        max_risk=RiskLevel.HIGH,
    )


def full_teardown() -> PlanningScenario:
    current = _rate_limiter_graph()
    stop_app = _op(StopNode, "app")
    stop_limiter = _op(StopNode, "limiter")
    remove_app = _op(RemoveNodeResource, "app")
    remove_limiter = _op(RemoveNodeResource, "limiter")
    stop_runtime = _op(StopRuntime, "docker")
    remove_runtime = _op(RemoveRuntimeResource, "docker")
    return _scenario(
        "full-teardown",
        "Tear down a deployment",
        current,
        DeploymentGraph("empty"),
        operations=(
            stop_app,
            stop_limiter,
            remove_app,
            remove_limiter,
            stop_runtime,
            remove_runtime,
        ),
        dependencies=(
            _dependency(stop_app, remove_app),
            _dependency(stop_limiter, remove_limiter),
            _dependency(remove_app, stop_runtime),
            _dependency(remove_limiter, stop_runtime),
            _dependency(stop_runtime, remove_runtime),
        ),
        max_risk=RiskLevel.HIGH,
    )


def no_change() -> PlanningScenario:
    graph = _router_graph("api-v1")
    return _scenario(
        "no-change",
        "Keep an unchanged deployment",
        graph,
        graph,
        operations=(),
        max_risk=RiskLevel.INFORMATIONAL,
    )


def unsupported_implementation_transition() -> PlanningScenario:
    current = _implementation_graph("implementation-current", kind="application")
    desired = _implementation_graph("implementation-desired", kind="plan-only")
    return _scenario(
        "unsupported-implementation-transition",
        "Review an implementation-kind transition",
        current,
        desired,
        operations=(_op(ReviewChange, "api"),),
        max_risk=RiskLevel.HIGH,
        ready_for_execution=False,
    )


class ScenarioActivityEventKind(StrEnum):
    """Closed semantic event names used by scenario expectations."""

    RUN_OPENED = "run_opened"
    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    RUN_SUCCEEDED = "run_succeeded"
    CURRENT_GRAPH_ADVANCED = "current_graph_advanced"


class ScenarioEventScope(StrEnum):
    RUN = "run"
    ACTIVITY = "activity"


class ScenarioRunStatus(StrEnum):
    PAUSED = "paused"
    SUCCEEDED = "succeeded"


class ScenarioCoordinatorStatus(StrEnum):
    COMPLETED = "completed"
    PAUSED = "paused"


class ScenarioObservationStatus(StrEnum):
    HEALTHY = "healthy"


class ApprovalExpectation(StrEnum):
    NOT_REQUESTED = "not-requested"
    REQUESTED = "requested"
    APPROVED = "approved"


class AdmissionExpectation(StrEnum):
    NOT_ADMITTED = "not-admitted"
    ADMITTED = "admitted"


class GraphAdvancementExpectation(StrEnum):
    UNCHANGED = "unchanged"
    ADVANCED_TO_DESIRED = "advanced-to-desired"


class ExternalReadinessRequirement(StrEnum):
    DATABASE_ENDPOINT_CUTOVER = "database-endpoint-cutover"


class ReviewBlockReason(StrEnum):
    UNSUPPORTED_CHANGE = "unsupported-change"


@dataclass(frozen=True)
class ExecutableScenario:
    """The canonical scenario is safe for typed-effect interpretation."""


@dataclass(frozen=True)
class NoChanges:
    """Planning proved that desired and current topology are identical."""


@dataclass(frozen=True)
class ExternalReadinessGated:
    requirement: ExternalReadinessRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, ExternalReadinessRequirement):
            raise TypeError("external readiness requirement must be typed")


@dataclass(frozen=True)
class ReviewBlocked:
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
    status: ScenarioRunStatus
    coordinator_status: ScenarioCoordinatorStatus

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScenarioRunStatus):
            raise TypeError("expected run status must be ScenarioRunStatus")
        if not isinstance(self.coordinator_status, ScenarioCoordinatorStatus):
            raise TypeError("coordinator status must be ScenarioCoordinatorStatus")


RunProjectionExpectation: TypeAlias = NoRunExpected | RunExpected


@dataclass(frozen=True)
class EventExpectation:
    kind: ScenarioActivityEventKind
    operation: OperationExpectation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ScenarioActivityEventKind):
            raise TypeError("expected event kind must be ScenarioActivityEventKind")
        if self.operation is not None and not isinstance(
            self.operation,
            OperationExpectation,
        ):
            raise TypeError("expected event operation must be typed")
        scope = scenario_event_scope(self.kind)
        if scope is ScenarioEventScope.ACTIVITY and self.operation is None:
            raise ValueError("activity event expectation requires an operation")
        if scope is ScenarioEventScope.RUN and self.operation is not None:
            raise ValueError("run event expectation cannot reference an operation")


@dataclass(frozen=True)
class EventOrderExpectation:
    predecessor: EventExpectation
    successor: EventExpectation

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, EventExpectation):
            raise TypeError("event order predecessor must be EventExpectation")
        if not isinstance(self.successor, EventExpectation):
            raise TypeError("event order successor must be EventExpectation")
        if self.predecessor == self.successor:
            raise ValueError("event order cannot require an event before itself")


@dataclass(frozen=True)
class ObservationExpectation:
    subject_id: str
    status: ScenarioObservationStatus
    probe_kind: ProbeKind
    probe_outcome: ProbeOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise ValueError("observation subject id must be non-empty text")
        if not isinstance(self.status, ScenarioObservationStatus):
            raise TypeError("expected observation status must be typed")
        if not isinstance(self.probe_kind, ProbeKind):
            raise TypeError("expected observation probe kind must be ProbeKind")
        if not isinstance(self.probe_outcome, ProbeOutcome):
            raise TypeError("expected observation outcome must be ProbeOutcome")


@dataclass(frozen=True)
class ExecutionScenarioExpectation:
    eligibility: ExecutionEligibility
    approval: ApprovalExpectation
    admission: AdmissionExpectation
    run: RunProjectionExpectation
    events: tuple[EventExpectation, ...] = ()
    event_order: tuple[EventOrderExpectation, ...] = ()
    observations: tuple[ObservationExpectation, ...] = ()
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
            isinstance(value, EventOrderExpectation)
            for value in self.event_order
        ):
            raise TypeError("event order expectations must be typed")
        event_set = set(self.events)
        if any(
            order.predecessor not in event_set or order.successor not in event_set
            for order in self.event_order
        ):
            raise ValueError("event order must reference declared semantic events")
        if not all(
            isinstance(value, ObservationExpectation)
            for value in self.observations
        ):
            raise TypeError("observation expectations must be typed")
        if not isinstance(self.graph_advancement, GraphAdvancementExpectation):
            raise TypeError("graph advancement expectation must be typed")
        if isinstance(self.run, NoRunExpected):
            if self.events or self.event_order or self.observations:
                raise ValueError("a no-run expectation cannot contain runtime evidence")
            if self.graph_advancement is not GraphAdvancementExpectation.UNCHANGED:
                raise ValueError("a no-run expectation cannot advance graph truth")
        if self.graph_advancement is GraphAdvancementExpectation.ADVANCED_TO_DESIRED:
            if not isinstance(self.run, RunExpected) or (
                self.run.status is not ScenarioRunStatus.SUCCEEDED
            ):
                raise ValueError("graph advancement requires a succeeded run")
        if self.admission is AdmissionExpectation.ADMITTED and (
            self.approval is not ApprovalExpectation.APPROVED
        ):
            raise ValueError("execution admission requires approved expectation")


@dataclass(frozen=True)
class ExecutionScenario:
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


@dataclass(frozen=True)
class ExecutionScenarioCase:
    case_id: str
    scenario: ExecutionScenario

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("scenario case id must be non-empty text")
        if not isinstance(self.scenario, ExecutionScenario):
            raise TypeError("scenario case must wrap ExecutionScenario")


def execution_scenarios() -> tuple[ExecutionScenario, ...]:
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


def execution_scenario_cases() -> tuple[ExecutionScenarioCase, ...]:
    canonical = tuple(
        ExecutionScenarioCase(f"canonical:{scenario.scenario_id}", scenario)
        for scenario in execution_scenarios()
    )
    base = execution_scenarios()[0]
    named = tuple(
        ExecutionScenarioCase(case_id, base)
        for case_id in (
            "independent-leaf-failure",
            "shared-leaf-failure",
            "uncertain-paused",
            "uncertainty-resolved-and-resumed",
            "reverse-order-compensation",
            "compensation-failure",
        )
    )
    return canonical + named


def scenario_event_scope(kind: ScenarioActivityEventKind) -> ScenarioEventScope:
    if kind in {
        ScenarioActivityEventKind.STEP_STARTED,
        ScenarioActivityEventKind.STEP_SUCCEEDED,
    }:
        return ScenarioEventScope.ACTIVITY
    return ScenarioEventScope.RUN


def _successful_expectation(
    scenario: PlanningScenario,
) -> ExecutionScenarioExpectation:
    opened = EventExpectation(ScenarioActivityEventKind.RUN_OPENED)
    started = EventExpectation(ScenarioActivityEventKind.RUN_STARTED)
    succeeded = EventExpectation(ScenarioActivityEventKind.RUN_SUCCEEDED)
    advanced = EventExpectation(ScenarioActivityEventKind.CURRENT_GRAPH_ADVANCED)
    step_events = tuple(
        event
        for operation in scenario.expectation.operations
        for event in (
            EventExpectation(ScenarioActivityEventKind.STEP_STARTED, operation),
            EventExpectation(ScenarioActivityEventKind.STEP_SUCCEEDED, operation),
        )
    )
    events = (opened, started, *step_events, succeeded, advanced)
    event_order = [
        EventOrderExpectation(opened, started),
        EventOrderExpectation(succeeded, advanced),
    ]
    for operation in scenario.expectation.operations:
        step_started = EventExpectation(
            ScenarioActivityEventKind.STEP_STARTED,
            operation,
        )
        step_succeeded = EventExpectation(
            ScenarioActivityEventKind.STEP_SUCCEEDED,
            operation,
        )
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
                    ScenarioActivityEventKind.STEP_SUCCEEDED,
                    dependency.predecessor,
                ),
                EventExpectation(
                    ScenarioActivityEventKind.STEP_STARTED,
                    dependency.successor,
                ),
            )
        )
    observations = tuple(
        ObservationExpectation(
            operation.target_id,
            ScenarioObservationStatus.HEALTHY,
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
        run=RunExpected(ScenarioRunStatus.SUCCEEDED, ScenarioCoordinatorStatus.COMPLETED),
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


def _scenario(
    scenario_id: str,
    title: str,
    current: DeploymentGraph,
    desired: DeploymentGraph,
    *,
    operations: tuple[OperationExpectation, ...],
    dependencies: tuple[DependencyExpectation, ...] = (),
    max_risk: RiskLevel,
    ready_for_execution: bool = True,
) -> PlanningScenario:
    return PlanningScenario(
        scenario_id=scenario_id,
        title=title,
        approval_comment=f"Review the {title.lower()} plan before execution.",
        current_graph=current,
        desired_graph=desired,
        expectation=ScenarioExpectation(
            operations=operations,
            required_dependencies=dependencies,
            max_risk=max_risk,
            ready_for_execution=ready_for_execution,
        ),
    )


def _op(operation_type: type[object], target_id: str) -> OperationExpectation:
    return OperationExpectation(operation_type, target_id)


def _dependency(
    predecessor: OperationExpectation,
    successor: OperationExpectation,
) -> DependencyExpectation:
    return DependencyExpectation(predecessor, successor)


def _docker_graph(name: str, *children: object) -> DeploymentGraph:
    return compile_topology(
        DeploymentTopology(name, DockerRuntime(children=tuple(children)))
    )


def _http_app(
    role_id: str,
    *,
    provider: str = "internal",
    requirements: tuple[RequirementSocket, ...] = (),
    implementation_kind: str = "application",
) -> ApplicationBlock:
    return ApplicationBlock(
        BlockSpec(role_id),
        _ScenarioImplementation(implementation_kind, {provider: f"http://{role_id}"}),
        BlockSockets(
            requirements=requirements,
            providers=(ProviderSocket(provider, Protocol.HTTP),),
        ),
    )


def _router_graph(active: str) -> DeploymentGraph:
    router = ApplicationBlock(
        BlockSpec("api-router"),
        _ScenarioImplementation("router", {"public": "http://api-router"}),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "active",
                    Protocol.HTTP,
                    (),
                    binding=SocketBinding.RUNTIME_CONTROL,
                ),
            ),
            providers=(ProviderSocket("public", Protocol.HTTP),),
        ),
    )
    return _docker_graph(
        f"router-{active}",
        router,
        _http_app("api-v1"),
        _http_app("api-v2"),
        SocketConnection(
            active,
            "internal",
            "api-router",
            "active",
            edge_id="api-router.active",
        ),
    )


def _balanced_graph() -> DeploymentGraph:
    balancer = ApplicationBlock(
        BlockSpec("balancer"),
        _ScenarioImplementation("weighted-balancer", {"public": "http://balancer"}),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "app-a",
                    Protocol.HTTP,
                    (),
                    binding=SocketBinding.RUNTIME_CONTROL,
                ),
                RequirementSocket(
                    "app-b",
                    Protocol.HTTP,
                    (),
                    binding=SocketBinding.RUNTIME_CONTROL,
                ),
            ),
            providers=(ProviderSocket("public", Protocol.HTTP),),
        ),
    )
    return _docker_graph(
        "weighted-balancer",
        _http_app("app-a"),
        _http_app("app-b"),
        balancer,
        SocketConnection("app-a", "internal", "balancer", "app-a"),
        SocketConnection("app-b", "internal", "balancer", "app-b"),
    )


def _rate_limiter_graph() -> DeploymentGraph:
    app = _http_app("app")
    limiter = ApplicationBlock(
        BlockSpec("limiter"),
        _ScenarioImplementation("rate-limiter", {"public": "http://limiter"}),
        BlockSockets(
            requirements=(RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",)),),
            providers=(ProviderSocket("public", Protocol.HTTP),),
        ),
    )
    return _docker_graph(
        "rate-limiter",
        app,
        limiter,
        SocketConnection("app", "internal", "limiter", "upstream"),
    )


def _multiplexer_graph() -> DeploymentGraph:
    multiplexer = ApplicationBlock(
        BlockSpec("multiplexer"),
        _ScenarioImplementation("multiplexer", {"public": "http://multiplexer"}),
        BlockSockets(
            requirements=(
                RequirementSocket("primary", Protocol.HTTP, ("PRIMARY_URL",)),
                RequirementSocket("observer", Protocol.HTTP, ("OBSERVER_URL",)),
            ),
            providers=(ProviderSocket("public", Protocol.HTTP),),
        ),
    )
    return _docker_graph(
        "multiplexer",
        _http_app("primary"),
        _http_app("observer"),
        multiplexer,
        SocketConnection("primary", "internal", "multiplexer", "primary"),
        SocketConnection("observer", "internal", "multiplexer", "observer"),
    )


def _runtime_move_graph(name: str, *, source: str) -> DeploymentGraph:
    worker = _http_app("worker", implementation_kind="worker")
    runtime_a = RuntimeContext(
        "runtime-a",
        RuntimeKind.DRY_RUN,
        children=(worker,) if source == "runtime-a" else (),
    )
    runtime_b = RuntimeContext(
        "runtime-b",
        RuntimeKind.DRY_RUN,
        children=(worker,) if source == "runtime-b" else (),
    )
    root = RuntimeContext(
        "deployment",
        RuntimeKind.DRY_RUN,
        children=(runtime_a, runtime_b),
    )
    return compile_topology(DeploymentTopology(name, root))


def _database_graph(
    name: str,
    *,
    active_database_id: str,
) -> DeploymentGraph:
    api = _http_app(
        "api",
        requirements=(
            RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),
        ),
    )
    databases = tuple(
        DataBlock(
            BlockSpec(database_id),
            _ScenarioImplementation(
                "data",
                {"internal": f"postgresql://{database_id}:5432/app"},
            ),
            BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
        )
        for database_id in ("postgres-a", "postgres-b")
    )
    return _docker_graph(
        name,
        api,
        *databases,
        SocketConnection(
            active_database_id,
            "internal",
            "api",
            "database",
            edge_id="api.database",
        ),
    )


def _implementation_graph(name: str, *, kind: str) -> DeploymentGraph:
    api = _http_app("api", implementation_kind=kind)
    return _docker_graph(name, api)


def _retain(
    graph: DeploymentGraph,
    name: str,
    *,
    nodes: tuple[str, ...],
    edges: tuple[str, ...] = (),
) -> DeploymentGraph:
    retained_nodes = {node_id: graph.nodes[node_id] for node_id in nodes}
    retained_edges = {edge_id: graph.edges[edge_id] for edge_id in edges}
    retained_runtimes = {
        runtime_id: replace(
            runtime,
            children=tuple(
                child for child in runtime.children if child in retained_nodes
            ),
        )
        for runtime_id, runtime in graph.runtimes.items()
    }
    return DeploymentGraph(name, retained_nodes, retained_edges, retained_runtimes)


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
