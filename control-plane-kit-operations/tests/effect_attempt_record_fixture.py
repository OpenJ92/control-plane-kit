from __future__ import annotations

import hashlib
import importlib
import json

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    RunId,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureEvidence,
)


EFFECT_ATTEMPTS_MODULE = "control_plane_kit_operations.effect_attempts"
REQUEST_FINGERPRINT = "a" * 64
OUTCOME_FINGERPRINT = "b" * 64
UNCERTAIN_FINGERPRINT = "c" * 64
STORIES = (
    "started",
    "succeeded",
    "failed",
    "unsupported",
    "uncertain",
    "recovered-succeeded",
    "recovered-failed",
    "abandoned",
)


def _load_effect_attempts_module(import_module=importlib.import_module):
    try:
        return import_module(EFFECT_ATTEMPTS_MODULE)
    except ModuleNotFoundError as error:
        if error.name != EFFECT_ATTEMPTS_MODULE:
            raise
        return None


effect_attempts_module = _load_effect_attempts_module()
EffectAttemptEventEvidence = getattr(
    effect_attempts_module,
    "EffectAttemptEventEvidence",
    None,
)
EffectAttemptRecord = getattr(effect_attempts_module, "EffectAttemptRecord", None)
effect_attempt_state_fingerprint = getattr(
    effect_attempts_module,
    "effect_attempt_state_fingerprint",
    None,
)


class HostileEffectAttemptState(EffectAttemptState):
    pass


class HostileActivityEventRecord(ActivityEventRecord):
    pass


class HostileBoundedEvidence(BoundedEvidence):
    pass


class HostileEffectAttemptIdentity(EffectAttemptIdentity):
    pass


class HostileEffectAttemptFence(EffectAttemptFence):
    pass


class HostileEffectRecoveryDecision(EffectRecoveryDecision):
    pass


class HostileFailureEvidence(FailureEvidence):
    pass


class HostileInt(int):
    pass


class HostileStr(str):
    pass


def canonical_state_fingerprint(state: EffectAttemptState) -> str:
    canonical = json.dumps(
        state.descriptor(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class EffectAttemptRecordFixture:
    maxDiff = None

    def require_language(self) -> None:
        required = {
            "EffectAttemptEventEvidence": EffectAttemptEventEvidence,
            "EffectAttemptRecord": EffectAttemptRecord,
            "effect_attempt_state_fingerprint": effect_attempt_state_fingerprint,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "effect-attempt Operations record language is missing",
        )

    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 256)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def identity(
        self,
        *,
        attempt: int = 1,
        run_id: str = "run-a",
        activity_id: str = "activity-a",
    ) -> EffectAttemptIdentity:
        return EffectAttemptIdentity(RunId(run_id), activity_id, attempt)

    def state(
        self,
        story: str = "started",
        *,
        attempt: int = 1,
        run_id: str = "run-a",
        activity_id: str = "activity-a",
    ) -> EffectAttemptState:
        identity = self.identity(
            attempt=attempt,
            run_id=run_id,
            activity_id=activity_id,
        )
        prior_attempt = (
            self.identity(
                attempt=attempt - 1,
                run_id=run_id,
                activity_id=activity_id,
            )
            if attempt > 1
            else None
        )
        status = {
            "started": EffectAttemptStatus.STARTED,
            "succeeded": EffectAttemptStatus.SUCCEEDED,
            "failed": EffectAttemptStatus.FAILED,
            "unsupported": EffectAttemptStatus.UNSUPPORTED,
            "uncertain": EffectAttemptStatus.UNCERTAIN,
            "recovered-succeeded": EffectAttemptStatus.SUCCEEDED,
            "recovered-failed": EffectAttemptStatus.FAILED,
            "abandoned": EffectAttemptStatus.ABANDONED,
        }[story]
        recovery = None
        if story.startswith("recovered-") or story == "abandoned":
            resolution = {
                "recovered-succeeded": EffectRecoveryResolution.SUCCEEDED,
                "recovered-failed": EffectRecoveryResolution.FAILED,
                "abandoned": EffectRecoveryResolution.ABANDONED,
            }[story]
            recovery = EffectRecoveryDecision(
                "decision-a",
                identity,
                resolution,
                UNCERTAIN_FINGERPRINT,
                OUTCOME_FINGERPRINT,
            )
        outcome = None if story == "started" else OUTCOME_FINGERPRINT
        if story == "uncertain":
            outcome = UNCERTAIN_FINGERPRINT
        return EffectAttemptState(
            identity=identity,
            request_fingerprint=REQUEST_FINGERPRINT,
            fence=EffectAttemptFence("worker-a", 7),
            status=status,
            outcome_fingerprint=outcome,
            prior_attempt=prior_attempt,
            recovery_decision=recovery,
        )

    def started_state(self, state: EffectAttemptState) -> EffectAttemptState:
        return EffectAttemptState(
            identity=state.identity,
            request_fingerprint=state.request_fingerprint,
            fence=state.fence,
            status=EffectAttemptStatus.STARTED,
            prior_attempt=state.prior_attempt,
        )

    def evidence_for(self, state: EffectAttemptState) -> BoundedEvidence:
        return BoundedEvidence.from_mapping(
            {
                "effect_attempt": {
                    "attempt": state.identity.attempt,
                    "state_fingerprint": canonical_state_fingerprint(state),
                }
            }
        )

    def event_kind(
        self,
        story: str,
        *,
        compensation: bool,
    ) -> ActivityEventKind:
        names = {
            "started": "STEP_COMPENSATION_STARTED"
            if compensation
            else "STEP_STARTED",
            "succeeded": "STEP_COMPENSATION_SUCCEEDED"
            if compensation
            else "STEP_SUCCEEDED",
            "failed": "STEP_COMPENSATION_FAILED"
            if compensation
            else "STEP_FAILED",
            "unsupported": "STEP_COMPENSATION_UNSUPPORTED"
            if compensation
            else "STEP_UNSUPPORTED",
            "uncertain": "STEP_COMPENSATION_UNCERTAIN"
            if compensation
            else "STEP_UNCERTAIN",
            "recovered-succeeded": (
                "STEP_COMPENSATION_UNCERTAINTY_RESOLVED_SUCCEEDED"
                if compensation
                else "STEP_UNCERTAINTY_RESOLVED_SUCCEEDED"
            ),
            "recovered-failed": (
                "STEP_COMPENSATION_UNCERTAINTY_RESOLVED_FAILED"
                if compensation
                else "STEP_UNCERTAINTY_RESOLVED_FAILED"
            ),
            "abandoned": "STEP_COMPENSATION_UNCERTAINTY_ABANDONED"
            if compensation
            else "STEP_UNCERTAINTY_ABANDONED",
        }
        return getattr(ActivityEventKind, names[story])

    def event(
        self,
        state: EffectAttemptState,
        kind: ActivityEventKind,
        *,
        event_id: str,
        ordinal: int,
        occurred_at: str,
        evidence: BoundedEvidence | None = None,
        run_id: str | None = None,
        activity_id: str | None = None,
    ) -> ActivityEventRecord:
        return ActivityEventRecord(
            event_id=event_id,
            run_id=run_id or state.identity.run_id.value,
            ordinal=ordinal,
            kind=kind,
            occurred_at=occurred_at,
            activity_id=activity_id or state.identity.activity_id,
            evidence=evidence or self.evidence_for(state),
        )

    def record(
        self,
        story: str = "started",
        *,
        compensation: bool = False,
        attempt: int = 1,
        run_id: str = "run-a",
        activity_id: str = "activity-a",
        event_prefix: str = "event",
        original_ordinal: int = 3,
        latest_ordinal: int = 7,
        original_time: str = "2030-01-01T00:00:02.000000Z",
        latest_time: str = "2030-01-01T00:00:01.000000Z",
    ):
        self.require_language()
        state = self.state(
            story,
            attempt=attempt,
            run_id=run_id,
            activity_id=activity_id,
        )
        original = self.event(
            self.started_state(state),
            self.event_kind("started", compensation=compensation),
            event_id=f"{event_prefix}-start",
            ordinal=original_ordinal,
            occurred_at=original_time,
        )
        latest = original
        if story != "started":
            latest = self.event(
                state,
                self.event_kind(story, compensation=compensation),
                event_id=f"{event_prefix}-latest",
                ordinal=latest_ordinal,
                occurred_at=latest_time,
            )
        return EffectAttemptRecord(state, original, latest)


__all__ = [
    "EffectAttemptEventEvidence",
    "EffectAttemptRecord",
    "EffectAttemptRecordFixture",
    "HostileActivityEventRecord",
    "HostileBoundedEvidence",
    "HostileEffectAttemptFence",
    "HostileEffectAttemptIdentity",
    "HostileEffectAttemptState",
    "HostileEffectRecoveryDecision",
    "HostileFailureEvidence",
    "HostileInt",
    "HostileStr",
    "STORIES",
    "canonical_state_fingerprint",
    "effect_attempt_state_fingerprint",
    "effect_attempts_module",
    "operations_root",
]
