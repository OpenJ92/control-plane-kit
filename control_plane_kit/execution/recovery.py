"""Pure recovery decisions, evidence context, and authorization policy.

Failure classification describes what execution observed.  A recovery decision
describes what an operator chooses to do next.  This module keeps those values
separate and performs no persistence or external effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Mapping
from typing import TypeAlias

from control_plane_kit.execution.values import ActivityRunStatus, MAX_EVIDENCE_TEXT


class RecoveryValueError(ValueError):
    """Raised when a recovery value cannot represent a valid closed fact."""


class UnknownRecoveryVariant(RecoveryValueError):
    """Raised when recovery descriptor data names an unknown closed member."""


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
        if len(self.reason) > MAX_EVIDENCE_TEXT:
            raise RecoveryValueError(
                f"reason must not exceed {MAX_EVIDENCE_TEXT} characters"
            )
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

    def descriptor(self) -> dict[str, object]:
        """Return strict secret-free data suitable for an ActivityEvent payload."""

        return {
            "decision_id": self.decision_id,
            "decision": _decision_descriptor(self.decision),
            "operator_id": self.authority.operator_id,
            "authority_reference": self.authority.authority_reference,
            "scopes": [scope.value for scope in self.authority.scopes],
            "reason": self.reason,
        }


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


def recovery_decision_record_from_descriptor(
    value: Mapping[str, object],
) -> RecoveryDecisionRecord:
    """Decode the one closed nested recovery descriptor language."""

    expected = {
        "decision_id",
        "decision",
        "operator_id",
        "authority_reference",
        "scopes",
        "reason",
    }
    if set(value) != expected:
        raise RecoveryValueError("recovery decision fields do not match schema")
    scopes = value["scopes"]
    if not isinstance(scopes, list):
        raise RecoveryValueError("recovery scopes must be a list")
    try:
        authority = RecoveryAuthority(
            _descriptor_text(value, "operator_id"),
            _descriptor_text(value, "authority_reference"),
            tuple(RecoveryScope(scope) for scope in scopes),
        )
    except (TypeError, ValueError) as error:
        raise UnknownRecoveryVariant(
            "recovery descriptor contains unknown scope"
        ) from error
    decision_value = value["decision"]
    if not isinstance(decision_value, Mapping):
        raise RecoveryValueError("recovery decision must be an object")
    return RecoveryDecisionRecord(
        _descriptor_text(value, "decision_id"),
        _decision_from_descriptor(decision_value),
        authority,
        _descriptor_text(value, "reason"),
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


def _decision_descriptor(value: RecoveryDecision) -> dict[str, object]:
    match value:
        case ConfirmEffectSucceeded(activity_id=activity_id):
            return {"kind": "confirm-effect-succeeded", "activity_id": activity_id}
        case ConfirmEffectFailed(activity_id=activity_id):
            return {"kind": "confirm-effect-failed", "activity_id": activity_id}
        case ResumeSameIntent():
            return {"kind": "resume-same-intent"}
        case RetryAsNewRun():
            return {"kind": "retry-as-new-run"}
        case BeginCompensation():
            return {"kind": "begin-compensation"}
        case AcceptUncompensatedFailure():
            return {"kind": "accept-uncompensated-failure"}
        case RemainPaused():
            return {"kind": "remain-paused"}


def _decision_from_descriptor(value: Mapping[str, object]) -> RecoveryDecision:
    kind = _descriptor_text(value, "kind")
    if kind in {"confirm-effect-succeeded", "confirm-effect-failed"}:
        if set(value) != {"kind", "activity_id"}:
            raise RecoveryValueError("uncertainty decision fields do not match schema")
        activity_id = _descriptor_text(value, "activity_id")
        return (
            ConfirmEffectSucceeded(activity_id)
            if kind == "confirm-effect-succeeded"
            else ConfirmEffectFailed(activity_id)
        )
    if set(value) != {"kind"}:
        raise RecoveryValueError("recovery decision fields do not match schema")
    match kind:
        case "resume-same-intent":
            return ResumeSameIntent()
        case "retry-as-new-run":
            return RetryAsNewRun()
        case "begin-compensation":
            return BeginCompensation()
        case "accept-uncompensated-failure":
            return AcceptUncompensatedFailure()
        case "remain-paused":
            return RemainPaused()
        case _:
            raise UnknownRecoveryVariant(f"unknown recovery decision {kind!r}")


def _descriptor_text(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise RecoveryValueError(f"{key} must be non-empty text")
    return result
