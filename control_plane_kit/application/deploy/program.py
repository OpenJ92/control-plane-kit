"""Readable deployment-program compositions over the canonical stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from control_plane_kit.application.deploy.stages import (
    Admit,
    Advance,
    Approve,
    Claim,
    Execute,
    Plan,
    PlanningServices,
)
from control_plane_kit.application.deploy.values import (
    AdmissionGrant,
    AdvancedDeployment,
    AdvancementGrant,
    ApprovalGrant,
    ApprovalSuspension,
    ApprovedDeployment,
    ClaimGrant,
    DeploymentExecutionResult,
    DeploymentPlanRequest,
    DeploymentPreparation,
    DeploymentPreparationResult,
    DeploymentTransition,
    ExecutedDeployment,
    ExecutionContinuation,
    ExecutionLimits,
    RecoverySuspension,
    classify_transition,
)
from control_plane_kit.topology.codec import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorError,
)
from control_plane_kit.topology import DeploymentGraph
from control_plane_kit.topology.validation import GraphValidationError, validate_graph
from control_plane_kit.workflows import (
    ActivityPlanningResult,
    ApprovalCommandService,
    ApprovalDecisionKind,
    ApprovalDecisionResult,
    ApprovalRequestResult,
    CurrentGraphAdvancementCommandService,
    DeploymentContextError,
    DeploymentPlanContext,
    DesiredGraphEditResult,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    OperationCommandResult,
    RunLifecycleCommandService,
)


@dataclass(frozen=True)
class DeploymentExecutionGrant:
    """Explicit operator and worker inputs for one approved deployment run."""

    admission: AdmissionGrant
    claim: ClaimGrant
    advancement: AdvancementGrant
    limits: ExecutionLimits = ExecutionLimits()


class DeploymentContextReader(Protocol):
    """Load one coherent durable plan context without retaining it."""

    def load(self, plan_id: str) -> DeploymentPlanContext: ...


@dataclass(frozen=True)
class DeploymentProgramServices:
    """Long-lived capabilities used to interpret deployment commands."""

    planning: PlanningServices
    approvals: ApprovalCommandService
    admission: ExecutionAdmissionCommandService
    lifecycle: RunLifecycleCommandService
    coordinator: ExecutionCoordinator
    advancement: CurrentGraphAdvancementCommandService
    contexts: DeploymentContextReader

    def __post_init__(self) -> None:
        expected = (
            ("planning", self.planning, PlanningServices),
            ("approvals", self.approvals, ApprovalCommandService),
            ("admission", self.admission, ExecutionAdmissionCommandService),
            ("lifecycle", self.lifecycle, RunLifecycleCommandService),
            ("coordinator", self.coordinator, ExecutionCoordinator),
            (
                "advancement",
                self.advancement,
                CurrentGraphAdvancementCommandService,
            ),
        )
        for name, value, kind in expected:
            if not isinstance(value, kind):
                raise TypeError(f"{name} must be {kind.__name__}")
        if not callable(getattr(self.contexts, "load", None)):
            raise TypeError("contexts must provide load(plan_id)")


@dataclass(frozen=True)
class PrepareDeployment:
    """Compose durable planning through the approval suspension boundary."""

    plan: Plan

    def __call__(self, request: DeploymentPlanRequest) -> DeploymentPreparationResult:
        return self.plan(request)


DeploymentProgramResult = AdvancedDeployment | ExecutionContinuation | RecoverySuspension


@dataclass(frozen=True)
class ExecuteApprovedDeployment:
    """Compose approved work through bounded execution and guarded advancement."""

    admit: Admit
    claim: Claim
    execute: Execute
    advance: Advance

    def __call__(
        self,
        approved: ApprovedDeployment,
        grant: DeploymentExecutionGrant,
    ) -> DeploymentProgramResult:
        admitted = self.admit(approved, grant.admission)
        claimed = self.claim(admitted, grant.claim)
        return self._execute_and_maybe_advance(
            self.execute(claimed, grant.limits),
            grant.advancement,
        )

    def resume(
        self,
        continuation: ExecutionContinuation,
        *,
        limits: ExecutionLimits,
        advancement: AdvancementGrant,
    ) -> DeploymentProgramResult:
        return self._execute_and_maybe_advance(
            self.execute(continuation, limits),
            advancement,
        )

    def resume_recovered(
        self,
        suspension: RecoverySuspension,
        *,
        limits: ExecutionLimits,
        advancement: AdvancementGrant,
    ) -> DeploymentProgramResult:
        return self._execute_and_maybe_advance(
            self.execute(suspension.claimed, limits),
            advancement,
        )

    def _execute_and_maybe_advance(
        self,
        result: DeploymentExecutionResult,
        advancement: AdvancementGrant,
    ) -> DeploymentProgramResult:
        match result:
            case ExecutedDeployment():
                return self.advance(result, advancement)
            case ExecutionContinuation() | RecoverySuspension():
                return result


@dataclass(frozen=True)
class Deploy:
    """Parameterized graph-transition program with explicit suspension methods."""

    current: DeploymentGraph
    desired: DeploymentGraph
    preparation: PrepareDeployment
    approval: Approve
    execution: ExecuteApprovedDeployment

    @property
    def transition(self) -> DeploymentTransition:
        return classify_transition(self.current, self.desired)

    def __call__(self, request: DeploymentPlanRequest) -> DeploymentPreparationResult:
        if request.transition != self.transition:
            raise ValueError("deployment request does not describe this graph transition")
        return self.preparation(request)

    def plan(self, request: DeploymentPlanRequest) -> DeploymentPreparationResult:
        """Prepare this graph pair through the explicit approval suspension."""

        return self(request)

    def approve(
        self,
        suspension: ApprovalSuspension,
        grant: ApprovalGrant,
    ) -> ApprovedDeployment:
        self._require_own_transition(suspension)
        return self.approval(suspension, grant)

    def execute_approved(
        self,
        approved: ApprovedDeployment,
        grant: DeploymentExecutionGrant,
    ) -> DeploymentProgramResult:
        self._require_own_transition(approved)
        return self.execution(approved, grant)

    def resume_execution(
        self,
        continuation: ExecutionContinuation,
        *,
        limits: ExecutionLimits,
        advancement: AdvancementGrant,
    ) -> DeploymentProgramResult:
        self._require_own_transition(continuation)
        return self.execution.resume(
            continuation,
            limits=limits,
            advancement=advancement,
        )

    def resume_recovered(
        self,
        suspension: RecoverySuspension,
        *,
        limits: ExecutionLimits,
        advancement: AdvancementGrant,
    ) -> DeploymentProgramResult:
        self._require_own_transition(suspension)
        return self.execution.resume_recovered(
            suspension,
            limits=limits,
            advancement=advancement,
        )

    def _require_own_transition(
        self,
        value: ApprovalSuspension
        | ApprovedDeployment
        | ExecutionContinuation
        | RecoverySuspension,
    ) -> None:
        match value:
            case ApprovalSuspension(preparation=preparation):
                pass
            case ApprovedDeployment(
                suspension=ApprovalSuspension(preparation=preparation)
            ):
                pass
            case ExecutionContinuation(claimed=claimed) | RecoverySuspension(
                claimed=claimed
            ):
                preparation = (
                    claimed.admitted.approved.suspension.preparation
                )
            case _:
                raise TypeError("deployment evidence has an unsupported shape")
        if preparation.request.transition != self.transition:
            raise ValueError(
                "deployment evidence belongs to another graph transition"
            )


@dataclass(frozen=True)
class DeploymentProgram:
    """Long-lived composition root for reconstructible deployment commands."""

    services: DeploymentProgramServices

    def __post_init__(self) -> None:
        if not isinstance(self.services, DeploymentProgramServices):
            raise TypeError("services must be DeploymentProgramServices")

    def between(
        self,
        current: DeploymentGraph,
        desired: DeploymentGraph,
    ) -> Deploy:
        """Bind the long-lived capabilities to one short-lived graph pair."""

        return Deploy(
            current,
            desired,
            PrepareDeployment(Plan(self.services.planning)),
            Approve(self.services.approvals),
            ExecuteApprovedDeployment(
                Admit(self.services.admission),
                Claim(self.services.lifecycle),
                Execute(self.services.coordinator),
                Advance(self.services.advancement),
            ),
        )

    def for_plan(self, plan_id: str) -> "StoredDeployment":
        """Return an ephemeral handle that reloads durable truth per command."""

        _required_text("plan_id", plan_id)
        return StoredDeployment(self, plan_id)

    def _load(
        self,
        plan_id: str,
        approval_request_id: str,
    ) -> tuple[Deploy, ApprovalSuspension, ApprovalDecisionResult | None]:
        context = self.services.contexts.load(plan_id)
        approval = context.approval(approval_request_id)
        try:
            current = validate_graph(
                DEFAULT_GRAPH_CODEC.decode(context.base_graph.graph_descriptor)
            ).require_valid()
            desired = validate_graph(
                DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)
            ).require_valid()
        except (GraphDescriptorError, GraphValidationError) as error:
            raise DeploymentContextError(
                "deployment plan references invalid graph truth"
            ) from error
        deploy = self.between(current, desired)
        previous_desired = context.desired_graph_action.payload.get(
            "previous_desired_graph_id"
        )
        if previous_desired is not None and not isinstance(previous_desired, str):
            raise DeploymentContextError(
                "desired graph action has invalid previous pointer evidence"
            )
        approval_comment = approval.request.comment
        if not isinstance(approval_comment, str) or not approval_comment.strip():
            raise DeploymentContextError(
                "deployment approval request has no reconstruction comment"
            )
        request = DeploymentPlanRequest(
            transition=deploy.transition,
            workspace_id=context.session.workspace_id,
            current_graph_id=context.plan.base_graph_id,
            expected_desired_graph_id=previous_desired,
            actor_id=context.session.actor_id,
            title=context.session.title,
            approval_comment=approval_comment,
            idempotency_prefix=_idempotency_prefix(context.session.idempotency_key),
        )
        preparation = DeploymentPreparation(
            request=request,
            session=OperationCommandResult(
                context.session,
                context.session_action,
                replayed=True,
            ),
            desired_graph=DesiredGraphEditResult(
                workspace_id=context.session.workspace_id,
                previous_desired_graph_id=previous_desired,
                graph_version=context.desired_graph,
                action=context.desired_graph_action,
                replayed=True,
            ),
            plan=ActivityPlanningResult(
                context.plan,
                context.plan_action,
                replayed=True,
            ),
        )
        suspension = ApprovalSuspension(
            preparation,
            ApprovalRequestResult(
                approval.request,
                approval.request_action,
                replayed=True,
            ),
        )
        if approval.decision is None:
            return deploy, suspension, None
        assert approval.decision_action is not None
        return (
            deploy,
            suspension,
            ApprovalDecisionResult(
                approval.request,
                approval.decision,
                approval.decision_action,
                replayed=True,
            ),
        )


@dataclass(frozen=True)
class StoredDeployment:
    """Plan-id handle that never treats an in-memory value as workflow state."""

    program: DeploymentProgram
    plan_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.program, DeploymentProgram):
            raise TypeError("program must be DeploymentProgram")
        _required_text("plan_id", self.plan_id)

    def approve(
        self,
        approval_request_id: str,
        grant: ApprovalGrant,
    ) -> ApprovedDeployment:
        """Reload pending approval truth and record one canonical decision."""

        _required_text("approval_request_id", approval_request_id)
        deploy, suspension, _ = self.program._load(
            self.plan_id,
            approval_request_id,
        )
        return deploy.approve(suspension, grant)

    def run(
        self,
        approval_request_id: str,
        grant: DeploymentExecutionGrant,
    ) -> DeploymentProgramResult:
        """Reload approved truth, then admit, claim, execute, and advance."""

        _required_text("approval_request_id", approval_request_id)
        deploy, suspension, decision = self.program._load(
            self.plan_id,
            approval_request_id,
        )
        if decision is None:
            raise DeploymentContextError("deployment plan has not been approved")
        if decision.decision.decision is not ApprovalDecisionKind.APPROVED:
            raise DeploymentContextError("deployment plan approval was rejected")
        return deploy.execute_approved(
            ApprovedDeployment(suspension, decision),
            grant,
        )


def _idempotency_prefix(value: str | None) -> str:
    suffix = ":session"
    if not isinstance(value, str) or not value.endswith(suffix):
        raise DeploymentContextError(
            "deployment session idempotency key has no canonical prefix"
        )
    prefix = value[: -len(suffix)]
    _required_text("deployment idempotency prefix", prefix)
    return prefix


def _required_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
