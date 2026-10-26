"""Bounded command-owned invocation history; records never grant a call."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re

import rfc8785

from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.effect_permissions import ExecutionCommandReference
from control_plane_kit_operations.records import OperationsRecordError


class ExecutionInvocationControlError(Exception):
    """Dependency-neutral control, with only closed codes/public correlation."""
    code = "invocation-control"

    def __init__(self, invocation_id=None):
        if invocation_id is not None and not _sha(invocation_id):
            raise OperationsRecordError("invocation correlation is malformed")
        self.invocation_id = invocation_id
        super().__init__(self.code)


class ExecutionInvocationDenied(ExecutionInvocationControlError):
    code = "authorization-denied"


class ExecutionInvocationExisting(ExecutionInvocationControlError):
    code = "invocation-already-admitted"


class ExecutionInvocationPlanned(ExecutionInvocationControlError):
    code = "known-no-final-dispatch"


class ExecutionInvocationConflict(ExecutionInvocationControlError):
    code = "invocation-conflict"


class ExecutionInvocationKind(StrEnum):
    ENDPOINT_OBSERVATION = "endpoint-observation"
    SECRET_RESOLUTION = "secret-resolution"
    SIGNED_HEALTH_TRANSPORT = "signed-health-transport"
    PREPARED_RUNTIME_DISPATCH = "prepared-runtime-dispatch"
    RECONCILIATION_OBSERVATION = "reconciliation-observation"


class ExecutionInvocationDisposition(StrEnum):
    PLANNED = "planned"
    ADMITTED = "admitted"
    DENIED = "denied"


class ExecutionInvocationOutcome(StrEnum):
    PENDING = "pending"
    RETURNED = "returned"
    FAILED = "failed"
    UNKNOWN = "unknown"


_FINAL_KINDS = (ExecutionInvocationKind.SIGNED_HEALTH_TRANSPORT,
    ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
_DENIAL_CODES = frozenset(("workspace-permission-denied", "required-scope-denied",
    "management-target-denied", "management-authority-denied", "management-assignment-denied",
    "secret-use-denied"))
_COMPLETION_CODES = frozenset(("capability-failed", "capability-outcome-unknown", "recording-uncertain"))
_ERROR = "invocation record is malformed"


def _require(condition):
    if not condition:
        raise OperationsRecordError(_ERROR)


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _identifier(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value) is not None


def _text(value):
    return type(value) is str and 1 <= len(value) <= 512 and all(ord(character) >= 32 for character in value)


def _revision(value):
    return type(value) is int and 1 <= value <= 9223372036854775807


def _time(value):
    try:
        return datetime.fromisoformat(validate_canonical_utc_timestamp(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    raise OperationsRecordError(_ERROR)


@dataclass(frozen=True)
class ExecutionInvocationDecision:
    permission_revision: int | None
    assignment_id: str | None = None
    assignment_revision: int | None = None
    denial_code: str | None = None

    def __post_init__(self):
        _require(self.permission_revision is None or _revision(self.permission_revision))
        _require((self.assignment_id is None) == (self.assignment_revision is None))
        if self.assignment_id is not None:
            _require(type(self.assignment_id) is str
                and re.fullmatch(r"mra_[0-9a-f]{64}", self.assignment_id) is not None
                and _revision(self.assignment_revision))
        _require(self.denial_code is None or (type(self.denial_code) is str and self.denial_code in _DENIAL_CODES))
        _require(not self.allowed or self.permission_revision is not None)
        if self.assignment_id is not None:
            _require(self.permission_revision is not None and self.denial_code in (None, "secret-use-denied"))

    @property
    def allowed(self):
        return self.denial_code is None

    def descriptor(self):
        return dict(permission_revision=self.permission_revision, assignment_id=self.assignment_id,
            assignment_revision=self.assignment_revision, denial_code=self.denial_code)

    @classmethod
    def from_descriptor(cls, payload):
        _require(type(payload) is dict and len(payload) == 4 and set(payload) == {
            "permission_revision", "assignment_id", "assignment_revision", "denial_code"})
        return cls(**payload)


@dataclass(frozen=True)
class ExecutionInvocationRecord:
    command: ExecutionCommandReference
    workspace_id: str
    activity_id: str
    kind: ExecutionInvocationKind
    target_fingerprint: str
    created_at: str
    attempt: int | None = None
    original_event_id: str | None = None
    request_fingerprint: str | None = None
    authorization_id: str | None = None
    disposition: ExecutionInvocationDisposition = ExecutionInvocationDisposition.PLANNED
    decision_at: str | None = None
    decision: ExecutionInvocationDecision | None = None
    outcome: ExecutionInvocationOutcome | None = None
    completed_at: str | None = None
    completion_code: str | None = None
    version: int = 1

    def __post_init__(self):
        _require(type(self.version) is int and self.version == 1
            and type(self.command) is ExecutionCommandReference)
        try:
            self.command.__post_init__()
        except ValueError:
            raise OperationsRecordError(_ERROR) from None
        _require(_text(self.workspace_id) and _identifier(self.activity_id)
            and type(self.kind) is ExecutionInvocationKind and _sha(self.target_fingerprint)
            and type(self.disposition) is ExecutionInvocationDisposition)
        created = _time(self.created_at)
        correlation = (self.attempt, self.original_event_id, self.request_fingerprint)
        _require(all(value is None for value in correlation) or (
            type(self.attempt) is int and 1 <= self.attempt <= 2147483647
            and _text(self.original_event_id) and _sha(self.request_fingerprint)))
        if self.kind in (*_FINAL_KINDS, ExecutionInvocationKind.RECONCILIATION_OBSERVATION):
            _require(self.attempt is not None)
        if self.kind is ExecutionInvocationKind.SECRET_RESOLUTION:
            _require(type(self.authorization_id) is str
                and re.fullmatch(r"suse_[0-9a-f]{64}", self.authorization_id) is not None)
        else:
            _require(self.authorization_id is None)
        if self.disposition is ExecutionInvocationDisposition.PLANNED:
            _require(self.kind in _FINAL_KINDS and self.decision_at is None and self.decision is None)
        else:
            _require(type(self.decision) is ExecutionInvocationDecision)
            self.decision.__post_init__()
            _require(_time(self.decision_at) >= created)
            _require(self.decision.allowed == (self.disposition is ExecutionInvocationDisposition.ADMITTED))
        if self.disposition is ExecutionInvocationDisposition.ADMITTED:
            _require(type(self.outcome) is ExecutionInvocationOutcome)
            if self.outcome is ExecutionInvocationOutcome.PENDING:
                _require(self.completed_at is None and self.completion_code is None)
            else:
                _require(_time(self.completed_at) >= _time(self.decision_at))
                _require(self.completion_code is None or (
                    type(self.completion_code) is str and self.completion_code in _COMPLETION_CODES))
                if self.outcome is ExecutionInvocationOutcome.RETURNED:
                    _require(self.completion_code is None)
                elif self.outcome is ExecutionInvocationOutcome.FAILED:
                    _require(self.completion_code in (None, "capability-failed"))
                else:
                    _require(self.completion_code in (None, "capability-outcome-unknown", "recording-uncertain"))
        else:
            _require(self.outcome is None and self.completed_at is None and self.completion_code is None)
        try:
            # Use the expanded JSON representation, also bounded by the SQL row.
            size = len(json.dumps(self.descriptor(), ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (ValueError, TypeError, UnicodeError):
            raise OperationsRecordError(_ERROR) from None
        _require(size <= 8192)

    @property
    def slot_key(self):
        return self.authorization_id if self.kind is ExecutionInvocationKind.SECRET_RESOLUTION else "singleton"

    def correlation(self):
        return dict(version=self.version, command=dict(run_id=self.command.run_id,
            idempotency_key=self.command.idempotency_key, intent_fingerprint=self.command.intent_fingerprint),
            workspace_id=self.workspace_id, activity_id=self.activity_id, kind=self.kind.value,
            slot_key=self.slot_key, target_fingerprint=self.target_fingerprint,
            attempt=self.attempt, original_event_id=self.original_event_id,
            request_fingerprint=self.request_fingerprint, authorization_id=self.authorization_id)

    @property
    def invocation_id(self):
        return hashlib.sha256(rfc8785.dumps(self.correlation())).hexdigest()

    def descriptor(self):
        return dict(**self.correlation(), invocation_id=self.invocation_id, created_at=self.created_at,
            disposition=self.disposition.value, decision_at=self.decision_at,
            decision=None if self.decision is None else self.decision.descriptor(),
            outcome=None if self.outcome is None else self.outcome.value,
            completed_at=self.completed_at, completion_code=self.completion_code)

    @classmethod
    def from_descriptor(cls, payload):
        _require(type(payload) is dict and len(payload) == 19 and set(payload) == {
            "version", "command", "workspace_id", "activity_id", "kind", "slot_key", "target_fingerprint",
            "attempt", "original_event_id", "request_fingerprint", "authorization_id", "invocation_id",
            "created_at", "disposition", "decision_at", "decision", "outcome", "completed_at", "completion_code"})
        try:
            values = dict(payload)
            identity, slot = values.pop("invocation_id"), values.pop("slot_key")
            command = values.pop("command")
            _require(type(command) is dict and len(command) == 3 and set(command) == {"run_id", "idempotency_key", "intent_fingerprint"})
            values["command"] = ExecutionCommandReference(**command)
            values["kind"] = ExecutionInvocationKind(values["kind"])
            values["disposition"] = ExecutionInvocationDisposition(values["disposition"])
            if values["decision"] is not None:
                values["decision"] = ExecutionInvocationDecision.from_descriptor(values["decision"])
            if values["outcome"] is not None:
                values["outcome"] = ExecutionInvocationOutcome(values["outcome"])
            record = cls(**values)
            _require(identity == record.invocation_id and slot == record.slot_key)
            return record
        except (ValueError, TypeError, KeyError):
            pass
        raise OperationsRecordError(_ERROR)

    def decided(self, decision, at):
        _require(self.disposition is ExecutionInvocationDisposition.PLANNED
            and type(decision) is ExecutionInvocationDecision)
        return replace(self, decision=decision, decision_at=at,
            disposition=ExecutionInvocationDisposition.ADMITTED if decision.allowed else ExecutionInvocationDisposition.DENIED,
            outcome=ExecutionInvocationOutcome.PENDING if decision.allowed else None)

    def completed(self, outcome, at, *, code=None):
        _require(self.disposition is ExecutionInvocationDisposition.ADMITTED
            and self.outcome is ExecutionInvocationOutcome.PENDING
            and type(outcome) is ExecutionInvocationOutcome and outcome is not ExecutionInvocationOutcome.PENDING)
        return replace(self, outcome=outcome, completed_at=at, completion_code=code)


def secret_invocation_target_fingerprint(authorization):
    """Correlate an existing use's public reference/purpose, never secret bytes."""
    from control_plane_kit_operations.secret_providers import (
        AuthorizedSecretUse, AuthorizedSecretVersionUse, validate_authorized_secret_use,
    )
    validate_authorized_secret_use(authorization)
    consumer = type(authorization) is AuthorizedSecretVersionUse
    _require(consumer or type(authorization) is AuthorizedSecretUse)
    reference = authorization.selection.reference if consumer else authorization.reference
    intent = authorization.selection.intent if consumer else authorization.intent
    return hashlib.sha256(rfc8785.dumps(dict(authorization_id=authorization.authorization_id,
        workspace_id=authorization.workspace_id, reference_registration_id=authorization.reference_registration_id,
        provider_registration_id=authorization.provider_registration_id, reference_id=reference.reference_id,
        use_intent=intent.value, selection_fingerprint=authorization.selection_fingerprint if consumer else None,
        request_fingerprint=authorization.request_fingerprint if consumer else None))).hexdigest()


def signed_health_invocation_target_fingerprint(preparation):
    """Reconstruct the exact prepared target without current authority or material."""
    from control_plane_kit_operations.health_effect_preparations import (
        HealthEffectPreparationRecord, ReceiverHealthEffectPreparationRecord,
    )
    _require(type(preparation) in (HealthEffectPreparationRecord, ReceiverHealthEffectPreparationRecord))
    preparation.__post_init__()
    return hashlib.sha256(rfc8785.dumps(dict(
        domain="cpk.signed-health-final-target.v1", kind="signed-health-transport",
        request_fingerprint=preparation.request_fingerprint,
        health_request_digest=preparation.request_digest.value,
        transit_key_registration_id=preparation.transit_key_registration_id,
        workload_key_registration_id=preparation.workload_key_registration_id,
        transit_authorization_id=preparation.transit_authorization_id,
        workload_authorization_id=preparation.workload_authorization_id,
    ))).hexdigest()


@dataclass(frozen=True)
class ExecutionInvocationCursor:
    command: ExecutionCommandReference
    after_invocation_id: str

    def __post_init__(self):
        _require(type(self.command) is ExecutionCommandReference and _sha(self.after_invocation_id))
        self.command.__post_init__()

    def descriptor(self):
        return dict(version=1, command=dict(run_id=self.command.run_id,
            idempotency_key=self.command.idempotency_key, intent_fingerprint=self.command.intent_fingerprint),
            after_invocation_id=self.after_invocation_id)

    @classmethod
    def from_descriptor(cls, payload):
        _require(type(payload) is dict and len(payload) == 3 and set(payload) == {"version", "command", "after_invocation_id"}
            and type(payload["version"]) is int and payload["version"] == 1
            and type(payload["command"]) is dict and len(payload["command"]) == 3
            and set(payload["command"]) == {"run_id", "idempotency_key", "intent_fingerprint"})
        try:
            return cls(ExecutionCommandReference(**payload["command"]), payload["after_invocation_id"])
        except (TypeError, ValueError):
            pass
        raise OperationsRecordError(_ERROR)


@dataclass(frozen=True)
class ExecutionInvocationPage:
    command: ExecutionCommandReference
    items: tuple[ExecutionInvocationRecord, ...]
    next_cursor: ExecutionInvocationCursor | None = None

    def __post_init__(self):
        _require(type(self.command) is ExecutionCommandReference
            and type(self.items) is tuple and len(self.items) <= 100
            and all(type(item) is ExecutionInvocationRecord and item.command == self.command for item in self.items))
        self.command.__post_init__()
        identities = tuple(item.invocation_id for item in self.items)
        _require(identities == tuple(sorted(set(identities))))
        if self.next_cursor is not None:
            _require(type(self.next_cursor) is ExecutionInvocationCursor and self.items
                and self.next_cursor.command == self.command
                and self.next_cursor.after_invocation_id == self.items[-1].invocation_id)
