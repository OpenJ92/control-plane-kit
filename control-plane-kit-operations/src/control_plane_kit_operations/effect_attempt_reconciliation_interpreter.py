"""DB-free preflight shell for runtime-effect reconciliation."""

from __future__ import annotations

from typing import Any, Callable

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempt_fold import EffectAttemptFoldResult
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationDenied,
    ReconcileEffectAttempt,
    RuntimeEffectObserver,
    _valid_reconcile_command,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class EffectAttemptReconciliationService:
    """Validate reconciliation intent before its Stage 2 transaction boundary."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        observer: RuntimeEffectObserver,
        fold_service: Any,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._observer = observer
        self._fold_service = fold_service

    def execute(
        self,
        command: ReconcileEffectAttempt,
    ) -> EffectAttemptFoldResult:
        if not _valid_reconcile_command(command):
            raise InvalidOperationCommand(
                "effect attempt reconciliation command is invalid"
            )
        if PolicyScope.EXECUTION_OPERATE not in command.authority.scopes:
            raise EffectAttemptReconciliationDenied(
                "scope execution:operate is missing"
            )
        return self._unit_of_work_factory()


__all__ = ["EffectAttemptReconciliationService"]
