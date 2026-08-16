from __future__ import annotations

import importlib

from control_plane_kit_core.operations import (
    EffectAttemptIdentity,
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    RunId,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority


START_MODULE = "control_plane_kit_operations.effect_attempt_start"
INTERPRETER_MODULE = (
    "control_plane_kit_operations.effect_attempt_start_interpreter"
)
REQUEST_FINGERPRINT = "a" * 64
OUTCOME_FINGERPRINT = "b" * 64


def _load_optional(module_name: str, import_module=importlib.import_module):
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        return None


start_module = _load_optional(START_MODULE)
interpreter_module = _load_optional(INTERPRETER_MODULE)

StartEffectAttempt = getattr(start_module, "StartEffectAttempt", None)
NewlyStarted = getattr(start_module, "NewlyStarted", None)
ExistingAttempt = getattr(start_module, "ExistingAttempt", None)
EffectAttemptStartResult = getattr(
    start_module,
    "EffectAttemptStartResult",
    None,
)
EffectAttemptStartError = getattr(start_module, "EffectAttemptStartError", None)
EffectAttemptStartNotFound = getattr(
    start_module,
    "EffectAttemptStartNotFound",
    None,
)
EffectAttemptStartConflict = getattr(
    start_module,
    "EffectAttemptStartConflict",
    None,
)
EffectAttemptStartDenied = getattr(
    start_module,
    "EffectAttemptStartDenied",
    None,
)
EffectAttemptStartService = getattr(
    interpreter_module,
    "EffectAttemptStartService",
    None,
)


class EffectAttemptStartFixture:
    maxDiff = None

    def require_language(self) -> None:
        required = {
            "StartEffectAttempt": StartEffectAttempt,
            "NewlyStarted": NewlyStarted,
            "ExistingAttempt": ExistingAttempt,
            "EffectAttemptStartResult": EffectAttemptStartResult,
            "EffectAttemptStartError": EffectAttemptStartError,
            "EffectAttemptStartNotFound": EffectAttemptStartNotFound,
            "EffectAttemptStartConflict": EffectAttemptStartConflict,
            "EffectAttemptStartDenied": EffectAttemptStartDenied,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "effect-attempt start public language is missing",
        )

    def require_service(self) -> None:
        self.assertIsNotNone(
            EffectAttemptStartService,
            "effect-attempt start service is missing",
        )

    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            if canary:
                self.assertNotIn(canary, rendered)

    def identity(
        self,
        *,
        attempt: int = 1,
        run_id: str = "run-a",
        activity_id: str = "activity-a",
    ) -> EffectAttemptIdentity:
        return EffectAttemptIdentity(RunId(run_id), activity_id, attempt)

    def transition(
        self,
        *,
        identity: EffectAttemptIdentity | None = None,
        attempt: int = 1,
        prior_attempt: EffectAttemptIdentity | None = None,
    ) -> EffectAttemptTransition:
        return EffectAttemptTransition(
            EffectAttemptTransitionKind.STARTED,
            identity or self.identity(attempt=attempt),
            request_fingerprint=REQUEST_FINGERPRINT,
            prior_attempt=prior_attempt,
        )

    def settled_transition(self) -> EffectAttemptTransition:
        return EffectAttemptTransition(
            EffectAttemptTransitionKind.SUCCEEDED,
            self.identity(),
            outcome_fingerprint=OUTCOME_FINGERPRINT,
        )

    def authority(
        self,
        worker_id: str = "worker-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def fence(
        self,
        worker_id: str = "worker-a",
        generation: int = 7,
    ) -> ExecutionLeaseFence:
        return ExecutionLeaseFence(worker_id, generation)

    def command(self, **changes):
        self.require_language()
        values = {
            "request_id": "request-a",
            "transition": self.transition(),
            "authority": self.authority(),
            "fence": self.fence(),
        }
        values.update(changes)
        return StartEffectAttempt(**values)


__all__ = [
    "EffectAttemptStartConflict",
    "EffectAttemptStartDenied",
    "EffectAttemptStartError",
    "EffectAttemptStartFixture",
    "EffectAttemptStartNotFound",
    "EffectAttemptStartResult",
    "EffectAttemptStartService",
    "ExistingAttempt",
    "INTERPRETER_MODULE",
    "NewlyStarted",
    "REQUEST_FINGERPRINT",
    "START_MODULE",
    "StartEffectAttempt",
    "interpreter_module",
    "start_module",
    "_load_optional",
]
