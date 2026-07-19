"""Capability-checked dispatch for graph-pinned semantic verification."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from control_plane_kit.effects.material import VerificationCheckMaterial
from control_plane_kit.verification import (
    VerificationCapability,
    VerificationIdentity,
    VerificationCompleted,
    VerificationResult,
    VerificationUnsupported,
    verification_capability,
)


class VerificationDispatchError(RuntimeError):
    """A verification interpreter violated the closed dispatch contract."""


class VerificationInterpreter(Protocol):
    @property
    def capabilities(self) -> frozenset[VerificationCapability]: ...

    def execute(self, material: VerificationCheckMaterial) -> VerificationResult: ...


def verification_identity(material: VerificationCheckMaterial) -> VerificationIdentity:
    if not isinstance(material, VerificationCheckMaterial):
        raise TypeError("verification identity requires VerificationCheckMaterial")
    return VerificationIdentity(
        material.node_id,
        material.graph_id,
        material.check.check_id,
    )


@dataclass(frozen=True)
class VerificationInterpreterRegistry:
    """One explicit immutable interpreter choice per verification capability."""

    interpreters: Mapping[VerificationCapability, VerificationInterpreter]

    def __post_init__(self) -> None:
        values = dict(self.interpreters)
        if not all(isinstance(key, VerificationCapability) for key in values):
            raise VerificationDispatchError(
                "verification registry keys must be typed capabilities"
            )
        for capability, interpreter in values.items():
            if capability not in _capabilities(interpreter):
                raise VerificationDispatchError(
                    "registered verification interpreter does not advertise its capability"
                )
        object.__setattr__(self, "interpreters", MappingProxyType(values))

    @property
    def capabilities(self) -> frozenset[VerificationCapability]:
        return frozenset(self.interpreters)

    def execute(self, material: VerificationCheckMaterial) -> VerificationResult:
        capability = verification_capability(material.check)
        identity = verification_identity(material)
        try:
            interpreter = self.interpreters[capability]
        except KeyError:
            return VerificationUnsupported(identity, capability)
        result = interpreter.execute(material)
        if not isinstance(result, (VerificationCompleted, VerificationUnsupported)):
            raise VerificationDispatchError(
                "verification interpreter must return a typed result"
            )
        if result.identity != identity or result.capability is not capability:
            raise VerificationDispatchError(
                "verification result identity or capability does not match its material"
            )
        return result


@dataclass(frozen=True)
class StaticVerificationInterpreter:
    """Deterministic effect-free capability provider for algebra and workflow tests."""

    supported: frozenset[VerificationCapability]
    results: Mapping[VerificationIdentity, VerificationCompleted]

    def __post_init__(self) -> None:
        if not isinstance(self.supported, frozenset) or not all(
            isinstance(value, VerificationCapability) for value in self.supported
        ):
            raise TypeError("static verification capabilities must be typed")
        values = dict(self.results)
        if not all(
            isinstance(key, VerificationIdentity)
            and isinstance(value, VerificationCompleted)
            and key == value.identity
            and value.capability in self.supported
            for key, value in values.items()
        ):
            raise VerificationDispatchError(
                "static verification results must match typed identities and capabilities"
            )
        object.__setattr__(self, "results", MappingProxyType(values))

    @property
    def capabilities(self) -> frozenset[VerificationCapability]:
        return self.supported

    def execute(self, material: VerificationCheckMaterial) -> VerificationResult:
        capability = verification_capability(material.check)
        identity = verification_identity(material)
        if capability not in self.supported:
            return VerificationUnsupported(identity, capability)
        try:
            return self.results[identity]
        except KeyError as error:
            raise VerificationDispatchError(
                "static verification result is not defined for this identity"
            ) from error


def _capabilities(
    interpreter: VerificationInterpreter,
) -> frozenset[VerificationCapability]:
    values = interpreter.capabilities
    if not isinstance(values, frozenset) or not all(
        isinstance(value, VerificationCapability) for value in values
    ):
        raise VerificationDispatchError(
            "verification interpreter capabilities must be a typed frozenset"
        )
    return values
