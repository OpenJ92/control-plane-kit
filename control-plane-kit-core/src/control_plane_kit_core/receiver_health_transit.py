"""Health transit claims bound to an independent gateway own-receiver target."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes, public_material_violation
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import NodeControlCanonicalization, NodeHealthReadKind
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationIdentity,
)
from control_plane_kit_core.node_health_transit import (
    MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_AUDIENCE_BYTES,
    MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES,
    MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS,
)
from control_plane_kit_core.receiver_identity import (
    NodeControlReceiverTarget, NodeControlReceiverTargetCodec, NodeControlAuthorityContext,
)
from control_plane_kit_core.receiver_health_reads import (
    ReceiverHealthReadContractError, ReceiverHealthReadRequest, ReceiverHealthReadRequestDigest,
    _INPUT_ERRORS, _require, _reference, _identifier, _digest, _epoch, _interval,
    _bounded, _closed, _enum, _canonical_document, _request_claims, _decode_request_fields,
    _validate_local, _local_mismatch, _binding_mismatch,
)

_ERROR = "receiver health transit value is invalid"
_GRANT_KEYS = frozenset({"profile", "canonicalization", "purpose", "issuer", "key_id", "audience",
                         "attempt_id", "gateway_target", "target", "authority_context", "kind",
                         "declaration_identity", "request_id", "request_digest", "issued_at",
                         "not_before", "expires_at", "jti"})


class GatewayReceiverHealthReadTransitContractError(ReceiverHealthReadContractError):
    """Categorical transit refusal, with no candidate or retained input exception."""


class DelegatedGatewayReceiverHealthReadTransitGrantProfile(StrEnum):
    V2 = "gateway-node-health-read-transit-grant.v2"


@dataclass(frozen=True, order=True, slots=True, repr=False)
class GatewayReceiverHealthReadTransitGrantDigest:
    value: str

    def __post_init__(self) -> None:
        try:
            _digest(self.value)
            return
        except _INPUT_ERRORS:
            failure = GatewayReceiverHealthReadTransitContractError(_ERROR)
        raise failure


def _same_scope(target: NodeControlReceiverTarget, gateway: NodeControlReceiverTarget) -> None:
    _require(target.workspace_id == gateway.workspace_id and target.runtime_id == gateway.runtime_id)


def _audience(gateway: NodeControlReceiverTarget) -> str:
    NodeControlReceiverTargetCodec().encode(gateway)
    value = f"gateway:{gateway.workspace_id.value}:{gateway.node_id.value}"
    _require(len(value) <= MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_AUDIENCE_BYTES
             and public_material_violation(value) is None)
    return value


def _document(grant) -> dict[str, object]:
    return {"profile": grant.profile.value, "canonicalization": grant.canonicalization.value,
            "purpose": grant.purpose.value, "issuer": grant.issuer, "key_id": grant.key_id,
            "audience": _audience(grant.gateway_target), "attempt_id": grant.attempt_id,
            "gateway_target": grant.gateway_target.descriptor(), **_request_claims(grant),
            "request_digest": grant.request_digest.value, "issued_at": grant.issued_at,
            "not_before": grant.not_before, "expires_at": grant.expires_at, "jti": grant.jti}


@dataclass(frozen=True, slots=True, repr=False)
class DelegatedGatewayReceiverHealthReadTransitGrant:
    profile: DelegatedGatewayReceiverHealthReadTransitGrantProfile
    canonicalization: NodeControlCanonicalization
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str
    attempt_id: str
    gateway_target: NodeControlReceiverTarget
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
            _require(self.profile is DelegatedGatewayReceiverHealthReadTransitGrantProfile.V2
                     and self.canonicalization is NodeControlCanonicalization.JCS_RFC8785_V1
                     and self.purpose is DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT)
            ReceiverHealthReadRequest(self.target, self.authority_context, self.kind,
                                      self.declaration_identity, self.request_id)
            NodeControlReceiverTargetCodec().encode(self.gateway_target)
            _same_scope(self.target, self.gateway_target)
            _require(type(self.request_digest) is ReceiverHealthReadRequestDigest)
            _digest(self.request_digest.value)
            _reference(self.issuer)
            for value in (self.key_id, self.attempt_id, self.jti):
                _identifier(value)
            _interval(self.issued_at, self.not_before, self.expires_at,
                      MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS)
            _bounded(_document(self), MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES)
            return
        except _INPUT_ERRORS:
            failure = GatewayReceiverHealthReadTransitContractError(_ERROR)
        raise failure

    @property
    def audience(self) -> str:
        try:
            return _audience(self.gateway_target)
        except _INPUT_ERRORS:
            failure = GatewayReceiverHealthReadTransitContractError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, object]:
        return DelegatedGatewayReceiverHealthReadTransitGrantCodec().encode(self)

    def canonical_bytes(self) -> bytes:
        return DelegatedGatewayReceiverHealthReadTransitGrantCodec().encode_canonical_bytes(self)

    def canonical_digest(self) -> GatewayReceiverHealthReadTransitGrantDigest:
        return GatewayReceiverHealthReadTransitGrantDigest(hashlib.sha256(self.canonical_bytes()).hexdigest())


class DelegatedGatewayReceiverHealthReadTransitGrantCodec:
    def encode(self, grant: DelegatedGatewayReceiverHealthReadTransitGrant) -> dict[str, object]:
        try:
            _require(type(grant) is DelegatedGatewayReceiverHealthReadTransitGrant)
            return _document(replace(grant))
        except _INPUT_ERRORS:
            failure = GatewayReceiverHealthReadTransitContractError(_ERROR)
        raise failure

    def decode(self, document: object) -> DelegatedGatewayReceiverHealthReadTransitGrant:
        try:
            value = _closed(document, _GRANT_KEYS, MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES)
            grant = DelegatedGatewayReceiverHealthReadTransitGrant(
                profile=_enum(DelegatedGatewayReceiverHealthReadTransitGrantProfile, value["profile"]),
                canonicalization=_enum(NodeControlCanonicalization, value["canonicalization"]),
                purpose=_enum(DelegationKeyPurpose, value["purpose"]), issuer=value["issuer"], key_id=value["key_id"],
                attempt_id=value["attempt_id"], gateway_target=NodeControlReceiverTargetCodec().decode(value["gateway_target"]),
                request_digest=ReceiverHealthReadRequestDigest(value["request_digest"]), issued_at=value["issued_at"],
                not_before=value["not_before"], expires_at=value["expires_at"], jti=value["jti"],
                **_decode_request_fields(value))
            _require(type(value["audience"]) is str and value["audience"] == grant.audience)
            return grant
        except _INPUT_ERRORS:
            failure = GatewayReceiverHealthReadTransitContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, grant: DelegatedGatewayReceiverHealthReadTransitGrant) -> bytes:
        return canonical_json_bytes(self.encode(grant))

    def decode_canonical_bytes(self, raw: bytes) -> DelegatedGatewayReceiverHealthReadTransitGrant:
        try:
            return self.decode(_canonical_document(raw, MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES))
        except _INPUT_ERRORS:
            failure = GatewayReceiverHealthReadTransitContractError(_ERROR)
        raise failure


class GatewayReceiverHealthReadTransitGrantVerificationCode(StrEnum):
    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    PURPOSE_MISMATCH = "purpose-mismatch"
    GRANT_INVALID = "grant-invalid"
    ISSUER_MISMATCH = "issuer-mismatch"
    KEY_MISMATCH = "key-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"
    ATTEMPT_MISMATCH = "attempt-mismatch"
    GATEWAY_MISMATCH = "gateway-mismatch"
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
class GatewayReceiverHealthReadTransitGrantVerificationResult:
    is_accepted: bool
    code: GatewayReceiverHealthReadTransitGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if (type(self.is_accepted) is not bool or (self.is_accepted and self.code is not None)
                or (not self.is_accepted and type(self.code) is not GatewayReceiverHealthReadTransitGrantVerificationCode)):
            raise GatewayReceiverHealthReadTransitContractError(_ERROR)

    def descriptor(self) -> dict[str, object]:
        return {"accepted": self.is_accepted, "code": None if self.code is None else self.code.value}


def verify_gateway_receiver_health_read_transit_grant(
    grant: object, request: ReceiverHealthReadRequest, *, expected_issuer: str, expected_key_id: str,
    expected_attempt_id: str, expected_gateway_target: NodeControlReceiverTarget,
    expected_target: NodeControlReceiverTarget, expected_declaration: WorkloadNodeControlSurfaceDeclaration,
    expected_kind: NodeHealthReadKind, now: int,
) -> GatewayReceiverHealthReadTransitGrantVerificationResult:
    """Check own receiver identity, independently admitted workload and request binding.

    The gateway target's socket is its installed own control endpoint. It neither
    names nor admits a transit socket, protocol, ingress or management path.
    Callers retain those checks, authentication and current approved-attempt truth.
    """
    try:
        _validate_local(request, expected_target, expected_declaration, expected_kind)
        NodeControlReceiverTargetCodec().encode(expected_gateway_target)
        _same_scope(expected_target, expected_gateway_target)
        _reference(expected_issuer)
        _identifier(expected_key_id)
        _identifier(expected_attempt_id)
        _epoch(now)
    except _INPUT_ERRORS:
        failure = GatewayReceiverHealthReadTransitContractError(_ERROR)
    else:
        failure = None
    if failure is not None:
        raise failure
    code = GatewayReceiverHealthReadTransitGrantVerificationCode
    result = GatewayReceiverHealthReadTransitGrantVerificationResult
    if type(grant) is not DelegatedGatewayReceiverHealthReadTransitGrant:
        return result(False, code.GRANT_TYPE_MISMATCH)
    try:
        purpose = grant.purpose
    except AttributeError:
        return result(False, code.GRANT_INVALID)
    if purpose is not DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT:
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
        (grant.gateway_target != expected_gateway_target, code.GATEWAY_MISMATCH),
    ):
        if invalid:
            return result(False, reason)
    mismatch = _local_mismatch(request, expected_target, expected_declaration, expected_kind)
    if mismatch is None:
        mismatch = _binding_mismatch(grant, request)
    return result(True) if mismatch is None else result(False, code(mismatch))


__all__ = [
    "GatewayReceiverHealthReadTransitContractError", "DelegatedGatewayReceiverHealthReadTransitGrantProfile",
    "GatewayReceiverHealthReadTransitGrantDigest", "DelegatedGatewayReceiverHealthReadTransitGrant",
    "DelegatedGatewayReceiverHealthReadTransitGrantCodec", "GatewayReceiverHealthReadTransitGrantVerificationCode",
    "GatewayReceiverHealthReadTransitGrantVerificationResult", "verify_gateway_receiver_health_read_transit_grant",
]
