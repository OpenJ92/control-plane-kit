"""Bounded inspection of retained uncertainty, never continuation authority."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import re
from typing import Protocol

from control_plane_kit_core.identity import PrincipalKind, TrustedCommandContext
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.policies import PolicyScope
from .errors import ReadModelError


MAX_RECOVERY_PROPOSAL_BYTES = 16384
_ERRORS = {
    "malformed": (400, "activity recovery query is malformed"),
    "forbidden": (403, "activity recovery inspection is forbidden"),
    "missing": (404, "activity recovery evidence was not found"),
    "stale-evidence": (409, "activity recovery evidence has changed"),
    "unavailable": (409, "activity recovery evidence is unavailable"),
    "capacity": (409, "activity recovery evidence exceeds capacity"),
}
_REQUIRED_BLOCKERS = (
    "provider-observation-not-collected",
    "predecessor-quiescence-not-established",
    "continuation-authority-not-established",
)
_BLOCKERS = frozenset((*_REQUIRED_BLOCKERS, "run-not-current", "run-not-running",
                       "plan-stale", "claim-unavailable-or-expired"))


class ActivityRecoveryError(ReadModelError):
    """Closed failure without original exception or candidate payload."""
    def __init__(self, category="unavailable"):
        self.category = category if category in _ERRORS else "unavailable"
        self.status, message = _ERRORS[self.category]
        super().__init__(message)


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _text(value, maximum=2048):
    if (type(value) is not str or not value or len(value.encode("utf-8")) > maximum
            or any(ord(character) < 32 for character in value)):
        raise ValueError


def _digest(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError


@dataclass(frozen=True, slots=True)
class ActivityRecoveryQuery:
    workspace_id: str
    identity: EffectAttemptIdentity
    expected_request_fingerprint: str
    expected_outcome_fingerprint: str

    def __post_init__(self):
        valid = False
        try:
            _text(self.workspace_id)
            if type(self.identity) is not EffectAttemptIdentity:
                raise ValueError
            if EffectAttemptIdentity.from_descriptor(self.identity.descriptor()) != self.identity:
                raise ValueError
            _text(self.identity.run_id.value, 512)
            _text(self.identity.activity_id, 200)
            _digest(self.expected_request_fingerprint)
            _digest(self.expected_outcome_fingerprint)
            valid = True
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
            pass
        if not valid:
            raise ActivityRecoveryError("malformed")

    def descriptor(self):
        return {"workspace_id": self.workspace_id, "identity": self.identity.descriptor(),
                "expected_request_fingerprint": self.expected_request_fingerprint,
                "expected_outcome_fingerprint": self.expected_outcome_fingerprint}

    @classmethod
    def from_mapping(cls, value):
        result = None
        try:
            if type(value) is not dict or set(value) != {
                    "workspace_id", "identity", "expected_request_fingerprint", "expected_outcome_fingerprint"}:
                raise ValueError
            if type(value["identity"]) is not dict:
                raise ValueError
            result = cls(value["workspace_id"], EffectAttemptIdentity.from_descriptor(value["identity"]),
                         value["expected_request_fingerprint"], value["expected_outcome_fingerprint"])
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError, ActivityRecoveryError):
            pass
        if result is None:
            raise ActivityRecoveryError("malformed")
        return result


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names.split()):
        raise ValueError


def _counter(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError


def _validate_document(value):
    _keys(value, "profile status workspace_id identity source evidence current historical_outcome provider_observation blockers")
    if (value["profile"] != "activity-recovery-proposal.v1" or value["status"] != "blocked"
            or value["historical_outcome"] != "uncertain" or value["provider_observation"] != "not-collected"):
        raise ValueError
    _text(value["workspace_id"])
    EffectAttemptIdentity.from_descriptor(value["identity"])
    source = value["source"]
    _keys(source, "request_id session_id plan_id base_graph_id desired_graph_id base_projection_id desired_projection_id desired_revision")
    for key in ("request_id", "session_id", "plan_id", "base_graph_id", "desired_graph_id"):
        _text(source[key])
    for key in ("base_projection_id", "desired_projection_id"):
        if source[key] is not None:
            _text(source[key])
    _counter(source["desired_revision"])
    evidence = value["evidence"]
    _keys(evidence, "request_fingerprint outcome_fingerprint profile original_event_id original_recorded_at outcome_event_id outcome_recorded_at configuration_count configuration_selection_fingerprint configuration_protection")
    for key in ("request_fingerprint", "outcome_fingerprint"):
        _digest(evidence[key])
    for key in ("original_event_id", "original_recorded_at", "outcome_event_id", "outcome_recorded_at"):
        _text(evidence[key])
    if evidence["profile"] not in ("execution-result", "provider-observation", "native-connection"):
        raise ValueError
    count = evidence["configuration_count"]
    if type(count) is not int or not 0 <= count <= 32:
        raise ValueError
    if count:
        _digest(evidence["configuration_selection_fingerprint"])
        if evidence["configuration_protection"] != "validated":
            raise ValueError
    elif (evidence["configuration_selection_fingerprint"] is not None
          or evidence["configuration_protection"] != "not-applicable"):
        raise ValueError
    current = value["current"]
    _keys(current, "snapshot_at latest_run_id run_status base_graph_id base_projection_id desired_graph_id desired_projection_id desired_revision claim")
    for key in ("snapshot_at", "latest_run_id", "run_status", "base_graph_id"):
        _text(current[key])
    for key in ("base_projection_id", "desired_graph_id", "desired_projection_id"):
        if current[key] is not None:
            _text(current[key])
    _counter(current["desired_revision"])
    if current["claim"] is not None:
        claim = current["claim"]
        _keys(claim, "worker_id generation claimed_at lease_expires_at expired")
        for key in ("worker_id", "claimed_at", "lease_expires_at"):
            _text(claim[key])
        _counter(claim["generation"])
        if claim["generation"] == 0 or type(claim["expired"]) is not bool:
            raise ValueError
    blockers = value["blockers"]
    if (type(blockers) is not list or len(blockers) > len(_BLOCKERS)
            or any(type(item) is not str for item in blockers)
            or len(set(blockers)) != len(blockers) or not set(blockers) <= _BLOCKERS
            or not set(_REQUIRED_BLOCKERS) <= set(blockers)):
        raise ValueError


@dataclass(frozen=True, slots=True)
class ActivityRecoveryProposal:
    """Detached snapshot identity; never an approval, reservation or grant."""
    _document: bytes = field(repr=False)

    def __post_init__(self):
        category = "unavailable"
        valid = False
        try:
            if type(self._document) is not bytes:
                raise ValueError
            if len(self._document) > MAX_RECOVERY_PROPOSAL_BYTES:
                category = "capacity"
                raise ValueError
            value = json.loads(self._document)
            _validate_document(value)
            valid = _bytes(value) == self._document
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
            pass
        if not valid:
            raise ActivityRecoveryError(category)

    def descriptor(self):
        return json.loads(self._document)

    @property
    def fingerprint(self):
        return sha256(b"control-plane-kit.activity-recovery-proposal.v1\0" + self._document).hexdigest()


class ActivityRecoverySnapshot(Protocol):
    def inspect(self, query: ActivityRecoveryQuery) -> ActivityRecoveryProposal: ...


class ActivityRecoverySnapshotUnitOfWork(Protocol):
    def activity_recovery_snapshot(self) -> AbstractContextManager[ActivityRecoverySnapshot]: ...


class ActivityRecoveryReadService:
    def __init__(self, snapshot_factory: Callable[[], ActivityRecoverySnapshotUnitOfWork]):
        self._snapshot_factory = snapshot_factory

    def read(self, query: ActivityRecoveryQuery, *, context: TrustedCommandContext):
        if type(query) is not ActivityRecoveryQuery:
            raise ActivityRecoveryError("malformed")
        query = replace(query)
        authorized = False
        try:
            if type(context) is TrustedCommandContext:
                checked = replace(context)
                authorized = (checked.workspace_id == query.workspace_id
                    and checked.principal.identity.kind is PrincipalKind.OPERATOR
                    and PolicyScope.INSTANCE_WORKSPACE_READ in checked.granted_scopes)
        except (ValueError, TypeError, AttributeError):
            pass
        if not authorized:
            raise ActivityRecoveryError("forbidden")
        category = "unavailable"
        try:
            with self._snapshot_factory().activity_recovery_snapshot() as snapshot:
                result = snapshot.inspect(query)
                if type(result) is not ActivityRecoveryProposal:
                    raise ValueError
                result = replace(result)
                data = result.descriptor()
                if (data["workspace_id"] != query.workspace_id or data["identity"] != query.identity.descriptor()
                        or data["evidence"]["request_fingerprint"] != query.expected_request_fingerprint
                        or data["evidence"]["outcome_fingerprint"] != query.expected_outcome_fingerprint):
                    raise ValueError
            return result
        except Exception as error:
            if type(error) is ActivityRecoveryError:
                category = error.category
        raise ActivityRecoveryError(category)


__all__ = ["ActivityRecoveryQuery", "ActivityRecoveryProposal", "ActivityRecoveryError", "ActivityRecoveryReadService"]
