"""Capability-checked dispatch boundary for typed effect requests."""

from __future__ import annotations

from typing import Protocol

from control_plane_kit.effects.values import (
    EffectAttemptResult,
    EffectCapability,
    EffectFailed,
    EffectRequest,
    EffectResult,
    EffectSucceeded,
    EffectUnsupported,
)


class EffectDispatchError(RuntimeError):
    """Raised when an interpreter violates the generic effect contract."""


class EffectInterpreter(Protocol):
    """Provider implementation boundary; no provider is selected here."""

    @property
    def capabilities(self) -> frozenset[EffectCapability]: ...

    def execute(self, request: EffectRequest) -> EffectAttemptResult: ...


def dispatch_effect(
    request: EffectRequest,
    interpreter: EffectInterpreter,
) -> EffectResult:
    """Reject unsupported work before invoking an effect interpreter."""

    if not isinstance(request, EffectRequest):
        raise TypeError("effect dispatch requires EffectRequest")
    capabilities = interpreter.capabilities
    if not isinstance(capabilities, frozenset) or not all(
        isinstance(value, EffectCapability) for value in capabilities
    ):
        raise EffectDispatchError("interpreter capabilities must be typed frozenset")
    if request.capability not in capabilities:
        return EffectUnsupported(request.identity, request.capability)

    result = interpreter.execute(request)
    if not isinstance(result, (EffectSucceeded, EffectFailed)):
        raise EffectDispatchError(
            "interpreter must return EffectSucceeded or EffectFailed"
        )
    if result.identity != request.identity:
        raise EffectDispatchError(
            "interpreter result identity does not match effect request"
        )
    return result
