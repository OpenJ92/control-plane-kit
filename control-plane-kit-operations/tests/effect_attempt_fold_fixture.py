from __future__ import annotations

import dataclasses
import importlib

from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import (
    FailureCategory,
    FailureEvidence,
)
from tests.effect_outcome_evidence_fixture import (
    EffectAttemptOutcomeRecord,
    EffectOutcomeEvidenceFixture,
    WORKSPACE_ID,
    effect_outcome_failure,
    effect_outcome_observation_records,
    effect_outcome_transition,
)


FOLD_MODULE = "control_plane_kit_operations.effect_attempt_fold"
INTERPRETER_MODULE = (
    "control_plane_kit_operations.effect_attempt_fold_interpreter"
)
OUTCOME_FINGERPRINT = "b" * 64
UNCERTAIN_FINGERPRINT = "c" * 64
RECOVERY_FINGERPRINT = "d" * 64
FOLD_STORIES = (
    "succeeded",
    "failed",
    "unsupported",
    "uncertain",
    "recovered-succeeded",
    "recovered-failed",
    "abandoned",
)
FAILURE_STORIES = frozenset(
    {"failed", "unsupported", "uncertain", "recovered-failed"}
)


def _load_optional(module_name: str, import_module=importlib.import_module):
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        return None


fold_module = _load_optional(FOLD_MODULE)
interpreter_module = _load_optional(INTERPRETER_MODULE)

FoldEffectAttempt = getattr(fold_module, "FoldEffectAttempt", None)
NewlyFolded = getattr(fold_module, "NewlyFolded", None)
ExistingFold = getattr(fold_module, "ExistingFold", None)
EffectAttemptFoldResult = getattr(fold_module, "EffectAttemptFoldResult", None)
EffectAttemptFoldError = getattr(fold_module, "EffectAttemptFoldError", None)
EffectAttemptFoldNotFound = getattr(
    fold_module,
    "EffectAttemptFoldNotFound",
    None,
)
EffectAttemptFoldConflict = getattr(
    fold_module,
    "EffectAttemptFoldConflict",
    None,
)
EffectAttemptFoldDenied = getattr(
    fold_module,
    "EffectAttemptFoldDenied",
    None,
)
EffectAttemptFoldService = getattr(
    interpreter_module,
    "EffectAttemptFoldService",
    None,
)


class EffectAttemptFoldFixture(EffectOutcomeEvidenceFixture):
    maxDiff = None

    def require_fold_language(self) -> None:
        required = {
            "FoldEffectAttempt": FoldEffectAttempt,
            "NewlyFolded": NewlyFolded,
            "ExistingFold": ExistingFold,
            "EffectAttemptFoldResult": EffectAttemptFoldResult,
            "EffectAttemptFoldError": EffectAttemptFoldError,
            "EffectAttemptFoldNotFound": EffectAttemptFoldNotFound,
            "EffectAttemptFoldConflict": EffectAttemptFoldConflict,
            "EffectAttemptFoldDenied": EffectAttemptFoldDenied,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "effect-attempt fold public language is missing",
        )

    def require_fold_service(self) -> None:
        self.assertIsNotNone(
            EffectAttemptFoldService,
            "effect-attempt fold service is missing",
        )

    def require_atomic_command_surface(self) -> None:
        self.require_fold_language()
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(FoldEffectAttempt)),
            (
                "request_id",
                "transition",
                "authority",
                "fence",
                "failure",
                "outcome",
            ),
            "effect-attempt fold command lacks the direct outcome arm",
        )

    def require_atomic_result_surface(self) -> None:
        self.require_fold_language()
        for variant in (NewlyFolded, ExistingFold):
            self.assertEqual(
                tuple(field.name for field in dataclasses.fields(variant)),
                ("attempt", "outcome_record"),
                "effect-attempt fold result lacks the durable outcome arm",
            )

    def outcome_story(self, story: str, *, compensation: bool = False):
        name = f"execution-{story}"
        return next(
            value
            for value in self.stories()
            if value.name == name and value.compensation is compensation
        )

    def direct_outcome(self, story: str, *, compensation: bool = False):
        return self.outcome_for(
            self.outcome_story(story, compensation=compensation)
        )

    def direct_outcome_record(
        self,
        story: str,
        *,
        compensation: bool = False,
    ) -> EffectAttemptOutcomeRecord:
        outcome_story = self.outcome_story(story, compensation=compensation)
        outcome = self.outcome_for(outcome_story)
        observations = effect_outcome_observation_records(
            outcome,
            outcome_story.attempt,
            workspace_id=WORKSPACE_ID,
            observation_ids=self.observation_ids(outcome_story),
        )
        return EffectAttemptOutcomeRecord(
            WORKSPACE_ID,
            outcome,
            outcome_story.attempt,
            observations,
        )

    def transition(self, story: str = "succeeded") -> EffectAttemptTransition:
        identity = self.identity()
        if story in {"succeeded", "failed", "unsupported", "uncertain"}:
            return effect_outcome_transition(self.direct_outcome(story))

        resolution = {
            "recovered-succeeded": EffectRecoveryResolution.SUCCEEDED,
            "recovered-failed": EffectRecoveryResolution.FAILED,
            "abandoned": EffectRecoveryResolution.ABANDONED,
        }[story]
        decision = EffectRecoveryDecision(
            "decision-a",
            identity,
            resolution,
            UNCERTAIN_FINGERPRINT,
            RECOVERY_FINGERPRINT,
        )
        return EffectAttemptTransition(
            EffectAttemptTransitionKind.ABANDONED
            if story == "abandoned"
            else EffectAttemptTransitionKind.RECONCILED,
            identity,
            recovery_decision=decision,
        )

    def failure(self, marker: str = "bounded") -> FailureEvidence:
        return FailureEvidence(
            FailureCategory.TERMINAL,
            f"failure-{marker}",
            f"safe failure {marker}",
        )

    def authority(
        self,
        worker_id: str = "worker-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def execution_fence(
        self,
        worker_id: str = "worker-a",
        generation: int = 7,
    ) -> ExecutionLeaseFence:
        return ExecutionLeaseFence(worker_id, generation)

    def command(self, story: str = "succeeded", **changes):
        self.require_atomic_command_surface()
        outcome = (
            self.direct_outcome(story)
            if story in {"succeeded", "failed", "unsupported", "uncertain"}
            else None
        )
        values = {
            "request_id": "request-a",
            "transition": self.transition(story),
            "authority": self.authority(),
            "fence": self.execution_fence(),
            "failure": (
                effect_outcome_failure(outcome)
                if outcome is not None
                else self.failure(story) if story == "recovered-failed" else None
            ),
            "outcome": outcome,
        }
        values.update(changes)
        return FoldEffectAttempt(**values)


__all__ = [
    "EffectAttemptFoldConflict",
    "EffectAttemptFoldDenied",
    "EffectAttemptFoldError",
    "EffectAttemptFoldFixture",
    "EffectAttemptFoldNotFound",
    "EffectAttemptFoldResult",
    "EffectAttemptFoldService",
    "ExistingFold",
    "FAILURE_STORIES",
    "FOLD_MODULE",
    "FOLD_STORIES",
    "FoldEffectAttempt",
    "INTERPRETER_MODULE",
    "NewlyFolded",
    "_load_optional",
]
