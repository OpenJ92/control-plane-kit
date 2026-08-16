"""Preflight interpreter shell for one effect-attempt fold."""

from __future__ import annotations

from typing import Any, Callable

from control_plane_kit_core.operations import EffectAttemptFence
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempt_fold import (
    EffectAttemptFoldDenied,
    EffectAttemptFoldResult,
    FoldEffectAttempt,
    _valid_fold_command,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class EffectAttemptFoldService:
    """Validate one fold command before its held transaction stage."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._id_factory = id_factory

    def execute(self, command: FoldEffectAttempt) -> EffectAttemptFoldResult:
        if not _valid_fold_command(command):
            raise InvalidOperationCommand("effect attempt fold command is invalid")
        if PolicyScope.EXECUTION_OPERATE not in command.authority.scopes:
            raise EffectAttemptFoldDenied("scope execution:operate is missing")
        _translate_fence(command)
        self._unit_of_work_factory()
        raise NotImplementedError("effect attempt fold transaction is not implemented")


def _translate_fence(command: FoldEffectAttempt) -> EffectAttemptFence:
    worker_id = command.fence.worker_id
    generation = command.fence.generation
    if not _representable_effect_fence(worker_id, generation):
        raise InvalidOperationCommand(
            "execution lease fence cannot identify an effect attempt"
        )
    return EffectAttemptFence(worker_id, generation)


def _representable_effect_fence(worker_id: object, generation: object) -> bool:
    if (
        type(worker_id) is not str
        or not worker_id.strip()
        or len(worker_id) > 256
        or "\x00" in worker_id
        or type(generation) is not int
        or not 1 <= generation <= 2**63 - 1
    ):
        return False
    try:
        worker_id.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


__all__ = ["EffectAttemptFoldService"]
