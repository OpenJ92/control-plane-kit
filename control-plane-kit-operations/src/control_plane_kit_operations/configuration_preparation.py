"""Configuration provenance values and capacity laws; none confer authority."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import re

import rfc8785

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRef, ConfigurationInstanceSelection,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import StartNode, ReconcileNode
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntentSource
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations.records import OperationsRecordError

_ERROR = "configuration preparation evidence is invalid"
_ARTIFACT = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition: bool) -> None:
    if not condition:
        raise OperationsRecordError(_ERROR)


def _identity(value: object) -> None:
    _require(type(value) is EffectAttemptIdentity)
    try:
        _require(EffectAttemptIdentity(RunId(value.run_id.value), value.activity_id, value.attempt) == value)
    except (AttributeError, TypeError, ValueError):
        raise OperationsRecordError(_ERROR) from None


def _ref(value: object) -> None:
    _require(type(value) is ConfigurationInstanceRef)
    try:
        _require(replace(value) == value)
    except (TypeError, ValueError):
        raise OperationsRecordError(_ERROR) from None


def _key(value: object) -> None:
    _require(type(value) is tuple and len(value) == 2)
    _identity(value[0])
    _require(type(value[1]) is str and _ARTIFACT.fullmatch(value[1]) is not None)


@dataclass(frozen=True)
class ConfigurationEvidenceFootprint:
    records: int
    value_octets: int
    scalar_markers: int
    statements: int

    def __post_init__(self) -> None:
        _require(all(type(value) is int and value >= 0 for value in (
            self.records, self.value_octets, self.scalar_markers, self.statements)))

    @property
    def accounted_bytes(self) -> int:
        return self.value_octets + 16 * self.scalar_markers + 128 * self.records + 256 * self.statements

    def plus(self, other: ConfigurationEvidenceFootprint) -> ConfigurationEvidenceFootprint:
        _footprint(self)
        _footprint(other)
        return ConfigurationEvidenceFootprint(self.records + other.records,
            self.value_octets + other.value_octets, self.scalar_markers + other.scalar_markers,
            self.statements + other.statements)


def _footprint(value: object) -> None:
    _require(type(value) is ConfigurationEvidenceFootprint)
    ConfigurationEvidenceFootprint.__post_init__(value)


class ConfigurationCapacityDecision(StrEnum):
    WITHIN_LIMITS = "within-limits"
    RECORD_LIMIT = "record-limit"
    BYTE_LIMIT = "byte-limit"
    PER_REF_CLAIM_LIMIT = "per-ref-claim-limit"
    TOTAL_CLAIM_LIMIT = "total-claim-limit"


def configuration_evidence_capacity(footprint: ConfigurationEvidenceFootprint) -> ConfigurationCapacityDecision:
    _footprint(footprint)
    if footprint.records > 4096:
        return ConfigurationCapacityDecision.RECORD_LIMIT
    if footprint.accounted_bytes > 16 * 1024 * 1024:
        return ConfigurationCapacityDecision.BYTE_LIMIT
    return ConfigurationCapacityDecision.WITHIN_LIMITS


def configuration_preparation_capacity(*, current: ConfigurationEvidenceFootprint,
        reserved_future: ConfigurationEvidenceFootprint, existing_claim_keys: tuple,
        proposed_claim_key: tuple, existing_total_claims: int) -> ConfigurationCapacityDecision:
    _footprint(current)
    _footprint(reserved_future)
    _require(type(existing_claim_keys) is tuple)
    for key in existing_claim_keys:
        _key(key)
    _key(proposed_claim_key)
    _require(len(set(existing_claim_keys)) == len(existing_claim_keys))
    _require(type(existing_total_claims) is int and existing_total_claims >= len(existing_claim_keys))
    delta = int(proposed_claim_key not in existing_claim_keys)
    if len(existing_claim_keys) + delta > 64:
        return ConfigurationCapacityDecision.PER_REF_CLAIM_LIMIT
    if existing_total_claims + delta > 256:
        return ConfigurationCapacityDecision.TOTAL_CLAIM_LIMIT
    return configuration_evidence_capacity(current.plus(reserved_future))


@dataclass(frozen=True)
class ConfigurationSourceProjection:
    identity: EffectAttemptIdentity
    request_fingerprint: str
    original_event_id: str
    original_event_ordinal: int
    kind: RuntimeEffectKind
    source: RuntimeEffectIntentSource
    operation: StartNode | ReconcileNode
    ref: ConfigurationInstanceRef

    def __post_init__(self) -> None:
        _identity(self.identity)
        _ref(self.ref)
        _require(type(self.request_fingerprint) is str and _DIGEST.fullmatch(self.request_fingerprint) is not None)
        _require(type(self.original_event_id) is str and bool(self.original_event_id.strip())
            and len(self.original_event_id.encode("utf-8")) <= 2048
            and all(ord(c) >= 32 for c in self.original_event_id))
        _require(type(self.original_event_ordinal) is int and self.original_event_ordinal >= 0)
        _require(self.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
        _require(type(self.source) is RuntimeEffectIntentSource)
        _require(type(self.operation) in (StartNode, ReconcileNode))
        try:
            _require(replace(self.source) == self.source and replace(self.operation) == self.operation)
        except (TypeError, ValueError):
            raise OperationsRecordError(_ERROR) from None
        _require(self.source.run_id == self.identity.run_id
            and self.source.workspace_id == self.ref.workspace_id
            and self.operation.target.node_id == self.ref.node_id)


@dataclass(frozen=True)
class ConfigurationSourceEvidence:
    state: str
    source: ConfigurationSourceProjection | None = None

    def __post_init__(self) -> None:
        _require(type(self.state) is str and self.state in ("complete", "unavailable", "capacity"))
        if self.state == "complete":
            _require(type(self.source) is ConfigurationSourceProjection)
            ConfigurationSourceProjection.__post_init__(self.source)
        else:
            _require(self.source is None)


@dataclass(frozen=True)
class ConfigurationRefEvidence:
    identity: EffectAttemptIdentity
    ref: ConfigurationInstanceRef
    birth_identity: EffectAttemptIdentity
    birth_artifact_id: str
    source: ConfigurationSourceProjection

    def __post_init__(self) -> None:
        _ref(self.ref)
        _key((self.identity, self.ref.artifact_id))
        _key((self.birth_identity, self.birth_artifact_id))
        _require(type(self.source) is ConfigurationSourceProjection)
        ConfigurationSourceProjection.__post_init__(self.source)
        _require(self.source.identity == self.identity and self.source.ref == self.ref
            and self.birth_artifact_id == self.ref.artifact_id)


@dataclass(frozen=True)
class ConfigurationAllocationEvidence:
    state: str
    birth: ConfigurationRefEvidence | None = None
    claims: tuple[ConfigurationRefEvidence, ...] = ()

    def __post_init__(self) -> None:
        _require(type(self.state) is str and self.state in ("complete", "unavailable", "capacity"))
        _require(type(self.claims) is tuple)
        if self.state != "complete":
            _require(self.birth is None and not self.claims)
            return
        _require(type(self.birth) is ConfigurationRefEvidence and 1 <= len(self.claims) <= 64)
        ConfigurationRefEvidence.__post_init__(self.birth)
        _require(self.birth.identity == self.birth.birth_identity)
        keys = []
        for claim in self.claims:
            _require(type(claim) is ConfigurationRefEvidence)
            ConfigurationRefEvidence.__post_init__(claim)
            _require(claim.ref == self.birth.ref and claim.birth_identity == self.birth.identity)
            keys.append((claim.identity.run_id.value, claim.identity.activity_id,
                claim.identity.attempt, claim.ref.artifact_id))
        _require(keys == sorted(set(keys)) and self.birth in self.claims)


@dataclass(frozen=True)
class ConfigurationAcceptedBinding:
    """Exact observed accepted use and direct birth; no mutation authority."""
    ref: ConfigurationInstanceRef
    source: ConfigurationRefEvidence
    birth: ConfigurationRefEvidence

    def __post_init__(self) -> None:
        _ref(self.ref)
        for value in (self.source, self.birth):
            _require(type(value) is ConfigurationRefEvidence)
            ConfigurationRefEvidence.__post_init__(value)
        _require(self.source.ref == self.ref == self.birth.ref
            and self.source.birth_identity == self.birth.identity == self.birth.birth_identity
            and self.source.birth_artifact_id == self.birth.ref.artifact_id)


@dataclass(frozen=True)
class ConfigurationCurrentEvidence:
    """Closed current snapshot observation with only requested proven bindings."""
    state: str
    workspace_id: str | None = None
    graph_id: str | None = None
    projection_id: str | None = None
    pinned_revision: int | None = None
    manifest_slot_count: int | None = None
    bindings: tuple[ConfigurationAcceptedBinding, ...] = ()

    def __post_init__(self) -> None:
        _require(type(self.state) is str and self.state in ("complete", "unavailable", "capacity"))
        _require(type(self.bindings) is tuple)
        if self.state != "complete":
            _require(all(value is None for value in (self.workspace_id, self.graph_id,
                self.projection_id, self.pinned_revision, self.manifest_slot_count)) and not self.bindings)
            return
        for value in (self.workspace_id, self.graph_id, self.projection_id):
            _require(type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= 2048
                and all(ord(character) >= 32 for character in value))
        _require(type(self.manifest_slot_count) is int and 0 <= self.manifest_slot_count <= 256)
        _require(self.pinned_revision is None or (type(self.pinned_revision) is int and self.pinned_revision >= 0))
        _require(self.pinned_revision is not None or self.manifest_slot_count == 0)
        _require(len(self.bindings) <= self.manifest_slot_count)
        slots, allocations = [], []
        for binding in self.bindings:
            _require(type(binding) is ConfigurationAcceptedBinding)
            ConfigurationAcceptedBinding.__post_init__(binding)
            _require(binding.ref.workspace_id == self.workspace_id)
            slots.append((binding.ref.runtime_id, binding.ref.node_id, binding.ref.artifact_id))
            allocations.append(binding.ref.allocation_id)
        _require(slots == sorted(set(slots)) and len(set(allocations)) == len(allocations))


def _birth_selection(identity: EffectAttemptIdentity,
        selection: ConfigurationInstanceSelection) -> ConfigurationInstanceSelection:
    """Derive identity only; the transactional preparation owner grants admission."""
    _identity(identity)
    refs = []
    for ref in selection.instances:
        fields = dict(workspace_id=ref.workspace_id, run_id=identity.run_id.value,
            activity_id=identity.activity_id, attempt=identity.attempt, runtime_id=ref.runtime_id,
            node_id=ref.node_id, artifact_id=ref.artifact_id, target_path=ref.target_path,
            media_type=ref.media_type.value, file_mode=ref.file_mode.value, content_digest=ref.content_digest)
        allocation = "cfg-" + sha256(b"control-plane-kit.configuration-allocation-birth.v1\x00"
            + rfc8785.dumps(fields)).hexdigest()
        refs.append(replace(ref, allocation_id=allocation))
    return ConfigurationInstanceSelection(tuple(refs))
