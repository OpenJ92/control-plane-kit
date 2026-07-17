"""Capability-checked dispatch boundary for typed effect requests."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreparedEffect:
    """A request paired with the interpreter that passed capability preflight."""

    request: EffectRequest
    interpreter: EffectInterpreter


def dispatch_effect(
    request: EffectRequest,
    interpreter: EffectInterpreter,
) -> EffectResult:
    """Reject unsupported work before invoking an effect interpreter."""

    prepared = prepare_effect(request, interpreter)
    if isinstance(prepared, EffectUnsupported):
        return prepared
    return dispatch_prepared_effect(prepared)


def dispatch_prepared_effect(prepared: PreparedEffect) -> EffectAttemptResult:
    """Attempt exactly one request after its capability decision is fixed."""

    if not isinstance(prepared, PreparedEffect):
        raise TypeError("prepared effect dispatch requires PreparedEffect")
    result = prepared.interpreter.execute(prepared.request)
    if not isinstance(result, (EffectSucceeded, EffectFailed)):
        raise EffectDispatchError(
            "interpreter must return EffectSucceeded or EffectFailed"
        )
    if result.identity != prepared.request.identity:
        raise EffectDispatchError(
            "interpreter result identity does not match effect request"
        )
    return result


def preflight_effect(
    request: EffectRequest,
    interpreter: EffectInterpreter,
) -> EffectUnsupported | None:
    """Check capability support without attempting an external effect."""

    prepared = prepare_effect(request, interpreter)
    return prepared if isinstance(prepared, EffectUnsupported) else None


def prepare_effect(
    request: EffectRequest,
    interpreter: EffectInterpreter,
) -> PreparedEffect | EffectUnsupported:
    """Freeze one capability decision before durable effect intent is recorded."""

    if not isinstance(request, EffectRequest):
        raise TypeError("effect preparation requires EffectRequest")
    capabilities = interpreter.capabilities
    if not isinstance(capabilities, frozenset) or not all(
        isinstance(value, EffectCapability) for value in capabilities
    ):
        raise EffectDispatchError("interpreter capabilities must be typed frozenset")
    if request.capability not in capabilities:
        return EffectUnsupported(request.identity, request.capability)
    return PreparedEffect(request, interpreter)
