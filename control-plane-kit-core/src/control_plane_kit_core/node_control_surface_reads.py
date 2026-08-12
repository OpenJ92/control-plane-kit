"""Pure authority language for reading workload node-control surfaces.

These values identify a static declaration and one exact capabilities or status
read. They do not parse HTTP, verify signatures, retain replay state, inspect a
live registry, or perform workload IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import ipaddress
import re
from typing import Mapping

import rfc8785

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import (
    MAX_NODE_CONTROL_PAYLOAD_BYTES,
    NodeControlCanonicalization,
    NodeControlContractError,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlTarget,
    WorkloadNodeControlSurfaceDescriptor,
    WorkloadNodeControlSurfaceDescriptorCodec,
)


MAX_NODE_CONTROL_SURFACE_DECLARATION_BYTES = MAX_NODE_CONTROL_PAYLOAD_BYTES + 69
MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES = 951
MAX_DELEGATED_NODE_CONTROL_SURFACE_READ_GRANT_BYTES = 1_984
MAX_WORKLOAD_NODE_CONTROL_SURFACE_READ_GRANT_LIFETIME_SECONDS = 300

_MAX_IDENTIFIER = 128
_MAX_REFERENCE = 256
_MAX_SAFE_INTEGER = 2**53 - 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ASCII_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_AUTHORIZATION_ENVELOPE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:"
    r"authorization[ \t]*:[ \t]*[A-Za-z][A-Za-z0-9._+-]*[ \t]+"
    r"|bearer[ \t]+"
    r")[^\s,;]+"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])"
    r"(?:credential|password|secret|signature|token)"
    r"[ \t]*=[ \t]*[^\s,;]+"
)
_PRIVATE_KEY_ARMOR = re.compile(
    r"(?i)-----begin(?: [A-Za-z0-9]+)* private key-----"
)
_COMPACT_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:sk-|sg\.)[A-Za-z0-9][A-Za-z0-9._-]*"
)
_SCHEME_ENDPOINT = re.compile(r"(?i)[A-Za-z][A-Za-z0-9+.-]*://[^\s/]")
_PROTOCOL_RELATIVE_ENDPOINT = re.compile(r"(?:^|[\s(\"'=])//[^\s/]")
_HOST_PORT_ENDPOINT = re.compile(
    r"(?<![A-Za-z0-9._:\[\]-])"
    r"(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]*):(\d{1,5})"
    r"(?![A-Za-z0-9])"
)
_ENDPOINT_TOKEN_SPLIT = re.compile(r"[\s,;(){}<>\"']+")

_DECLARATION_KEYS = frozenset({"profile", "surface"})
_REQUEST_KEYS = frozenset(
    {
        "profile",
        "canonicalization",
        "target",
        "kind",
        "declaration_identity",
        "request_id",
    }
)
_GRANT_KEYS = frozenset(
    {
        "profile",
        "canonicalization",
        "purpose",
        "issuer",
        "key_id",
        "audience",
        "target",
        "kind",
        "declaration_identity",
        "request_id",
        "request_digest",
        "issued_at",
        "not_before",
        "expires_at",
        "jti",
    }
)
_TARGET_KEYS = frozenset(
    {"workspace_id", "graph_revision", "node_id", "provider_socket_name"}
)


class NodeControlSurfaceReadContractError(NodeControlContractError):
    """Raised when public node-control surface-read material is malformed."""


class WorkloadNodeControlSurfaceDeclarationProfile(StrEnum):
    """Versioned identity domains for static node-control surfaces."""

    V1 = "workload-node-control-surface-declaration.v1"


@dataclass(frozen=True, order=True)
class WorkloadNodeControlSurfaceDeclarationIdentity:
    """SHA-256 identity of one complete versioned surface declaration."""

    value: str

    def __post_init__(self) -> None:
        _validate_digest(self.value, "surface declaration identity")


@dataclass(frozen=True, order=True)
class WorkloadNodeControlSurfaceDeclaration:
    """One versioned static surface declaration and its identity preimage."""

    surface: WorkloadNodeControlSurfaceDescriptor
    profile: WorkloadNodeControlSurfaceDeclarationProfile = (
        WorkloadNodeControlSurfaceDeclarationProfile.V1
    )

    def __post_init__(self) -> None:
        if not isinstance(self.surface, WorkloadNodeControlSurfaceDescriptor):
            raise NodeControlSurfaceReadContractError(
                "surface declaration requires a node-control surface"
            )
        if not isinstance(
            self.profile,
            WorkloadNodeControlSurfaceDeclarationProfile,
        ):
            raise NodeControlSurfaceReadContractError(
                "surface declaration profile is unknown"
            )
        _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_DECLARATION_BYTES,
            "surface declaration",
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "surface": self.surface.descriptor(),
        }

    def canonical_bytes(self) -> bytes:
        return _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_DECLARATION_BYTES,
            "surface declaration",
        )

    def identity(self) -> WorkloadNodeControlSurfaceDeclarationIdentity:
        return WorkloadNodeControlSurfaceDeclarationIdentity(
            hashlib.sha256(self.canonical_bytes()).hexdigest()
        )


class WorkloadNodeControlSurfaceDeclarationCodec:
    """Strict bounded codec for one versioned static surface declaration."""

    def encode(
        self,
        declaration: WorkloadNodeControlSurfaceDeclaration,
    ) -> dict[str, object]:
        if not isinstance(declaration, WorkloadNodeControlSurfaceDeclaration):
            raise NodeControlSurfaceReadContractError(
                "encode requires WorkloadNodeControlSurfaceDeclaration"
            )
        return declaration.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> WorkloadNodeControlSurfaceDeclaration:
        mapping = _bounded_mapping(
            descriptor,
            MAX_NODE_CONTROL_SURFACE_DECLARATION_BYTES,
            "surface declaration",
        )
        _require_exact_keys(mapping, _DECLARATION_KEYS, "surface declaration")
        profile = _enum(
            WorkloadNodeControlSurfaceDeclarationProfile,
            mapping.get("profile"),
            "surface declaration profile",
        )
        raw_surface = _mapping(mapping.get("surface"), "surface declaration body")
        try:
            surface = WorkloadNodeControlSurfaceDescriptorCodec().decode(raw_surface)
        except NodeControlContractError:
            pass
        else:
            return WorkloadNodeControlSurfaceDeclaration(
                surface=surface,
                profile=profile,
            )
        raise NodeControlSurfaceReadContractError(
            "surface declaration body is malformed"
        )


class NodeControlSurfaceReadKind(StrEnum):
    """Closed observations available at a node-control surface boundary."""

    CAPABILITIES = "capabilities"
    STATUS = "status"


class NodeControlSurfaceReadRequestProfile(StrEnum):
    """Versioned identities for exact surface-read requests."""

    V1 = "workload-node-control-surface-read-request.v1"


@dataclass(frozen=True, order=True)
class NodeControlSurfaceReadRequestDigest:
    """SHA-256 identity of one complete canonical surface-read request."""

    value: str

    def __post_init__(self) -> None:
        _validate_digest(self.value, "surface-read request digest")


@dataclass(frozen=True, order=True)
class NodeControlSurfaceReadRequest:
    """One exact capabilities or status request for a declared surface."""

    target: NodeControlTarget
    kind: NodeControlSurfaceReadKind
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    request_id: str = field(repr=False)
    profile: NodeControlSurfaceReadRequestProfile = (
        NodeControlSurfaceReadRequestProfile.V1
    )
    canonicalization: NodeControlCanonicalization = (
        NodeControlCanonicalization.JCS_RFC8785_V1
    )

    def __post_init__(self) -> None:
        if not isinstance(self.target, NodeControlTarget):
            raise NodeControlSurfaceReadContractError(
                "surface-read request target is malformed"
            )
        if not isinstance(self.kind, NodeControlSurfaceReadKind):
            raise NodeControlSurfaceReadContractError(
                "surface-read request kind is unknown"
            )
        if not isinstance(
            self.declaration_identity,
            WorkloadNodeControlSurfaceDeclarationIdentity,
        ):
            raise NodeControlSurfaceReadContractError(
                "surface-read request declaration identity is malformed"
            )
        _validate_identifier(self.request_id, "surface-read request_id")
        if not isinstance(self.profile, NodeControlSurfaceReadRequestProfile):
            raise NodeControlSurfaceReadContractError(
                "surface-read request profile is unknown"
            )
        if self.canonicalization is not NodeControlCanonicalization.JCS_RFC8785_V1:
            raise NodeControlSurfaceReadContractError(
                "surface-read request canonicalization is unknown"
            )
        _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES,
            "surface-read request",
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "canonicalization": self.canonicalization.value,
            "target": self.target.descriptor(),
            "kind": self.kind.value,
            "declaration_identity": self.declaration_identity.value,
            "request_id": self.request_id,
        }

    def canonical_bytes(self) -> bytes:
        return _bounded_canonical_bytes(
            self.descriptor(),
            MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES,
            "surface-read request",
        )

    def canonical_digest(self) -> NodeControlSurfaceReadRequestDigest:
        return NodeControlSurfaceReadRequestDigest(
            hashlib.sha256(self.canonical_bytes()).hexdigest()
        )


class NodeControlSurfaceReadRequestCodec:
    """Strict bounded codec for exact canonical surface-read requests."""

    def encode(self, request: NodeControlSurfaceReadRequest) -> dict[str, object]:
        if not isinstance(request, NodeControlSurfaceReadRequest):
            raise NodeControlSurfaceReadContractError(
                "encode requires NodeControlSurfaceReadRequest"
            )
        return request.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> NodeControlSurfaceReadRequest:
        mapping = _bounded_mapping(
            descriptor,
            MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES,
            "surface-read request",
        )
        _require_exact_keys(mapping, _REQUEST_KEYS, "surface-read request")
        return NodeControlSurfaceReadRequest(
            target=_decode_target(mapping.get("target")),
            kind=_enum(
                NodeControlSurfaceReadKind,
                mapping.get("kind"),
                "surface-read request kind",
            ),
            declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity(
                _text(mapping, "declaration_identity")
            ),
            request_id=_text(mapping, "request_id"),
            profile=_enum(
                NodeControlSurfaceReadRequestProfile,
                mapping.get("profile"),
                "surface-read request profile",
            ),
            canonicalization=_enum(
                NodeControlCanonicalization,
                mapping.get("canonicalization"),
                "surface-read request canonicalization",
            ),
        )


class DelegatedWorkloadNodeControlSurfaceReadGrantProfile(StrEnum):
    """Versioned identities for unsigned surface-read authority claims."""

    V1 = "workload-node-control-surface-read-grant.v1"


@dataclass(frozen=True, order=True)
class DelegatedWorkloadNodeControlSurfaceReadGrant:
    """Unsigned exact end-to-end authority for one surface-read request."""

    profile: DelegatedWorkloadNodeControlSurfaceReadGrantProfile
    canonicalization: NodeControlCanonicalization
    purpose: DelegationKeyPurpose
    issuer: str = field(repr=False)
    key_id: str = field(repr=False)
    audience: str = field(repr=False)
    target: NodeControlTarget
    kind: NodeControlSurfaceReadKind
    declaration_identity: WorkloadNodeControlSurfaceDeclarationIdentity
    request_id: str = field(repr=False)
    request_digest: NodeControlSurfaceReadRequestDigest
    issued_at: int
    not_before: int
    expires_at: int
    jti: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(
            self.profile,
            DelegatedWorkloadNodeControlSurfaceReadGrantProfile,
        ):
            raise NodeControlSurfaceReadContractError(
                "surface-read grant profile is unknown"
            )
        if self.canonicalization is not NodeControlCanonicalization.JCS_RFC8785_V1:
            raise NodeControlSurfaceReadContractError(
                "surface-read grant canonicalization is unknown"
            )
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise NodeControlSurfaceReadContractError(
                "surface-read grant purpose is unknown"
            )
        _validate_reference(self.issuer, "surface-read grant issuer")
        _validate_identifier(self.key_id, "surface-read grant key_id")
        _validate_reference(self.audience, "surface-read grant audience")
        if not isinstance(self.target, NodeControlTarget):
            raise NodeControlSurfaceReadContractError(
                "surface-read grant target is malformed"
            )
        if not isinstance(self.kind, NodeControlSurfaceReadKind):
            raise NodeControlSurfaceReadContractError(
                "surface-read grant kind is unknown"
            )
        if not isinstance(
            self.declaration_identity,
            WorkloadNodeControlSurfaceDeclarationIdentity,
        ):
            raise NodeControlSurfaceReadContractError(
                "surface-read grant declaration identity is malformed"
            )
        _validate_identifier(self.request_id, "surface-read grant request_id")
        if not isinstance(self.request_digest, NodeControlSurfaceReadRequestDigest):
            raise NodeControlSurfaceReadContractError(
                "surface-read grant request digest is malformed"
            )
        for value, name in (
            (self.issued_at, "surface-read grant issued_at"),
            (self.not_before, "surface-read grant not_before"),
            (self.expires_at, "surface-read grant expires_at"),
        ):
            _validate_epoch(value, name)
        if self.not_before < self.issued_at:
            raise NodeControlSurfaceReadContractError(
                "surface-read grant not_before precedes issued_at"
            )
        if self.expires_at <= self.not_before:
            raise NodeControlSurfaceReadContractError(
                "surface-read grant expires_at must follow not_before"
            )
        if (
            self.expires_at - self.issued_at
            > MAX_WORKLOAD_NODE_CONTROL_SURFACE_READ_GRANT_LIFETIME_SECONDS
        ):
            raise NodeControlSurfaceReadContractError(
                "surface-read grant lifetime exceeds 300 seconds"
            )
        _validate_identifier(self.jti, "surface-read grant jti")
        _bounded_canonical_bytes(
            self.descriptor(),
            MAX_DELEGATED_NODE_CONTROL_SURFACE_READ_GRANT_BYTES,
            "surface-read grant",
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "canonicalization": self.canonicalization.value,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
            "key_id": self.key_id,
            "audience": self.audience,
            "target": self.target.descriptor(),
            "kind": self.kind.value,
            "declaration_identity": self.declaration_identity.value,
            "request_id": self.request_id,
            "request_digest": self.request_digest.value,
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "jti": self.jti,
        }


class DelegatedWorkloadNodeControlSurfaceReadGrantCodec:
    """Strict bounded codec for unsigned surface-read grants."""

    def encode(
        self,
        grant: DelegatedWorkloadNodeControlSurfaceReadGrant,
    ) -> dict[str, object]:
        if not isinstance(grant, DelegatedWorkloadNodeControlSurfaceReadGrant):
            raise NodeControlSurfaceReadContractError(
                "encode requires DelegatedWorkloadNodeControlSurfaceReadGrant"
            )
        return grant.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> DelegatedWorkloadNodeControlSurfaceReadGrant:
        mapping = _bounded_mapping(
            descriptor,
            MAX_DELEGATED_NODE_CONTROL_SURFACE_READ_GRANT_BYTES,
            "surface-read grant",
        )
        _require_exact_keys(mapping, _GRANT_KEYS, "surface-read grant")
        return DelegatedWorkloadNodeControlSurfaceReadGrant(
            profile=_enum(
                DelegatedWorkloadNodeControlSurfaceReadGrantProfile,
                mapping.get("profile"),
                "surface-read grant profile",
            ),
            canonicalization=_enum(
                NodeControlCanonicalization,
                mapping.get("canonicalization"),
                "surface-read grant canonicalization",
            ),
            purpose=_enum(
                DelegationKeyPurpose,
                mapping.get("purpose"),
                "surface-read grant purpose",
            ),
            issuer=_text(mapping, "issuer"),
            key_id=_text(mapping, "key_id"),
            audience=_text(mapping, "audience"),
            target=_decode_target(mapping.get("target")),
            kind=_enum(
                NodeControlSurfaceReadKind,
                mapping.get("kind"),
                "surface-read grant kind",
            ),
            declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity(
                _text(mapping, "declaration_identity")
            ),
            request_id=_text(mapping, "request_id"),
            request_digest=NodeControlSurfaceReadRequestDigest(
                _text(mapping, "request_digest")
            ),
            issued_at=_integer(mapping, "issued_at"),
            not_before=_integer(mapping, "not_before"),
            expires_at=_integer(mapping, "expires_at"),
            jti=_text(mapping, "jti"),
        )


class WorkloadNodeControlSurfaceReadGrantVerificationCode(StrEnum):
    """Bounded reasons why exact surface-read authority was rejected."""

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
    KIND_MISMATCH = "kind-mismatch"
    DECLARATION_MISMATCH = "declaration-mismatch"
    REQUEST_MISMATCH = "request-mismatch"


@dataclass(frozen=True)
class WorkloadNodeControlSurfaceReadGrantVerificationResult:
    """Secret-free result of exact surface-read grant claim comparison."""

    is_accepted: bool
    code: WorkloadNodeControlSurfaceReadGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if type(self.is_accepted) is not bool:
            raise NodeControlSurfaceReadContractError(
                "surface-read verification acceptance must be boolean"
            )
        if self.is_accepted and self.code is not None:
            raise NodeControlSurfaceReadContractError(
                "accepted surface-read verification has no rejection code"
            )
        if not self.is_accepted and not isinstance(
            self.code,
            WorkloadNodeControlSurfaceReadGrantVerificationCode,
        ):
            raise NodeControlSurfaceReadContractError(
                "rejected surface-read verification requires a bounded code"
            )

    @classmethod
    def allow(cls) -> "WorkloadNodeControlSurfaceReadGrantVerificationResult":
        return cls(True)

    @classmethod
    def reject(
        cls,
        code: WorkloadNodeControlSurfaceReadGrantVerificationCode,
    ) -> "WorkloadNodeControlSurfaceReadGrantVerificationResult":
        return cls(False, code)

    def descriptor(self) -> dict[str, object]:
        return {
            "accepted": self.is_accepted,
            "code": self.code.value if self.code is not None else None,
        }


def verify_workload_node_control_surface_read_grant(
    grant: object,
    request: NodeControlSurfaceReadRequest,
    *,
    expected_issuer: str,
    expected_key_id: str,
    expected_audience: str,
    now: int,
) -> WorkloadNodeControlSurfaceReadGrantVerificationResult:
    """Compare one unsigned grant to one exact request without crypto or IO."""

    if not isinstance(request, NodeControlSurfaceReadRequest):
        raise NodeControlSurfaceReadContractError(
            "surface-read verification requires NodeControlSurfaceReadRequest"
        )
    _validate_reference(expected_issuer, "expected surface-read issuer")
    _validate_identifier(expected_key_id, "expected surface-read key_id")
    _validate_reference(expected_audience, "expected surface-read audience")
    _validate_epoch(now, "surface-read verification time")

    reject = WorkloadNodeControlSurfaceReadGrantVerificationResult.reject
    code = WorkloadNodeControlSurfaceReadGrantVerificationCode
    if not isinstance(grant, DelegatedWorkloadNodeControlSurfaceReadGrant):
        return reject(code.GRANT_TYPE_MISMATCH)
    if grant.purpose is not DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ:
        return reject(code.PURPOSE_MISMATCH)
    if grant.issuer != expected_issuer:
        return reject(code.ISSUER_MISMATCH)
    if grant.key_id != expected_key_id:
        return reject(code.KEY_MISMATCH)
    if grant.audience != expected_audience:
        return reject(code.AUDIENCE_MISMATCH)
    if now < grant.not_before or now >= grant.expires_at:
        return reject(code.TEMPORALLY_INVALID)
    if grant.target.workspace_id != request.target.workspace_id:
        return reject(code.WORKSPACE_MISMATCH)
    if grant.target.graph_revision != request.target.graph_revision:
        return reject(code.REVISION_MISMATCH)
    if grant.target.node_id != request.target.node_id:
        return reject(code.NODE_MISMATCH)
    if grant.target.provider_socket_name != request.target.provider_socket_name:
        return reject(code.SOCKET_MISMATCH)
    if grant.kind is not request.kind:
        return reject(code.KIND_MISMATCH)
    if grant.declaration_identity != request.declaration_identity:
        return reject(code.DECLARATION_MISMATCH)
    if (
        grant.request_id != request.request_id
        or grant.request_digest != request.canonical_digest()
    ):
        return reject(code.REQUEST_MISMATCH)
    return WorkloadNodeControlSurfaceReadGrantVerificationResult.allow()


def _bounded_mapping(
    value: object,
    maximum: int,
    name: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, name)
    _bounded_canonical_bytes(mapping, maximum, name)
    return mapping


def _bounded_canonical_bytes(value: object, maximum: int, name: str) -> bytes:
    try:
        encoded = rfc8785.dumps(value)
    except rfc8785.CanonicalizationError:
        pass
    else:
        if len(encoded) <= maximum:
            return encoded
        raise NodeControlSurfaceReadContractError(
            f"{name} aggregate exceeds the public bound"
        )
    raise NodeControlSurfaceReadContractError(
        f"{name} is outside the canonical JSON domain"
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise NodeControlSurfaceReadContractError(f"{name} must be an object")
    return value


def _require_exact_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    name: str,
) -> None:
    if set(mapping) != expected:
        raise NodeControlSurfaceReadContractError(
            f"{name} must contain the exact public fields"
        )


def _decode_target(value: object) -> NodeControlTarget:
    mapping = _mapping(value, "surface-read target")
    _require_exact_keys(mapping, _TARGET_KEYS, "surface-read target")
    try:
        target = NodeControlTarget(
            workspace_id=NodeControlGraphReference(
                NodeControlGraphReferenceRole.WORKSPACE,
                _text(mapping, "workspace_id"),
            ),
            graph_revision=NodeControlGraphReference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                _text(mapping, "graph_revision"),
            ),
            node_id=NodeControlGraphReference(
                NodeControlGraphReferenceRole.NODE,
                _text(mapping, "node_id"),
            ),
            provider_socket_name=NodeControlGraphReference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                _text(mapping, "provider_socket_name"),
            ),
        )
    except NodeControlContractError:
        pass
    else:
        return target
    raise NodeControlSurfaceReadContractError(
        "surface-read target is malformed"
    )


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise NodeControlSurfaceReadContractError(
            "surface-read field must be text"
        )
    return value


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise NodeControlSurfaceReadContractError(
            "surface-read field must be an integer"
        )
    return value


def _enum(enum_type: type[StrEnum], value: object, name: str):
    if not isinstance(value, str):
        raise NodeControlSurfaceReadContractError(f"{name} must be text")
    member = None
    try:
        member = enum_type(value)
    except ValueError:
        pass
    if member is None:
        raise NodeControlSurfaceReadContractError(f"{name} is unknown")
    return member


def _validate_digest(value: object, name: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise NodeControlSurfaceReadContractError(
            f"{name} must be 64 lowercase hex characters"
        )


def _validate_identifier(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_IDENTIFIER
        or not _IDENTIFIER.fullmatch(value)
    ):
        raise NodeControlSurfaceReadContractError(
            f"{name} must be a bounded identifier"
        )
    _reject_prohibited_public_material(value, name)


def _validate_reference(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_REFERENCE
        or not _REFERENCE.fullmatch(value)
    ):
        raise NodeControlSurfaceReadContractError(
            f"{name} must be a bounded reference"
        )
    _reject_prohibited_public_material(value, name)


def _validate_epoch(value: object, name: str) -> None:
    if type(value) is not int or value < 0 or value > _MAX_SAFE_INTEGER:
        raise NodeControlSurfaceReadContractError(
            f"{name} must be a bounded nonnegative integer epoch second"
        )


def _reject_prohibited_public_material(value: str, name: str) -> None:
    projections = (value, _ascii_percent_projection(value))
    if any(_contains_credential_envelope(candidate) for candidate in projections):
        raise NodeControlSurfaceReadContractError(
            f"{name} violates credential-envelope public-material law"
        )
    if any(_contains_endpoint_envelope(candidate) for candidate in projections):
        raise NodeControlSurfaceReadContractError(
            f"{name} violates endpoint-envelope public-material law"
        )


def _ascii_percent_projection(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        decoded = int(match.group(1), 16)
        return chr(decoded) if decoded <= 0x7F else match.group(0)

    return _ASCII_PERCENT_ESCAPE.sub(replace, value)


def _contains_credential_envelope(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (
            _AUTHORIZATION_ENVELOPE,
            _CREDENTIAL_ASSIGNMENT,
            _PRIVATE_KEY_ARMOR,
            _COMPACT_TOKEN,
        )
    )


def _contains_endpoint_envelope(value: str) -> bool:
    if (
        _SCHEME_ENDPOINT.search(value) is not None
        or _PROTOCOL_RELATIVE_ENDPOINT.search(value) is not None
    ):
        return True
    for match in _HOST_PORT_ENDPOINT.finditer(value):
        if 1 <= int(match.group(2)) <= 65_535:
            return True
    for token in _ENDPOINT_TOKEN_SPLIT.split(value):
        atom = token.strip("[]").rstrip(".")
        if not atom:
            continue
        if _is_localhost_endpoint(atom):
            return True
        try:
            ipaddress.ip_address(atom)
        except ValueError:
            continue
        return True
    return False


def _is_localhost_endpoint(atom: str) -> bool:
    lowered = atom.lower().rstrip(".")
    if ":" in lowered:
        host, separator, port = lowered.rpartition(":")
        if (
            not separator
            or not port.isdigit()
            or not 1 <= int(port) <= 65_535
        ):
            return False
        lowered = host.rstrip(".")
    return lowered == "localhost" or lowered.endswith(".localhost")


__all__ = [
    "DelegatedWorkloadNodeControlSurfaceReadGrant",
    "DelegatedWorkloadNodeControlSurfaceReadGrantCodec",
    "DelegatedWorkloadNodeControlSurfaceReadGrantProfile",
    "MAX_DELEGATED_NODE_CONTROL_SURFACE_READ_GRANT_BYTES",
    "MAX_NODE_CONTROL_SURFACE_DECLARATION_BYTES",
    "MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES",
    "MAX_WORKLOAD_NODE_CONTROL_SURFACE_READ_GRANT_LIFETIME_SECONDS",
    "NodeControlSurfaceReadContractError",
    "NodeControlSurfaceReadKind",
    "NodeControlSurfaceReadRequest",
    "NodeControlSurfaceReadRequestCodec",
    "NodeControlSurfaceReadRequestDigest",
    "NodeControlSurfaceReadRequestProfile",
    "WorkloadNodeControlSurfaceDeclaration",
    "WorkloadNodeControlSurfaceDeclarationCodec",
    "WorkloadNodeControlSurfaceDeclarationIdentity",
    "WorkloadNodeControlSurfaceDeclarationProfile",
    "WorkloadNodeControlSurfaceReadGrantVerificationCode",
    "WorkloadNodeControlSurfaceReadGrantVerificationResult",
    "verify_workload_node_control_surface_read_grant",
]
