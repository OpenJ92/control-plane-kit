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
    domains = {
        ConfigurationCleanupProposal: b"control-plane-kit.configuration-cleanup-proposal.v1\x00",
        ConfigurationCleanupProposalV2: b"control-plane-kit.configuration-cleanup-proposal.v2\x00",
    }
    domain = domains.get(type(proposal))
    _require(domain is not None)
    proposal.__post_init__()
    return sha256(domain + proposal.canonical).hexdigest()


@dataclass(frozen=True)
class ConfigurationCleanupInspectionResult:
    state: str
    inspection: ConfigurationCleanupInspection | ConfigurationCleanupInspectionV2 | None = None
    reason: str | None = None

    def __post_init__(self):
        _require(type(self.state) is str and self.state in ("complete", "unavailable", "capacity"))
        if self.state == "complete":
            _require(type(self.inspection) in (ConfigurationCleanupInspection, ConfigurationCleanupInspectionV2)
                     and self.reason is None)
            self.inspection.__post_init__()
        else:
            _require(self.inspection is None and self.reason == "evidence-" + self.state)


# V2 separates physical candidates from original-selection provenance. These
# detached commitments express a proof obligation; only the store can prove it.
_V2_MEMBER = {"artifact_id", "allocation_id", "ref_fingerprint"}
_V2_TRANSFER = _V2_MEMBER | {"source_identity", "acceptance_revision"}
_V2_WITNESS = (_WITNESS - {"selection_allocations"}) | {"selection_members"}


def _member(value):
    _object(value, _V2_MEMBER)
    _require(type(value["artifact_id"]) is str and _ARTIFACT.fullmatch(value["artifact_id"]) is not None)
    _scope(value["allocation_id"])
    _digest(value["ref_fingerprint"])
    return value["artifact_id"], value["allocation_id"], value["ref_fingerprint"]


def _ref_member(ref):
    return ref.artifact_id, ref.allocation_id, sha256(
        ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest()


def _selection_members(values):
    _require(type(values) is list and 1 <= len(values) <= 32)
    members = tuple(_member(value) for value in values)
    # Ordinary Core selection has unique artifacts and sorts artifact first;
    # physical cleanup candidates instead sort allocation identity.
    artifacts = tuple(value[0] for value in members)
    _require(artifacts == tuple(sorted(set(artifacts))))
    _require(len({value[1] for value in members}) == len(members))
    return members


def _v2_sources(values):
    _require(type(values) is list and len(values) <= 64)
    keys = tuple(_identity_key(_identity(value)) for value in values)
    _require(keys == tuple(sorted(set(keys))))
    return keys


def _transfers(values):
    _require(type(values) is list and len(values) <= 8256)
    result, ordered = {}, []
    for value in values:
        _object(value, _V2_TRANSFER)
        identity = _identity_key(_identity(value["source_identity"]))
        member = _member({field: value[field] for field in _V2_MEMBER})
        _integer(value["acceptance_revision"])
        key = identity, member[0]
        ordered.append(key)
        result[key] = member
    _require(tuple(ordered) == tuple(sorted(set(ordered))))
    return result


def _v2_witness(value, node_id):
    _object(value, _V2_WITNESS)
    identity = _identity_key(_identity(value["source_identity"]))
    _require(value["effect_kind"] == "configuration-activity.v1")
    operation = activity_operation_from_descriptor(value["operation"])
    _require(type(operation) in (StartNode, ReconcileNode) and type(operation.target) is NodeTarget)
    _require(activity_operation_descriptor(operation) == value["operation"] and operation.target.node_id == node_id)
    for name in ("request_fingerprint", "selection_fingerprint", "outcome_fingerprint"):
        _digest(value[name])
    _event(value, "original_")
    _event(value, "direct_")
    _require(value["direct_event_ordinal"] > value["original_event_ordinal"])
    _require(value["direct_event_id"] != value["original_event_id"])
    _require(value["result_kind"] in ("succeeded", "failed"))
    return identity, _selection_members(value["selection_members"])


def _v2_coverage(candidates, refs, uses, selections, transfers):
    """Validate exact N/T coverage and positive roots, without asserting DB truth."""
    _require(sum(len(value) for value in uses.values()) <= 256)
    _require(len(selections) <= 256 and sum(len(value) for value in selections.values()) <= 8192)
    pairs = {(identity, artifact): member for identity, members in uses.items()
             for artifact, member in members.items()}
    _require(set(pairs).isdisjoint(transfers))
    known = {ref.allocation_id: _ref_member(ref) for ref in refs.values()}
    required = set()
    for identity, members in selections.items():
        selected = {member[0]: member for member in members}
        _require(identity in uses and set(uses[identity]) <= set(selected))
        for member in members:
            artifact, allocation, _ = member
            _require(allocation not in known or known[allocation] == member)
            known[allocation] = member
            pair = identity, artifact
            if pair in pairs:
                _require(pairs[pair] == member)
            else:
                _require(transfers.get(pair) == member)
                required.add(pair)
    for candidate in candidates:
        member = _ref_member(refs[candidate["ref"]["allocation_id"]])
        for name in ("seed", "birth"):
            locator = candidate[name]
            pair = _identity_key(_identity(locator["source_identity"])), locator["artifact_id"]
            if pair in pairs:
                _require(pairs[pair] == member)
            else:
                _require(transfers.get(pair) == member)
                required.add(pair)
    _require(set(transfers) == required)


def _add_uses(uses, identities, member):
    for identity in identities:
        existing = uses.setdefault(identity, {})
        _require(member[0] not in existing)
        existing[member[0]] = member


def _proposal_v2(document):
    _object(document, {"profile", "context", "candidates", "invocations", "accepted_transfers"})
    _require(document["profile"] == "configuration-cleanup-proposal.v2")
    refs = _candidates(document, {"ref", "seed", "birth", "protecting_uses", "proposed_closures"})
    uses = {}
    for row in document["candidates"]:
        identities = _v2_sources(row["protecting_uses"])
        _require(identities == _v2_sources(row["proposed_closures"]))
        _add_uses(uses, identities, _ref_member(refs[row["ref"]["allocation_id"]]))
    values = document["invocations"]
    _require(type(values) is list and len(values) <= 256)
    witnesses = tuple(_v2_witness(value, next(iter(refs.values())).node_id) for value in values)
    identities = tuple(identity for identity, _ in witnesses)
    _require(identities == tuple(sorted(set(identities))) and set(identities) == set(uses))
    _v2_coverage(document["candidates"], refs, uses, dict(witnesses), _transfers(document["accepted_transfers"]))
    return _canonical(document)


def _summary_v2(value):
    _require(type(value) is dict and "unselected_count" not in value and "uncovered_outstanding_count" in value)
    return _summary({("unselected_count" if field == "uncovered_outstanding_count" else field): item
                     for field, item in value.items()})


def _inspection_v2(document):
    _object(document, {"profile", "context", "candidates", "accepted_transfers", "invocation_accounting"})
    _require(document["profile"] == "configuration-cleanup-inspection.v2")
    refs = _candidates(document, {"ref", "seed", "birth", "invocations", "blockers"})
    summaries, uses = {}, {}
    for row in document["candidates"]:
        values, blockers = row["invocations"], row["blockers"]
        _require(type(values) is list and len(values) <= 64)
        _require(type(blockers) is list and all(type(value) is str and value in _BLOCKERS for value in blockers))
        _require(blockers == sorted(set(blockers)))
        identities = []
        for value in values:
            identity = _summary_v2(value)
            _require(identity not in summaries or summaries[identity] == value)
            summaries[identity] = value
            identities.append(identity)
        _require(tuple(identities) == tuple(sorted(set(identities))))
        _add_uses(uses, identities, _ref_member(refs[row["ref"]["allocation_id"]]))
        _require(("unresolved-invocation" in blockers) == any(value["kind"] != "completed" for value in values))
        _require(("incomplete-invocation-selection" in blockers) == any(
            value["uncovered_outstanding_count"] > 0 for value in values))
    rows = document["invocation_accounting"]
    _require(type(rows) is list and len(rows) <= 256)
    selections, ordered = {}, []
    for row in rows:
        _object(row, {"source_identity", "selection_members"})
        identity = _identity_key(_identity(row["source_identity"]))
        selections[identity] = _selection_members(row["selection_members"])
        ordered.append(identity)
    _require(tuple(ordered) == tuple(sorted(set(ordered))))
    complete = {identity for identity, value in summaries.items() if value["uncovered_outstanding_count"] == 0}
    _require(set(selections) == complete)
    for identity, value in summaries.items():
        _require(value["uncovered_outstanding_count"] <= value["selection_count"] - len(uses[identity]))
        if identity in selections:
            _require(len(selections[identity]) == value["selection_count"])
    _v2_coverage(document["candidates"], refs, uses, selections, _transfers(document["accepted_transfers"]))
    return _canonical(document)


@dataclass(frozen=True, repr=False)
class ConfigurationCleanupProposalV2:
    """Full selection accounting; decoding never admits a transfer or deletion."""
    canonical: bytes

    def __post_init__(self):
        _validated_bytes(self.canonical, _proposal_v2)

    def descriptor(self):
        self.__post_init__()
        return json.loads(self.canonical)


@dataclass(frozen=True, repr=False)
class ConfigurationCleanupInspectionV2:
    """Bounded observed commitments; a complete inspection may still be blocked."""
    canonical: bytes

    def __post_init__(self):
        _validated_bytes(self.canonical, _inspection_v2)

    def descriptor(self):
        self.__post_init__()
        return json.loads(self.canonical)

    @property
    def evidence_digest(self):
        self.__post_init__()
        return sha256(b"control-plane-kit.configuration-cleanup-inspection.v2\x00" + self.canonical).hexdigest()


class ConfigurationCleanupProposalV2Codec:
    def decode(self, document):
        return ConfigurationCleanupProposalV2(_validated(document, _proposal_v2))

    def encode(self, value):
        _require(type(value) is ConfigurationCleanupProposalV2)
        return value.descriptor()


class ConfigurationCleanupInspectionV2Codec:
    def decode(self, document):
        return ConfigurationCleanupInspectionV2(_validated(document, _inspection_v2))

    def encode(self, value):
        _require(type(value) is ConfigurationCleanupInspectionV2)
        return value.descriptor()


def configuration_cleanup_inspection_from_proposal(proposal: ConfigurationCleanupProposalV2) -> ConfigurationCleanupInspectionV2:
    """Project an eligible v2 review deterministically, without reading live truth."""
    document = ConfigurationCleanupProposalV2Codec().encode(proposal)
    witnesses = {_identity_key(_identity(value["source_identity"])): value for value in document["invocations"]}
    fields = ("source_identity", "original_event_id", "original_event_ordinal", "request_fingerprint",
              "selection_fingerprint", "direct_event_id", "direct_event_ordinal", "result_kind", "outcome_fingerprint")
    candidates = []
    for row in document["candidates"]:
        summaries = []
        for identity in row["protecting_uses"]:
            witness = witnesses[_identity_key(_identity(identity))]
            summary = {field: witness[field] for field in fields}
            summary.update(kind="completed", attempt_status=witness["result_kind"],
                selection_count=len(witness["selection_members"]), uncovered_outstanding_count=0)
            summaries.append(summary)
        candidates.append(dict(ref=row["ref"], seed=row["seed"], birth=row["birth"], invocations=summaries, blockers=[]))
    accounting = [dict(source_identity=value["source_identity"], selection_members=value["selection_members"])
                  for value in document["invocations"]]
    return ConfigurationCleanupInspectionV2Codec().decode(dict(profile="configuration-cleanup-inspection.v2",
        context=document["context"], candidates=candidates, accepted_transfers=document["accepted_transfers"],
        invocation_accounting=accounting))
