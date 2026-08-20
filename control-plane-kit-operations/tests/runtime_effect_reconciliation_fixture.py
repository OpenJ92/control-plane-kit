from __future__ import annotations

import importlib

from tests.effect_attempt_start_fixture import EffectAttemptStartFixture


LANGUAGE_MODULE = "control_plane_kit_operations.effect_attempt_reconciliation"
INTERPRETER_MODULE = (
    "control_plane_kit_operations.effect_attempt_reconciliation_interpreter"
)


def _load_optional(module_name: str, import_module=importlib.import_module):
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        return None


language_module = _load_optional(LANGUAGE_MODULE)
interpreter_module = _load_optional(INTERPRETER_MODULE)

ReconcileEffectAttempt = getattr(language_module, "ReconcileEffectAttempt", None)
RuntimeEffectObserver = getattr(language_module, "RuntimeEffectObserver", None)
EffectAttemptReconciliationError = getattr(
    language_module,
    "EffectAttemptReconciliationError",
    None,
)
EffectAttemptReconciliationNotFound = getattr(
    language_module,
    "EffectAttemptReconciliationNotFound",
    None,
)
EffectAttemptReconciliationConflict = getattr(
    language_module,
    "EffectAttemptReconciliationConflict",
    None,
)
EffectAttemptReconciliationDenied = getattr(
    language_module,
    "EffectAttemptReconciliationDenied",
    None,
)
EffectAttemptReconciliationService = getattr(
    interpreter_module,
    "EffectAttemptReconciliationService",
    None,
)


class RuntimeEffectReconciliationFixture(EffectAttemptStartFixture):
    maxDiff = None

    def require_language(self) -> None:
        required = {
            "ReconcileEffectAttempt": ReconcileEffectAttempt,
            "RuntimeEffectObserver": RuntimeEffectObserver,
            "EffectAttemptReconciliationError": EffectAttemptReconciliationError,
            "EffectAttemptReconciliationNotFound": (
                EffectAttemptReconciliationNotFound
            ),
            "EffectAttemptReconciliationConflict": (
                EffectAttemptReconciliationConflict
            ),
            "EffectAttemptReconciliationDenied": EffectAttemptReconciliationDenied,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "runtime-effect reconciliation language is missing",
        )

    def require_service(self) -> None:
        self.assertIsNotNone(
            EffectAttemptReconciliationService,
            "runtime-effect reconciliation service is missing",
        )

    def command(self, **changes):
        self.require_language()
        values = {
            "request_id": "request-a",
            "identity": self.identity(),
            "authority": self.authority(),
            "fence": self.fence(),
        }
        values.update(changes)
        return ReconcileEffectAttempt(**values)


__all__ = [
    "EffectAttemptReconciliationConflict",
    "EffectAttemptReconciliationDenied",
    "EffectAttemptReconciliationError",
    "EffectAttemptReconciliationNotFound",
    "EffectAttemptReconciliationService",
    "INTERPRETER_MODULE",
    "LANGUAGE_MODULE",
    "ReconcileEffectAttempt",
    "RuntimeEffectObserver",
    "RuntimeEffectReconciliationFixture",
    "_load_optional",
    "interpreter_module",
    "language_module",
]
