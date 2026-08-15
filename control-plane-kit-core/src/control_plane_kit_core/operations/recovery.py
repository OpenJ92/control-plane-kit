"""Pure effect-attempt recovery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Mapping

from control_plane_kit_core._activity_identity import (
    _is_canonical_activity_identity,
)
from control_plane_kit_core.operations.run_identity import RunId


_MAX_PUBLIC_TEXT_LENGTH = 256
_MAX_EFFECT_ATTEMPT = 2_147_483_647
_MAX_EFFECT_FENCE_GENERATION = 9_223_372_036_854_775_807


class InvalidEffectRecoveryContract(ValueError):
    """Raised when effect-attempt recovery contract data is incoherent."""


class EffectAttemptStatus(StrEnum):
    """Closed durable states for one external-effect attempt."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"
    ABANDONED = "abandoned"


class EffectAttemptTransitionKind(StrEnum):
    """Closed transition vocabulary for effect-attempt folding."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"
    RECONCILED = "reconciled"
    ABANDONED = "abandoned"


class EffectRecoveryResolution(StrEnum):
    """Closed outcomes available to explicit effect recovery."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class EffectAttemptIdentity:
    """Stable identity for one activity's external-effect attempt."""

    run_id: RunId
    activity_id: str
    attempt: int

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId:
            raise InvalidEffectRecoveryContract("run_id must be RunId")
        if not _is_canonical_activity_identity(self.activity_id):
            raise InvalidEffectRecoveryContract("activity_id is malformed")
        _bounded_positive_int(
            self.attempt,
            "attempt",
            _MAX_EFFECT_ATTEMPT,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "run_id": self.run_id.value,
            "activity_id": self.activity_id,
            "attempt": self.attempt,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "EffectAttemptIdentity":
        _strict_keys(value, {"run_id", "activity_id", "attempt"}, "identity")
        run_id_text = _text(value["run_id"], "run_id")
        try:
            run_id = RunId(run_id_text)
        except ValueError:
            run_id = None
        if run_id is None:
            raise InvalidEffectRecoveryContract("run_id is malformed")
        return cls(
            run_id=run_id,
            activity_id=_text(value["activity_id"], "activity_id"),
            attempt=_bounded_int(
                value["attempt"],
                "attempt",
                _MAX_EFFECT_ATTEMPT,
            ),
        )


@dataclass(frozen=True)
class EffectAttemptFence:
    """Worker ownership plus monotonic lease generation."""

    worker_id: str
    generation: int

    def __post_init__(self) -> None:
        _bounded_text(self.worker_id, "worker_id")
        _bounded_positive_int(
            self.generation,
            "generation",
            _MAX_EFFECT_FENCE_GENERATION,
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "generation": self.generation,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "EffectAttemptFence":
        _strict_keys(value, {"worker_id", "generation"}, "fence")
        return cls(
            worker_id=_text(value["worker_id"], "worker_id"),
            generation=_bounded_int(
                value["generation"],
                "generation",
                _MAX_EFFECT_FENCE_GENERATION,
            ),
        )


@dataclass(frozen=True)
class EffectRecoveryDecision:
    """Explicit evidence resolving or abandoning one uncertain attempt."""

    decision_id: str
    attempt_identity: EffectAttemptIdentity
    resolution: EffectRecoveryResolution
    uncertain_fingerprint: str
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _bounded_text(self.decision_id, "decision_id")
        if not isinstance(self.attempt_identity, EffectAttemptIdentity):
            raise InvalidEffectRecoveryContract(
                "attempt_identity must be EffectAttemptIdentity"
            )
        if not isinstance(self.resolution, EffectRecoveryResolution):
            raise InvalidEffectRecoveryContract(
                "resolution must be EffectRecoveryResolution"
            )
        _sha256_fingerprint(
            self.uncertain_fingerprint,
            "uncertain_fingerprint",
        )
        _sha256_fingerprint(self.evidence_fingerprint, "evidence_fingerprint")

    def descriptor(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "attempt_identity": self.attempt_identity.descriptor(),
            "resolution": self.resolution.value,
            "uncertain_fingerprint": self.uncertain_fingerprint,
            "evidence_fingerprint": self.evidence_fingerprint,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "EffectRecoveryDecision":
        _strict_keys(
            value,
            {
                "decision_id",
                "attempt_identity",
                "resolution",
                "uncertain_fingerprint",
                "evidence_fingerprint",
            },
            "recovery decision",
        )
        try:
            resolution = EffectRecoveryResolution(
                _text(value["resolution"], "resolution")
            )
        except ValueError as error:
            raise InvalidEffectRecoveryContract(str(error)) from error
        return cls(
            decision_id=_text(value["decision_id"], "decision_id"),
            attempt_identity=EffectAttemptIdentity.from_descriptor(
                _mapping(value["attempt_identity"], "attempt_identity")
            ),
            resolution=resolution,
            uncertain_fingerprint=_text(
                value["uncertain_fingerprint"],
                "uncertain_fingerprint",
            ),
            evidence_fingerprint=_text(
                value["evidence_fingerprint"],
                "evidence_fingerprint",
            ),
        )


@dataclass(frozen=True)
class EffectAttemptTransition:
    """One proposed transition in the effect-attempt state machine."""

    kind: EffectAttemptTransitionKind
    identity: EffectAttemptIdentity
    request_fingerprint: str | None = None
    outcome_fingerprint: str | None = None
    prior_attempt: EffectAttemptIdentity | None = None
    recovery_decision: EffectRecoveryDecision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EffectAttemptTransitionKind):
            raise InvalidEffectRecoveryContract(
                "kind must be EffectAttemptTransitionKind"
            )
        if not isinstance(self.identity, EffectAttemptIdentity):
            raise InvalidEffectRecoveryContract(
                "identity must be EffectAttemptIdentity"
            )
        if self.kind is EffectAttemptTransitionKind.STARTED:
            _sha256_fingerprint(self.request_fingerprint, "request_fingerprint")
            _validate_retry_lineage(self.identity, self.prior_attempt)
            _require_none(self.outcome_fingerprint, "outcome_fingerprint")
            _require_none(self.recovery_decision, "recovery_decision")
            return
        _require_none(self.request_fingerprint, "request_fingerprint")
        _require_none(self.prior_attempt, "prior_attempt")
        if self.kind in _DIRECT_RESULT_TRANSITIONS:
            _sha256_fingerprint(self.outcome_fingerprint, "outcome_fingerprint")
            _require_none(self.recovery_decision, "recovery_decision")
            return
        _require_none(self.outcome_fingerprint, "outcome_fingerprint")
        if not isinstance(self.recovery_decision, EffectRecoveryDecision):
            raise InvalidEffectRecoveryContract(
                "recovery transition requires EffectRecoveryDecision"
            )
        if self.recovery_decision.attempt_identity != self.identity:
            raise InvalidEffectRecoveryContract(
                "recovery decision must identify the transitioned attempt"
            )
        if (
            self.kind is EffectAttemptTransitionKind.ABANDONED
            and self.recovery_decision.resolution
            is not EffectRecoveryResolution.ABANDONED
        ):
            raise InvalidEffectRecoveryContract(
                "abandoned transition requires abandoned resolution"
            )
        if (
            self.kind is EffectAttemptTransitionKind.RECONCILED
            and self.recovery_decision.resolution
            is EffectRecoveryResolution.ABANDONED
        ):
            raise InvalidEffectRecoveryContract(
                "reconciled transition requires succeeded or failed resolution"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "identity": self.identity.descriptor(),
            "request_fingerprint": self.request_fingerprint,
            "outcome_fingerprint": self.outcome_fingerprint,
            "prior_attempt": (
                self.prior_attempt.descriptor()
                if self.prior_attempt is not None
                else None
            ),
            "recovery_decision": (
                self.recovery_decision.descriptor()
                if self.recovery_decision is not None
                else None
            ),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "EffectAttemptTransition":
        _strict_keys(
            value,
            {
                "kind",
                "identity",
                "request_fingerprint",
                "outcome_fingerprint",
                "prior_attempt",
                "recovery_decision",
            },
            "transition",
        )
        try:
            kind = EffectAttemptTransitionKind(_text(value["kind"], "kind"))
        except ValueError as error:
            raise InvalidEffectRecoveryContract(str(error)) from error
        return cls(
            kind=kind,
            identity=EffectAttemptIdentity.from_descriptor(
                _mapping(value["identity"], "identity")
            ),
            request_fingerprint=_optional_text(
                value["request_fingerprint"],
                "request_fingerprint",
            ),
            outcome_fingerprint=_optional_text(
                value["outcome_fingerprint"],
                "outcome_fingerprint",
            ),
            prior_attempt=_optional_identity(value["prior_attempt"]),
            recovery_decision=_optional_decision(value["recovery_decision"]),
        )


@dataclass(frozen=True)
class EffectAttemptState:
    """Current durable coordination truth for one effect attempt."""

    identity: EffectAttemptIdentity
    request_fingerprint: str
    fence: EffectAttemptFence
    status: EffectAttemptStatus
    outcome_fingerprint: str | None = None
    prior_attempt: EffectAttemptIdentity | None = None
    recovery_decision: EffectRecoveryDecision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, EffectAttemptIdentity):
            raise InvalidEffectRecoveryContract(
                "identity must be EffectAttemptIdentity"
            )
        _sha256_fingerprint(self.request_fingerprint, "request_fingerprint")
        if not isinstance(self.fence, EffectAttemptFence):
            raise InvalidEffectRecoveryContract("fence must be EffectAttemptFence")
        if not isinstance(self.status, EffectAttemptStatus):
            raise InvalidEffectRecoveryContract(
                "status must be EffectAttemptStatus"
            )
        _validate_retry_lineage(self.identity, self.prior_attempt)
        if self.status is EffectAttemptStatus.STARTED:
            _require_none(self.outcome_fingerprint, "outcome_fingerprint")
            _require_none(self.recovery_decision, "recovery_decision")
            return
        _sha256_fingerprint(self.outcome_fingerprint, "outcome_fingerprint")
        if self.recovery_decision is None:
            if self.status is EffectAttemptStatus.ABANDONED:
                raise InvalidEffectRecoveryContract(
                    "abandoned state requires recovery decision"
                )
            return
        if not isinstance(self.recovery_decision, EffectRecoveryDecision):
            raise InvalidEffectRecoveryContract(
                "recovery_decision must be EffectRecoveryDecision"
            )
        if self.recovery_decision.attempt_identity != self.identity:
            raise InvalidEffectRecoveryContract(
                "recovery decision must identify the state attempt"
            )
        expected_status = _STATUS_BY_RECOVERY_RESOLUTION[
            self.recovery_decision.resolution
        ]
        if self.status is not expected_status:
            raise InvalidEffectRecoveryContract(
                "recovery resolution does not match attempt status"
            )
        if self.outcome_fingerprint != self.recovery_decision.evidence_fingerprint:
            raise InvalidEffectRecoveryContract(
                "recovered outcome must retain the decision evidence fingerprint"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "identity": self.identity.descriptor(),
            "request_fingerprint": self.request_fingerprint,
            "fence": self.fence.descriptor(),
            "status": self.status.value,
            "outcome_fingerprint": self.outcome_fingerprint,
            "prior_attempt": (
                self.prior_attempt.descriptor()
                if self.prior_attempt is not None
                else None
            ),
            "recovery_decision": (
                self.recovery_decision.descriptor()
                if self.recovery_decision is not None
                else None
            ),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "EffectAttemptState":
        _strict_keys(
            value,
            {
                "identity",
                "request_fingerprint",
                "fence",
                "status",
                "outcome_fingerprint",
                "prior_attempt",
                "recovery_decision",
            },
            "state",
        )
        try:
            status = EffectAttemptStatus(_text(value["status"], "status"))
        except ValueError as error:
            raise InvalidEffectRecoveryContract(str(error)) from error
        return cls(
            identity=EffectAttemptIdentity.from_descriptor(
                _mapping(value["identity"], "identity")
            ),
            request_fingerprint=_text(
                value["request_fingerprint"],
                "request_fingerprint",
            ),
            fence=EffectAttemptFence.from_descriptor(
                _mapping(value["fence"], "fence")
            ),
            status=status,
            outcome_fingerprint=_optional_text(
                value["outcome_fingerprint"],
                "outcome_fingerprint",
            ),
            prior_attempt=_optional_identity(value["prior_attempt"]),
            recovery_decision=_optional_decision(value["recovery_decision"]),
        )


def fold_effect_attempt(
    state: EffectAttemptState | None,
    transition: EffectAttemptTransition,
    *,
    fence: EffectAttemptFence,
) -> EffectAttemptState:
    """Fold one durable transition under the active ownership fence."""

    if not isinstance(transition, EffectAttemptTransition):
        raise InvalidEffectRecoveryContract(
            "transition must be EffectAttemptTransition"
        )
    if not isinstance(fence, EffectAttemptFence):
        raise InvalidEffectRecoveryContract("fence must be EffectAttemptFence")
    if state is None:
        if transition.kind is not EffectAttemptTransitionKind.STARTED:
            raise InvalidEffectRecoveryContract(
                "first effect-attempt transition must be started"
            )
        return EffectAttemptState(
            identity=transition.identity,
            request_fingerprint=transition.request_fingerprint,
            fence=fence,
            status=EffectAttemptStatus.STARTED,
            prior_attempt=transition.prior_attempt,
        )
    if not isinstance(state, EffectAttemptState):
        raise InvalidEffectRecoveryContract("state must be EffectAttemptState")
    if transition.identity != state.identity:
        raise InvalidEffectRecoveryContract(
            "transition identity does not match attempt state"
        )
    if fence != state.fence:
        raise InvalidEffectRecoveryContract(
            "effect-attempt transition rejected by active fence"
        )
    if _is_exact_duplicate(state, transition):
        return state
    if transition.kind is EffectAttemptTransitionKind.STARTED:
        raise InvalidEffectRecoveryContract("attempt has already started")
    if transition.kind in _RECOVERY_TRANSITIONS:
        if state.status is not EffectAttemptStatus.UNCERTAIN:
            raise InvalidEffectRecoveryContract(
                "recovery requires an uncertain attempt"
            )
        decision = transition.recovery_decision
        if decision.uncertain_fingerprint != state.outcome_fingerprint:
            raise InvalidEffectRecoveryContract(
                "recovery decision does not identify the uncertain evidence"
            )
        return EffectAttemptState(
            identity=state.identity,
            request_fingerprint=state.request_fingerprint,
            fence=state.fence,
            status=_STATUS_BY_RECOVERY_RESOLUTION[decision.resolution],
            outcome_fingerprint=decision.evidence_fingerprint,
            prior_attempt=state.prior_attempt,
            recovery_decision=decision,
        )
    if state.status is not EffectAttemptStatus.STARTED:
        raise InvalidEffectRecoveryContract(
            f"cannot fold {transition.kind.value} from {state.status.value}"
        )
    return EffectAttemptState(
        identity=state.identity,
        request_fingerprint=state.request_fingerprint,
        fence=state.fence,
        status=_STATUS_BY_DIRECT_TRANSITION[transition.kind],
        outcome_fingerprint=transition.outcome_fingerprint,
        prior_attempt=state.prior_attempt,
    )


_DIRECT_RESULT_TRANSITIONS = frozenset(
    {
        EffectAttemptTransitionKind.SUCCEEDED,
        EffectAttemptTransitionKind.FAILED,
        EffectAttemptTransitionKind.UNSUPPORTED,
        EffectAttemptTransitionKind.UNCERTAIN,
    }
)
_RECOVERY_TRANSITIONS = frozenset(
    {
        EffectAttemptTransitionKind.RECONCILED,
        EffectAttemptTransitionKind.ABANDONED,
    }
)
_STATUS_BY_DIRECT_TRANSITION = {
    EffectAttemptTransitionKind.SUCCEEDED: EffectAttemptStatus.SUCCEEDED,
    EffectAttemptTransitionKind.FAILED: EffectAttemptStatus.FAILED,
    EffectAttemptTransitionKind.UNSUPPORTED: EffectAttemptStatus.UNSUPPORTED,
    EffectAttemptTransitionKind.UNCERTAIN: EffectAttemptStatus.UNCERTAIN,
}
_STATUS_BY_RECOVERY_RESOLUTION = {
    EffectRecoveryResolution.SUCCEEDED: EffectAttemptStatus.SUCCEEDED,
    EffectRecoveryResolution.FAILED: EffectAttemptStatus.FAILED,
    EffectRecoveryResolution.ABANDONED: EffectAttemptStatus.ABANDONED,
}


def _is_exact_duplicate(
    state: EffectAttemptState,
    transition: EffectAttemptTransition,
) -> bool:
    if transition.kind is EffectAttemptTransitionKind.STARTED:
        return (
            state.status is EffectAttemptStatus.STARTED
            and state.request_fingerprint == transition.request_fingerprint
            and state.prior_attempt == transition.prior_attempt
        )
    if transition.kind in _DIRECT_RESULT_TRANSITIONS:
        return (
            state.status is _STATUS_BY_DIRECT_TRANSITION[transition.kind]
            and state.outcome_fingerprint == transition.outcome_fingerprint
            and state.recovery_decision is None
        )
    decision = transition.recovery_decision
    return (
        state.status is _STATUS_BY_RECOVERY_RESOLUTION[decision.resolution]
        and state.outcome_fingerprint == decision.evidence_fingerprint
        and state.recovery_decision == decision
    )


def _validate_retry_lineage(
    identity: EffectAttemptIdentity,
    prior_attempt: EffectAttemptIdentity | None,
) -> None:
    if identity.attempt == 1:
        if prior_attempt is not None:
            raise InvalidEffectRecoveryContract(
                "first attempt cannot declare prior attempt"
            )
        return
    if not isinstance(prior_attempt, EffectAttemptIdentity):
        raise InvalidEffectRecoveryContract("retry attempt requires prior attempt")
    if (
        prior_attempt.run_id != identity.run_id
        or prior_attempt.activity_id != identity.activity_id
        or prior_attempt.attempt != identity.attempt - 1
    ):
        raise InvalidEffectRecoveryContract(
            "retry lineage must reference the immediately prior attempt"
        )


def _strict_keys(
    value: Mapping[str, object],
    expected: set[str],
    subject: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise InvalidEffectRecoveryContract(
            f"{subject} descriptor has unexpected keys"
        )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidEffectRecoveryContract(f"{field} must be a mapping")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidEffectRecoveryContract(f"{field} must be text")
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _bounded_text(value: object, field: str) -> None:
    valid = (
        isinstance(value, str)
        and bool(str.strip(value))
        and str.__len__(value) <= _MAX_PUBLIC_TEXT_LENGTH
        and not str.__contains__(value, "\x00")
    )
    if valid:
        try:
            str.encode(value, "utf-8")
        except UnicodeEncodeError:
            valid = False
    if not valid:
        raise InvalidEffectRecoveryContract(
            f"{field} must be non-empty PostgreSQL-compatible text of at most "
            f"{_MAX_PUBLIC_TEXT_LENGTH} characters"
        )


def _bounded_positive_int(value: object, field: str, maximum: int) -> None:
    _bounded_int(value, field, maximum)


def _bounded_int(value: object, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise InvalidEffectRecoveryContract(
            f"{field} must be an integer from 1 through {maximum}"
        )
    return value


def _sha256_fingerprint(value: object, field: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise InvalidEffectRecoveryContract(
            f"{field} must be a lowercase sha256 fingerprint"
        )


def _require_none(value: object, field: str) -> None:
    if value is not None:
        raise InvalidEffectRecoveryContract(f"{field} is not allowed")


def _optional_identity(value: object) -> EffectAttemptIdentity | None:
    if value is None:
        return None
    return EffectAttemptIdentity.from_descriptor(_mapping(value, "prior_attempt"))


def _optional_decision(value: object) -> EffectRecoveryDecision | None:
    if value is None:
        return None
    return EffectRecoveryDecision.from_descriptor(
        _mapping(value, "recovery_decision")
    )
