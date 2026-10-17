"""Pure receiver-scoped state-read and command claims; no execution or signing."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib

from control_plane_kit_core._node_control_public_wire import (
    canonical_json_bytes, digest_violation, epoch_violation, identifier_violation, reference_violation,
)
from control_plane_kit_core.node_control import (
    NodeControlContractError, NodeControlCanonicalization, NodeControlGraphReference,
    NodeControlGraphReferenceRole, NodeControlOperation, ControlPlaneCommandCodec,
    ControlPlaneTransitionPrecondition, NodeControlPayload, ScalarControlState,
    MapControlState, WeightedRoutingControlState, WorkloadNodeControlSurfaceDescriptor,
    MAX_NODE_CONTROL_PAYLOAD_BYTES, MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES,
    MAX_WORKLOAD_NODE_CONTROL_GRANT_LIFETIME_SECONDS,
    _decode_payload, _decode_precondition, _parse_json_object,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec,
    WorkloadNodeControlSurfaceDeclarationIdentity,
)
from control_plane_kit_core.receiver_identity import (
    NodeControlReceiverTarget, NodeControlReceiverTargetCodec,
    NodeControlAuthorityContext, NodeControlAuthorityContextCodec,
)

_ERROR = "receiver node-control value is invalid"
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError)
_CLAIM_KEYS = frozenset({"target", "authority_context", "declaration_identity", "variable_name",
                         "operation", "command_codec", "request_id", "idempotency_key"})
_REQUEST_KEYS = _CLAIM_KEYS | {"profile", "canonicalization", "precondition", "payload"}
_GRANT_KEYS = _CLAIM_KEYS | {"profile", "issuer", "key_id", "audience", "request_digest",
                            "issued_at", "not_before", "expires_at", "jti"}


class ReceiverNodeControlContractError(NodeControlContractError):
    """Bounded categorical refusal, detached from candidate material."""


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


def _canonical_document(raw: bytes, maximum: int) -> dict:
    _require(type(raw) is bytes and 1 <= len(raw) <= maximum)
    # The command parser observes integer-looking JCS float tokens before
    # canonical equality. Plain json.loads would reject valid large floats.
    document = _parse_json_object(raw, "receiver node-control")
    _require(_bounded(document, maximum) == raw)
    return document


def _variable(value: object) -> None:
    _require(type(value) is NodeControlGraphReference and value.role is NodeControlGraphReferenceRole.VARIABLE)
    _identifier(value.value)


def _declaration(value: object) -> None:
    _require(type(value) is WorkloadNodeControlSurfaceDeclaration)
    _require(type(value.surface) is WorkloadNodeControlSurfaceDescriptor and type(value.surface.health_reads) is tuple)
    codec = WorkloadNodeControlSurfaceDeclarationCodec()
    _require(codec.decode(codec.encode(value)) == value)


def _claims(value) -> dict[str, object]:
    return {"target": value.target.descriptor(), "authority_context": value.authority_context.descriptor(),
            "declaration_identity": value.declaration_identity.value, "variable_name": value.variable_name.value,
            "operation": value.operation.value, "command_codec": None if value.command_codec is None else value.command_codec.value,
            "request_id": value.request_id, "idempotency_key": value.idempotency_key}


def _validate_claims(value) -> None:
    NodeControlReceiverTargetCodec().encode(value.target)
    NodeControlAuthorityContextCodec().encode(value.authority_context)
    _require(type(value.declaration_identity) is WorkloadNodeControlSurfaceDeclarationIdentity)
    _digest(value.declaration_identity.value)
    _variable(value.variable_name)
    _require(type(value.operation) is NodeControlOperation)
    _identifier(value.request_id)
    _identifier(value.idempotency_key)
    if value.operation is NodeControlOperation.READ_STATE:
        _require(value.command_codec is None)
    else:
        _require(type(value.command_codec) is ControlPlaneCommandCodec)


def _decode_claims(value: dict) -> dict[str, object]:
    _digest(value["declaration_identity"])
    _identifier(value["variable_name"])
    return dict(target=NodeControlReceiverTargetCodec().decode(value["target"]),
                authority_context=NodeControlAuthorityContextCodec().decode(value["authority_context"]),
                declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity(value["declaration_identity"]),
                variable_name=NodeControlGraphReference(NodeControlGraphReferenceRole.VARIABLE, value["variable_name"]),
                operation=_enum(NodeControlOperation, value["operation"]),
                command_codec=None if value["command_codec"] is None else _enum(ControlPlaneCommandCodec, value["command_codec"]),
                request_id=value["request_id"], idempotency_key=value["idempotency_key"])


class ReceiverNodeControlRequestProfile(StrEnum):
    V2 = "workload-node-control-request.v2"


class DelegatedWorkloadReceiverNodeControlGrantProfile(StrEnum):
    V2 = "workload-node-control-grant.v2"


@dataclass(frozen=True, order=True, slots=True, repr=False)
class ReceiverNodeControlRequestDigest:
    value: str

    def __post_init__(self) -> None:
        try:
            _digest(self.value)
            return
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure


@dataclass(frozen=True, order=True, slots=True, repr=False)
class WorkloadReceiverNodeControlGrantDigest:
    value: str

    def __post_init__(self) -> None:
        try:
            _digest(self.value)
            return
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure


def _request_document(value) -> dict[str, object]:
    return {"profile": value.profile.value, "canonicalization": value.canonicalization.value,
            **_claims(value), "precondition": None if value.precondition is None else value.precondition.descriptor(),
            "payload": None if value.payload is None else value.payload.descriptor()}


@dataclass(frozen=True, order=True, slots=True, repr=False)
class ReceiverNodeControlRequest:
    target: NodeControlReceiverTarget
    authority_context: NodeControlAuthorityContext
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    variable_name: NodeControlGraphReference
    operation: NodeControlOperation
    request_id: str
    idempotency_key: str
    command_codec: ControlPlaneCommandCodec | None = None
    precondition: ControlPlaneTransitionPrecondition | None = None
    payload: NodeControlPayload | None = None
    profile: ReceiverNodeControlRequestProfile = ReceiverNodeControlRequestProfile.V2
    canonicalization: NodeControlCanonicalization = NodeControlCanonicalization.JCS_RFC8785_V1

    def __post_init__(self) -> None:
        try:
            _require(self.profile is ReceiverNodeControlRequestProfile.V2
                     and self.canonicalization is NodeControlCanonicalization.JCS_RFC8785_V1)
            _validate_claims(self)
            if self.operation is NodeControlOperation.READ_STATE:
                _require(self.precondition is None and self.payload is None)
            else:
                _require(type(self.precondition) is ControlPlaneTransitionPrecondition and type(self.payload) is NodeControlPayload)
                _require(type(self.payload.state) in (ScalarControlState, MapControlState, WeightedRoutingControlState))
                _require(self.payload.codec is self.command_codec)
                _require(_decode_precondition(self.precondition.descriptor()) == self.precondition)
                _require(_decode_payload(self.payload.descriptor()) == self.payload)
            _bounded(_request_document(self), MAX_NODE_CONTROL_PAYLOAD_BYTES)
            return
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, object]:
        return ReceiverNodeControlRequestCodec().encode(self)

    def canonical_bytes(self) -> bytes:
        return ReceiverNodeControlRequestCodec().encode_canonical_bytes(self)

    def canonical_digest(self) -> ReceiverNodeControlRequestDigest:
        return ReceiverNodeControlRequestDigest(hashlib.sha256(self.canonical_bytes()).hexdigest())


class ReceiverNodeControlRequestCodec:
    def encode(self, request: ReceiverNodeControlRequest) -> dict[str, object]:
        try:
            _require(type(request) is ReceiverNodeControlRequest)
            return _request_document(replace(request))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> ReceiverNodeControlRequest:
        try:
            value = _closed(document, _REQUEST_KEYS, MAX_NODE_CONTROL_PAYLOAD_BYTES)
            return ReceiverNodeControlRequest(
                profile=_enum(ReceiverNodeControlRequestProfile, value["profile"]),
                canonicalization=_enum(NodeControlCanonicalization, value["canonicalization"]),
                precondition=None if value["precondition"] is None else _decode_precondition(value["precondition"]),
                payload=None if value["payload"] is None else _decode_payload(value["payload"]), **_decode_claims(value))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, request: ReceiverNodeControlRequest) -> bytes:
        return canonical_json_bytes(self.encode(request))

    def decode_canonical_bytes(self, raw: bytes) -> ReceiverNodeControlRequest:
        try:
            return self.decode(_canonical_document(raw, MAX_NODE_CONTROL_PAYLOAD_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure


def _grant_fields(value) -> dict[str, object]:
    return {"issuer": value.issuer, "key_id": value.key_id, "request_digest": value.request_digest.value,
            "issued_at": value.issued_at, "not_before": value.not_before, "expires_at": value.expires_at, "jti": value.jti}


def _validate_grant_fields(value, maximum_lifetime: int) -> None:
    _validate_claims(value)
    _reference(value.issuer)
    _identifier(value.key_id)
    _identifier(value.jti)
    _require(type(value.request_digest) is ReceiverNodeControlRequestDigest)
    _digest(value.request_digest.value)
    _interval(value.issued_at, value.not_before, value.expires_at, maximum_lifetime)


def _decode_grant_fields(value: dict) -> dict[str, object]:
    return dict(issuer=value["issuer"], key_id=value["key_id"],
                request_digest=ReceiverNodeControlRequestDigest(value["request_digest"]),
                issued_at=value["issued_at"], not_before=value["not_before"], expires_at=value["expires_at"], jti=value["jti"])


def _grant_document(value) -> dict[str, object]:
    return {"profile": value.profile.value, "audience": value.audience, **_claims(value), **_grant_fields(value)}


@dataclass(frozen=True, order=True, slots=True, repr=False)
class DelegatedWorkloadReceiverNodeControlGrant:
    profile: DelegatedWorkloadReceiverNodeControlGrantProfile
    issuer: str
    key_id: str
    audience: str
    target: NodeControlReceiverTarget
    authority_context: NodeControlAuthorityContext
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    variable_name: NodeControlGraphReference
    operation: NodeControlOperation
    command_codec: ControlPlaneCommandCodec | None
    request_id: str
    idempotency_key: str
    request_digest: ReceiverNodeControlRequestDigest
    issued_at: int
    not_before: int
    expires_at: int
    jti: str

    def __post_init__(self) -> None:
        try:
            _require(self.profile is DelegatedWorkloadReceiverNodeControlGrantProfile.V2)
            _validate_grant_fields(self, MAX_WORKLOAD_NODE_CONTROL_GRANT_LIFETIME_SECONDS)
            _reference(self.audience)
            _bounded(_grant_document(self), MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES)
            return
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, object]:
        return DelegatedWorkloadReceiverNodeControlGrantCodec().encode(self)

    def canonical_bytes(self) -> bytes:
        return DelegatedWorkloadReceiverNodeControlGrantCodec().encode_canonical_bytes(self)

    def canonical_digest(self) -> WorkloadReceiverNodeControlGrantDigest:
        return WorkloadReceiverNodeControlGrantDigest(hashlib.sha256(self.canonical_bytes()).hexdigest())


class DelegatedWorkloadReceiverNodeControlGrantCodec:
    def encode(self, grant: DelegatedWorkloadReceiverNodeControlGrant) -> dict[str, object]:
        try:
            _require(type(grant) is DelegatedWorkloadReceiverNodeControlGrant)
            return _grant_document(replace(grant))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> DelegatedWorkloadReceiverNodeControlGrant:
        try:
            value = _closed(document, _GRANT_KEYS, MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES)
            return DelegatedWorkloadReceiverNodeControlGrant(
                profile=_enum(DelegatedWorkloadReceiverNodeControlGrantProfile, value["profile"]),
                audience=value["audience"], **_decode_claims(value), **_decode_grant_fields(value))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, grant: DelegatedWorkloadReceiverNodeControlGrant) -> bytes:
        return canonical_json_bytes(self.encode(grant))

    def decode_canonical_bytes(self, raw: bytes) -> DelegatedWorkloadReceiverNodeControlGrant:
        try:
            return self.decode(_canonical_document(raw, MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverNodeControlContractError(_ERROR)
        raise failure


class WorkloadReceiverNodeControlGrantVerificationCode(StrEnum):
    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
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
    AUTHORITY_CONTEXT_MISMATCH = "authority-context-mismatch"
    DECLARATION_MISMATCH = "declaration-mismatch"
    VARIABLE_MISMATCH = "variable-mismatch"
    COMMAND_MISMATCH = "command-mismatch"
    REQUEST_MISMATCH = "request-mismatch"


@dataclass(frozen=True, slots=True, repr=False)
class WorkloadReceiverNodeControlGrantVerificationResult:
    is_accepted: bool
    code: WorkloadReceiverNodeControlGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if (type(self.is_accepted) is not bool or (self.is_accepted and self.code is not None)
                or (not self.is_accepted and type(self.code) is not WorkloadReceiverNodeControlGrantVerificationCode)):
            raise ReceiverNodeControlContractError(_ERROR)

    def descriptor(self) -> dict[str, object]:
        return {"accepted": self.is_accepted, "code": None if self.code is None else self.code.value}


def _validate_local(request, target, declaration, variable_name, operation) -> None:
    """Structural validation only; well-formed disagreement gets an ordered code."""
    ReceiverNodeControlRequestCodec().encode(request)
    NodeControlReceiverTargetCodec().encode(target)
    _declaration(declaration)
    _variable(variable_name)
    _require(type(operation) is NodeControlOperation)


def _scope_mismatch(grant, request, target, fields) -> str | None:
    # Field order takes precedence over source: local then grant per dimension.
    for field, reason in fields:
        if (getattr(request.target, field) != getattr(target, field)
                or getattr(grant.target, field) != getattr(request.target, field)):
            return reason
    return None


_WORKSPACE_FIELDS = (("workspace_id", "workspace-mismatch"), ("runtime_id", "runtime-mismatch"))
_NODE_FIELDS = (("node_id", "node-mismatch"), ("provider_socket_name", "socket-mismatch"), ("receiver_id", "receiver-mismatch"))


def _binding_mismatch(grant, request, declaration, variable_name, operation) -> str | None:
    if grant.authority_context != request.authority_context:
        return "authority-context-mismatch"
    if (request.declaration_identity != declaration.identity()
            or request.target.provider_socket_name != declaration.surface.provider_socket_name
            or grant.declaration_identity != request.declaration_identity):
        return "declaration-mismatch"
    variable = next((v for v in declaration.surface.variables if v.variable_name == variable_name), None)
    if variable is None or request.variable_name != variable_name or grant.variable_name != request.variable_name:
        return "variable-mismatch"
    if (request.operation is not operation or request.command_codec is not variable.contract_for(operation).command_codec
            or grant.operation is not request.operation or grant.command_codec is not request.command_codec):
        return "command-mismatch"
    if (grant.request_id != request.request_id or grant.idempotency_key != request.idempotency_key
            or grant.request_digest != request.canonical_digest()):
        return "request-mismatch"
    return None


def verify_workload_receiver_node_control_grant(
    grant: object, request: ReceiverNodeControlRequest, *, expected_target: NodeControlReceiverTarget,
    expected_declaration: WorkloadNodeControlSurfaceDeclaration, expected_variable_name: NodeControlGraphReference,
    expected_operation: NodeControlOperation, expected_issuer: str, expected_key_id: str, expected_audience: str, now: int,
) -> WorkloadReceiverNodeControlGrantVerificationResult:
    """Compare authenticated claims to independent installed/admitted inputs.

    Congruent context is not current permission. Signature verification, route
    admission, replay handling and execution belong to the caller's effect owner.
    """
    try:
        _validate_local(request, expected_target, expected_declaration, expected_variable_name, expected_operation)
        _reference(expected_issuer)
        _identifier(expected_key_id)
        _reference(expected_audience)
        _epoch(now)
    except _INPUT_ERRORS:
        failure = ReceiverNodeControlContractError(_ERROR)
    else:
        failure = None
    if failure is not None:
        raise failure
    code = WorkloadReceiverNodeControlGrantVerificationCode
    result = WorkloadReceiverNodeControlGrantVerificationResult
    if type(grant) is not DelegatedWorkloadReceiverNodeControlGrant:
        return result(False, code.GRANT_TYPE_MISMATCH)
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
    mismatch = _scope_mismatch(grant, request, expected_target, _WORKSPACE_FIELDS + _NODE_FIELDS)
    if mismatch is None:
        mismatch = _binding_mismatch(grant, request, expected_declaration, expected_variable_name, expected_operation)
    return result(True) if mismatch is None else result(False, code(mismatch))


__all__ = [
    "ReceiverNodeControlContractError", "ReceiverNodeControlRequestProfile", "ReceiverNodeControlRequestDigest",
    "ReceiverNodeControlRequest", "ReceiverNodeControlRequestCodec", "DelegatedWorkloadReceiverNodeControlGrantProfile",
    "WorkloadReceiverNodeControlGrantDigest", "DelegatedWorkloadReceiverNodeControlGrant", "DelegatedWorkloadReceiverNodeControlGrantCodec",
    "WorkloadReceiverNodeControlGrantVerificationCode", "WorkloadReceiverNodeControlGrantVerificationResult",
    "verify_workload_receiver_node_control_grant",
]
