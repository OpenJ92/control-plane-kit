"""Closed evidence and program values for failed-run compensation admission."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Mapping

from control_plane_kit_core.operations.recovery import EffectAttemptIdentity
from control_plane_kit_core.operations.run_identity import RunId
from control_plane_kit_core.planning import (
    ActivityOperation,
    CompensationMaterialSource,
    activity_operation_descriptor,
    activity_operation_from_descriptor,
)


FAILED_RUN_COMPENSATION_SCHEMA = "cpk.failed-run-compensation-program"
FAILED_RUN_COMPENSATION_VERSION = 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class InvalidFailedRunCompensationContract(ValueError):
    """Raised when compensation evidence is open, stale, or incoherent."""


class FailedRunCompensationReason(StrEnum):
    """Closed reasons that admit failed-run compensation planning."""

    POST_EFFECT_FAILURE = "post-effect-failure"


@dataclass(frozen=True, slots=True)
class FailedRunCompensationLineage:
    workspace_id: str
    request_id: str
    run_id: RunId
    plan_id: str
    current_graph_id: str
    desired_graph_id: str
    desired_graph_revision: int
    execution_intent_fingerprint: str

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "request_id",
            "plan_id",
            "current_graph_id",
            "desired_graph_id",
        ):
            _require_identifier(getattr(self, name), name)
        if type(self.run_id) is not RunId:
            raise InvalidFailedRunCompensationContract("run_id must be RunId")
        if (
            type(self.desired_graph_revision) is not int
            or self.desired_graph_revision < 0
        ):
            raise InvalidFailedRunCompensationContract(
                "desired_graph_revision must be non-negative"
            )
        _require_fingerprint(
            self.execution_intent_fingerprint,
            "execution_intent_fingerprint",
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "run_id": self.run_id.value,
            "plan_id": self.plan_id,
            "current_graph_id": self.current_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "desired_graph_revision": self.desired_graph_revision,
            "execution_intent_fingerprint": self.execution_intent_fingerprint,
        }

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, object]) -> "FailedRunCompensationLineage":
        value = _closed_mapping(
            descriptor,
            {
                "workspace_id",
                "request_id",
                "run_id",
                "plan_id",
                "current_graph_id",
                "desired_graph_id",
                "desired_graph_revision",
                "execution_intent_fingerprint",
            },
            "lineage",
        )
        try:
            return cls(
                workspace_id=value["workspace_id"],
                request_id=value["request_id"],
                run_id=RunId(value["run_id"]),
                plan_id=value["plan_id"],
                current_graph_id=value["current_graph_id"],
                desired_graph_id=value["desired_graph_id"],
                desired_graph_revision=value["desired_graph_revision"],
                execution_intent_fingerprint=value["execution_intent_fingerprint"],
            )
        except (TypeError, ValueError) as error:
            raise InvalidFailedRunCompensationContract("lineage is invalid") from error


@dataclass(frozen=True, slots=True)
class SuccessfulEffectEvidence:
    attempt_identity: EffectAttemptIdentity
    request_fingerprint: str
    outcome_fingerprint: str
    completion_event_id: str
    completion_ordinal: int

    def __post_init__(self) -> None:
        if type(self.attempt_identity) is not EffectAttemptIdentity:
            raise InvalidFailedRunCompensationContract(
                "attempt_identity must be EffectAttemptIdentity"
            )
        _require_fingerprint(self.request_fingerprint, "request_fingerprint")
        _require_fingerprint(self.outcome_fingerprint, "outcome_fingerprint")
        _require_identifier(self.completion_event_id, "completion_event_id")
        if type(self.completion_ordinal) is not int or self.completion_ordinal < 1:
            raise InvalidFailedRunCompensationContract(
                "completion_ordinal must be positive"
            )

    def descriptor(self) -> dict[str, object]:
        identity = self.attempt_identity
        return {
            "attempt_identity": {
                "run_id": identity.run_id.value,
                "activity_id": identity.activity_id,
                "attempt": identity.attempt,
            },
            "request_fingerprint": self.request_fingerprint,
            "outcome_fingerprint": self.outcome_fingerprint,
            "completion_event_id": self.completion_event_id,
            "completion_ordinal": self.completion_ordinal,
        }

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, object]) -> "SuccessfulEffectEvidence":
        value = _closed_mapping(
            descriptor,
            {
                "attempt_identity",
                "request_fingerprint",
                "outcome_fingerprint",
                "completion_event_id",
                "completion_ordinal",
            },
            "successful effect",
        )
        identity = _closed_mapping(
            value["attempt_identity"],
            {"run_id", "activity_id", "attempt"},
            "attempt identity",
        )
        try:
            return cls(
                EffectAttemptIdentity(
                    RunId(identity["run_id"]),
                    identity["activity_id"],
                    identity["attempt"],
                ),
                value["request_fingerprint"],
                value["outcome_fingerprint"],
                value["completion_event_id"],
                value["completion_ordinal"],
            )
        except (TypeError, ValueError) as error:
            raise InvalidFailedRunCompensationContract(
                "successful effect is invalid"
            ) from error


@dataclass(frozen=True, slots=True)
class FailedRunCompensationEvidence:
    lineage: FailedRunCompensationLineage
    reason: FailedRunCompensationReason
    source_failure_fingerprint: str
    successful_effects: tuple[SuccessfulEffectEvidence, ...]

    def __post_init__(self) -> None:
        if type(self.lineage) is not FailedRunCompensationLineage:
            raise InvalidFailedRunCompensationContract("lineage is invalid")
        if type(self.reason) is not FailedRunCompensationReason:
            raise InvalidFailedRunCompensationContract("reason is invalid")
        _require_fingerprint(
            self.source_failure_fingerprint,
            "source_failure_fingerprint",
        )
        if type(self.successful_effects) is not tuple or not self.successful_effects:
            raise InvalidFailedRunCompensationContract(
                "successful_effects must be a non-empty tuple"
            )
        if not all(
            type(effect) is SuccessfulEffectEvidence
            for effect in self.successful_effects
        ):
            raise InvalidFailedRunCompensationContract(
                "successful_effects are invalid"
            )
        if any(
            effect.attempt_identity.run_id != self.lineage.run_id
            for effect in self.successful_effects
        ):
            raise InvalidFailedRunCompensationContract(
                "successful effects must belong to the failed run"
            )
        ordinals = tuple(
            effect.completion_ordinal for effect in self.successful_effects
        )
        if ordinals != tuple(sorted(ordinals, reverse=True)) or len(set(ordinals)) != len(ordinals):
            raise InvalidFailedRunCompensationContract(
                "successful effects must be in reverse completion order"
            )
        identities = tuple(effect.attempt_identity for effect in self.successful_effects)
        if len(set(identities)) != len(identities):
            raise InvalidFailedRunCompensationContract(
                "successful effect identities must be unique"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "lineage": self.lineage.descriptor(),
            "reason": self.reason.value,
            "source_failure_fingerprint": self.source_failure_fingerprint,
            "successful_effects": [
                effect.descriptor() for effect in self.successful_effects
            ],
        }

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, object]) -> "FailedRunCompensationEvidence":
        value = _closed_mapping(
            descriptor,
            {
                "lineage",
                "reason",
                "source_failure_fingerprint",
                "successful_effects",
            },
            "evidence",
        )
        effects = value["successful_effects"]
        if type(effects) is not list:
            raise InvalidFailedRunCompensationContract(
                "successful_effects must be a list"
            )
        try:
            return cls(
                FailedRunCompensationLineage.from_descriptor(value["lineage"]),
                FailedRunCompensationReason(value["reason"]),
                value["source_failure_fingerprint"],
                tuple(
                    SuccessfulEffectEvidence.from_descriptor(effect)
                    for effect in effects
                ),
            )
        except (TypeError, ValueError) as error:
            raise InvalidFailedRunCompensationContract("evidence is invalid") from error


@dataclass(frozen=True, slots=True)
class FailedRunCompensationStep:
    position: int
    source_effect: SuccessfulEffectEvidence
    operation: ActivityOperation
    material_source: CompensationMaterialSource

    def __post_init__(self) -> None:
        if type(self.position) is not int or self.position < 1:
            raise InvalidFailedRunCompensationContract("position must be positive")
        if type(self.source_effect) is not SuccessfulEffectEvidence:
            raise InvalidFailedRunCompensationContract("source_effect is invalid")
        try:
            activity_operation_descriptor(self.operation)
        except (TypeError, ValueError) as error:
            raise InvalidFailedRunCompensationContract("operation is invalid") from error
        if type(self.material_source) is not CompensationMaterialSource:
            raise InvalidFailedRunCompensationContract("material_source is invalid")

    def descriptor(self) -> dict[str, object]:
        return {
            "position": self.position,
            "source_effect": self.source_effect.descriptor(),
            "operation": activity_operation_descriptor(self.operation),
            "material_source": self.material_source.value,
        }

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, object]) -> "FailedRunCompensationStep":
        value = _closed_mapping(
            descriptor,
            {"position", "source_effect", "operation", "material_source"},
            "step",
        )
        try:
            return cls(
                value["position"],
                SuccessfulEffectEvidence.from_descriptor(value["source_effect"]),
                activity_operation_from_descriptor(value["operation"]),
                CompensationMaterialSource(value["material_source"]),
            )
        except (TypeError, ValueError) as error:
            raise InvalidFailedRunCompensationContract("step is invalid") from error


@dataclass(frozen=True, slots=True)
class FailedRunCompensationProgram:
    program_id: str
    evidence: FailedRunCompensationEvidence
    steps: tuple[FailedRunCompensationStep, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.program_id, "program_id")
        if type(self.evidence) is not FailedRunCompensationEvidence:
            raise InvalidFailedRunCompensationContract("evidence is invalid")
        if type(self.steps) is not tuple or not self.steps:
            raise InvalidFailedRunCompensationContract("steps must be non-empty")
        if not all(type(step) is FailedRunCompensationStep for step in self.steps):
            raise InvalidFailedRunCompensationContract("steps are invalid")
        if tuple(step.position for step in self.steps) != tuple(
            range(1, len(self.steps) + 1)
        ):
            raise InvalidFailedRunCompensationContract(
                "step positions must be contiguous"
            )
        if tuple(step.source_effect for step in self.steps) != self.evidence.successful_effects:
            raise InvalidFailedRunCompensationContract(
                "steps must exactly cover successful effects"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "schema": FAILED_RUN_COMPENSATION_SCHEMA,
            "version": FAILED_RUN_COMPENSATION_VERSION,
            "program_id": self.program_id,
            "evidence": self.evidence.descriptor(),
            "steps": [step.descriptor() for step in self.steps],
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(self.descriptor())).hexdigest()

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, object]) -> "FailedRunCompensationProgram":
        value = _closed_mapping(
            descriptor,
            {"schema", "version", "program_id", "evidence", "steps"},
            "program",
        )
        if (
            value["schema"] != FAILED_RUN_COMPENSATION_SCHEMA
            or value["version"] != FAILED_RUN_COMPENSATION_VERSION
        ):
            raise InvalidFailedRunCompensationContract(
                "program schema or version is invalid"
            )
        steps = value["steps"]
        if type(steps) is not list:
            raise InvalidFailedRunCompensationContract("steps must be a list")
        try:
            return cls(
                value["program_id"],
                FailedRunCompensationEvidence.from_descriptor(value["evidence"]),
                tuple(
                    FailedRunCompensationStep.from_descriptor(step)
                    for step in steps
                ),
            )
        except (TypeError, ValueError) as error:
            raise InvalidFailedRunCompensationContract("program is invalid") from error


def _closed_mapping(
    value: object,
    keys: set[str],
    name: str,
) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise InvalidFailedRunCompensationContract(f"{name} shape is invalid")
    if not all(type(key) is str for key in value):
        raise InvalidFailedRunCompensationContract(f"{name} keys are invalid")
    return value


def _require_identifier(value: object, name: str) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise InvalidFailedRunCompensationContract(f"{name} is invalid")


def _require_fingerprint(value: object, name: str) -> None:
    if type(value) is not str or _FINGERPRINT.fullmatch(value) is None:
        raise InvalidFailedRunCompensationContract(f"{name} is invalid")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


__all__ = [
    "FAILED_RUN_COMPENSATION_SCHEMA",
    "FAILED_RUN_COMPENSATION_VERSION",
    "FailedRunCompensationEvidence",
    "FailedRunCompensationLineage",
    "FailedRunCompensationProgram",
    "FailedRunCompensationReason",
    "FailedRunCompensationStep",
    "InvalidFailedRunCompensationContract",
    "SuccessfulEffectEvidence",
]
