"""Pure recovery decisions, evidence context, and authorization policy.

Failure classification describes what execution observed.  A recovery decision
describes what an operator chooses to do next.  This module keeps those values
separate and performs no persistence or external effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from control_plane_kit.execution.values import ActivityRunStatus


class RecoveryValueError(ValueError):
    """Raised when a recovery value cannot represent a valid closed fact."""


class RecoveryScope(StrEnum):
    """Closed authorities for consequential recovery choices."""

    OPERATE = "recovery:operate"
    RESOLVE_UNCERTAINTY = "recovery:resolve-uncertainty"
    COMPENSATE = "recovery:compensate"
    ACCEPT_LOSS = "recovery:accept-loss"


@dataclass(frozen=True)
class RecoveryAuthority:
    """Authenticated operator and the recovery scopes proven for this command."""

    operator_id: str
    authority_reference: str
    scopes: tuple[RecoveryScope, ...]

    def __post_init__(self) -> None:
        _require_text("operator_id", self.operator_id)
        _require_text("authority_reference", self.authority_reference)
        if not all(isinstance(scope, RecoveryScope) for scope in self.scopes):
            raise TypeError("recovery scopes must be RecoveryScope values")
        object.__setattr__(self, "scopes", tuple(dict.fromkeys(self.scopes)))


@dataclass(frozen=True)
class ConfirmEffectSucceeded:
    """Resolve one uncertain leaf using independent success evidence."""

    activity_id: str

    def __post_init__(self) -> None:
        _require_text("activity_id", self.activity_id)


@dataclass(frozen=True)
class ConfirmEffectFailed:
    """Resolve one uncertain leaf using independent failure evidence."""

    activity_id: str

    def __post_init__(self) -> None:
        _require_text("activity_id", self.activity_id)


@dataclass(frozen=True)
class ResumeSameIntent:
    """Continue the exact admitted plan after its pause condition is resolved."""


@dataclass(frozen=True)
class RetryAsNewRun:
    """Request a separately admitted attempt of the unchanged plan."""


@dataclass(frozen=True)
class BeginCompensation:
    """Admit the compensation program pinned by the original plan."""


@dataclass(frozen=True)
class AcceptUncompensatedFailure:
    """Acknowledge visible loss without pretending the run was repaired."""


@dataclass(frozen=True)
class RemainPaused:
    """Record that no effectful recovery branch is currently authorized."""


RecoveryDecision: TypeAlias = (
    ConfirmEffectSucceeded
    | ConfirmEffectFailed
    | ResumeSameIntent
    | RetryAsNewRun
    | BeginCompensation
    | AcceptUncompensatedFailure
    | RemainPaused
)


@dataclass(frozen=True)
class RecoveryDecisionRecord:
    """Attributable operator choice before persistence assigns event identity."""

    decision_id: str
    decision: RecoveryDecision
    authority: RecoveryAuthority
    reason: str

    def __post_init__(self) -> None:
        _require_text("decision_id", self.decision_id)
        _require_text("reason", self.reason)
        if not isinstance(
            self.decision,
            (
                ConfirmEffectSucceeded,
                ConfirmEffectFailed,
                ResumeSameIntent,
                RetryAsNewRun,
                BeginCompensation,
                AcceptUncompensatedFailure,
                RemainPaused,
            ),
        ):
            raise TypeError("decision must be a RecoveryDecision")
        if not isinstance(self.authority, RecoveryAuthority):
            raise TypeError("authority must be RecoveryAuthority")


@dataclass(frozen=True)
class RecoveryContext:
    """Journal-derived facts used to interpret a proposed decision."""

    run_status: ActivityRunStatus
    uncertain_activity_ids: frozenset[str] = frozenset()
    compensation_available: bool = False
    intent_matches_admitted_plan: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.run_status, ActivityRunStatus):
            raise TypeError("run_status must be ActivityRunStatus")
        if not all(
            isinstance(value, str) and bool(value.strip())
            for value in self.uncertain_activity_ids
        ):
            raise RecoveryValueError("uncertain activity ids must be non-empty text")
        if type(self.compensation_available) is not bool:
            raise TypeError("compensation_available must be bool")
        if type(self.intent_matches_admitted_plan) is not bool:
            raise TypeError("intent_matches_admitted_plan must be bool")


class RecoveryDecisionRejected(RecoveryValueError):
    """Raised when journal-derived facts do not admit a recovery decision."""


class RecoveryAuthorizationDenied(RecoveryValueError):
    """Raised when a legal decision lacks its required recovery authority."""


def validate_recovery_decision(
    decision: RecoveryDecision,
    context: RecoveryContext,
) -> None:
    """Reject a decision that is inconsistent with canonical journal evidence."""

    if not context.intent_matches_admitted_plan and not isinstance(decision, RemainPaused):
        raise RecoveryDecisionRejected(
            "changed intent requires a fresh graph plan and approval"
        )

    match decision:
        case ConfirmEffectSucceeded(activity_id=activity_id) | ConfirmEffectFailed(
            activity_id=activity_id
        ):
            if context.run_status is not ActivityRunStatus.PAUSED:
                raise RecoveryDecisionRejected(
                    "uncertainty may be resolved only while the run is paused"
                )
            if activity_id not in context.uncertain_activity_ids:
                raise RecoveryDecisionRejected(
                    "the selected activity has no unresolved uncertainty"
                )
        case ResumeSameIntent():
            if context.run_status is not ActivityRunStatus.PAUSED:
                raise RecoveryDecisionRejected("only a paused run may resume")
            if context.uncertain_activity_ids:
                raise RecoveryDecisionRejected(
                    "unresolved uncertainty must be decided before resume"
                )
        case RetryAsNewRun():
            _require_failed_run(context)
            _require_no_uncertainty(context)
        case BeginCompensation():
            _require_failed_run(context)
            _require_no_uncertainty(context)
            if not context.compensation_available:
                raise RecoveryDecisionRejected(
                    "the admitted plan has no available compensation"
                )
        case AcceptUncompensatedFailure():
            _require_failed_run(context)
            _require_no_uncertainty(context)
        case RemainPaused():
            if context.run_status not in {
                ActivityRunStatus.PAUSED,
                ActivityRunStatus.FAILED,
                ActivityRunStatus.PARTIALLY_FAILED,
            }:
                raise RecoveryDecisionRejected(
                    "only paused or failed runs may remain paused for recovery"
                )


def authorize_recovery_decision(
    decision: RecoveryDecision,
    authority: RecoveryAuthority,
) -> None:
    """Require the closed authority associated with one legal decision."""

    match decision:
        case ConfirmEffectSucceeded() | ConfirmEffectFailed():
            required = RecoveryScope.RESOLVE_UNCERTAINTY
        case BeginCompensation():
            required = RecoveryScope.COMPENSATE
        case AcceptUncompensatedFailure():
            required = RecoveryScope.ACCEPT_LOSS
        case ResumeSameIntent() | RetryAsNewRun() | RemainPaused():
            required = RecoveryScope.OPERATE
    if required not in authority.scopes:
        raise RecoveryAuthorizationDenied(
            f"recovery decision requires {required.value} authority"
        )


def _require_failed_run(context: RecoveryContext) -> None:
    if context.run_status not in {
        ActivityRunStatus.FAILED,
        ActivityRunStatus.PARTIALLY_FAILED,
    }:
        raise RecoveryDecisionRejected("decision requires a failed run")


def _require_no_uncertainty(context: RecoveryContext) -> None:
    if context.uncertain_activity_ids:
        raise RecoveryDecisionRejected(
            "unresolved uncertainty blocks retry, compensation, and acceptance"
        )


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryValueError(f"{name} must be non-empty text")
