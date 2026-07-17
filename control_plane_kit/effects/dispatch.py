"""Capability-checked dispatch boundary for typed effect requests."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from control_plane_kit.effects.values import (
    EffectAttemptResult,
    EffectCapability,
    EffectFailed,
    EffectResult,
    EffectSucceeded,
    EffectUnsupported,
)
from control_plane_kit.effects.material import MaterializedEffectRequest


class EffectDispatchError(RuntimeError):
    """Raised when an interpreter violates the generic effect contract."""


class EffectInterpreter(Protocol):
    """Provider implementation boundary; no provider is selected here."""

    @property
    def capabilities(self) -> frozenset[EffectCapability]: ...

    def execute(self, request: MaterializedEffectRequest) -> EffectResult: ...


@dataclass(frozen=True)
class CapabilityInterpreterRegistry:
    """One explicit, immutable interpreter choice per effect capability."""

    interpreters: Mapping[EffectCapability, EffectInterpreter]

    def __post_init__(self) -> None:
        values = dict(self.interpreters)
        if not all(isinstance(key, EffectCapability) for key in values):
            raise EffectDispatchError("registry keys must be typed effect capabilities")
        for capability, interpreter in values.items():
            if capability not in interpreter.capabilities:
                raise EffectDispatchError(
                    f"registered interpreter does not advertise {capability.value!r}"
                )
        object.__setattr__(self, "interpreters", MappingProxyType(values))

    @property
    def capabilities(self) -> frozenset[EffectCapability]:
        return frozenset(self.interpreters)

    def execute(self, request: MaterializedEffectRequest) -> EffectResult:
        try:
            interpreter = self.interpreters[request.capability]
        except KeyError:
            return EffectUnsupported(request.identity, request.capability)
        return interpreter.execute(request)


@dataclass(frozen=True)
class PreparedEffect:
    """A request paired with the interpreter that passed capability preflight."""

    request: MaterializedEffectRequest
    interpreter: EffectInterpreter


def dispatch_effect(
    request: MaterializedEffectRequest,
    interpreter: EffectInterpreter,
) -> EffectResult:
    """Reject unsupported work before invoking an effect interpreter."""

    prepared = prepare_effect(request, interpreter)
    if isinstance(prepared, EffectUnsupported):
        return prepared
    return dispatch_prepared_effect(prepared)


def dispatch_prepared_effect(prepared: PreparedEffect) -> EffectResult:
    """Attempt exactly one request after its capability decision is fixed."""

    if not isinstance(prepared, PreparedEffect):
        raise TypeError("prepared effect dispatch requires PreparedEffect")
    result = prepared.interpreter.execute(prepared.request)
    if not isinstance(result, (EffectSucceeded, EffectFailed, EffectUnsupported)):
        raise EffectDispatchError(
            "interpreter must return a typed effect result"
        )
    if result.identity != prepared.request.identity:
        raise EffectDispatchError(
            "interpreter result identity does not match effect request"
        )
    return result


def preflight_effect(
    request: MaterializedEffectRequest,
    interpreter: EffectInterpreter,
) -> EffectUnsupported | None:
    """Check capability support without attempting an external effect."""

    prepared = prepare_effect(request, interpreter)
    return prepared if isinstance(prepared, EffectUnsupported) else None


def prepare_effect(
    request: MaterializedEffectRequest,
    interpreter: EffectInterpreter,
) -> PreparedEffect | EffectUnsupported:
    """Freeze one capability decision before durable effect intent is recorded."""

    if not isinstance(request, MaterializedEffectRequest):
        raise TypeError("effect preparation requires MaterializedEffectRequest")
    capabilities = interpreter.capabilities
    if not isinstance(capabilities, frozenset) or not all(
        isinstance(value, EffectCapability) for value in capabilities
    ):
        raise EffectDispatchError("interpreter capabilities must be typed frozenset")
    if request.capability not in capabilities:
        return EffectUnsupported(request.identity, request.capability)
    return PreparedEffect(request, interpreter)
