"""Pure gateway transit authority over the existing exact health request.

These unsigned values neither verify signatures nor perform HTTP, resolve keys,
retain replay state or establish current approved-attempt membership.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Mapping

from control_plane_kit_core._node_control_public_wire import (
    canonical_json_bytes, digest_violation, epoch_violation,
    identifier_violation, public_material_violation, reference_violation,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import (
    NodeControlCanonicalization, NodeControlGraphReference, NodeControlGraphReferenceRole,
    NodeControlTarget, NodeHealthReadKind,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationIdentity,
    WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_reads import (
    NodeHealthReadRequest, NodeHealthReadRequestCodec, NodeHealthReadRequestDigest,
    NodeHealthReadRequestProfile,
)

MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_AUDIENCE_BYTES = 265
MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES = 2_423
MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS = 300

_GRANT_KEYS = frozenset({
    "profile", "canonicalization", "purpose", "issuer", "key_id", "audience", "attempt_id",
    "gateway_node_id", "target", "runtime_id", "kind", "declaration_identity", "request_id",
    "request_digest", "issued_at", "not_before", "expires_at", "jti",
})


class GatewayNodeHealthReadTransitContractError(ValueError):
    """Bounded, redacted rejection of malformed public health transit material."""


class DelegatedGatewayNodeHealthReadTransitGrantProfile(StrEnum):
    V1 = "gateway-node-health-read-transit-grant.v1"


@dataclass(frozen=True, order=True)
class GatewayNodeHealthReadTransitGrantDigest:
    value: str

    def __post_init__(self) -> None:
        if digest_violation(self.value) is not None:
            raise GatewayNodeHealthReadTransitContractError("health transit grant digest is malformed")


@dataclass(frozen=True)
class DelegatedGatewayNodeHealthReadTransitGrant:
    profile: DelegatedGatewayNodeHealthReadTransitGrantProfile
    canonicalization: NodeControlCanonicalization
    purpose: DelegationKeyPurpose
    issuer: str = field(repr=False)
    key_id: str = field(repr=False)
    attempt_id: str = field(repr=False)
    gateway_node_id: NodeControlGraphReference
    target: NodeControlTarget
    runtime_id: NodeControlGraphReference
    kind: NodeHealthReadKind
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    request_id: str = field(repr=False)
    request_digest: NodeHealthReadRequestDigest = field(repr=False)
    issued_at: int
    not_before: int
    expires_at: int
    jti: str = field(repr=False)

    def __post_init__(self) -> None:
        if (self.profile is not DelegatedGatewayNodeHealthReadTransitGrantProfile.V1
                or self.canonicalization is not NodeControlCanonicalization.JCS_RFC8785_V1
                or self.purpose is not DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT
                or type(self.request_digest) is not NodeHealthReadRequestDigest):
            raise GatewayNodeHealthReadTransitContractError("health transit profile or digest is malformed")
        valid_request = False
        try:
            NodeHealthReadRequest(self.target, self.runtime_id, self.kind, self.declaration_identity, self.request_id)
            valid_request = True
        except (ValueError, TypeError):
            pass
        if not valid_request:
            raise GatewayNodeHealthReadTransitContractError("health transit request fields are malformed")
        _require_reference(self.issuer)
        for value in (self.key_id, self.attempt_id, self.request_id, self.jti):
            _require_identifier(value)
        _require_gateway(self.gateway_node_id)
        for value in (self.issued_at, self.not_before, self.expires_at):
            _require_epoch(value)
        if (not self.issued_at <= self.not_before < self.expires_at
                or self.expires_at - self.issued_at > MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS):
            raise GatewayNodeHealthReadTransitContractError("health transit validity interval is invalid")
        self.canonical_bytes()

    @property
    def audience(self) -> str:
        audience = f"gateway:{self.target.workspace_id.value}:{self.gateway_node_id.value}"
        if (len(audience) > MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_AUDIENCE_BYTES
                or public_material_violation(audience) is not None):
            raise GatewayNodeHealthReadTransitContractError("health transit audience is malformed")
        return audience

    def descriptor(self) -> dict[str, object]:
        return {"profile": self.profile.value, "canonicalization": self.canonicalization.value,
                "purpose": self.purpose.value, "issuer": self.issuer, "key_id": self.key_id,
                "audience": self.audience, "attempt_id": self.attempt_id,
                "gateway_node_id": self.gateway_node_id.value, "target": self.target.descriptor(),
                "runtime_id": self.runtime_id.value, "kind": self.kind.value,
                "declaration_identity": self.declaration_identity.value, "request_id": self.request_id,
                "request_digest": self.request_digest.value, "issued_at": self.issued_at,
                "not_before": self.not_before, "expires_at": self.expires_at, "jti": self.jti}

    def canonical_bytes(self) -> bytes:
        return _bounded_bytes(self.descriptor())

    def canonical_digest(self) -> GatewayNodeHealthReadTransitGrantDigest:
        return GatewayNodeHealthReadTransitGrantDigest(hashlib.sha256(self.canonical_bytes()).hexdigest())


class DelegatedGatewayNodeHealthReadTransitGrantCodec:
    """Exact Mapping and canonical-byte interpretation, without authentication."""

    def encode(self, grant: DelegatedGatewayNodeHealthReadTransitGrant) -> dict[str, object]:
        if type(grant) is not DelegatedGatewayNodeHealthReadTransitGrant:
            raise GatewayNodeHealthReadTransitContractError("health transit grant type is invalid")
        return grant.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> DelegatedGatewayNodeHealthReadTransitGrant:
        if not isinstance(descriptor, Mapping):
            raise GatewayNodeHealthReadTransitContractError("health transit grant must be an object")
        _bounded_bytes(descriptor)
        if set(descriptor) != _GRANT_KEYS:
            raise GatewayNodeHealthReadTransitContractError("health transit grant requires exact public fields")
        grant = None
        try:
            # Reuse the accepted health request decoder, including target roles.
            request = NodeHealthReadRequestCodec().decode({
                "profile": NodeHealthReadRequestProfile.V1.value,
                "canonicalization": descriptor["canonicalization"], "target": descriptor["target"],
                "runtime_id": descriptor["runtime_id"], "kind": descriptor["kind"],
                "declaration_identity": descriptor["declaration_identity"], "request_id": descriptor["request_id"],
            })
            grant = DelegatedGatewayNodeHealthReadTransitGrant(
                profile=DelegatedGatewayNodeHealthReadTransitGrantProfile(descriptor["profile"]),
                canonicalization=NodeControlCanonicalization(descriptor["canonicalization"]),
                purpose=DelegationKeyPurpose(descriptor["purpose"]), issuer=descriptor["issuer"],
                key_id=descriptor["key_id"], attempt_id=descriptor["attempt_id"],
                gateway_node_id=NodeControlGraphReference(NodeControlGraphReferenceRole.NODE, descriptor["gateway_node_id"]),
                target=request.target, runtime_id=request.runtime_id, kind=request.kind,
                declaration_identity=request.declaration_identity, request_id=request.request_id,
                request_digest=NodeHealthReadRequestDigest(descriptor["request_digest"]),
                issued_at=descriptor["issued_at"], not_before=descriptor["not_before"],
                expires_at=descriptor["expires_at"], jti=descriptor["jti"],
            )
        except (ValueError, TypeError, KeyError):
            pass
        if grant is None:
            raise GatewayNodeHealthReadTransitContractError("health transit grant is malformed")
        if descriptor["audience"] != grant.audience:
            raise GatewayNodeHealthReadTransitContractError("health transit audience disagrees with coordinates")
        return grant

    def encode_canonical_bytes(self, grant: DelegatedGatewayNodeHealthReadTransitGrant) -> bytes:
        return _bounded_bytes(self.encode(grant))

    def decode_canonical_bytes(self, encoded: bytes) -> DelegatedGatewayNodeHealthReadTransitGrant:
        if type(encoded) is not bytes:
            raise GatewayNodeHealthReadTransitContractError("health transit canonical input must be bytes")
        if len(encoded) > MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES:
            raise GatewayNodeHealthReadTransitContractError("health transit aggregate exceeds the public bound")
        parsed = None
        valid = False
        try:
            parsed = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            valid = True
        except (ValueError, RecursionError):
            pass
        if not valid:
            raise GatewayNodeHealthReadTransitContractError("health transit canonical bytes are malformed")
        grant = self.decode(parsed)
        if grant.canonical_bytes() != encoded:
            raise GatewayNodeHealthReadTransitContractError("health transit bytes are not canonical")
        return grant


class GatewayNodeHealthReadTransitGrantVerificationCode(StrEnum):
    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    PURPOSE_MISMATCH = "purpose-mismatch"
    ISSUER_MISMATCH = "issuer-mismatch"
    KEY_MISMATCH = "key-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"
    ATTEMPT_MISMATCH = "attempt-mismatch"
    GATEWAY_MISMATCH = "gateway-mismatch"
    WORKSPACE_MISMATCH = "workspace-mismatch"
    REVISION_MISMATCH = "revision-mismatch"
    NODE_MISMATCH = "node-mismatch"
    SOCKET_MISMATCH = "socket-mismatch"
    RUNTIME_MISMATCH = "runtime-mismatch"
    DECLARATION_MISMATCH = "declaration-mismatch"
    KIND_MISMATCH = "kind-mismatch"
    REQUEST_MISMATCH = "request-mismatch"


@dataclass(frozen=True)
class GatewayNodeHealthReadTransitGrantVerificationResult:
    is_accepted: bool
    code: GatewayNodeHealthReadTransitGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if (type(self.is_accepted) is not bool or (self.is_accepted and self.code is not None)
                or (not self.is_accepted and type(self.code) is not GatewayNodeHealthReadTransitGrantVerificationCode)):
            raise GatewayNodeHealthReadTransitContractError("health transit verification result is malformed")

    def descriptor(self) -> dict[str, object]:
        return {"accepted": self.is_accepted, "code": self.code.value if self.code is not None else None}


def verify_gateway_node_health_read_transit_grant(
    grant: object, request: NodeHealthReadRequest, *, expected_issuer: str, expected_key_id: str,
    expected_attempt_id: str, expected_gateway_node_id: NodeControlGraphReference,
    expected_target: NodeControlTarget, expected_runtime_id: NodeControlGraphReference,
    expected_declaration: WorkloadNodeControlSurfaceDeclaration, expected_kind: NodeHealthReadKind, now: int,
) -> GatewayNodeHealthReadTransitGrantVerificationResult:
    """Compare authenticated claims to independently supplied relay context.

    Callers own authentication and admitted target-map/attempt membership. Local
    gateway/runtime and approved target/declaration must not come from the grant;
    expected_kind is the actual requested relay action. This is not replay state.
    """
    if (type(request) is not NodeHealthReadRequest or type(expected_target) is not NodeControlTarget
            or type(expected_declaration) is not WorkloadNodeControlSurfaceDeclaration
            or type(expected_kind) is not NodeHealthReadKind
            or type(expected_runtime_id) is not NodeControlGraphReference
            or expected_runtime_id.role is not NodeControlGraphReferenceRole.RUNTIME):
        raise GatewayNodeHealthReadTransitContractError("health transit verifier context is malformed")
    _require_gateway(expected_gateway_node_id)
    _require_reference(expected_issuer)
    _require_identifier(expected_key_id)
    _require_identifier(expected_attempt_id)
    _require_epoch(now)
    code = GatewayNodeHealthReadTransitGrantVerificationCode
    if type(grant) is not DelegatedGatewayNodeHealthReadTransitGrant:
        return GatewayNodeHealthReadTransitGrantVerificationResult(False, code.GRANT_TYPE_MISMATCH)
    for invalid, reason in (
        (grant.purpose is not DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT, code.PURPOSE_MISMATCH),
        (grant.issuer != expected_issuer, code.ISSUER_MISMATCH),
        (grant.key_id != expected_key_id, code.KEY_MISMATCH),
        (not grant.not_before <= now < grant.expires_at, code.TEMPORALLY_INVALID),
        (grant.attempt_id != expected_attempt_id, code.ATTEMPT_MISMATCH),
        (grant.gateway_node_id != expected_gateway_node_id, code.GATEWAY_MISMATCH),
    ):
        if invalid:
            return GatewayNodeHealthReadTransitGrantVerificationResult(False, reason)
    mismatch = _target_mismatch(request.target, expected_target)
    if mismatch is not None:
        return GatewayNodeHealthReadTransitGrantVerificationResult(False, mismatch)
    for invalid, reason in (
        (request.runtime_id != expected_runtime_id, code.RUNTIME_MISMATCH),
        (expected_declaration.profile is not WorkloadNodeControlSurfaceDeclarationProfile.V2
         or request.declaration_identity != expected_declaration.identity()
         or expected_target.provider_socket_name != expected_declaration.surface.provider_socket_name, code.DECLARATION_MISMATCH),
        (request.kind is not expected_kind or expected_kind not in expected_declaration.surface.health_reads, code.KIND_MISMATCH),
    ):
        if invalid:
            return GatewayNodeHealthReadTransitGrantVerificationResult(False, reason)
    mismatch = _target_mismatch(grant.target, request.target)
    if mismatch is not None:
        return GatewayNodeHealthReadTransitGrantVerificationResult(False, mismatch)
    # Gateway/workspace equality above also proves the derived audience matches.
    for invalid, reason in (
        (grant.runtime_id != request.runtime_id, code.RUNTIME_MISMATCH),
        (grant.kind is not request.kind, code.KIND_MISMATCH),
        (grant.declaration_identity != request.declaration_identity, code.DECLARATION_MISMATCH),
        (grant.request_id != request.request_id or grant.request_digest != request.canonical_digest(), code.REQUEST_MISMATCH),
    ):
        if invalid:
            return GatewayNodeHealthReadTransitGrantVerificationResult(False, reason)
    return GatewayNodeHealthReadTransitGrantVerificationResult(True)


def _target_mismatch(left: NodeControlTarget, right: NodeControlTarget):
    code = GatewayNodeHealthReadTransitGrantVerificationCode
    for field_name, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("graph_revision", code.REVISION_MISMATCH),
                               ("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH)):
        if getattr(left, field_name) != getattr(right, field_name):
            return reason
    return None


def _bounded_bytes(value: object) -> bytes:
    try:
        encoded = canonical_json_bytes(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        pass
    else:
        if len(encoded) <= MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES:
            return encoded
        raise GatewayNodeHealthReadTransitContractError("health transit aggregate exceeds the public bound")
    raise GatewayNodeHealthReadTransitContractError("health transit value is outside the canonical JSON domain")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise ValueError


def _require_gateway(value: object) -> None:
    if type(value) is not NodeControlGraphReference or value.role is not NodeControlGraphReferenceRole.NODE:
        raise GatewayNodeHealthReadTransitContractError("health transit gateway reference is malformed")


def _require_identifier(value: object) -> None:
    if identifier_violation(value) is not None:
        raise GatewayNodeHealthReadTransitContractError("health transit identifier is malformed")


def _require_reference(value: object) -> None:
    if reference_violation(value) is not None:
        raise GatewayNodeHealthReadTransitContractError("health transit public reference is malformed")


def _require_epoch(value: object) -> None:
    if epoch_violation(value) is not None:
        raise GatewayNodeHealthReadTransitContractError("health transit epoch is malformed")


__all__ = [
    "MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_AUDIENCE_BYTES",
    "MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES",
    "MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS",
    "GatewayNodeHealthReadTransitContractError", "DelegatedGatewayNodeHealthReadTransitGrantProfile",
    "GatewayNodeHealthReadTransitGrantDigest", "DelegatedGatewayNodeHealthReadTransitGrant",
    "DelegatedGatewayNodeHealthReadTransitGrantCodec", "GatewayNodeHealthReadTransitGrantVerificationCode",
    "GatewayNodeHealthReadTransitGrantVerificationResult", "verify_gateway_node_health_read_transit_grant",
]
