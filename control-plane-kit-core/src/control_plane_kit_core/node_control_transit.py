"""Pure authority language for relaying one exact node-control request.

These values authorize a selected graph gateway to relay public command
material. They do not sign grants, retain replay state, select endpoints,
resolve secrets, dispatch requests, or perform workload IO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Mapping

from control_plane_kit_core._node_control_public_wire import (
    NodeControlCanonicalDomainError,
    NodeControlPublicWireViolation,
    canonical_json_bytes,
    digest_violation,
    epoch_violation,
    identifier_violation,
    public_material_violation,
    reference_violation,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    NodeControlCanonicalization,
    NodeControlCommandRequest,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlRequestDigest,
    NodeControlTarget,
)


MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES = 265
MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES = 2_834
MAX_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_LIFETIME_SECONDS = 300

_GRANT_KEYS = frozenset(
    {
        "profile",
        "canonicalization",
        "purpose",
        "issuer",
        "key_id",
        "audience",
        "attempt_id",
        "workspace_id",
        "graph_revision",
        "gateway_node_id",
        "target",
        "variable_name",
        "operation",
        "command_codec",
        "request_id",
        "idempotency_key",
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


class GatewayNodeControlTransitContractError(ValueError):
    """Raised when public gateway transit authority is malformed."""


class DelegatedGatewayNodeControlTransitGrantProfile(StrEnum):
    """Versioned identity for unsigned gateway node-control transit claims."""

    V1 = "gateway-node-control-transit-grant.v1"


@dataclass(frozen=True, order=True)
class GatewayNodeControlTransitGrantDigest:
    """SHA-256 identity of one complete canonical transit grant."""

    value: str

    def __post_init__(self) -> None:
        if digest_violation(self.value) is not None:
            raise GatewayNodeControlTransitContractError(
                "transit grant digest must be 64 lowercase hex characters"
            )


@dataclass(frozen=True, order=True)
class DelegatedGatewayNodeControlTransitGrant:
    """Unsigned short-lived authority to relay one exact workload request."""

    profile: DelegatedGatewayNodeControlTransitGrantProfile
    canonicalization: NodeControlCanonicalization
    purpose: DelegationKeyPurpose
    issuer: str = field(repr=False)
    key_id: str = field(repr=False)
    attempt_id: str = field(repr=False)
    workspace_id: NodeControlGraphReference
    graph_revision: NodeControlGraphReference
    gateway_node_id: NodeControlGraphReference
    target: NodeControlTarget
    variable_name: NodeControlGraphReference
    operation: NodeControlOperation
    command_codec: ControlPlaneCommandCodec | None
    request_id: str = field(repr=False)
    idempotency_key: str = field(repr=False)
    request_digest: NodeControlRequestDigest = field(repr=False)
    issued_at: int
    not_before: int
    expires_at: int
    jti: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.profile is not DelegatedGatewayNodeControlTransitGrantProfile.V1:
            raise GatewayNodeControlTransitContractError(
                "transit grant profile is unknown"
            )
        if self.canonicalization is not NodeControlCanonicalization.JCS_RFC8785_V1:
            raise GatewayNodeControlTransitContractError(
                "transit grant canonicalization is unknown"
            )
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise GatewayNodeControlTransitContractError(
                "transit grant purpose is unknown"
            )
        _require_reference(self.issuer, "transit grant issuer")
        for value, name in (
            (self.key_id, "transit grant key_id"),
            (self.attempt_id, "transit grant attempt_id"),
            (self.request_id, "transit grant request_id"),
            (self.idempotency_key, "transit grant idempotency_key"),
            (self.jti, "transit grant jti"),
        ):
            _require_identifier(value, name)
        _require_graph_reference(
            self.workspace_id,
            NodeControlGraphReferenceRole.WORKSPACE,
            "transit grant workspace_id",
        )
        _require_graph_reference(
            self.graph_revision,
            NodeControlGraphReferenceRole.GRAPH_REVISION,
            "transit grant graph_revision",
        )
        _require_graph_reference(
            self.gateway_node_id,
            NodeControlGraphReferenceRole.NODE,
            "transit grant gateway_node_id",
        )
        if not isinstance(self.target, NodeControlTarget):
            raise GatewayNodeControlTransitContractError(
                "transit grant target is malformed"
            )
        if self.workspace_id != self.target.workspace_id:
            raise GatewayNodeControlTransitContractError(
                "transit grant workspace_id must match target"
            )
        if self.graph_revision != self.target.graph_revision:
            raise GatewayNodeControlTransitContractError(
                "transit grant graph_revision must match target"
            )
        _require_graph_reference(
            self.variable_name,
            NodeControlGraphReferenceRole.VARIABLE,
            "transit grant variable_name",
        )
        if not isinstance(self.operation, NodeControlOperation):
            raise GatewayNodeControlTransitContractError(
                "transit grant operation is unknown"
            )
        if self.operation is NodeControlOperation.READ_STATE:
            if self.command_codec is not None:
                raise GatewayNodeControlTransitContractError(
                    "read-state transit grant must not carry command codec"
                )
        elif not isinstance(self.command_codec, ControlPlaneCommandCodec):
            raise GatewayNodeControlTransitContractError(
                "apply-command transit grant requires command codec"
            )
        if not isinstance(self.request_digest, NodeControlRequestDigest):
            raise GatewayNodeControlTransitContractError(
                "transit grant request digest is malformed"
            )
        for value, name in (
            (self.issued_at, "transit grant issued_at"),
            (self.not_before, "transit grant not_before"),
            (self.expires_at, "transit grant expires_at"),
        ):
            _require_epoch(value, name)
        if self.not_before < self.issued_at:
            raise GatewayNodeControlTransitContractError(
                "transit grant not_before precedes issued_at"
            )
        if self.expires_at <= self.not_before:
            raise GatewayNodeControlTransitContractError(
                "transit grant expires_at must follow not_before"
            )
        if (
            self.expires_at - self.issued_at
            > MAX_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_LIFETIME_SECONDS
        ):
            raise GatewayNodeControlTransitContractError(
                "transit grant lifetime exceeds 300 seconds"
            )
        _derive_audience(self.workspace_id, self.gateway_node_id)
        _bounded_canonical_bytes(
            self.descriptor(),
            MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES,
            "transit grant",
        )

    @property
    def audience(self) -> str:
        return _derive_audience(self.workspace_id, self.gateway_node_id)

    def descriptor(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "canonicalization": self.canonicalization.value,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
            "key_id": self.key_id,
            "audience": self.audience,
            "attempt_id": self.attempt_id,
            "workspace_id": self.workspace_id.value,
            "graph_revision": self.graph_revision.value,
            "gateway_node_id": self.gateway_node_id.value,
            "target": self.target.descriptor(),
            "variable_name": self.variable_name.value,
            "operation": self.operation.value,
            "command_codec": (
                self.command_codec.value if self.command_codec is not None else None
            ),
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest.value,
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "jti": self.jti,
        }

    def canonical_bytes(self) -> bytes:
        return _bounded_canonical_bytes(
            self.descriptor(),
            MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES,
            "transit grant",
        )

    def canonical_digest(self) -> GatewayNodeControlTransitGrantDigest:
        return GatewayNodeControlTransitGrantDigest(
            hashlib.sha256(self.canonical_bytes()).hexdigest()
        )


class DelegatedGatewayNodeControlTransitGrantCodec:
    """Strict mapping and canonical-byte codec for unsigned transit grants."""

    def encode(
        self,
        grant: DelegatedGatewayNodeControlTransitGrant,
    ) -> dict[str, object]:
        if not isinstance(grant, DelegatedGatewayNodeControlTransitGrant):
            raise GatewayNodeControlTransitContractError(
                "encode requires DelegatedGatewayNodeControlTransitGrant"
            )
        return grant.descriptor()

    def decode(
        self,
        descriptor: Mapping[str, object],
    ) -> DelegatedGatewayNodeControlTransitGrant:
        mapping = _bounded_mapping(
            descriptor,
            MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES,
            "transit grant",
        )
        _require_exact_keys(mapping, _GRANT_KEYS, "transit grant")
        target = _decode_target(mapping.get("target"))
        workspace_id = _decode_graph_reference(
            mapping,
            "workspace_id",
            NodeControlGraphReferenceRole.WORKSPACE,
        )
        graph_revision = _decode_graph_reference(
            mapping,
            "graph_revision",
            NodeControlGraphReferenceRole.GRAPH_REVISION,
        )
        gateway_node_id = _decode_graph_reference(
            mapping,
            "gateway_node_id",
            NodeControlGraphReferenceRole.NODE,
        )
        expected_audience = _derive_audience(workspace_id, gateway_node_id)
        if _text(mapping, "audience") != expected_audience:
            raise GatewayNodeControlTransitContractError(
                "transit grant audience must match graph coordinates"
            )
        return DelegatedGatewayNodeControlTransitGrant(
            profile=_enum(
                DelegatedGatewayNodeControlTransitGrantProfile,
                mapping.get("profile"),
                "transit grant profile",
            ),
            canonicalization=_enum(
                NodeControlCanonicalization,
                mapping.get("canonicalization"),
                "transit grant canonicalization",
            ),
            purpose=_enum(
                DelegationKeyPurpose,
                mapping.get("purpose"),
                "transit grant purpose",
            ),
            issuer=_text(mapping, "issuer"),
            key_id=_text(mapping, "key_id"),
            attempt_id=_text(mapping, "attempt_id"),
            workspace_id=workspace_id,
            graph_revision=graph_revision,
            gateway_node_id=gateway_node_id,
            target=target,
            variable_name=_decode_graph_reference(
                mapping,
                "variable_name",
                NodeControlGraphReferenceRole.VARIABLE,
            ),
            operation=_enum(
                NodeControlOperation,
                mapping.get("operation"),
                "transit grant operation",
            ),
            command_codec=_optional_enum(
                ControlPlaneCommandCodec,
                mapping.get("command_codec"),
                "transit grant command codec",
            ),
            request_id=_text(mapping, "request_id"),
            idempotency_key=_text(mapping, "idempotency_key"),
            request_digest=_decode_request_digest(mapping),
            issued_at=_integer(mapping, "issued_at"),
            not_before=_integer(mapping, "not_before"),
            expires_at=_integer(mapping, "expires_at"),
            jti=_text(mapping, "jti"),
        )

    def encode_canonical_bytes(
        self,
        grant: DelegatedGatewayNodeControlTransitGrant,
    ) -> bytes:
        if not isinstance(grant, DelegatedGatewayNodeControlTransitGrant):
            raise GatewayNodeControlTransitContractError(
                "canonical encoding requires a transit grant"
            )
        return grant.canonical_bytes()

    def decode_canonical_bytes(
        self,
        encoded: bytes,
    ) -> DelegatedGatewayNodeControlTransitGrant:
        if type(encoded) is not bytes:
            raise GatewayNodeControlTransitContractError(
                "canonical transit grant input must be bytes"
            )
        if len(encoded) > MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES:
            raise GatewayNodeControlTransitContractError(
                "transit grant aggregate exceeds the public bound"
            )
        parsed = _parse_json_object(encoded)
        grant = self.decode(parsed)
        if grant.canonical_bytes() != encoded:
            raise GatewayNodeControlTransitContractError(
                "transit grant bytes are not canonical"
            )
        return grant


class GatewayNodeControlTransitGrantVerificationCode(StrEnum):
    """Bounded reasons why exact gateway transit authority was rejected."""

    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    PURPOSE_MISMATCH = "purpose-mismatch"
    ISSUER_MISMATCH = "issuer-mismatch"
    KEY_MISMATCH = "key-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"
    ATTEMPT_MISMATCH = "attempt-mismatch"
    WORKSPACE_MISMATCH = "workspace-mismatch"
    REVISION_MISMATCH = "revision-mismatch"
    GATEWAY_MISMATCH = "gateway-mismatch"
    NODE_MISMATCH = "node-mismatch"
    SOCKET_MISMATCH = "socket-mismatch"
    VARIABLE_MISMATCH = "variable-mismatch"
    COMMAND_MISMATCH = "command-mismatch"
    REQUEST_MISMATCH = "request-mismatch"


@dataclass(frozen=True)
class GatewayNodeControlTransitGrantVerificationResult:
    """Secret-free result of pure transit claim comparison."""

    is_accepted: bool
    code: GatewayNodeControlTransitGrantVerificationCode | None = None

    def __post_init__(self) -> None:
        if type(self.is_accepted) is not bool:
            raise GatewayNodeControlTransitContractError(
                "transit verification acceptance must be boolean"
            )
        if self.is_accepted and self.code is not None:
            raise GatewayNodeControlTransitContractError(
                "accepted transit verification has no rejection code"
            )
        if not self.is_accepted and not isinstance(
            self.code,
            GatewayNodeControlTransitGrantVerificationCode,
        ):
            raise GatewayNodeControlTransitContractError(
                "rejected transit verification requires a bounded code"
            )

    @classmethod
    def allow(cls) -> "GatewayNodeControlTransitGrantVerificationResult":
        return cls(True)

    @classmethod
    def reject(
        cls,
        code: GatewayNodeControlTransitGrantVerificationCode,
    ) -> "GatewayNodeControlTransitGrantVerificationResult":
        return cls(False, code)

    def descriptor(self) -> dict[str, object]:
        return {
            "accepted": self.is_accepted,
            "code": self.code.value if self.code is not None else None,
        }


def verify_gateway_node_control_transit_grant(
    grant: object,
    request: NodeControlCommandRequest,
    *,
    expected_issuer: str,
    expected_key_id: str,
    expected_attempt_id: str,
    expected_gateway_node_id: NodeControlGraphReference,
    now: int,
) -> GatewayNodeControlTransitGrantVerificationResult:
    """Compare one unsigned transit grant to trusted graph/request truth."""

    if not isinstance(request, NodeControlCommandRequest):
        raise GatewayNodeControlTransitContractError(
            "transit verification requires NodeControlCommandRequest"
        )
    _require_reference(expected_issuer, "expected transit issuer")
    _require_identifier(expected_key_id, "expected transit key_id")
    _require_identifier(expected_attempt_id, "expected transit attempt_id")
    _require_graph_reference(
        expected_gateway_node_id,
        NodeControlGraphReferenceRole.NODE,
        "expected transit gateway_node_id",
    )
    _require_epoch(now, "transit verification time")

    reject = GatewayNodeControlTransitGrantVerificationResult.reject
    code = GatewayNodeControlTransitGrantVerificationCode
    if not isinstance(grant, DelegatedGatewayNodeControlTransitGrant):
        return reject(code.GRANT_TYPE_MISMATCH)
    if grant.purpose is not DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT:
        return reject(code.PURPOSE_MISMATCH)
    if grant.issuer != expected_issuer:
        return reject(code.ISSUER_MISMATCH)
    if grant.key_id != expected_key_id:
        return reject(code.KEY_MISMATCH)
    if now < grant.not_before or now >= grant.expires_at:
        return reject(code.TEMPORALLY_INVALID)
    if grant.attempt_id != expected_attempt_id:
        return reject(code.ATTEMPT_MISMATCH)
    if grant.workspace_id != request.target.workspace_id:
        return reject(code.WORKSPACE_MISMATCH)
    if grant.graph_revision != request.target.graph_revision:
        return reject(code.REVISION_MISMATCH)
    if grant.gateway_node_id != expected_gateway_node_id:
        return reject(code.GATEWAY_MISMATCH)
    if grant.target.node_id != request.target.node_id:
        return reject(code.NODE_MISMATCH)
    if grant.target.provider_socket_name != request.target.provider_socket_name:
        return reject(code.SOCKET_MISMATCH)
    if grant.variable_name != request.variable_name:
        return reject(code.VARIABLE_MISMATCH)
    if (
        grant.operation is not request.operation
        or grant.command_codec is not request.command_codec
    ):
        return reject(code.COMMAND_MISMATCH)
    if (
        grant.request_id != request.request_id
        or grant.idempotency_key != request.idempotency_key
        or grant.request_digest != request.canonical_digest()
    ):
        return reject(code.REQUEST_MISMATCH)
    return GatewayNodeControlTransitGrantVerificationResult.allow()


class _DuplicateJsonKey(ValueError):
    pass


def _parse_json_object(encoded: bytes) -> Mapping[str, object]:
    parsed: object | None = None
    failed = False
    try:
        text = encoded.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        RecursionError,
        ValueError,
    ):
        failed = True
    if failed:
        raise GatewayNodeControlTransitContractError(
            "transit grant bytes are malformed"
        )
    return _mapping(parsed, "transit grant")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError


def _bounded_mapping(
    value: object,
    maximum: int,
    name: str,
) -> Mapping[str, object]:
    mapping = _mapping(value, name)
    _bounded_canonical_bytes(mapping, maximum, name)
    return mapping


def _bounded_canonical_bytes(value: object, maximum: int, name: str) -> bytes:
    encoded: bytes | None = None
    try:
        encoded = canonical_json_bytes(value)
    except NodeControlCanonicalDomainError:
        pass
    if encoded is None:
        raise GatewayNodeControlTransitContractError(
            f"{name} is outside the canonical JSON domain"
        )
    if len(encoded) > maximum:
        raise GatewayNodeControlTransitContractError(
            f"{name} aggregate exceeds the public bound"
        )
    return encoded


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise GatewayNodeControlTransitContractError(f"{name} must be an object")
    return value


def _require_exact_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    name: str,
) -> None:
    if set(mapping) != expected:
        raise GatewayNodeControlTransitContractError(
            f"{name} must contain the exact public fields"
        )


def _decode_target(value: object) -> NodeControlTarget:
    mapping = _mapping(value, "transit grant target")
    _require_exact_keys(mapping, _TARGET_KEYS, "transit grant target")
    return NodeControlTarget(
        workspace_id=_decode_graph_reference(
            mapping,
            "workspace_id",
            NodeControlGraphReferenceRole.WORKSPACE,
        ),
        graph_revision=_decode_graph_reference(
            mapping,
            "graph_revision",
            NodeControlGraphReferenceRole.GRAPH_REVISION,
        ),
        node_id=_decode_graph_reference(
            mapping,
            "node_id",
            NodeControlGraphReferenceRole.NODE,
        ),
        provider_socket_name=_decode_graph_reference(
            mapping,
            "provider_socket_name",
            NodeControlGraphReferenceRole.PROVIDER_SOCKET,
        ),
    )


def _decode_graph_reference(
    mapping: Mapping[str, object],
    key: str,
    role: NodeControlGraphReferenceRole,
) -> NodeControlGraphReference:
    try:
        return NodeControlGraphReference(role, _text(mapping, key))
    except (TypeError, ValueError):
        pass
    raise GatewayNodeControlTransitContractError(
        f"transit grant {key} is malformed"
    )


def _decode_request_digest(
    mapping: Mapping[str, object],
) -> NodeControlRequestDigest:
    try:
        return NodeControlRequestDigest(_text(mapping, "request_digest"))
    except (TypeError, ValueError):
        pass
    raise GatewayNodeControlTransitContractError(
        "transit grant request_digest is malformed"
    )


def _enum(enum_type, value: object, name: str):
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise GatewayNodeControlTransitContractError(f"{name} is unknown")


def _optional_enum(enum_type, value: object, name: str):
    if value is None:
        return None
    return _enum(enum_type, value, name)


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise GatewayNodeControlTransitContractError(
            f"transit grant {key} must be text"
        )
    return value


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise GatewayNodeControlTransitContractError(
            f"transit grant {key} must be an integer"
        )
    return value


def _derive_audience(
    workspace_id: NodeControlGraphReference,
    gateway_node_id: NodeControlGraphReference,
) -> str:
    _require_graph_reference(
        workspace_id,
        NodeControlGraphReferenceRole.WORKSPACE,
        "transit audience workspace_id",
    )
    _require_graph_reference(
        gateway_node_id,
        NodeControlGraphReferenceRole.NODE,
        "transit audience gateway_node_id",
    )
    audience = f"gateway:{workspace_id.value}:{gateway_node_id.value}"
    violation = public_material_violation(audience)
    if (
        len(audience.encode("ascii"))
        > MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES
        or violation is not None
    ):
        raise GatewayNodeControlTransitContractError(
            "transit grant audience is malformed"
        )
    return audience


def _require_graph_reference(
    value: object,
    role: NodeControlGraphReferenceRole,
    name: str,
) -> None:
    if not isinstance(value, NodeControlGraphReference) or value.role is not role:
        raise GatewayNodeControlTransitContractError(
            f"{name} must have role {role.value}"
        )


def _require_identifier(value: object, name: str) -> None:
    _raise_wire_violation(identifier_violation(value), name)


def _require_reference(value: object, name: str) -> None:
    _raise_wire_violation(reference_violation(value), name)


def _require_epoch(value: object, name: str) -> None:
    if epoch_violation(value) is not None:
        raise GatewayNodeControlTransitContractError(
            f"{name} must be a nonnegative interoperable integer"
        )


def _raise_wire_violation(
    violation: NodeControlPublicWireViolation | None,
    name: str,
) -> None:
    if violation is None:
        return
    if violation is NodeControlPublicWireViolation.CREDENTIAL_ENVELOPE:
        message = f"{name} contains credential material"
    elif violation is NodeControlPublicWireViolation.ENDPOINT_ENVELOPE:
        message = f"{name} contains runtime endpoint material"
    else:
        message = f"{name} is malformed"
    raise GatewayNodeControlTransitContractError(message)


__all__ = [
    "MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES",
    "MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES",
    "MAX_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_LIFETIME_SECONDS",
    "DelegatedGatewayNodeControlTransitGrant",
    "DelegatedGatewayNodeControlTransitGrantCodec",
    "DelegatedGatewayNodeControlTransitGrantProfile",
    "GatewayNodeControlTransitContractError",
    "GatewayNodeControlTransitGrantDigest",
    "GatewayNodeControlTransitGrantVerificationCode",
    "GatewayNodeControlTransitGrantVerificationResult",
    "verify_gateway_node_control_transit_grant",
]
