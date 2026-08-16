from __future__ import annotations

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
from tests.effect_attempt_record_fixture import EffectAttemptRecordFixture


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


class EffectAttemptFoldFixture(EffectAttemptRecordFixture):
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

    def transition(self, story: str = "succeeded") -> EffectAttemptTransition:
        identity = self.identity()
        if story in {"succeeded", "failed", "unsupported", "uncertain"}:
            kind = {
                "succeeded": EffectAttemptTransitionKind.SUCCEEDED,
                "failed": EffectAttemptTransitionKind.FAILED,
                "unsupported": EffectAttemptTransitionKind.UNSUPPORTED,
                "uncertain": EffectAttemptTransitionKind.UNCERTAIN,
            }[story]
            return EffectAttemptTransition(
                kind,
                identity,
                outcome_fingerprint=(
                    UNCERTAIN_FINGERPRINT
                    if story == "uncertain"
                    else OUTCOME_FINGERPRINT
                ),
            )

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
        self.require_fold_language()
        values = {
            "request_id": "request-a",
            "transition": self.transition(story),
            "authority": self.authority(),
            "fence": self.execution_fence(),
            "failure": self.failure(story) if story in FAILURE_STORIES else None,
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
