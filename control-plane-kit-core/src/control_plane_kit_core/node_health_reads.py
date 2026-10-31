"""Pure, locally bound workload health requests and unsigned authority.

Signature verification, clocks, HTTP admission and durable observation identity
allocation are interpreter/Operations responsibilities, not effects of these values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
from typing import Mapping

from control_plane_kit_core._node_control_public_wire import (
    canonical_json_bytes, digest_violation, epoch_violation,
    identifier_violation, reference_violation,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import (
    NodeControlCanonicalization, NodeControlContractError, NodeControlGraphReference,
    NodeControlGraphReferenceRole, NodeControlTarget, NodeHealthReadKind,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationIdentity,
    WorkloadNodeControlSurfaceDeclarationProfile,
)

MAX_NODE_HEALTH_READ_REQUEST_BYTES = 1_083
MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES = 2_107
MAX_WORKLOAD_NODE_HEALTH_READ_GRANT_LIFETIME_SECONDS = 300

_REQUEST_KEYS = frozenset({"profile", "canonicalization", "target", "runtime_id", "kind",
                           "declaration_identity", "request_id"})
_GRANT_KEYS = _REQUEST_KEYS | frozenset({"purpose", "issuer", "key_id", "audience", "request_digest",
                                       "issued_at", "not_before", "expires_at", "jti"})
_TARGET_ROLES = {
    "workspace_id": NodeControlGraphReferenceRole.WORKSPACE,
    "graph_revision": NodeControlGraphReferenceRole.GRAPH_REVISION,
    "node_id": NodeControlGraphReferenceRole.NODE,
    "provider_socket_name": NodeControlGraphReferenceRole.PROVIDER_SOCKET,
}


class NodeHealthReadContractError(NodeControlContractError):
    """Categorical, redacted rejection of malformed health-contract material."""


class NodeHealthReadRequestProfile(StrEnum):
    V1 = "workload-node-health-read-request.v1"


class DelegatedWorkloadNodeHealthReadGrantProfile(StrEnum):
    V1 = "workload-node-health-read-grant.v1"


@dataclass(frozen=True, order=True)
class NodeHealthReadRequestDigest:
    value: str

    def __post_init__(self) -> None:
        if digest_violation(self.value) is not None:
            raise NodeHealthReadContractError("health request digest is malformed")


@dataclass(frozen=True, order=True)
class NodeHealthReadRequest:
    target: NodeControlTarget
    runtime_id: NodeControlGraphReference
    kind: NodeHealthReadKind
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    request_id: str = field(repr=False)
    profile: NodeHealthReadRequestProfile = NodeHealthReadRequestProfile.V1
    canonicalization: NodeControlCanonicalization = NodeControlCanonicalization.JCS_RFC8785_V1

    def __post_init__(self) -> None:
        if (type(self.target) is not NodeControlTarget
                or type(self.kind) is not NodeHealthReadKind
                or type(self.declaration_identity) is not WorkloadNodeControlSurfaceDeclarationIdentity
                or self.profile is not NodeHealthReadRequestProfile.V1
                or self.canonicalization is not NodeControlCanonicalization.JCS_RFC8785_V1):
            raise NodeHealthReadContractError("health request context is malformed")
        _require_runtime(self.runtime_id)
        _require_identifier(self.request_id)
        self.canonical_bytes()

    def descriptor(self) -> dict[str, object]:
        return {"profile": self.profile.value, "canonicalization": self.canonicalization.value,
                "target": self.target.descriptor(), "runtime_id": self.runtime_id.value,
                "kind": self.kind.value, "declaration_identity": self.declaration_identity.value,
                "request_id": self.request_id}

    def canonical_bytes(self) -> bytes:
        return _bounded_bytes(self.descriptor(), MAX_NODE_HEALTH_READ_REQUEST_BYTES)

    def canonical_digest(self) -> NodeHealthReadRequestDigest:
        return NodeHealthReadRequestDigest(hashlib.sha256(self.canonical_bytes()).hexdigest())


class NodeHealthReadRequestCodec:
    """Strict Mapping codec; raw JSON duplicate detection belongs to its parser."""

    def encode(self, request: NodeHealthReadRequest) -> dict[str, object]:
        if type(request) is not NodeHealthReadRequest:
            raise NodeHealthReadContractError("health request type is invalid")
        return request.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> NodeHealthReadRequest:
        value = _bounded_mapping(descriptor, _REQUEST_KEYS, MAX_NODE_HEALTH_READ_REQUEST_BYTES)
        try:
            return NodeHealthReadRequest(
                profile=NodeHealthReadRequestProfile(value["profile"]),
                canonicalization=NodeControlCanonicalization(value["canonicalization"]),
                **_decode_request_fields(value),
            )
        except (ValueError, TypeError, KeyError):
            pass
        raise NodeHealthReadContractError("health request is malformed")


@dataclass(frozen=True)
class DelegatedWorkloadNodeHealthReadGrant:
    profile: DelegatedWorkloadNodeHealthReadGrantProfile
    canonicalization: NodeControlCanonicalization
    purpose: DelegationKeyPurpose
    issuer: str = field(repr=False)
    key_id: str = field(repr=False)
    audience: str = field(repr=False)
    target: NodeControlTarget
    runtime_id: NodeControlGraphReference
    kind: NodeHealthReadKind
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    request_id: str = field(repr=False)
    request_digest: NodeHealthReadRequestDigest
    issued_at: int
    not_before: int
    expires_at: int
    jti: str = field(repr=False)

    def __post_init__(self) -> None:
        if (self.profile is not DelegatedWorkloadNodeHealthReadGrantProfile.V1
                or self.canonicalization is not NodeControlCanonicalization.JCS_RFC8785_V1
                or self.purpose is not DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ
                or type(self.request_digest) is not NodeHealthReadRequestDigest):
            raise NodeHealthReadContractError("health grant profile or identity is malformed")
        # Validate request fields without treating grant self-consistency as authority.
        NodeHealthReadRequest(self.target, self.runtime_id, self.kind, self.declaration_identity, self.request_id)
        _require_reference(self.issuer)
        _require_reference(self.audience)
        _require_identifier(self.key_id)
        _require_identifier(self.jti)
        for value in (self.issued_at, self.not_before, self.expires_at):
            _require_epoch(value)
        if (not self.issued_at <= self.not_before < self.expires_at
                or self.expires_at - self.issued_at > MAX_WORKLOAD_NODE_HEALTH_READ_GRANT_LIFETIME_SECONDS):
            raise NodeHealthReadContractError("health grant validity interval is invalid")
        self.canonical_bytes()

    def descriptor(self) -> dict[str, object]:
        return {"profile": self.profile.value, "canonicalization": self.canonicalization.value,
                "purpose": self.purpose.value, "issuer": self.issuer, "key_id": self.key_id,
                "audience": self.audience, "target": self.target.descriptor(),
                "runtime_id": self.runtime_id.value, "kind": self.kind.value,
                "declaration_identity": self.declaration_identity.value, "request_id": self.request_id,
                "request_digest": self.request_digest.value, "issued_at": self.issued_at,
                "not_before": self.not_before, "expires_at": self.expires_at, "jti": self.jti}

    def canonical_bytes(self) -> bytes:
        return _bounded_bytes(self.descriptor(), MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES)


class DelegatedWorkloadNodeHealthReadGrantCodec:
    def encode(self, grant: DelegatedWorkloadNodeHealthReadGrant) -> dict[str, object]:
        if type(grant) is not DelegatedWorkloadNodeHealthReadGrant:
            raise NodeHealthReadContractError("health grant type is invalid")
        return grant.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> DelegatedWorkloadNodeHealthReadGrant:
        value = _bounded_mapping(descriptor, _GRANT_KEYS, MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES)
        try:
            return DelegatedWorkloadNodeHealthReadGrant(
                profile=DelegatedWorkloadNodeHealthReadGrantProfile(value["profile"]),
                canonicalization=NodeControlCanonicalization(value["canonicalization"]),
                purpose=DelegationKeyPurpose(value["purpose"]),
                issuer=value["issuer"], key_id=value["key_id"], audience=value["audience"],
                request_digest=NodeHealthReadRequestDigest(value["request_digest"]),
                issued_at=value["issued_at"], not_before=value["not_before"],
                expires_at=value["expires_at"], jti=value["jti"], **_decode_request_fields(value),
            )
        except (ValueError, TypeError, KeyError):
            pass
        raise NodeHealthReadContractError("health grant is malformed")


class WorkloadNodeHealthReadGrantVerificationCode(StrEnum):
    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    PURPOSE_MISMATCH = "purpose-mismatch"
    ISSUER_MISMATCH = "issuer-mismatch"
    KEY_MISMATCH = "key-mismatch"
    AUDIENCE_MISMATCH = "audience-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"
    WORKSPACE_MISMATCH = "workspace-mismatch"
    REVISION_MISMATCH = "revision-mismatch"
    NODE_MISMATCH = "node-mismatch"
    SOCKET_MISMATCH = "socket-mismatch"
    RUNTIME_MISMATCH = "runtime-mismatch"
    DECLARATION_MISMATCH = "declaration-mismatch"
    KIND_MISMATCH = "kind-mismatch"
    REQUEST_MISMATCH = "request-mismatch"


@dataclass(frozen=True)
class WorkloadNodeHealthReadGrantVerificationResult:
    is_accepted: bool
    code: WorkloadNodeHealthReadGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if (type(self.is_accepted) is not bool
                or (self.is_accepted and self.code is not None)
                or (not self.is_accepted and type(self.code) is not WorkloadNodeHealthReadGrantVerificationCode)):
            raise NodeHealthReadContractError("health verification result is malformed")

    def descriptor(self) -> dict[str, object]:
        return {"accepted": self.is_accepted, "code": self.code.value if self.code is not None else None}


def verify_workload_node_health_read_grant(
    grant: object, request: NodeHealthReadRequest, *, expected_target: NodeControlTarget,
    expected_runtime_id: NodeControlGraphReference,
    expected_declaration: WorkloadNodeControlSurfaceDeclaration, expected_kind: NodeHealthReadKind,
    expected_issuer: str, expected_key_id: str, expected_audience: str, now: int,
) -> WorkloadNodeHealthReadGrantVerificationResult:
    """Compare unsigned claims with independently supplied local composition.

    Callers must authenticate the grant first. Local target/runtime/declaration
    must never be derived from the credential; kind must come from the actual
    admitted route. This predicate does not prove a current approved attempt.
    """
    if (type(request) is not NodeHealthReadRequest or type(expected_target) is not NodeControlTarget
            or type(expected_declaration) is not WorkloadNodeControlSurfaceDeclaration
            or type(expected_kind) is not NodeHealthReadKind):
        raise NodeHealthReadContractError("health verifier context is malformed")
    _require_runtime(expected_runtime_id)
    _require_reference(expected_issuer)
    _require_identifier(expected_key_id)
    _require_reference(expected_audience)
    _require_epoch(now)
    code = WorkloadNodeHealthReadGrantVerificationCode
    if type(grant) is not DelegatedWorkloadNodeHealthReadGrant:
        return WorkloadNodeHealthReadGrantVerificationResult(False, code.GRANT_TYPE_MISMATCH)
    for invalid, reason in (
        (grant.purpose is not DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ, code.PURPOSE_MISMATCH),
        (grant.issuer != expected_issuer, code.ISSUER_MISMATCH),
        (grant.key_id != expected_key_id, code.KEY_MISMATCH),
        (grant.audience != expected_audience, code.AUDIENCE_MISMATCH),
        (not grant.not_before <= now < grant.expires_at, code.TEMPORALLY_INVALID),
    ):
        if invalid:
            return WorkloadNodeHealthReadGrantVerificationResult(False, reason)
    # First prove locality independently, then prove exact grant/request binding.
    mismatch = _target_mismatch(request.target, expected_target)
    if mismatch is not None:
        return WorkloadNodeHealthReadGrantVerificationResult(False, mismatch)
    for invalid, reason in (
        (request.runtime_id != expected_runtime_id, code.RUNTIME_MISMATCH),
        (expected_declaration.profile is not WorkloadNodeControlSurfaceDeclarationProfile.V2
         or request.declaration_identity != expected_declaration.identity()
         or expected_target.provider_socket_name != expected_declaration.surface.provider_socket_name,
         code.DECLARATION_MISMATCH),
        (request.kind is not expected_kind or expected_kind not in expected_declaration.surface.health_reads,
         code.KIND_MISMATCH),
    ):
        if invalid:
            return WorkloadNodeHealthReadGrantVerificationResult(False, reason)
    mismatch = _target_mismatch(grant.target, request.target)
    if mismatch is not None:
        return WorkloadNodeHealthReadGrantVerificationResult(False, mismatch)
    for invalid, reason in (
        (grant.runtime_id != request.runtime_id, code.RUNTIME_MISMATCH),
        (grant.kind is not request.kind, code.KIND_MISMATCH),
        (grant.declaration_identity != request.declaration_identity, code.DECLARATION_MISMATCH),
        (grant.request_id != request.request_id or grant.request_digest != request.canonical_digest(), code.REQUEST_MISMATCH),
    ):
        if invalid:
            return WorkloadNodeHealthReadGrantVerificationResult(False, reason)
    return WorkloadNodeHealthReadGrantVerificationResult(True)


def _target_mismatch(left: NodeControlTarget, right: NodeControlTarget):
    code = WorkloadNodeHealthReadGrantVerificationCode
    for name, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("graph_revision", code.REVISION_MISMATCH),
                         ("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH)):
        if getattr(left, name) != getattr(right, name):
            return reason
    return None


def _decode_request_fields(value: Mapping[str, object]) -> dict[str, object]:
    target = value["target"]
    if not isinstance(target, Mapping) or set(target) != set(_TARGET_ROLES):
        raise NodeHealthReadContractError("health target fields are malformed")
    return dict(target=NodeControlTarget(**{
                    name: NodeControlGraphReference(role, target[name]) for name, role in _TARGET_ROLES.items()}),
                runtime_id=NodeControlGraphReference(NodeControlGraphReferenceRole.RUNTIME, value["runtime_id"]),
                kind=NodeHealthReadKind(value["kind"]),
                declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity(value["declaration_identity"]),
                request_id=value["request_id"])


def _bounded_bytes(value: object, maximum: int) -> bytes:
    try:
        encoded = canonical_json_bytes(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        pass
    else:
        if len(encoded) <= maximum:
            return encoded
        raise NodeHealthReadContractError("health aggregate exceeds the public bound")
    raise NodeHealthReadContractError("health value is outside the canonical JSON domain")


def _bounded_mapping(value: object, keys: frozenset[str], maximum: int) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise NodeHealthReadContractError("health value must be an object")
    _bounded_bytes(value, maximum)
    if set(value) != keys:
        raise NodeHealthReadContractError("health value requires exact public fields")
    return value


def _require_runtime(value: object) -> None:
    if type(value) is not NodeControlGraphReference or value.role is not NodeControlGraphReferenceRole.RUNTIME:
        raise NodeHealthReadContractError("health runtime reference is malformed")


def _require_identifier(value: object) -> None:
    if identifier_violation(value) is not None:
        raise NodeHealthReadContractError("health identifier is malformed")


def _require_reference(value: object) -> None:
    if reference_violation(value) is not None:
        raise NodeHealthReadContractError("health public reference is malformed")


def _require_epoch(value: object) -> None:
    if epoch_violation(value) is not None:
        raise NodeHealthReadContractError("health epoch is malformed")


__all__ = [
    "MAX_NODE_HEALTH_READ_REQUEST_BYTES", "MAX_DELEGATED_WORKLOAD_NODE_HEALTH_READ_GRANT_BYTES",
    "MAX_WORKLOAD_NODE_HEALTH_READ_GRANT_LIFETIME_SECONDS", "NodeHealthReadContractError",
    "NodeHealthReadRequestProfile", "NodeHealthReadRequestDigest", "NodeHealthReadRequest",
    "NodeHealthReadRequestCodec", "DelegatedWorkloadNodeHealthReadGrantProfile",
    "DelegatedWorkloadNodeHealthReadGrant", "DelegatedWorkloadNodeHealthReadGrantCodec",
    "WorkloadNodeHealthReadGrantVerificationCode", "WorkloadNodeHealthReadGrantVerificationResult",
    "verify_workload_node_health_read_grant",
]
