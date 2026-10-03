"""Closed immutable cleanup review values. None confer execution authority."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

import rfc8785

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRef, ConfigurationInstanceRefCodec, ConfigurationInstanceSelection,
)
from control_plane_kit_core.configuration_invocation import configuration_invocation_selection_fingerprint
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, StartNode
from control_plane_kit_core.planning.codec import activity_operation_descriptor, activity_operation_from_descriptor


MAX_CLEANUP_DOCUMENT_BYTES = 512 * 1024
MAX_CANONICAL_REVISION = 9007199254740991
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ARTIFACT = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_PINS = {"base_graph_id", "base_realized_projection_id", "desired_graph_id",
         "desired_realized_projection_id", "desired_graph_revision"}
_CONTEXT = _PINS | {"workspace_id", "session_id", "current_occurrence"}
_BLOCKERS = {"current-selected-use", "unresolved-invocation", "incomplete-invocation-selection"}
_SUMMARY = {"kind", "source_identity", "original_event_id", "original_event_ordinal",
            "request_fingerprint", "selection_fingerprint", "selection_count", "unselected_count"}
_TERMINAL = {"result_kind", "attempt_status", "direct_event_id", "direct_event_ordinal", "outcome_fingerprint"}
_WITNESS = {"source_identity", "effect_kind", "operation", "original_event_id", "original_event_ordinal",
            "request_fingerprint", "selection_fingerprint", "selection_allocations", "direct_event_id",
            "direct_event_ordinal", "result_kind", "outcome_fingerprint"}


class ConfigurationCleanupContractError(ValueError):
    """Fixed redacted failure of a cleanup review value."""


def _require(condition):
    if not condition:
        raise ConfigurationCleanupContractError("configuration cleanup review is malformed")


def _object(value, fields):
    _require(type(value) is dict and set(value) == fields)
    return value


def _text(value):
    _require(type(value) is str and 1 <= len(value) <= 512
             and all(ord(char) >= 32 for char in value))


def _scope(value):
    _require(type(value) is str and _SCOPE.fullmatch(value) is not None)


def _digest(value):
    _require(type(value) is str and _DIGEST.fullmatch(value) is not None)


def _integer(value, minimum=0, maximum=MAX_CANONICAL_REVISION):
    _require(type(value) is int and minimum <= value <= maximum)


def _identity_document(identity):
    _require(type(identity) is EffectAttemptIdentity and type(identity.run_id) is RunId)
    _require(EffectAttemptIdentity(RunId(identity.run_id.value), identity.activity_id, identity.attempt) == identity)
    _integer(identity.attempt, 1, 2147483647)
    return {"run_id": identity.run_id.value, "activity_id": identity.activity_id, "attempt": identity.attempt}


def _identity(value):
    _object(value, {"run_id", "activity_id", "attempt"})
    identity = EffectAttemptIdentity(RunId(value["run_id"]), value["activity_id"], value["attempt"])
    _identity_document(identity)
    return identity


def _identity_key(value):
    identity = _identity(value) if type(value) is dict else value
    return identity.run_id.value, identity.activity_id, identity.attempt


def _locator(value):
    _object(value, {"source_identity", "artifact_id"})
    _identity(value["source_identity"])
    _require(type(value["artifact_id"]) is str and _ARTIFACT.fullmatch(value["artifact_id"]) is not None)


def _pins(value, *, owner_integer=False):
    for name in _PINS - {"desired_graph_revision"}:
        _text(value[name])
    _integer(value["desired_graph_revision"], maximum=9223372036854775807 if owner_integer else MAX_CANONICAL_REVISION)


@dataclass(frozen=True)
class ConfigurationCleanupExpectedContext:
    base_graph_id: str
    base_realized_projection_id: str
    desired_graph_id: str
    desired_realized_projection_id: str
    desired_graph_revision: int

    def __post_init__(self):
        _pins(vars(self), owner_integer=True)

    def descriptor(self):
        self.__post_init__()
        return {name: getattr(self, name) for name in _PINS}


@dataclass(frozen=True)
class ConfigurationCleanupSourceSelector:
    source_identity: EffectAttemptIdentity
    artifact_id: str
    expected_ref: ConfigurationInstanceRef

    def __post_init__(self):
        identity = _identity_document(self.source_identity)
        _locator({"source_identity": identity, "artifact_id": self.artifact_id})
        ConfigurationInstanceRefCodec().encode(self.expected_ref)
        _require(self.expected_ref.artifact_id == self.artifact_id)

    def descriptor(self):
        self.__post_init__()
        return {"source_identity": _identity_document(self.source_identity), "artifact_id": self.artifact_id,
                "expected_ref": ConfigurationInstanceRefCodec().encode(self.expected_ref)}


def _context(value):
    _object(value, _CONTEXT)
    _pins(value)
    _scope(value["workspace_id"])
    _text(value["session_id"])
    occurrence = value["current_occurrence"]
    _require(type(occurrence) is dict)
    if occurrence.get("kind") == "workspace-initialization":
        _object(occurrence, {"kind", "workspace_id", "profile", "initial_graph_id", "initial_projection_id",
            "graph_descriptor_sha256", "projection_digest", "configuration_slot_count", "created_by",
            "creation_idempotency_key"})
        _require(occurrence["profile"] == "workspace-initialization.v1")
        for field in ("initial_graph_id", "initial_projection_id", "created_by"):
            _text(occurrence[field])
        for field in ("graph_descriptor_sha256", "projection_digest"):
            _digest(occurrence[field])
        _integer(occurrence["configuration_slot_count"], maximum=0)
        key = occurrence["creation_idempotency_key"]
        _require(type(key) is str and 1 <= len(key) <= 200)
        pair = occurrence["initial_graph_id"], occurrence["initial_projection_id"]
    elif occurrence.get("kind") == "configuration-acceptance":
        _object(occurrence, {"kind", "workspace_id", "pinned_revision", "graph_id", "projection_id",
            "projection_digest", "action_id", "event_id", "run_id", "request_id", "plan_id", "slot_count", "slot_digest"})
        for field in ("graph_id", "projection_id", "action_id", "event_id", "run_id", "request_id", "plan_id"):
            _text(occurrence[field])
        RunId(occurrence["run_id"])
        _integer(occurrence["pinned_revision"])
        _integer(occurrence["slot_count"], maximum=256)
        _digest(occurrence["projection_digest"])
        _digest(occurrence["slot_digest"])
        pair = occurrence["graph_id"], occurrence["projection_id"]
    else:
        _require(False)
    _require(occurrence["workspace_id"] == value["workspace_id"])
    _require(pair == (value["base_graph_id"], value["base_realized_projection_id"]))


def _canonical(document):
    encoded = None
    try:
        encoded = rfc8785.dumps(document)
    except (TypeError, ValueError, OverflowError, RecursionError):
        pass
    _require(encoded is not None and 0 < len(encoded) <= MAX_CLEANUP_DOCUMENT_BYTES)
    return encoded


def _sources(values):
    _require(type(values) is list and 1 <= len(values) <= 64)
    keys = tuple(_identity_key(_identity(item)) for item in values)
    _require(keys == tuple(sorted(set(keys))))
    return keys


def _candidates(document, fields):
    _context(document["context"])
    rows = document["candidates"]
    _require(type(rows) is list and 1 <= len(rows) <= 32)
    refs = []
    for row in rows:
        _object(row, fields)
        ref = ConfigurationInstanceRefCodec().decode(row["ref"])
        refs.append(ref)
        for field in ("seed", "birth"):
            _locator(row[field])
            _require(row[field]["artifact_id"] == ref.artifact_id)
    ids = tuple(ref.allocation_id for ref in refs)
    _require(ids == tuple(sorted(set(ids))))
    _require(len({(ref.workspace_id, ref.runtime_id, ref.node_id) for ref in refs}) == 1)
    _require(refs[0].workspace_id == document["context"]["workspace_id"])
    return dict(zip(ids, refs))


def _event(value, prefix):
    _text(value[prefix + "event_id"])
    _integer(value[prefix + "event_ordinal"], 1, 2147483647)


def _witness(value, refs):
    _object(value, _WITNESS)
    identity = _identity(value["source_identity"])
    _require(value["effect_kind"] == "configuration-activity.v1")
    operation = activity_operation_from_descriptor(value["operation"])
    _require(type(operation) in (StartNode, ReconcileNode) and type(operation.target) is NodeTarget)
    _require(activity_operation_descriptor(operation) == value["operation"])
    for field in ("request_fingerprint", "selection_fingerprint", "outcome_fingerprint"):
        _digest(value[field])
    _event(value, "original_")
    _event(value, "direct_")
    _require(value["direct_event_ordinal"] > value["original_event_ordinal"])
    _require(value["original_event_id"] != value["direct_event_id"])
    _require(value["result_kind"] in ("succeeded", "failed"))
    ids = value["selection_allocations"]
    _require(type(ids) is list and 1 <= len(ids) <= 32 and all(type(item) is str for item in ids))
    _require(len(set(ids)) == len(ids) and all(item in refs for item in ids))
    selected = ConfigurationInstanceSelection(tuple(refs[item] for item in ids))
    _require(ids == [ref.allocation_id for ref in selected.instances])
    _require(value["selection_fingerprint"] == configuration_invocation_selection_fingerprint(selected))
    _require(operation.target.node_id == selected.instances[0].node_id)
    return _identity_key(identity)


def _proposal(document):
    _object(document, {"profile", "context", "candidates"})
    _require(document["profile"] == "configuration-cleanup-proposal.v1")
    refs = _candidates(document, {"ref", "seed", "birth", "protecting_uses", "completion_witnesses", "proposed_closures"})
    repeated, memberships, count = {}, {}, 0
    for row in document["candidates"]:
        protection = _sources(row["protecting_uses"])
        _require(protection == _sources(row["proposed_closures"]))
        _require(type(row["completion_witnesses"]) is list and len(row["completion_witnesses"]) == len(protection))
        keys = []
        for witness in row["completion_witnesses"]:
            key = _witness(witness, refs)
            keys.append(key)
            _require(row["ref"]["allocation_id"] in witness["selection_allocations"])
            _require(key not in repeated or repeated[key] == witness)
            repeated[key] = witness
        _require(tuple(keys) == protection)
        memberships[row["ref"]["allocation_id"]] = set(keys)
        _require(all(_identity_key(row[field]["source_identity"]) in protection for field in ("seed", "birth")))
        count += len(protection)
    _require(count <= 256)
    for key, witness in repeated.items():
        _require(all(key in memberships[allocation] for allocation in witness["selection_allocations"]))
    return _canonical(document)


def _summary(value):
    _require(type(value) is dict)
    kind = value.get("kind")
    _require(kind in ("active", "terminal-unprofiled", "completed"))
    _object(value, _SUMMARY if kind == "active" else _SUMMARY | _TERMINAL)
    identity = _identity(value["source_identity"])
    _event(value, "original_")
    _digest(value["request_fingerprint"])
    _digest(value["selection_fingerprint"])
    _integer(value["selection_count"], 1, 32)
    _integer(value["unselected_count"], 0, value["selection_count"])
    if kind != "active":
        _event(value, "direct_")
        _digest(value["outcome_fingerprint"])
        _require(value["direct_event_ordinal"] > value["original_event_ordinal"])
        _require(value["direct_event_id"] != value["original_event_id"])
        allowed = ("succeeded", "failed") if kind == "completed" else ("succeeded", "failed", "unsupported", "uncertain")
        _require(value["result_kind"] in allowed and value["attempt_status"] == value["result_kind"])
    return _identity_key(identity)


def _inspection(document):
    _object(document, {"profile", "context", "candidates"})
    _require(document["profile"] == "configuration-cleanup-inspection.v1")
    _candidates(document, {"ref", "seed", "birth", "invocations", "blockers"})
    repeated, count = {}, 0
    for row in document["candidates"]:
        summaries, blockers = row["invocations"], row["blockers"]
        _require(type(summaries) is list and 1 <= len(summaries) <= 64)
        _require(type(blockers) is list and all(type(item) is str and item in _BLOCKERS for item in blockers))
        _require(blockers == sorted(set(blockers)))
        keys = []
        for summary in summaries:
            key = _summary(summary)
            keys.append(key)
            _require(key not in repeated or repeated[key] == summary)
            repeated[key] = summary
        _require(tuple(keys) == tuple(sorted(set(keys))))
        _require(all(_identity_key(row[field]["source_identity"]) in keys for field in ("seed", "birth")))
        _require(("unresolved-invocation" in blockers) == any(value["kind"] != "completed" for value in summaries))
        _require(("incomplete-invocation-selection" in blockers) == any(value["unselected_count"] > 0 for value in summaries))
        count += len(summaries)
    _require(count <= 256)
    return _canonical(document)


def _validated(document, validate):
    result = None
    try:
        result = validate(document)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        pass
    _require(result is not None)
    return result


def _validated_bytes(canonical, validate):
    result = None
    try:
        _require(type(canonical) is bytes and 0 < len(canonical) <= MAX_CLEANUP_DOCUMENT_BYTES)
        result = validate(json.loads(canonical))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        pass
    _require(result is not None and result == canonical)


@dataclass(frozen=True, repr=False)
class ConfigurationCleanupProposal:
    """An immutable canonical review value; decoding does not prove its origin."""
    canonical: bytes

    def __post_init__(self):
        _validated_bytes(self.canonical, _proposal)

    def descriptor(self):
        self.__post_init__()
        return json.loads(self.canonical)


@dataclass(frozen=True, repr=False)
class ConfigurationCleanupInspection:
    """Bounded detached inspection with no durable reservation or authority."""
    canonical: bytes

    def __post_init__(self):
        _validated_bytes(self.canonical, _inspection)

    def descriptor(self):
        self.__post_init__()
        return json.loads(self.canonical)

    @property
    def evidence_digest(self):
        self.__post_init__()
        return sha256(b"control-plane-kit.configuration-cleanup-inspection.v1\x00" + self.canonical).hexdigest()


class ConfigurationCleanupProposalCodec:
    def decode(self, document):
        return ConfigurationCleanupProposal(_validated(document, _proposal))

    def encode(self, value):
        _require(type(value) is ConfigurationCleanupProposal)
        return value.descriptor()


class ConfigurationCleanupInspectionCodec:
    def decode(self, document):
        return ConfigurationCleanupInspection(_validated(document, _inspection))

    def encode(self, value):
        _require(type(value) is ConfigurationCleanupInspection)
        return value.descriptor()


def configuration_cleanup_proposal_fingerprint(proposal):
    _require(type(proposal) is ConfigurationCleanupProposal)
    proposal.__post_init__()
    return sha256(b"control-plane-kit.configuration-cleanup-proposal.v1\x00" + proposal.canonical).hexdigest()


@dataclass(frozen=True)
class ConfigurationCleanupInspectionResult:
    state: str
    inspection: ConfigurationCleanupInspection | None = None
    reason: str | None = None

    def __post_init__(self):
        _require(type(self.state) is str and self.state in ("complete", "unavailable", "capacity"))
        if self.state == "complete":
            _require(type(self.inspection) is ConfigurationCleanupInspection and self.reason is None)
            self.inspection.__post_init__()
        else:
            _require(self.inspection is None and self.reason == "evidence-" + self.state)
