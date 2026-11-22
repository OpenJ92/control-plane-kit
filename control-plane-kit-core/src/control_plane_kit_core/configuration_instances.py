"""Pure exact configuration allocation references and conserved cleanup outcomes.

These values carry public identity and material, never authority or provider truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re

import rfc8785

from control_plane_kit_core.configuration import (
    ConfigurationFileMode, ConfigurationMediaType, validate_configuration_target_path,
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ARTIFACT = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 65_536
_REF_PROFILE = "configuration-instance.v1"
_SELECTION_PROFILE = "configuration-instance-selection.v1"
_OUTCOMES_PROFILE = "configuration-cleanup-outcomes.v1"
_REF_KEYS = frozenset(("profile", "allocation_id", "workspace_id", "runtime_id", "node_id",
    "artifact_id", "target_path", "media_type", "file_mode", "content_digest"))


class ConfigurationInstanceError(ValueError):
    """A configuration instance value or descriptor is malformed."""


def _invalid() -> None:
    raise ConfigurationInstanceError("configuration instance contract is malformed") from None


@dataclass(frozen=True)
class ConfigurationInstanceRef:
    allocation_id: str
    workspace_id: str
    runtime_id: str
    node_id: str
    artifact_id: str
    target_path: str
    media_type: ConfigurationMediaType
    file_mode: ConfigurationFileMode
    content_digest: str

    def __post_init__(self) -> None:
        for value in (self.allocation_id, self.workspace_id, self.runtime_id, self.node_id):
            if type(value) is not str or not _ID.fullmatch(value):
                _invalid()
        if type(self.artifact_id) is not str or not _ARTIFACT.fullmatch(self.artifact_id):
            _invalid()
        if type(self.content_digest) is not str or not _DIGEST.fullmatch(self.content_digest):
            _invalid()
        if type(self.target_path) is not str:
            _invalid()
        invalid_path = False
        try:
            validate_configuration_target_path(self.target_path)
            invalid_path = len(self.target_path.encode("utf-8")) > 512
        except (TypeError, ValueError):
            invalid_path = True
        if invalid_path:
            _invalid()
        if type(self.media_type) is not ConfigurationMediaType or type(self.file_mode) is not ConfigurationFileMode:
            _invalid()


def _require_ref(ref: object) -> None:
    if type(ref) is not ConfigurationInstanceRef:
        _invalid()
    ConfigurationInstanceRef.__post_init__(ref)


def _configuration_instance_candidates(instances: object, *, ordinary: bool = False) -> tuple[ConfigurationInstanceRef, ...]:
    if type(instances) is not tuple or not 1 <= len(instances) <= 32:
        _invalid()
    for ref in instances:
        _require_ref(ref)
    if len({(ref.workspace_id, ref.runtime_id, ref.node_id) for ref in instances}) != 1:
        _invalid()
    if len({ref.allocation_id for ref in instances}) != len(instances):
        _invalid()
    if ordinary:
        if (len({ref.artifact_id for ref in instances}) != len(instances)
                or len({ref.target_path for ref in instances}) != len(instances)):
            _invalid()
        return tuple(sorted(instances, key=lambda ref: (ref.artifact_id, ref.target_path, ref.allocation_id)))
    return tuple(sorted(instances, key=lambda ref: ref.allocation_id))


@dataclass(frozen=True)
class ConfigurationInstanceSelection:
    instances: tuple[ConfigurationInstanceRef, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "instances", _configuration_instance_candidates(self.instances, ordinary=True))


class ConfigurationCleanupStatus(StrEnum):
    REMOVED = "removed"
    ALREADY_ABSENT = "already-absent"
    RETAINED_IN_USE = "retained-in-use"
    REFUSED = "refused"
    UNKNOWN = "unknown"


class ConfigurationCleanupReason(StrEnum):
    IN_USE = "in-use"
    OWNERSHIP_MISMATCH = "ownership-mismatch"
    PROVENANCE_UNPROVEN = "provenance-unproven"
    AUTHORITY_REFUSED = "authority-refused"
    PROVIDER_UNCERTAIN = "provider-uncertain"
    NOT_ATTEMPTED = "not-attempted"


_REASONS = {
    ConfigurationCleanupStatus.REMOVED: (None,),
    ConfigurationCleanupStatus.ALREADY_ABSENT: (None,),
    ConfigurationCleanupStatus.RETAINED_IN_USE: (ConfigurationCleanupReason.IN_USE,),
    ConfigurationCleanupStatus.REFUSED: (ConfigurationCleanupReason.OWNERSHIP_MISMATCH,
        ConfigurationCleanupReason.PROVENANCE_UNPROVEN, ConfigurationCleanupReason.AUTHORITY_REFUSED),
    ConfigurationCleanupStatus.UNKNOWN: (ConfigurationCleanupReason.PROVIDER_UNCERTAIN,
        ConfigurationCleanupReason.NOT_ATTEMPTED),
}


@dataclass(frozen=True)
class ConfigurationCleanupOutcome:
    ref: ConfigurationInstanceRef
    status: ConfigurationCleanupStatus
    reason: ConfigurationCleanupReason | None

    def __post_init__(self) -> None:
        _require_ref(self.ref)
        if type(self.status) is not ConfigurationCleanupStatus:
            _invalid()
        if self.reason is not None and type(self.reason) is not ConfigurationCleanupReason:
            _invalid()
        if self.reason not in _REASONS[self.status]:
            _invalid()


@dataclass(frozen=True)
class ConfigurationCleanupOutcomeSet:
    outcomes: tuple[ConfigurationCleanupOutcome, ...]

    def __post_init__(self) -> None:
        if type(self.outcomes) is not tuple or not 1 <= len(self.outcomes) <= 32:
            _invalid()
        for outcome in self.outcomes:
            if type(outcome) is not ConfigurationCleanupOutcome:
                _invalid()
            ConfigurationCleanupOutcome.__post_init__(outcome)
        _configuration_instance_candidates(tuple(outcome.ref for outcome in self.outcomes))
        object.__setattr__(self, "outcomes", tuple(sorted(self.outcomes, key=lambda row: row.ref.allocation_id)))


def _descriptor(value: object, keys: frozenset[str], profile: str | None = None) -> dict:
    if type(value) is not dict or value.keys() != keys:
        _invalid()
    if profile is not None and (type(value["profile"]) is not str or value["profile"] != profile):
        _invalid()
    return value


def _canonical(value: dict) -> bytes:
    document = b""
    try:
        document = rfc8785.dumps(value)
    except (TypeError, ValueError):
        pass
    if not document or len(document) > _MAX_BYTES:
        _invalid()
    return document


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid()
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> None:
    _invalid()


def _decode_canonical(codec: object, document: bytes) -> object:
    if type(document) is not bytes or not 1 <= len(document) <= _MAX_BYTES:
        _invalid()
    invalid = False
    value = None
    try:
        value = json.loads(document, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
    except (TypeError, ValueError, RecursionError):
        invalid = True
    if invalid:
        _invalid()
    result = codec.decode(value)
    if codec.encode_canonical_bytes(result) != document:
        _invalid()
    return result


class ConfigurationInstanceRefCodec:
    def encode(self, ref: ConfigurationInstanceRef) -> dict[str, str]:
        _require_ref(ref)
        return {"profile": _REF_PROFILE, "allocation_id": ref.allocation_id,
            "workspace_id": ref.workspace_id, "runtime_id": ref.runtime_id, "node_id": ref.node_id,
            "artifact_id": ref.artifact_id, "target_path": ref.target_path,
            "media_type": ref.media_type.value, "file_mode": ref.file_mode.value,
            "content_digest": ref.content_digest}

    def decode(self, value: object) -> ConfigurationInstanceRef:
        value = _descriptor(value, _REF_KEYS, _REF_PROFILE)
        if not all(type(item) is str for item in value.values()):
            _invalid()
        result = None
        try:
            result = ConfigurationInstanceRef(allocation_id=value["allocation_id"],
                workspace_id=value["workspace_id"], runtime_id=value["runtime_id"], node_id=value["node_id"],
                artifact_id=value["artifact_id"], target_path=value["target_path"],
                media_type=ConfigurationMediaType(value["media_type"]),
                file_mode=ConfigurationFileMode(value["file_mode"]), content_digest=value["content_digest"])
        except (TypeError, ValueError):
            pass
        if result is None:
            _invalid()
        return result

    def encode_canonical_bytes(self, value: ConfigurationInstanceRef) -> bytes:
        return _canonical(self.encode(value))

    def decode_canonical_bytes(self, document: bytes) -> ConfigurationInstanceRef:
        return _decode_canonical(self, document)


class ConfigurationInstanceSelectionCodec:
    def encode(self, selection: ConfigurationInstanceSelection) -> dict:
        if type(selection) is not ConfigurationInstanceSelection:
            _invalid()
        ConfigurationInstanceSelection.__post_init__(selection)
        return {"profile": _SELECTION_PROFILE,
            "instances": [ConfigurationInstanceRefCodec().encode(ref) for ref in selection.instances]}

    def decode(self, value: object) -> ConfigurationInstanceSelection:
        value = _descriptor(value, frozenset(("profile", "instances")), _SELECTION_PROFILE)
        if type(value["instances"]) is not list or not 1 <= len(value["instances"]) <= 32:
            _invalid()
        return ConfigurationInstanceSelection(tuple(ConfigurationInstanceRefCodec().decode(ref) for ref in value["instances"]))

    def encode_canonical_bytes(self, value: ConfigurationInstanceSelection) -> bytes:
        return _canonical(self.encode(value))

    def decode_canonical_bytes(self, document: bytes) -> ConfigurationInstanceSelection:
        return _decode_canonical(self, document)


def _outcome_descriptor(row: ConfigurationCleanupOutcome) -> dict:
    return {"ref": ConfigurationInstanceRefCodec().encode(row.ref), "status": row.status.value,
        "reason": None if row.reason is None else row.reason.value}


class ConfigurationCleanupOutcomeSetCodec:
    def encode(self, outcomes: ConfigurationCleanupOutcomeSet) -> dict:
        if type(outcomes) is not ConfigurationCleanupOutcomeSet:
            _invalid()
        ConfigurationCleanupOutcomeSet.__post_init__(outcomes)
        return {"profile": _OUTCOMES_PROFILE, "outcomes": [_outcome_descriptor(row) for row in outcomes.outcomes]}

    def decode(self, value: object) -> ConfigurationCleanupOutcomeSet:
        value = _descriptor(value, frozenset(("profile", "outcomes")), _OUTCOMES_PROFILE)
        if type(value["outcomes"]) is not list or not 1 <= len(value["outcomes"]) <= 32:
            _invalid()
        rows = []
        for row in value["outcomes"]:
            row = _descriptor(row, frozenset(("ref", "status", "reason")))
            if type(row["status"]) is not str or (row["reason"] is not None and type(row["reason"]) is not str):
                _invalid()
            parsed = None
            try:
                parsed = ConfigurationCleanupOutcome(ConfigurationInstanceRefCodec().decode(row["ref"]),
                    ConfigurationCleanupStatus(row["status"]),
                    None if row["reason"] is None else ConfigurationCleanupReason(row["reason"]))
            except (TypeError, ValueError):
                pass
            if parsed is None:
                _invalid()
            rows.append(parsed)
        return ConfigurationCleanupOutcomeSet(tuple(rows))

    def encode_canonical_bytes(self, value: ConfigurationCleanupOutcomeSet) -> bytes:
        return _canonical(self.encode(value))

    def decode_canonical_bytes(self, document: bytes) -> ConfigurationCleanupOutcomeSet:
        return _decode_canonical(self, document)


def _maximum_cleanup_outcomes(instances: tuple[ConfigurationInstanceRef, ...]) -> ConfigurationCleanupOutcomeSet:
    """Conservative row-size envelope; it makes no provider-outcome claim."""
    rows = []
    for ref in _configuration_instance_candidates(instances):
        candidates = (ConfigurationCleanupOutcome(ref, status, reason)
            for status, reasons in _REASONS.items() for reason in reasons)
        rows.append(max(candidates, key=lambda row: len(_canonical(_outcome_descriptor(row)))))
    return ConfigurationCleanupOutcomeSet(tuple(rows))
