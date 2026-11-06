"""Pure exact-command transit claims with an independent gateway receiver."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import (
    NodeControlCanonicalization, NodeControlGraphReference, NodeControlOperation, ControlPlaneCommandCodec,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationIdentity,
)
from control_plane_kit_core.node_control_transit import (
    MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES, MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES,
    MAX_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_LIFETIME_SECONDS,
)
from control_plane_kit_core.receiver_identity import (
    NodeControlReceiverTarget, NodeControlReceiverTargetCodec, NodeControlAuthorityContext,
)
from control_plane_kit_core.receiver_node_control import (
    ReceiverNodeControlContractError, ReceiverNodeControlRequest, ReceiverNodeControlRequestDigest,
    _INPUT_ERRORS, _CLAIM_KEYS, _require, _identifier, _reference, _digest, _epoch, _enum,
    _bounded, _closed, _canonical_document, _claims, _decode_claims, _grant_fields,
    _decode_grant_fields, _validate_grant_fields, _validate_local, _scope_mismatch,
    _binding_mismatch, _WORKSPACE_FIELDS, _NODE_FIELDS,
)

_ERROR = "receiver node-control transit value is invalid"
_KEYS = _CLAIM_KEYS | {"profile", "canonicalization", "purpose", "issuer", "key_id", "attempt_id",
                       "gateway_target", "audience", "request_digest", "issued_at", "not_before", "expires_at", "jti"}


class GatewayReceiverNodeControlTransitContractError(ReceiverNodeControlContractError):
    """Categorical transit refusal without candidate material."""


class DelegatedGatewayReceiverNodeControlTransitGrantProfile(StrEnum):
    V2 = "gateway-node-control-transit-grant.v2"


@dataclass(frozen=True, order=True, slots=True, repr=False)
class GatewayReceiverNodeControlTransitGrantDigest:
    value: str

    def __post_init__(self) -> None:
        try:
            _digest(self.value)
            return
        except _INPUT_ERRORS:
            failure = GatewayReceiverNodeControlTransitContractError(_ERROR)
        raise failure


def _shared_scope(gateway: NodeControlReceiverTarget, workload: NodeControlReceiverTarget) -> None:
    _require(gateway.workspace_id == workload.workspace_id and gateway.runtime_id == workload.runtime_id)


def _document(value) -> dict[str, object]:
    return {"profile": value.profile.value, "canonicalization": value.canonicalization.value,
            "purpose": value.purpose.value, "attempt_id": value.attempt_id,
            "gateway_target": value.gateway_target.descriptor(), "audience": value.audience,
            **_claims(value), **_grant_fields(value)}


@dataclass(frozen=True, order=True, slots=True, repr=False)
class DelegatedGatewayReceiverNodeControlTransitGrant:
    profile: DelegatedGatewayReceiverNodeControlTransitGrantProfile
    canonicalization: NodeControlCanonicalization
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str
    attempt_id: str
    gateway_target: NodeControlReceiverTarget
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
            _require(self.profile is DelegatedGatewayReceiverNodeControlTransitGrantProfile.V2
                     and self.canonicalization is NodeControlCanonicalization.JCS_RFC8785_V1
                     and self.purpose is DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT)
            _validate_grant_fields(self, MAX_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_LIFETIME_SECONDS)
            _identifier(self.attempt_id)
            NodeControlReceiverTargetCodec().encode(self.gateway_target)
            _shared_scope(self.gateway_target, self.target)
            _require(len(self.audience.encode("ascii")) <= MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES)
            _bounded(_document(self), MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES)
            return
        except _INPUT_ERRORS:
            failure = GatewayReceiverNodeControlTransitContractError(_ERROR)
        raise failure

    @property
    def audience(self) -> str:
        return f"gateway:{self.gateway_target.workspace_id.value}:{self.gateway_target.node_id.value}"

    def descriptor(self) -> dict[str, object]:
        return DelegatedGatewayReceiverNodeControlTransitGrantCodec().encode(self)

    def canonical_bytes(self) -> bytes:
        return DelegatedGatewayReceiverNodeControlTransitGrantCodec().encode_canonical_bytes(self)

    def canonical_digest(self) -> GatewayReceiverNodeControlTransitGrantDigest:
        return GatewayReceiverNodeControlTransitGrantDigest(hashlib.sha256(self.canonical_bytes()).hexdigest())


class DelegatedGatewayReceiverNodeControlTransitGrantCodec:
    def encode(self, grant: DelegatedGatewayReceiverNodeControlTransitGrant) -> dict[str, object]:
        try:
            _require(type(grant) is DelegatedGatewayReceiverNodeControlTransitGrant)
            return _document(replace(grant))
        except _INPUT_ERRORS:
            failure = GatewayReceiverNodeControlTransitContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> DelegatedGatewayReceiverNodeControlTransitGrant:
        try:
            value = _closed(document, _KEYS, MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES)
            grant = DelegatedGatewayReceiverNodeControlTransitGrant(
                profile=_enum(DelegatedGatewayReceiverNodeControlTransitGrantProfile, value["profile"]),
                canonicalization=_enum(NodeControlCanonicalization, value["canonicalization"]),
                purpose=_enum(DelegationKeyPurpose, value["purpose"]), attempt_id=value["attempt_id"],
                gateway_target=NodeControlReceiverTargetCodec().decode(value["gateway_target"]),
                **_decode_claims(value), **_decode_grant_fields(value))
            _require(type(value["audience"]) is str and value["audience"] == grant.audience)
            return grant
        except _INPUT_ERRORS:
            failure = GatewayReceiverNodeControlTransitContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, grant: DelegatedGatewayReceiverNodeControlTransitGrant) -> bytes:
        return canonical_json_bytes(self.encode(grant))

    def decode_canonical_bytes(self, raw: bytes) -> DelegatedGatewayReceiverNodeControlTransitGrant:
        try:
            return self.decode(_canonical_document(raw, MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES))
        except _INPUT_ERRORS:
            failure = GatewayReceiverNodeControlTransitContractError(_ERROR)
        raise failure


class GatewayReceiverNodeControlTransitGrantVerificationCode(StrEnum):
    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    PURPOSE_MISMATCH = "purpose-mismatch"
    GRANT_INVALID = "grant-invalid"
    ISSUER_MISMATCH = "issuer-mismatch"
    KEY_MISMATCH = "key-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"
    ATTEMPT_MISMATCH = "attempt-mismatch"
    WORKSPACE_MISMATCH = "workspace-mismatch"
    RUNTIME_MISMATCH = "runtime-mismatch"
    GATEWAY_MISMATCH = "gateway-mismatch"
    NODE_MISMATCH = "node-mismatch"
    SOCKET_MISMATCH = "socket-mismatch"
    RECEIVER_MISMATCH = "receiver-mismatch"
    AUTHORITY_CONTEXT_MISMATCH = "authority-context-mismatch"
    DECLARATION_MISMATCH = "declaration-mismatch"
    VARIABLE_MISMATCH = "variable-mismatch"
    COMMAND_MISMATCH = "command-mismatch"
    REQUEST_MISMATCH = "request-mismatch"


@dataclass(frozen=True, slots=True, repr=False)
class GatewayReceiverNodeControlTransitGrantVerificationResult:
    is_accepted: bool
    code: GatewayReceiverNodeControlTransitGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if (type(self.is_accepted) is not bool or (self.is_accepted and self.code is not None)
                or (not self.is_accepted and type(self.code) is not GatewayReceiverNodeControlTransitGrantVerificationCode)):
            raise GatewayReceiverNodeControlTransitContractError(_ERROR)

    def descriptor(self) -> dict[str, object]:
        return {"accepted": self.is_accepted, "code": None if self.code is None else self.code.value}


def verify_gateway_receiver_node_control_transit_grant(
    grant: object, request: ReceiverNodeControlRequest, *, expected_issuer: str, expected_key_id: str,
    expected_attempt_id: str, expected_gateway_target: NodeControlReceiverTarget, expected_target: NodeControlReceiverTarget,
    expected_declaration: WorkloadNodeControlSurfaceDeclaration, expected_variable_name: NodeControlGraphReference,
    expected_operation: NodeControlOperation, now: int,
) -> GatewayReceiverNodeControlTransitGrantVerificationResult:
    """Exact transit correlation, not signature, route admission or execution proof."""
    try:
        _validate_local(request, expected_target, expected_declaration, expected_variable_name, expected_operation)
        NodeControlReceiverTargetCodec().encode(expected_gateway_target)
        _shared_scope(expected_gateway_target, expected_target)
        _reference(expected_issuer)
        _identifier(expected_key_id)
        _identifier(expected_attempt_id)
        _epoch(now)
    except _INPUT_ERRORS:
        failure = GatewayReceiverNodeControlTransitContractError(_ERROR)
    else:
        failure = None
    if failure is not None:
        raise failure
    code = GatewayReceiverNodeControlTransitGrantVerificationCode
    result = GatewayReceiverNodeControlTransitGrantVerificationResult
    if type(grant) is not DelegatedGatewayReceiverNodeControlTransitGrant:
        return result(False, code.GRANT_TYPE_MISMATCH)
    try:
        purpose = grant.purpose
    except AttributeError:
        return result(False, code.GRANT_INVALID)
    if purpose is not DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT:
        return result(False, code.PURPOSE_MISMATCH)
    try:
        grant = replace(grant)
    except _INPUT_ERRORS:
        return result(False, code.GRANT_INVALID)
    for invalid, reason in (
        (grant.issuer != expected_issuer, code.ISSUER_MISMATCH),
        (grant.key_id != expected_key_id, code.KEY_MISMATCH),
        (not grant.not_before <= now < grant.expires_at, code.TEMPORALLY_INVALID),
        (grant.attempt_id != expected_attempt_id, code.ATTEMPT_MISMATCH),
    ):
        if invalid:
            return result(False, reason)
    # Preserve command-family workspace/runtime precedence before gateway.
    mismatch = _scope_mismatch(grant, request, expected_target, _WORKSPACE_FIELDS)
    if mismatch is None and grant.gateway_target != expected_gateway_target:
        mismatch = "gateway-mismatch"
    if mismatch is None:
        mismatch = _scope_mismatch(grant, request, expected_target, _NODE_FIELDS)
    if mismatch is None:
        mismatch = _binding_mismatch(grant, request, expected_declaration, expected_variable_name, expected_operation)
    return result(True) if mismatch is None else result(False, code(mismatch))


__all__ = [
    "GatewayReceiverNodeControlTransitContractError", "DelegatedGatewayReceiverNodeControlTransitGrantProfile",
    "GatewayReceiverNodeControlTransitGrantDigest", "DelegatedGatewayReceiverNodeControlTransitGrant",
    "DelegatedGatewayReceiverNodeControlTransitGrantCodec", "GatewayReceiverNodeControlTransitGrantVerificationCode",
    "GatewayReceiverNodeControlTransitGrantVerificationResult", "verify_gateway_receiver_node_control_transit_grant",
]
