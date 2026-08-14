"""Pure computed deployment-program projection values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, TypeAlias

from control_plane_kit_core.operations import (
    ActivityRunStatus,
    DeploymentProgramStage,
    EffectAttemptIdentity,
    ExecutionRequestStatus,
    RunId,
)
from control_plane_kit_core.planning import ActivityId
from control_plane_kit_operations.deployment_program import (
    DeploymentProgramReference,
    InvalidDeploymentProgramContract,
)
from control_plane_kit_operations.records import (
    ActivityPlanStatus,
    OperationSessionStatus,
)


@dataclass(frozen=True, slots=True)
class _Projection:
    reference: DeploymentProgramReference

    stage: ClassVar[DeploymentProgramStage]
    _projection: ClassVar[str]

    def __post_init__(self) -> None:
        _reference(self.reference)

    def _descriptor(self, **values: object) -> dict[str, object]:
        return {
            "projection": self._projection,
            "reference": self.reference.descriptor(),
            "stage": self.stage.value,
            **values,
        }


@dataclass(frozen=True, slots=True)
class DeploymentCompleted(_Projection):
    event_id: str

    stage = DeploymentProgramStage.ADVANCE
    _projection = "completed"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _bounded_identity(self.event_id, "event_id")

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(event_id=self.event_id)


@dataclass(frozen=True, slots=True)
class DeploymentSessionStopped(_Projection):
    session_status: OperationSessionStatus

    stage = DeploymentProgramStage.PLAN
    _projection = "session-stopped"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _status(
            self.session_status,
            OperationSessionStatus,
            (OperationSessionStatus.CLOSED, OperationSessionStatus.CANCELLED),
            "session_status",
        )

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(session_status=self.session_status.value)


@dataclass(frozen=True, slots=True)
class DeploymentPlanStopped(_Projection):
    plan_status: ActivityPlanStatus

    stage = DeploymentProgramStage.PLAN
    _projection = "plan-stopped"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _status(
            self.plan_status,
            ActivityPlanStatus,
            (ActivityPlanStatus.SUPERSEDED, ActivityPlanStatus.CANCELLED),
            "plan_status",
        )

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(plan_status=self.plan_status.value)


@dataclass(frozen=True, slots=True)
class DeploymentNoChanges(_Projection):
    stage = DeploymentProgramStage.PLAN
    _projection = "no-changes"

    def descriptor(self) -> dict[str, object]:
        return self._descriptor()


@dataclass(frozen=True, slots=True)
class DeploymentReviewBlocked(_Projection):
    stage = DeploymentProgramStage.PLAN
    _projection = "review-blocked"

    def descriptor(self) -> dict[str, object]:
        return self._descriptor()


@dataclass(frozen=True, slots=True)
class DeploymentApprovalRequestReady(_Projection):
    stage = DeploymentProgramStage.APPROVE
    _projection = "approval-request-ready"

    def descriptor(self) -> dict[str, object]:
        return self._descriptor()


@dataclass(frozen=True, slots=True)
class DeploymentApprovalRequired(_Projection):
    approval_request_id: str

    stage = DeploymentProgramStage.APPROVE
    _projection = "approval-required"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _bounded_identity(self.approval_request_id, "approval_request_id")

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(approval_request_id=self.approval_request_id)


@dataclass(frozen=True, slots=True)
class DeploymentApprovalRejected(_Projection):
    approval_request_id: str
    approval_decision_id: str

    stage = DeploymentProgramStage.APPROVE
    _projection = "approval-rejected"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _bounded_identity(self.approval_request_id, "approval_request_id")
        _bounded_identity(self.approval_decision_id, "approval_decision_id")

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            approval_request_id=self.approval_request_id,
            approval_decision_id=self.approval_decision_id,
        )


@dataclass(frozen=True, slots=True)
class DeploymentReadinessRequired(_Projection):
    approval_request_id: str
    approval_decision_id: str
    activity_id: ActivityId

    stage = DeploymentProgramStage.ADMIT
    _projection = "readiness-required"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _bounded_identity(self.approval_request_id, "approval_request_id")
        _bounded_identity(self.approval_decision_id, "approval_decision_id")
        if type(self.activity_id) is not ActivityId:
            raise InvalidDeploymentProgramContract(
                "activity_id must be ActivityId"
            )

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            approval_request_id=self.approval_request_id,
            approval_decision_id=self.approval_decision_id,
            activity_id=self.activity_id.value,
        )


@dataclass(frozen=True, slots=True)
class DeploymentAdmissionReady(_Projection):
    approval_request_id: str
    approval_decision_id: str

    stage = DeploymentProgramStage.ADMIT
    _projection = "admission-ready"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _bounded_identity(self.approval_request_id, "approval_request_id")
        _bounded_identity(self.approval_decision_id, "approval_decision_id")

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            approval_request_id=self.approval_request_id,
            approval_decision_id=self.approval_decision_id,
        )


@dataclass(frozen=True, slots=True)
class DeploymentClaimReady(_Projection):
    execution_request_id: str

    stage = DeploymentProgramStage.CLAIM
    _projection = "claim-ready"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _bounded_identity(self.execution_request_id, "execution_request_id")

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(execution_request_id=self.execution_request_id)


@dataclass(frozen=True, slots=True)
class DeploymentExecutionStopped(_Projection):
    execution_request_id: str
    execution_request_status: ExecutionRequestStatus

    stage = DeploymentProgramStage.CLAIM
    _projection = "execution-stopped"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _bounded_identity(self.execution_request_id, "execution_request_id")
        _status(
            self.execution_request_status,
            ExecutionRequestStatus,
            (ExecutionRequestStatus.CANCELLED, ExecutionRequestStatus.ABANDONED),
            "execution_request_status",
        )

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            execution_request_id=self.execution_request_id,
            execution_request_status=self.execution_request_status.value,
        )


@dataclass(frozen=True, slots=True)
class DeploymentExecutionReady(_Projection):
    run_id: RunId

    stage = DeploymentProgramStage.EXECUTE
    _projection = "execution-ready"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _run_id(self.run_id)

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(run_id=self.run_id.value)


@dataclass(frozen=True, slots=True)
class DeploymentExecutionRunning(_Projection):
    run_id: RunId

    stage = DeploymentProgramStage.EXECUTE
    _projection = "execution-running"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _run_id(self.run_id)

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(run_id=self.run_id.value)


@dataclass(frozen=True, slots=True)
class DeploymentExecutionPaused(_Projection):
    run_id: RunId

    stage = DeploymentProgramStage.EXECUTE
    _projection = "execution-paused"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _run_id(self.run_id)

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(run_id=self.run_id.value)


@dataclass(frozen=True, slots=True)
class DeploymentEffectInFlight(_Projection):
    run_id: RunId
    run_status: ActivityRunStatus
    effect_attempt: EffectAttemptIdentity

    stage = DeploymentProgramStage.EXECUTE
    _projection = "effect-in-flight"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _live_attempt(self.run_id, self.run_status, self.effect_attempt)

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            run_id=self.run_id.value,
            run_status=self.run_status.value,
            effect_attempt=self.effect_attempt.descriptor(),
        )


@dataclass(frozen=True, slots=True)
class DeploymentRecoveryRequired(_Projection):
    run_id: RunId
    run_status: ActivityRunStatus
    effect_attempt: EffectAttemptIdentity

    stage = DeploymentProgramStage.EXECUTE
    _projection = "recovery-required"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _live_attempt(self.run_id, self.run_status, self.effect_attempt)

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            run_id=self.run_id.value,
            run_status=self.run_status.value,
            effect_attempt=self.effect_attempt.descriptor(),
        )


@dataclass(frozen=True, slots=True)
class DeploymentCompensationInProgress(_Projection):
    run_id: RunId

    stage = DeploymentProgramStage.EXECUTE
    _projection = "compensation-in-progress"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _run_id(self.run_id)

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(run_id=self.run_id.value)


@dataclass(frozen=True, slots=True)
class DeploymentExecutionFailed(_Projection):
    run_id: RunId
    run_status: ActivityRunStatus

    stage = DeploymentProgramStage.EXECUTE
    _projection = "execution-failed"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _run_id(self.run_id)
        _status(
            self.run_status,
            ActivityRunStatus,
            (ActivityRunStatus.FAILED,),
            "run_status",
        )

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            run_id=self.run_id.value,
            run_status=self.run_status.value,
        )


@dataclass(frozen=True, slots=True)
class DeploymentExecutionSettled(_Projection):
    run_id: RunId
    run_status: ActivityRunStatus

    stage = DeploymentProgramStage.EXECUTE
    _projection = "execution-settled"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _run_id(self.run_id)
        _status(
            self.run_status,
            ActivityRunStatus,
            (
                ActivityRunStatus.COMPENSATED,
                ActivityRunStatus.PARTIALLY_FAILED,
                ActivityRunStatus.UNCOMPENSATED_FAILURE,
                ActivityRunStatus.CANCELLED,
            ),
            "run_status",
        )

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(
            run_id=self.run_id.value,
            run_status=self.run_status.value,
        )


@dataclass(frozen=True, slots=True)
class DeploymentAdvancementReady(_Projection):
    run_id: RunId

    stage = DeploymentProgramStage.ADVANCE
    _projection = "advancement-ready"

    def __post_init__(self) -> None:
        _Projection.__post_init__(self)
        _run_id(self.run_id)

    def descriptor(self) -> dict[str, object]:
        return self._descriptor(run_id=self.run_id.value)


def _reference(value: object) -> None:
    if type(value) is not DeploymentProgramReference:
        raise InvalidDeploymentProgramContract(
            "reference must be DeploymentProgramReference"
        )


def _bounded_identity(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise InvalidDeploymentProgramContract(
            f"{field_name} must be nonempty bounded text"
        )


def _run_id(value: object) -> None:
    if type(value) is not RunId:
        raise InvalidDeploymentProgramContract("run_id must be RunId")


def _status(
    value: object,
    status_type: type,
    accepted: tuple[object, ...],
    field_name: str,
) -> None:
    if type(value) is not status_type or value not in accepted:
        raise InvalidDeploymentProgramContract(f"{field_name} is unsupported")


def _live_attempt(
    run_id: object,
    run_status: object,
    effect_attempt: object,
) -> None:
    _run_id(run_id)
    _status(
        run_status,
        ActivityRunStatus,
        (
            ActivityRunStatus.RUNNING,
            ActivityRunStatus.PAUSED,
            ActivityRunStatus.COMPENSATING,
        ),
        "run_status",
    )
    if type(effect_attempt) is not EffectAttemptIdentity:
        raise InvalidDeploymentProgramContract(
            "effect_attempt must be EffectAttemptIdentity"
        )
    if effect_attempt.run_id != run_id:
        raise InvalidDeploymentProgramContract(
            "effect_attempt and projection run identities differ"
        )


DeploymentProgramProjection: TypeAlias = (
    DeploymentCompleted
    | DeploymentSessionStopped
    | DeploymentPlanStopped
    | DeploymentNoChanges
    | DeploymentReviewBlocked
    | DeploymentApprovalRequestReady
    | DeploymentApprovalRequired
    | DeploymentApprovalRejected
    | DeploymentReadinessRequired
    | DeploymentAdmissionReady
    | DeploymentClaimReady
    | DeploymentExecutionStopped
    | DeploymentExecutionReady
    | DeploymentExecutionRunning
    | DeploymentExecutionPaused
    | DeploymentEffectInFlight
    | DeploymentRecoveryRequired
    | DeploymentCompensationInProgress
    | DeploymentExecutionFailed
    | DeploymentExecutionSettled
    | DeploymentAdvancementReady
)


__all__ = [
    "DeploymentProgramProjection",
    "DeploymentCompleted",
    "DeploymentSessionStopped",
    "DeploymentPlanStopped",
    "DeploymentNoChanges",
    "DeploymentReviewBlocked",
    "DeploymentApprovalRequestReady",
    "DeploymentApprovalRequired",
    "DeploymentApprovalRejected",
    "DeploymentReadinessRequired",
    "DeploymentAdmissionReady",
    "DeploymentClaimReady",
    "DeploymentExecutionStopped",
    "DeploymentExecutionReady",
    "DeploymentExecutionRunning",
    "DeploymentExecutionPaused",
    "DeploymentEffectInFlight",
    "DeploymentRecoveryRequired",
    "DeploymentCompensationInProgress",
    "DeploymentExecutionFailed",
    "DeploymentExecutionSettled",
    "DeploymentAdvancementReady",
]
