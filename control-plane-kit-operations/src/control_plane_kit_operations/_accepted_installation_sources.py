"""Closed acceptance locators; positive installation proof belongs to the store."""
from dataclasses import dataclass
from hashlib import sha256
import json
import re

import rfc8785

from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.runtime_effect_observation import _exact_text
from control_plane_kit_operations.records import OperationsRecordError, _validate_text


_PROFILE = "accepted-installation-sources.v1"
_DOMAIN = b"cpk.accepted-installation-sources.v1\0"
_MAX_BYTES = 1048576
_MAX_BINDINGS = 1024


def _require(value):
    if not value:
        raise OperationsRecordError("accepted installation sources are invalid")


def _text(value):
    _exact_text(value, "accepted installation identity")
    _require(bool(value.strip()))


def _digest(value):
    _require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None)


@dataclass(frozen=True, slots=True, repr=False)
class _GeneratedAllocationSource:
    source_event_id: str
    terminal_event_id: str
    journal_prefix_digest: str

    def __post_init__(self):
        _validate_text(self.source_event_id, "accepted allocation event")
        _validate_text(self.terminal_event_id, "accepted allocation event")
        _require(self.source_event_id != self.terminal_event_id)
        _digest(self.journal_prefix_digest)

    def descriptor(self):
        self.__post_init__()
        return {"source_event_id": self.source_event_id, "terminal_event_id": self.terminal_event_id,
            "journal_prefix_digest": self.journal_prefix_digest}


@dataclass(frozen=True, slots=True, repr=False)
class _InstallationSource:
    runtime_id: str
    node_id: str
    source: EffectAttemptIdentity
    accepted_revision: int
    original_request_fingerprint: str
    outcome_fingerprint: str
    generated_allocations: tuple[_GeneratedAllocationSource, ...]

    def __post_init__(self):
        _text(self.runtime_id)
        _text(self.node_id)
        _require(type(self.source) is EffectAttemptIdentity)
        EffectAttemptIdentity.__post_init__(self.source)
        type(self.source.run_id).__post_init__(self.source.run_id)
        _require(type(self.accepted_revision) is int and 0 <= self.accepted_revision <= 9007199254740991)
        _digest(self.original_request_fingerprint)
        _digest(self.outcome_fingerprint)
        _require(type(self.generated_allocations) is tuple and len(self.generated_allocations) <= _MAX_BINDINGS)
        for value in self.generated_allocations:
            _require(type(value) is _GeneratedAllocationSource)
            value.__post_init__()
        keys = tuple(value.source_event_id for value in self.generated_allocations)
        _require(keys == tuple(sorted(set(keys))))

    @property
    def key(self):
        return self.runtime_id, self.node_id

    def descriptor(self):
        self.__post_init__()
        return {"runtime_id": self.runtime_id, "node_id": self.node_id,
            "source": {"run_id": self.source.run_id.value, "activity_id": self.source.activity_id,
                "attempt": self.source.attempt}, "accepted_revision": self.accepted_revision,
            "original_request_fingerprint": self.original_request_fingerprint,
            "outcome_fingerprint": self.outcome_fingerprint,
            "generated_allocations": [value.descriptor() for value in self.generated_allocations]}


@dataclass(frozen=True, slots=True, repr=False)
class _InstallationSources:
    bindings: tuple[_InstallationSource, ...]

    def __post_init__(self):
        _require(type(self.bindings) is tuple and len(self.bindings) <= _MAX_BINDINGS)
        generated = {}
        for binding in self.bindings:
            _require(type(binding) is _InstallationSource)
            binding.__post_init__()
            for value in binding.generated_allocations:
                _require(generated.setdefault(value.source_event_id, value) == value)
        keys = tuple(binding.key for binding in self.bindings)
        _require(keys == tuple(sorted(set(keys))))

    def canonical_bytes(self):
        self.__post_init__()
        value = rfc8785.dumps({"profile": _PROFILE,
            "bindings": [binding.descriptor() for binding in self.bindings]})
        _require(len(value) <= _MAX_BYTES)
        return value

    @property
    def digest(self):
        return sha256(_DOMAIN + self.canonical_bytes()).hexdigest()

    @property
    def commitment(self):
        return {"installation_source_count": len(self.bindings), "installation_sources_digest": self.digest}

    @classmethod
    def decode(cls, preimage):
        valid, result = False, None
        try:
            _require(type(preimage) is str and len(preimage.encode("utf-8")) <= _MAX_BYTES)
            def pairs(items):
                result = dict(items)
                _require(len(result) == len(items))
                return result
            value = json.loads(preimage, object_pairs_hook=pairs)
            _require(type(value) is dict and set(value) == {"profile", "bindings"}
                and value["profile"] == _PROFILE and type(value["bindings"]) is list
                and len(value["bindings"]) <= _MAX_BINDINGS)
            bindings = []
            for item in value["bindings"]:
                _require(type(item) is dict and set(item) == {"runtime_id", "node_id", "source",
                    "accepted_revision", "original_request_fingerprint", "outcome_fingerprint", "generated_allocations"})
                source = item["source"]
                _require(type(source) is dict and set(source) == {"run_id", "activity_id", "attempt"})
                _require(type(item["generated_allocations"]) is list)
                allocations = []
                for allocation in item["generated_allocations"]:
                    _require(type(allocation) is dict and set(allocation) == {
                        "source_event_id", "terminal_event_id", "journal_prefix_digest"})
                    allocations.append(_GeneratedAllocationSource(**allocation))
                bindings.append(_InstallationSource(item["runtime_id"], item["node_id"],
                    EffectAttemptIdentity.from_descriptor(source), item["accepted_revision"],
                    item["original_request_fingerprint"], item["outcome_fingerprint"], tuple(allocations)))
            result = cls(tuple(bindings))
            _require(result.canonical_bytes() == preimage.encode("utf-8"))
            valid = True
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            pass
        _require(valid)
        return result
