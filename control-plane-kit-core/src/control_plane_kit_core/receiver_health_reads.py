"""Pure health claims for logical receivers, separate from current graph authority."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json

from control_plane_kit_core._node_control_public_wire import (
    canonical_json_bytes, digest_violation, epoch_violation, identifier_violation, reference_violation,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import (
    NodeControlCanonicalization, NodeHealthReadKind, WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec,
    WorkloadNodeControlSurfaceDeclarationIdentity, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_reads import (
    MAX_NODE_HEALTH_READ_REQUEST_BYTES, MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES,
    MAX_WORKLOAD_NODE_HEALTH_READ_GRANT_LIFETIME_SECONDS, NodeHealthReadContractError,
)
from control_plane_kit_core.receiver_identity import (
    NodeControlReceiverTarget, NodeControlReceiverTargetCodec,
    NodeControlAuthorityContext, NodeControlAuthorityContextCodec,
)

_ERROR = "receiver health value is invalid"
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError)
_REQUEST_KEYS = frozenset({"profile", "canonicalization", "target", "authority_context", "kind",
                           "declaration_identity", "request_id"})
_GRANT_KEYS = _REQUEST_KEYS | frozenset({"purpose", "issuer", "key_id", "audience", "request_digest",
                                       "issued_at", "not_before", "expires_at", "jti"})


class ReceiverHealthReadContractError(NodeHealthReadContractError):
    """Categorical refusal without candidate material or retained input exceptions."""


def _require(condition: bool) -> None:
    if condition is not True:
        raise ValueError


def _identifier(value: object) -> None:
    _require(type(value) is str and identifier_violation(value) is None)


def _reference(value: object) -> None:
    _require(type(value) is str and reference_violation(value) is None)


def _digest(value: object) -> None:
    _require(type(value) is str and digest_violation(value) is None)


def _epoch(value: object) -> None:
    _require(epoch_violation(value) is None)


def _interval(issued_at: int, not_before: int, expires_at: int, maximum: int) -> None:
    for value in (issued_at, not_before, expires_at):
        _epoch(value)
    _require(issued_at <= not_before < expires_at and expires_at - issued_at <= maximum)


def _bounded(value: object, maximum: int) -> bytes:
    encoded = canonical_json_bytes(value)
    _require(len(encoded) <= maximum)
    return encoded


def _closed(value: object, keys: frozenset[str], maximum: int) -> dict:
    _require(type(value) is dict and all(type(key) is str for key in value))
    _bounded(value, maximum)
    _require(set(value) == keys)
    return value


def _enum(enum_type, value):
    _require(type(value) is str)
    return enum_type(value)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError


def _canonical_document(raw: bytes, maximum: int) -> object:
    _require(type(raw) is bytes and 1 <= len(raw) <= maximum)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    _require(_bounded(document, maximum) == raw)
    return document


def _declaration(value: object) -> None:
    _require(type(value) is WorkloadNodeControlSurfaceDeclaration)
    _require(type(value.surface) is WorkloadNodeControlSurfaceDescriptor and type(value.surface.health_reads) is tuple)
    codec = WorkloadNodeControlSurfaceDeclarationCodec()
    _require(codec.decode(codec.encode(value)) == value)


class ReceiverHealthReadRequestProfile(StrEnum):
    V2 = "workload-node-health-read-request.v2"


class DelegatedWorkloadReceiverHealthReadGrantProfile(StrEnum):
    V2 = "workload-node-health-read-grant.v2"


@dataclass(frozen=True, order=True, slots=True, repr=False)
class ReceiverHealthReadRequestDigest:
    value: str

    def __post_init__(self) -> None:
        try:
            _digest(self.value)
            return
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure


def _request_claims(value) -> dict[str, object]:
    return {"target": value.target.descriptor(), "authority_context": value.authority_context.descriptor(),
            "kind": value.kind.value, "declaration_identity": value.declaration_identity.value,
            "request_id": value.request_id}


def _request_document(value) -> dict[str, object]:
    return {"profile": value.profile.value, "canonicalization": value.canonicalization.value,
            **_request_claims(value)}


def _decode_request_fields(value: dict) -> dict[str, object]:
    _digest(value["declaration_identity"])
    return dict(target=NodeControlReceiverTargetCodec().decode(value["target"]),
                authority_context=NodeControlAuthorityContextCodec().decode(value["authority_context"]),
                kind=_enum(NodeHealthReadKind, value["kind"]),
                declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity(value["declaration_identity"]),
                request_id=value["request_id"])


@dataclass(frozen=True, order=True, slots=True, repr=False)
class ReceiverHealthReadRequest:
    target: NodeControlReceiverTarget
    authority_context: NodeControlAuthorityContext
    kind: NodeHealthReadKind
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    request_id: str
    profile: ReceiverHealthReadRequestProfile = ReceiverHealthReadRequestProfile.V2
    canonicalization: NodeControlCanonicalization = NodeControlCanonicalization.JCS_RFC8785_V1

    def __post_init__(self) -> None:
        try:
            _require(self.profile is ReceiverHealthReadRequestProfile.V2
                     and self.canonicalization is NodeControlCanonicalization.JCS_RFC8785_V1)
            NodeControlReceiverTargetCodec().encode(self.target)
            NodeControlAuthorityContextCodec().encode(self.authority_context)
            _require(type(self.kind) is NodeHealthReadKind
                     and type(self.declaration_identity) is WorkloadNodeControlSurfaceDeclarationIdentity)
            _digest(self.declaration_identity.value)
            _identifier(self.request_id)
            _bounded(_request_document(self), MAX_NODE_HEALTH_READ_REQUEST_BYTES)
            return
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, object]:
        return ReceiverHealthReadRequestCodec().encode(self)

    def canonical_bytes(self) -> bytes:
        return ReceiverHealthReadRequestCodec().encode_canonical_bytes(self)

    def canonical_digest(self) -> ReceiverHealthReadRequestDigest:
        return ReceiverHealthReadRequestDigest(hashlib.sha256(self.canonical_bytes()).hexdigest())


class ReceiverHealthReadRequestCodec:
    def encode(self, request: ReceiverHealthReadRequest) -> dict[str, object]:
        try:
            _require(type(request) is ReceiverHealthReadRequest)
            return _request_document(replace(request))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> ReceiverHealthReadRequest:
        try:
            value = _closed(document, _REQUEST_KEYS, MAX_NODE_HEALTH_READ_REQUEST_BYTES)
            return ReceiverHealthReadRequest(
                profile=_enum(ReceiverHealthReadRequestProfile, value["profile"]),
                canonicalization=_enum(NodeControlCanonicalization, value["canonicalization"]),
                **_decode_request_fields(value))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, request: ReceiverHealthReadRequest) -> bytes:
        return canonical_json_bytes(self.encode(request))

    def decode_canonical_bytes(self, raw: bytes) -> ReceiverHealthReadRequest:
        try:
            return self.decode(_canonical_document(raw, MAX_NODE_HEALTH_READ_REQUEST_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure


def _grant_document(grant) -> dict[str, object]:
    return {**_request_document(grant), "purpose": grant.purpose.value,
            "issuer": grant.issuer, "key_id": grant.key_id, "audience": grant.audience,
            "request_digest": grant.request_digest.value, "issued_at": grant.issued_at,
            "not_before": grant.not_before, "expires_at": grant.expires_at, "jti": grant.jti}


@dataclass(frozen=True, slots=True, repr=False)
class DelegatedWorkloadReceiverHealthReadGrant:
    profile: DelegatedWorkloadReceiverHealthReadGrantProfile
    canonicalization: NodeControlCanonicalization
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str
    audience: str
    target: NodeControlReceiverTarget
    authority_context: NodeControlAuthorityContext
    kind: NodeHealthReadKind
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    request_id: str
    request_digest: ReceiverHealthReadRequestDigest
    issued_at: int
    not_before: int
    expires_at: int
    jti: str

    def __post_init__(self) -> None:
        try:
            _require(self.profile is DelegatedWorkloadReceiverHealthReadGrantProfile.V2
                     and self.canonicalization is NodeControlCanonicalization.JCS_RFC8785_V1
                     and self.purpose is DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
            ReceiverHealthReadRequest(self.target, self.authority_context, self.kind,
                                      self.declaration_identity, self.request_id)
            _require(type(self.request_digest) is ReceiverHealthReadRequestDigest)
            _digest(self.request_digest.value)
            _reference(self.issuer)
            _reference(self.audience)
            _identifier(self.key_id)
            _identifier(self.jti)
            _interval(self.issued_at, self.not_before, self.expires_at, MAX_WORKLOAD_NODE_HEALTH_READ_GRANT_LIFETIME_SECONDS)
            _bounded(_grant_document(self), MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES)
            return
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, object]:
        return DelegatedWorkloadReceiverHealthReadGrantCodec().encode(self)

    def canonical_bytes(self) -> bytes:
        return DelegatedWorkloadReceiverHealthReadGrantCodec().encode_canonical_bytes(self)


class DelegatedWorkloadReceiverHealthReadGrantCodec:
    def encode(self, grant: DelegatedWorkloadReceiverHealthReadGrant) -> dict[str, object]:
        try:
            _require(type(grant) is DelegatedWorkloadReceiverHealthReadGrant)
            return _grant_document(replace(grant))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> DelegatedWorkloadReceiverHealthReadGrant:
        try:
            value = _closed(document, _GRANT_KEYS, MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES)
            return DelegatedWorkloadReceiverHealthReadGrant(
                profile=_enum(DelegatedWorkloadReceiverHealthReadGrantProfile, value["profile"]),
                canonicalization=_enum(NodeControlCanonicalization, value["canonicalization"]),
                purpose=_enum(DelegationKeyPurpose, value["purpose"]),
                issuer=value["issuer"], key_id=value["key_id"], audience=value["audience"],
                request_digest=ReceiverHealthReadRequestDigest(value["request_digest"]),
                issued_at=value["issued_at"], not_before=value["not_before"], expires_at=value["expires_at"],
                jti=value["jti"], **_decode_request_fields(value))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, grant: DelegatedWorkloadReceiverHealthReadGrant) -> bytes:
        return canonical_json_bytes(self.encode(grant))

    def decode_canonical_bytes(self, raw: bytes) -> DelegatedWorkloadReceiverHealthReadGrant:
        try:
            return self.decode(_canonical_document(raw, MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverHealthReadContractError(_ERROR)
        raise failure


class WorkloadReceiverHealthReadGrantVerificationCode(StrEnum):
    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    PURPOSE_MISMATCH = "purpose-mismatch"
    GRANT_INVALID = "grant-invalid"
    ISSUER_MISMATCH = "issuer-mismatch"
    KEY_MISMATCH = "key-mismatch"
    AUDIENCE_MISMATCH = "audience-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"
    WORKSPACE_MISMATCH = "workspace-mismatch"
    RUNTIME_MISMATCH = "runtime-mismatch"
    NODE_MISMATCH = "node-mismatch"
    SOCKET_MISMATCH = "socket-mismatch"
    RECEIVER_MISMATCH = "receiver-mismatch"
    DECLARATION_MISMATCH = "declaration-mismatch"
    KIND_MISMATCH = "kind-mismatch"
    AUTHORITY_CONTEXT_MISMATCH = "authority-context-mismatch"
    REQUEST_MISMATCH = "request-mismatch"


@dataclass(frozen=True, slots=True)
class WorkloadReceiverHealthReadGrantVerificationResult:
    is_accepted: bool
    code: WorkloadReceiverHealthReadGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if (type(self.is_accepted) is not bool or (self.is_accepted and self.code is not None)
                or (not self.is_accepted and type(self.code) is not WorkloadReceiverHealthReadGrantVerificationCode)):
            raise ReceiverHealthReadContractError(_ERROR)

    def descriptor(self) -> dict[str, object]:
        return {"accepted": self.is_accepted, "code": None if self.code is None else self.code.value}


def _validate_local(request, target, declaration, kind) -> None:
    """Structural only: semantic declaration/kind disagreement gets ordered codes."""
    ReceiverHealthReadRequestCodec().encode(request)
    NodeControlReceiverTargetCodec().encode(target)
    _declaration(declaration)
    _require(type(kind) is NodeHealthReadKind)


def _scope_mismatch(left: NodeControlReceiverTarget, right: NodeControlReceiverTarget) -> str | None:
    for name, code in (("workspace_id", "workspace-mismatch"), ("runtime_id", "runtime-mismatch"),
                       ("node_id", "node-mismatch"), ("provider_socket_name", "socket-mismatch"),
                       ("receiver_id", "receiver-mismatch")):
        if getattr(left, name) != getattr(right, name):
            return code
    return None


def _local_mismatch(request, target, declaration, kind) -> str | None:
    mismatch = _scope_mismatch(request.target, target)
    if mismatch is not None:
        return mismatch
    if (declaration.profile is not WorkloadNodeControlSurfaceDeclarationProfile.V2
            or request.declaration_identity != declaration.identity()
            or target.provider_socket_name != declaration.surface.provider_socket_name):
        return "declaration-mismatch"
    if request.kind is not kind or kind not in declaration.surface.health_reads:
        return "kind-mismatch"
    return None


def _binding_mismatch(grant, request: ReceiverHealthReadRequest) -> str | None:
    mismatch = _scope_mismatch(grant.target, request.target)
    if mismatch is not None:
        return mismatch
    if grant.authority_context != request.authority_context:
        return "authority-context-mismatch"
    if grant.kind is not request.kind:
        return "kind-mismatch"
    if grant.declaration_identity != request.declaration_identity:
        return "declaration-mismatch"
    if grant.request_id != request.request_id or grant.request_digest != request.canonical_digest():
        return "request-mismatch"
    return None


def verify_workload_receiver_health_read_grant(
    grant: object, request: ReceiverHealthReadRequest, *, expected_target: NodeControlReceiverTarget,
    expected_declaration: WorkloadNodeControlSurfaceDeclaration, expected_kind: NodeHealthReadKind,
    expected_issuer: str, expected_key_id: str, expected_audience: str, now: int,
) -> WorkloadReceiverHealthReadGrantVerificationResult:
    """Check authenticated claims against independent installed and admitted inputs.

    A congruent authority context is not proof of current controller permission.
    Callers own signature verification, current authority and actual route admission.
    """
    try:
        _validate_local(request, expected_target, expected_declaration, expected_kind)
        _reference(expected_issuer)
        _identifier(expected_key_id)
        _reference(expected_audience)
        _epoch(now)
    except _INPUT_ERRORS:
        failure = ReceiverHealthReadContractError(_ERROR)
    else:
        failure = None
    if failure is not None:
        raise failure
    code = WorkloadReceiverHealthReadGrantVerificationCode
    result = WorkloadReceiverHealthReadGrantVerificationResult
    if type(grant) is not DelegatedWorkloadReceiverHealthReadGrant:
        return result(False, code.GRANT_TYPE_MISMATCH)
    try:
        purpose = grant.purpose
    except AttributeError:
        return result(False, code.GRANT_INVALID)
    if purpose is not DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ:
        return result(False, code.PURPOSE_MISMATCH)
    try:
        grant = replace(grant)
    except _INPUT_ERRORS:
        return result(False, code.GRANT_INVALID)
    for invalid, reason in (
        (grant.issuer != expected_issuer, code.ISSUER_MISMATCH),
        (grant.key_id != expected_key_id, code.KEY_MISMATCH),
        (grant.audience != expected_audience, code.AUDIENCE_MISMATCH),
        (not grant.not_before <= now < grant.expires_at, code.TEMPORALLY_INVALID),
    ):
        if invalid:
            return result(False, reason)
    mismatch = _local_mismatch(request, expected_target, expected_declaration, expected_kind)
    if mismatch is None:
        mismatch = _binding_mismatch(grant, request)
    return result(True) if mismatch is None else result(False, code(mismatch))


__all__ = [
    "ReceiverHealthReadContractError", "ReceiverHealthReadRequestProfile", "ReceiverHealthReadRequestDigest",
    "ReceiverHealthReadRequest", "ReceiverHealthReadRequestCodec", "DelegatedWorkloadReceiverHealthReadGrantProfile",
    "DelegatedWorkloadReceiverHealthReadGrant", "DelegatedWorkloadReceiverHealthReadGrantCodec",
    "WorkloadReceiverHealthReadGrantVerificationCode", "WorkloadReceiverHealthReadGrantVerificationResult",
    "verify_workload_receiver_health_read_grant",
]
