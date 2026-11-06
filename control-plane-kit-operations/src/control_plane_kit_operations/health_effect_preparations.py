"""Closed, protected unsigned health evidence; no current authority or effects."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import json
import re

import rfc8785

from control_plane_kit_core.node_control import workload_node_control_audience
from control_plane_kit_core.node_health_reads import (
    DelegatedWorkloadNodeHealthReadGrant, DelegatedWorkloadNodeHealthReadGrantCodec,
    NodeHealthReadRequest, NodeHealthReadRequestCodec,
)
from control_plane_kit_core.node_health_transit import (
    DelegatedGatewayNodeHealthReadTransitGrant, DelegatedGatewayNodeHealthReadTransitGrantCodec,
)
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.receiver_identity import receiver_node_control_audience
from control_plane_kit_core.receiver_health_reads import (
    ReceiverHealthReadRequest, ReceiverHealthReadRequestCodec,
    DelegatedWorkloadReceiverHealthReadGrant, DelegatedWorkloadReceiverHealthReadGrantCodec,
)
from control_plane_kit_core.receiver_health_transit import (
    DelegatedGatewayReceiverHealthReadTransitGrant, DelegatedGatewayReceiverHealthReadTransitGrantCodec,
)


class HealthEffectPreparationError(ValueError):
    """Malformed preparation input or unavailable retained owner evidence."""


class HealthEffectPreparationConflict(HealthEffectPreparationError):
    """A logical observation or grant identity is already retained elsewhere."""


class HealthEffectPreparationCorrupt(HealthEffectPreparationError):
    """Retained preparation cannot be reconstructed with its exact owner joins."""


_PROFILE = "health-effect-preparation.v1"
_RECEIVER_PROFILE = "health-effect-preparation.v2"
_MAX_BYTES = 16_384
_ERROR = "health effect preparation is invalid"
_TEXT_FIELDS = ("original_event_id", "base_realized_projection_id", "desired_realized_projection_id")
_KEY_FIELDS = ("transit_key_registration_id", "workload_key_registration_id")
_USE_FIELDS = ("transit_authorization_id", "workload_authorization_id")
_FIELDS = frozenset(("identity", "request_fingerprint", *_TEXT_FIELDS, *_KEY_FIELDS, *_USE_FIELDS,
    "request", "transit_grant", "workload_grant"))


@dataclass(frozen=True, slots=True)
class HealthEffectPreparationRecord:
    """One exact unsigned pair joined to an original attempt, never a bearer."""

    identity: EffectAttemptIdentity = field(repr=False)
    request_fingerprint: str = field(repr=False)
    original_event_id: str = field(repr=False)
    base_realized_projection_id: str = field(repr=False)
    desired_realized_projection_id: str = field(repr=False)
    transit_key_registration_id: str = field(repr=False)
    workload_key_registration_id: str = field(repr=False)
    transit_authorization_id: str = field(repr=False)
    workload_authorization_id: str = field(repr=False)
    request: NodeHealthReadRequest = field(repr=False)
    transit_grant: DelegatedGatewayNodeHealthReadTransitGrant = field(repr=False)
    workload_grant: DelegatedWorkloadNodeHealthReadGrant = field(repr=False)

    def __post_init__(self) -> None:
        _require_record(self)

    @property
    def workspace_id(self) -> str:
        return self.request.target.workspace_id.value

    @property
    def logical_request_id(self) -> str:
        return self.request.request_id

    @property
    def request_digest(self):
        return self.request.canonical_digest()


@dataclass(frozen=True, slots=True)
class ReceiverHealthEffectPreparationRecord:
    """Exact receiver-addressed history; current authority is checked separately."""

    identity: EffectAttemptIdentity = field(repr=False)
    request_fingerprint: str = field(repr=False)
    original_event_id: str = field(repr=False)
    base_realized_projection_id: str = field(repr=False)
    desired_realized_projection_id: str = field(repr=False)
    transit_key_registration_id: str = field(repr=False)
    workload_key_registration_id: str = field(repr=False)
    transit_authorization_id: str = field(repr=False)
    workload_authorization_id: str = field(repr=False)
    request: ReceiverHealthReadRequest = field(repr=False)
    transit_grant: DelegatedGatewayReceiverHealthReadTransitGrant = field(repr=False)
    workload_grant: DelegatedWorkloadReceiverHealthReadGrant = field(repr=False)

    def __post_init__(self) -> None:
        _require_record(self)

    @property
    def workspace_id(self) -> str:
        return self.request.target.workspace_id.value

    @property
    def logical_request_id(self) -> str:
        return self.request.request_id

    @property
    def request_digest(self):
        return self.request.canonical_digest()


_PROFILES = {
    _PROFILE: (HealthEffectPreparationRecord, NodeHealthReadRequest,
        DelegatedGatewayNodeHealthReadTransitGrant, DelegatedWorkloadNodeHealthReadGrant,
        NodeHealthReadRequestCodec, DelegatedGatewayNodeHealthReadTransitGrantCodec,
        DelegatedWorkloadNodeHealthReadGrantCodec),
    _RECEIVER_PROFILE: (ReceiverHealthEffectPreparationRecord, ReceiverHealthReadRequest,
        DelegatedGatewayReceiverHealthReadTransitGrant, DelegatedWorkloadReceiverHealthReadGrant,
        ReceiverHealthReadRequestCodec, DelegatedGatewayReceiverHealthReadTransitGrantCodec,
        DelegatedWorkloadReceiverHealthReadGrantCodec),
}


class HealthEffectPreparationCodec:
    """Bounded canonical persistence codec, not a public disclosure surface."""

    def encode_canonical_bytes(self, record: HealthEffectPreparationRecord | ReceiverHealthEffectPreparationRecord) -> bytes:
        _require_record(record)
        return _bounded_document(_document(record))

    def decode_canonical_bytes(self, encoded: bytes) -> HealthEffectPreparationRecord | ReceiverHealthEffectPreparationRecord:
        if type(encoded) is not bytes or not 1 <= len(encoded) <= _MAX_BYTES:
            raise HealthEffectPreparationError(_ERROR)
        record = None
        try:
            value = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object,
                parse_constant=_reject_constant)
            if (type(value) is not dict or set(value) != _FIELDS | {"profile"}
                    or type(value["profile"]) is not str or value["profile"] not in _PROFILES):
                raise ValueError
            record_type, _, _, _, request_codec, transit_codec, workload_codec = _PROFILES[value["profile"]]
            record = record_type(
                identity=EffectAttemptIdentity.from_descriptor(value["identity"]),
                request_fingerprint=value["request_fingerprint"],
                **{name: value[name] for name in (*_TEXT_FIELDS, *_KEY_FIELDS, *_USE_FIELDS)},
                request=request_codec().decode(value["request"]),
                transit_grant=transit_codec().decode(value["transit_grant"]),
                workload_grant=workload_codec().decode(value["workload_grant"]),
            )
            if self.encode_canonical_bytes(record) != encoded:
                record = None
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            record = None
        if record is None:
            raise HealthEffectPreparationError(_ERROR)
        return record


def health_effect_attempt_wire_id(identity: EffectAttemptIdentity) -> str:
    """Correlate the entire owner identity within the bounded health alphabet."""
    admitted = None
    if type(identity) is EffectAttemptIdentity:
        try:
            candidate = EffectAttemptIdentity.from_descriptor(identity.descriptor())
            if _same_nominal_tree(identity, candidate):
                admitted = candidate
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
    if admitted is None:
        raise HealthEffectPreparationError(_ERROR)
    preimage = rfc8785.dumps({"domain": "cpk.health-effect-attempt.v1", "identity": admitted.descriptor()})
    return "health_" + hashlib.sha256(preimage).hexdigest()


def _same_nominal_tree(value: object, admitted: object) -> bool:
    """Equality alone must not bless subclasses or forged nested scalar types."""
    if type(value) is not type(admitted):
        return False
    if is_dataclass(admitted):
        return all(_same_nominal_tree(getattr(value, item.name), getattr(admitted, item.name))
            for item in fields(admitted))
    return value == admitted


def _require_record(record: object) -> None:
    if type(record) not in (HealthEffectPreparationRecord, ReceiverHealthEffectPreparationRecord):
        raise HealthEffectPreparationError(_ERROR)
    valid = False
    try:
        receiver = type(record) is ReceiverHealthEffectPreparationRecord
        _, request_type, transit_type, workload_type, request_codec, transit_codec, workload_codec = _PROFILES[
            _RECEIVER_PROFILE if receiver else _PROFILE]
        nominal = ((record.identity, EffectAttemptIdentity), (record.request, request_type),
            (record.transit_grant, transit_type), (record.workload_grant, workload_type))
        if any(type(value) is not expected for value, expected in nominal):
            raise ValueError
        identity = EffectAttemptIdentity.from_descriptor(record.identity.descriptor())
        request = request_codec().decode(request_codec().encode(record.request))
        transit = transit_codec().decode(transit_codec().encode(record.transit_grant))
        workload = workload_codec().decode(workload_codec().encode(record.workload_grant))
        if not all(_same_nominal_tree(value, admitted) for (value, _), admitted
                in zip(nominal, (identity, request, transit, workload))):
            raise ValueError
        if (type(record.request_fingerprint) is not str
                or re.fullmatch(r"[0-9a-f]{64}", record.request_fingerprint) is None):
            raise ValueError
        for name in _TEXT_FIELDS:
            text = getattr(record, name)
            if (type(text) is not str or not 1 <= len(text) <= 512
                    or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in text)):
                raise ValueError
        for names, prefix in ((_KEY_FIELDS, "dkey_"), (_USE_FIELDS, "suse_")):
            values = tuple(getattr(record, name) for name in names)
            if any(type(value) is not str or re.fullmatch(prefix + r"[0-9a-f]{64}", value) is None
                    for value in values) or values[0] == values[1]:
                raise ValueError
        context = request.authority_context if receiver else request.runtime_id
        common = (request.target, context, request.kind, request.declaration_identity,
            request.request_id, request.canonical_digest())
        if any((grant.target, grant.authority_context if receiver else grant.runtime_id, grant.kind, grant.declaration_identity,
                grant.request_id, grant.request_digest) != common for grant in (transit, workload)):
            raise ValueError
        audience = receiver_node_control_audience if receiver else workload_node_control_audience
        if (transit.attempt_id != health_effect_attempt_wire_id(identity)
                or workload.audience != audience(request.target)
                or (transit.issued_at, transit.not_before, transit.expires_at)
                != (workload.issued_at, workload.not_before, workload.expires_at)):
            raise ValueError
        _bounded_document(_document(record))
        valid = True
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        pass
    if not valid:
        raise HealthEffectPreparationError(_ERROR)


def _document(record) -> dict[str, object]:
    return {"profile": _RECEIVER_PROFILE if type(record) is ReceiverHealthEffectPreparationRecord else _PROFILE,
        "identity": record.identity.descriptor(),
        "request_fingerprint": record.request_fingerprint,
        **{name: getattr(record, name) for name in (*_TEXT_FIELDS, *_KEY_FIELDS, *_USE_FIELDS)},
        "request": record.request.descriptor(), "transit_grant": record.transit_grant.descriptor(),
        "workload_grant": record.workload_grant.descriptor()}


def _bounded_document(document: dict[str, object]) -> bytes:
    encoded = None
    try:
        encoded = rfc8785.dumps(document)
    except (ValueError, TypeError, RecursionError, OverflowError):
        pass
    if encoded is None or not 1 <= len(encoded) <= _MAX_BYTES:
        raise HealthEffectPreparationError(_ERROR)
    return encoded


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError


__all__ = ["HealthEffectPreparationRecord", "ReceiverHealthEffectPreparationRecord", "HealthEffectPreparationCodec",
    "HealthEffectPreparationError", "HealthEffectPreparationConflict", "HealthEffectPreparationCorrupt",
    "health_effect_attempt_wire_id"]
