"""Postgres-backed acceptance runner over canonical control-plane services.

The runner is composition, not another execution application.  It accepts an
already provisioned workspace and invokes the same planning, approval,
admission, lifecycle, coordinator, advancement, and read services used by
transport adapters.  Only the external effect interpreter is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Mapping

from control_plane_kit.application.deploy import (
    AdmissionGrant,
    AdvancedDeployment,
    AdvancementGrant,
    ApprovalGrant,
    ApprovalSuspension,
    ClaimedDeployment,
    ClaimGrant,
    DeploymentExecutionGrant,
    DeploymentPlanRequest,
    DeploymentPreparation,
    DeploymentProgramResult,
    DeploymentReviewBlocked,
    Deploy,
    ExecutionLimits,
    ExecutionContinuation,
    NoDeploymentChanges,
    PlanningServices,
    PrepareDeployment,
    Plan,
    Approve,
    Admit,
    Claim,
    Execute,
    Advance,
    ExecuteApprovedDeployment,
    RecoverySuspension,
)

from control_plane_kit.effects import (
    EffectCapability,
    EffectFailed,
    EffectPurpose,
    EffectObservation,
    EffectSucceeded,
    MaterializedEffectRequest,
    ObservationKind,
)
from control_plane_kit.execution import (
    ActivityEventKind,
    BeginCompensation,
    BoundedEvidence,
    ConfirmEffectFailed,
    ConfirmEffectSucceeded,
    EndpointContext,
    FailureCategory,
    FailureEvidence,
    ObservationStatus,
    ProbeKind,
    ProbeOutcome,
    RecoveryAuthority,
    RecoveryDecision,
    RecoveryDecisionRecord,
    RecoveryScope,
    ResumeSameIntent,
)
from control_plane_kit.planning import WaitForHealthy
from control_plane_kit.read_services import (
    FocusedDetailReadModel,
    InstanceReadService,
    ObservedStateReadModel,
    WorkspaceReadModel,
)
from control_plane_kit.workflows import (
    ApprovalCommandService,
    ApprovalDecisionResult,
    ApprovalRequestResult,
    CoordinatorStatus,
    CurrentGraphAdvancementCommandService,
    CurrentGraphAdvancementResult,
    DecideActivityRunRecovery,
    ExecutionAdmissionCommandService,
    ExecutionAdmissionResult,
    ExecutionCoordinator,
    ExecutionCoordinatorResult,
    ExecutionReadinessRequired,
    ExecutionWorkerAuthority,
    IdempotencyKey,
    PauseActivityRun,
    RunLifecycleCommandService,
    RunLifecycleResult,
)
from examples.scenarios.execution import (
    AdmissionExpectation,
    ApprovalExpectation,
    BeginScenarioCompensation,
    EventExpectation,
    CompensationExpected,
    ExecutionScenario,
    ExternalReadinessGated,
    GraphAdvancementExpectation,
    FailurePhase,
    NoRunExpected,
    NoChanges,
    PauseScenarioExecution,
    ReviewBlocked,
    ResolveScenarioUncertainty,
    ResumeScenarioExecution,
    RunExpected,
    ScenarioRecoveryProgram,
    ScenarioRecoveryStep,
    UncertaintyResolution,
)
from examples.scenarios.model import OperationExpectation, operation_expectation


class ScenarioEffectDisposition(StrEnum):
    """Closed synthetic outcomes available to acceptance scenarios."""

    SUCCEED = "succeed"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ScenarioEffectDirective:
    """Select one deterministic outcome for a semantic plan operation."""

    operation: OperationExpectation
    disposition: ScenarioEffectDisposition
    phase: FailurePhase = FailurePhase.FORWARD

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationExpectation):
            raise TypeError("scenario effect operation must be typed")
        if not isinstance(self.disposition, ScenarioEffectDisposition):
            raise TypeError("scenario effect disposition must be typed")
        if not isinstance(self.phase, FailurePhase):
            raise TypeError("scenario effect phase must be typed")


@dataclass(frozen=True)
class ScenarioEffectProgram:
    """Immutable provider program; unspecified operations succeed."""

    directives: tuple[ScenarioEffectDirective, ...] = ()

    def __post_init__(self) -> None:
        if not all(isinstance(value, ScenarioEffectDirective) for value in self.directives):
            raise TypeError("scenario effect directives must be typed")
        keys = tuple((value.operation, value.phase) for value in self.directives)
        if len(keys) != len(set(keys)):
            raise ValueError("scenario effect program cannot repeat an operation phase")

    def disposition_for(
        self,
        operation: OperationExpectation,
        phase: FailurePhase,
    ) -> ScenarioEffectDisposition:
        return next(
            (
                directive.disposition
                for directive in self.directives
                if directive.operation == operation and directive.phase is phase
            ),
            ScenarioEffectDisposition.SUCCEED,
        )


@dataclass
class ScenarioEffectInterpreter:
    """Deterministic fake provider that still consumes materialized effects."""

    program: ScenarioEffectProgram = field(default_factory=ScenarioEffectProgram)
    transaction_active: Callable[[], bool] = lambda: False
    requests: list[MaterializedEffectRequest] = field(default_factory=list)
    capabilities: frozenset[EffectCapability] = frozenset(EffectCapability)

    def execute(self, request: MaterializedEffectRequest):
        if self.transaction_active():
            raise AssertionError("scenario effect ran while a UnitOfWork was active")
        operation = operation_expectation(request.action)
        self.requests.append(request)
        match request.purpose:
            case EffectPurpose.FORWARD:
                phase = FailurePhase.FORWARD
            case EffectPurpose.COMPENSATION:
                phase = FailurePhase.COMPENSATION
        match self.program.disposition_for(operation, phase):
            case ScenarioEffectDisposition.SUCCEED:
                return EffectSucceeded(
                    request.identity,
                    BoundedEvidence.from_mapping({"scenario": "succeeded"}),
                    _observations(request),
                )
            case ScenarioEffectDisposition.FAIL:
                return EffectFailed(
                    request.identity,
                    FailureEvidence(
                        FailureCategory.RETRYABLE,
                        "scenario.effect-failed",
                        "The deterministic scenario effect failed.",
                    ),
                )
            case ScenarioEffectDisposition.UNCERTAIN:
                return EffectFailed(
                    request.identity,
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "scenario.effect-uncertain",
                        "The deterministic scenario effect outcome is unknown.",
                    ),
                )


@dataclass(frozen=True)
class ScenarioRunnerServices:
    """Canonical application services composed by the acceptance runner."""

    planning: PlanningServices
    approvals: ApprovalCommandService
    admission: ExecutionAdmissionCommandService
    lifecycle: RunLifecycleCommandService
    coordinator: ExecutionCoordinator
    advancement: CurrentGraphAdvancementCommandService
    reads: InstanceReadService


@dataclass(frozen=True)
class ScenarioRunContext:
    """Identity and authority for one isolated, pre-provisioned workspace."""

    workspace_id: str
    current_graph_id: str
    actor_id: str
    approver_id: str
    worker: ExecutionWorkerAuthority
    lease_expires_at: str


@dataclass(frozen=True)
class ScenarioEvaluation:
    """Semantic findings against the typed scenario expectation."""

    findings: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        return not self.findings

    def require_satisfied(self) -> None:
        if self.findings:
            raise AssertionError("scenario expectation failed:\n- " + "\n- ".join(self.findings))


@dataclass(frozen=True)
class ScenarioRunnerResult:
    """Canonical workflow evidence plus an acceptance-only evaluation."""

    preparation: DeploymentPreparation
    approval_request: ApprovalRequestResult | None
    approval: ApprovalDecisionResult | None
    admission: ExecutionAdmissionResult | None
    opened: RunLifecycleResult | None
    coordinator: ExecutionCoordinatorResult | None
    advancement: CurrentGraphAdvancementResult | None
    session_view: FocusedDetailReadModel
    workspace_view: WorkspaceReadModel
    observed_state: ObservedStateReadModel
    evaluation: ScenarioEvaluation


def run_execution_scenario(
    services: ScenarioRunnerServices,
    scenario: ExecutionScenario,
    context: ScenarioRunContext,
    recovery: ScenarioRecoveryProgram = ScenarioRecoveryProgram(),
) -> ScenarioRunnerResult:
    """Interpret one scenario through the canonical Postgres-backed workflow."""

    prefix = f"scenario:{scenario.scenario_id}"
    deploy = Deploy(
        scenario.planning.current_graph,
        scenario.planning.desired_graph,
        PrepareDeployment(Plan(services.planning)),
        Approve(services.approvals),
        ExecuteApprovedDeployment(
            Admit(services.admission),
            Claim(services.lifecycle),
            Execute(services.coordinator),
            Advance(services.advancement),
        ),
    )
    prepared = deploy(
        DeploymentPlanRequest(
            transition=deploy.transition,
            workspace_id=context.workspace_id,
            current_graph_id=context.current_graph_id,
            expected_desired_graph_id=context.current_graph_id,
            actor_id=context.actor_id,
            title=scenario.planning.title,
            approval_comment=scenario.planning.approval_comment,
            idempotency_prefix=prefix,
        )
    )
    match prepared:
        case ApprovalSuspension() as approval_suspension:
            preparation = approval_suspension.preparation
            approval_request = approval_suspension.approval_request
        case NoDeploymentChanges(preparation=value) | DeploymentReviewBlocked(
            preparation=value
        ):
            preparation = value
            approval_request = None
    approval: ApprovalDecisionResult | None = None
    admission: ExecutionAdmissionResult | None = None
    opened: RunLifecycleResult | None = None
    coordinated: ExecutionCoordinatorResult | None = None
    advancement: CurrentGraphAdvancementResult | None = None
    recovery_suspension: RecoverySuspension | None = None

    if not isinstance(scenario.expectation.eligibility, (ReviewBlocked, NoChanges)):
        if approval_request is None:
            raise AssertionError("executable scenario did not produce an approval request")
        approved = deploy.approve(
            approval_suspension,
            ApprovalGrant(
                actor_id=context.approver_id,
                actor_scopes=(approval_request.request.required_scope,),
                idempotency_key=IdempotencyKey(f"{prefix}:approval-decision"),
                comment="Approved by the deterministic scenario runner.",
            ),
        )
        approval = approved.approval
        execution_grant = DeploymentExecutionGrant(
            admission=AdmissionGrant(
                actor_id=context.actor_id,
                actor_scopes=("plan:execute",),
                idempotency_key=IdempotencyKey(f"{prefix}:admission"),
            ),
            claim=ClaimGrant(
                authority=context.worker,
                lease_expires_at=context.lease_expires_at,
                claim_idempotency_key=IdempotencyKey(f"{prefix}:claim"),
                start_idempotency_key=IdempotencyKey(f"{prefix}:start"),
            ),
            advancement=AdvancementGrant(
                IdempotencyKey(f"{prefix}:advance")
            ),
        )
        if isinstance(scenario.expectation.eligibility, ExternalReadinessGated):
            try:
                deploy.execute_approved(approved, execution_grant)
            except ExecutionReadinessRequired:
                pass
            else:
                raise AssertionError("readiness-gated scenario was admitted without evidence")
        else:
            outcome = deploy.execute_approved(approved, execution_grant)
            claimed, coordinated, advancement = _program_evidence(outcome)
            admission = claimed.admitted.admission
            opened = claimed.opened
            if isinstance(outcome, RecoverySuspension):
                recovery_suspension = outcome
            if recovery.steps:
                activities = {
                    operation_expectation(activity.operation): activity.activity_id.value
                    for activity in preparation.plan.plan_record.plan.activities
                }
                for ordinal, step in enumerate(recovery.steps, start=1):
                    if isinstance(step, PauseScenarioExecution):
                        services.lifecycle.execute(
                            PauseActivityRun(
                                run_id=opened.run.run_id,
                                authority=context.worker,
                                idempotency_key=IdempotencyKey(
                                    f"{prefix}:pause:{ordinal}"
                                ),
                            )
                        )
                        continue
                    decision, scope = _recovery_decision(step, activities)
                    events = _projected_events(
                        services.reads.session_detail(
                            context.workspace_id,
                            preparation.session.session.session_id,
                            limit=100,
                        ),
                        plan_id=preparation.plan.plan_record.plan_id,
                        run_id=opened.run.run_id,
                    )
                    if not events:
                        raise AssertionError(
                            "scenario recovery requires durable run events"
                        )
                    services.lifecycle.execute(
                        DecideActivityRunRecovery(
                            run_id=opened.run.run_id,
                            expected_worker_id=context.worker.worker_id,
                            expected_event_ordinal=max(
                                int(event["ordinal"]) for event in events
                            ),
                            recovery=RecoveryDecisionRecord(
                                decision_id=f"{prefix}:recovery:{ordinal}",
                                decision=decision,
                                authority=RecoveryAuthority(
                                    context.approver_id,
                                    f"{prefix}:recovery-grant:{ordinal}",
                                    (scope,),
                                ),
                                reason=(
                                    "Execute the typed acceptance recovery program."
                                ),
                            ),
                            idempotency_key=IdempotencyKey(
                                f"{prefix}:recovery-command:{ordinal}"
                            ),
                        )
                        )
                if recovery_suspension is None:
                    raise AssertionError(
                        "scenario recovery program requires a recovery suspension"
                    )
                outcome = deploy.resume_recovered(
                    recovery_suspension,
                    limits=ExecutionLimits(),
                    advancement=execution_grant.advancement,
                )
                _, coordinated, advancement = _program_evidence(outcome)

    session_view = services.reads.session_detail(
        context.workspace_id,
        preparation.session.session.session_id,
        limit=100,
    )
    workspace_view = services.reads.workspace(context.workspace_id)
    observed_state = services.reads.observed_state(context.workspace_id)
    result = ScenarioRunnerResult(
        preparation,
        approval_request,
        approval,
        admission,
        opened,
        coordinated,
        advancement,
        session_view,
        workspace_view,
        observed_state,
        ScenarioEvaluation(),
    )
    return ScenarioRunnerResult(
        preparation,
        approval_request,
        approval,
        admission,
        opened,
        coordinated,
        advancement,
        session_view,
        workspace_view,
        observed_state,
        evaluate_execution_scenario(scenario, result),
    )


def _program_evidence(
    result: DeploymentProgramResult,
) -> tuple[
    ClaimedDeployment,
    ExecutionCoordinatorResult,
    CurrentGraphAdvancementResult | None,
]:
    match result:
        case AdvancedDeployment(executed=executed, advancement=advancement):
            return executed.claimed, executed.execution, advancement
        case ExecutionContinuation(claimed=claimed, execution=execution):
            return claimed, execution, None
        case RecoverySuspension(claimed=claimed, execution=execution):
            return claimed, execution, None


def evaluate_execution_scenario(
    scenario: ExecutionScenario,
    result: ScenarioRunnerResult,
) -> ScenarioEvaluation:
    """Compare canonical read projections with semantic typed expectations."""

    findings: list[str] = []
    expectation = scenario.expectation
    _expect_presence(
        findings,
        "approval request",
        result.approval_request,
        expectation.approval is not ApprovalExpectation.NOT_REQUESTED,
    )
    _expect_presence(
        findings,
        "approval decision",
        result.approval,
        expectation.approval is ApprovalExpectation.APPROVED,
    )
    _expect_presence(
        findings,
        "execution admission",
        result.admission,
        expectation.admission is AdmissionExpectation.ADMITTED,
    )
    _expect_presence(
        findings,
        "activity run",
        result.opened,
        isinstance(expectation.run, RunExpected),
    )
    _expect_presence(
        findings,
        "coordinator result",
        result.coordinator,
        isinstance(expectation.run, RunExpected),
    )

    if isinstance(expectation.run, RunExpected) and result.coordinator is not None:
        if result.coordinator.run.status is not expectation.run.status:
            findings.append(
                f"run status expected {expectation.run.status.value!r}, "
                f"observed {result.coordinator.run.status.value!r}"
            )
        if result.coordinator.status is not expectation.run.coordinator_status:
            findings.append(
                f"coordinator status expected {expectation.run.coordinator_status.value!r}, "
                f"observed {result.coordinator.status.value!r}"
            )
    elif isinstance(expectation.run, NoRunExpected) and result.coordinator is not None:
        findings.append("no-run scenario produced coordinator evidence")

    current_graph_id = result.workspace_view.workspace.current_graph_id
    desired_graph_id = result.preparation.desired_graph.graph_version.graph_id
    expected_current = (
        desired_graph_id
        if expectation.graph_advancement is GraphAdvancementExpectation.ADVANCED_TO_DESIRED
        else result.preparation.plan.plan_record.base_graph_id
    )
    if current_graph_id != expected_current:
        findings.append(
            f"current graph expected {expected_current!r}, observed {current_graph_id!r}"
        )

    if result.opened is not None:
        projected_events = _projected_events(
            result.session_view,
            plan_id=result.preparation.plan.plan_record.plan_id,
            run_id=result.opened.run.run_id,
        )
        activities = {
            operation_expectation(activity.operation): activity.activity_id.value
            for activity in result.preparation.plan.plan_record.plan.activities
        }
        positions: dict[EventExpectation, int] = {}
        for expected in expectation.events:
            key = _event_key(expected, activities)
            matches = [event for event in projected_events if _projected_event_key(event) == key]
            if not matches:
                findings.append(f"missing event {key!r}")
            else:
                positions[expected] = int(matches[0]["ordinal"])
        for order in expectation.event_order:
            before = positions.get(order.predecessor)
            after = positions.get(order.successor)
            if before is not None and after is not None and before >= after:
                findings.append(
                    f"event order violated: {order.predecessor!r} before {order.successor!r}"
                )

        for expected in expectation.failures:
            activity_id = activities[expected.operation]
            kind = (
                ActivityEventKind.STEP_FAILED
                if expected.phase is FailurePhase.FORWARD
                else ActivityEventKind.STEP_COMPENSATION_FAILED
            )
            matching = [
                event
                for event in projected_events
                if _projected_event_key(event) == (kind.value, activity_id)
            ]
            if not matching or not any(
                _failure_category(event) == expected.category.value
                for event in matching
            ):
                findings.append(
                    f"missing {expected.phase.value} failure "
                    f"{expected.category.value!r} for {expected.operation!r}"
                )

        uncertain_kinds = {
            ActivityEventKind.STEP_UNCERTAIN.value,
            ActivityEventKind.STEP_COMPENSATION_UNCERTAIN.value,
        }
        for expected in expectation.uncertain:
            activity_id = activities[expected]
            if not any(
                event["event_type"] in uncertain_kinds
                and event["activity_id"] == activity_id
                for event in projected_events
            ):
                findings.append(f"missing uncertainty for {expected!r}")

        if isinstance(expectation.compensation, CompensationExpected):
            expected_order = tuple(
                activities[operation]
                for operation in expectation.compensation.reverse_completion_order
            )
            actual_order = tuple(
                str(event["activity_id"])
                for event in projected_events
                if event["event_type"]
                == ActivityEventKind.STEP_COMPENSATION_STARTED.value
            )
            if actual_order != expected_order:
                findings.append(
                    f"compensation order expected {expected_order!r}, "
                    f"observed {actual_order!r}"
                )

    actual_observations = {
        (
            value["subject_id"],
            value["status"],
            value["probe_kind"],
            value["probe_outcome"],
        )
        for value in result.observed_state.observations
    }
    for expected in expectation.observations:
        key = (
            expected.subject_id,
            expected.status.value,
            expected.probe_kind.value,
            expected.probe_outcome.value,
        )
        if key not in actual_observations:
            findings.append(f"missing observation {key!r}")
    return ScenarioEvaluation(tuple(findings))


def _recovery_decision(
    step: ScenarioRecoveryStep,
    activities: Mapping[OperationExpectation, str],
) -> tuple[RecoveryDecision, RecoveryScope]:
    match step:
        case ResolveScenarioUncertainty(operation=operation, resolution=resolution):
            activity_id = activities[operation]
            decision = (
                ConfirmEffectSucceeded(activity_id)
                if resolution is UncertaintyResolution.SUCCEEDED
                else ConfirmEffectFailed(activity_id)
            )
            return decision, RecoveryScope.RESOLVE_UNCERTAINTY
        case ResumeScenarioExecution():
            return ResumeSameIntent(), RecoveryScope.OPERATE
        case BeginScenarioCompensation():
            return BeginCompensation(), RecoveryScope.COMPENSATE
        case _:
            raise TypeError("unknown scenario recovery step")


def _observations(request: MaterializedEffectRequest) -> tuple[EffectObservation, ...]:
    match request.action:
        case WaitForHealthy(target=target):
            return (
                EffectObservation(
                    subject_id=target.node_id,
                    kind=ObservationKind.HEALTH,
                    status=ObservationStatus.HEALTHY,
                    evidence=BoundedEvidence.from_mapping({"scenario": "healthy"}),
                    graph_id=request.material_graph_id,
                    probe_kind=ProbeKind.APPLICATION_HEALTH,
                    probe_outcome=ProbeOutcome.HEALTHY,
                    endpoint_context=EndpointContext.RUNTIME_PRIVATE,
                ),
            )
        case _:
            return ()


def _expect_presence(findings: list[str], label: str, value: object, expected: bool) -> None:
    if expected and value is None:
        findings.append(f"expected {label}")
    if not expected and value is not None:
        findings.append(f"unexpected {label}")


def _projected_events(
    view: FocusedDetailReadModel,
    *,
    plan_id: str,
    run_id: str,
) -> tuple[Mapping[str, object], ...]:
    session = view.payload["session"]
    plan = next(
        (value for value in session["plans"] if value["plan_id"] == plan_id),
        None,
    )
    if plan is None:
        return ()
    run = next(
        (value for value in plan["runs"] if value["run_id"] == run_id),
        None,
    )
    return () if run is None else tuple(run["events"])


def _event_key(
    event: EventExpectation,
    activities: Mapping[OperationExpectation, str],
) -> tuple[str, str | None]:
    return (
        event.kind.value,
        None if event.operation is None else activities[event.operation],
    )


def _projected_event_key(event: Mapping[str, object]) -> tuple[str, str | None]:
    return str(event["event_type"]), event["activity_id"]


def _failure_category(event: Mapping[str, object]) -> str | None:
    failure = event["failure"]
    return None if failure is None else str(failure["category"])
