"""Readable deployment-program compositions over the canonical stages."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit.application.deploy.stages import (
    Admit,
    Advance,
    Approve,
    Claim,
    Execute,
    Plan,
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
    DeploymentPreparationResult,
    DeploymentTransition,
    ExecutedDeployment,
    ExecutionContinuation,
    ExecutionLimits,
    RecoverySuspension,
    classify_transition,
)
from control_plane_kit.topology import DeploymentGraph


@dataclass(frozen=True)
class DeploymentExecutionGrant:
    """Explicit operator and worker inputs for one approved deployment run."""

    admission: AdmissionGrant
    claim: ClaimGrant
    advancement: AdvancementGrant
    limits: ExecutionLimits = ExecutionLimits()


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

    def approve(
        self,
        suspension: ApprovalSuspension,
        grant: ApprovalGrant,
    ) -> ApprovedDeployment:
        return self.approval(suspension, grant)

    def execute_approved(
        self,
        approved: ApprovedDeployment,
        grant: DeploymentExecutionGrant,
    ) -> DeploymentProgramResult:
        return self.execution(approved, grant)

    def resume_execution(
        self,
        continuation: ExecutionContinuation,
        *,
        limits: ExecutionLimits,
        advancement: AdvancementGrant,
    ) -> DeploymentProgramResult:
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
        return self.execution.resume_recovered(
            suspension,
            limits=limits,
            advancement=advancement,
        )
