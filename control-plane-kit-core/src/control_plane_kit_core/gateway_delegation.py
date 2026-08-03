"""Pure language for delegating bounded gateway probe authority.

This module names what may be delegated and what a verifier may conclude. It
does not authenticate operators, sign compact envelopes, inspect headers, keep
replay state, or perform target IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Mapping

from control_plane_kit_core.runtime_effects import GatewayTargetId


_MAX_TEXT = 256
_MAX_HTTP_PATH = 512
_MAX_GRANT_LIFETIME_SECONDS = 300
_REFERENCE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,255}$")
_KEY_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SECRET_MARKERS = (
    "password=",
    "token=",
    "secret=",
    "authorization:",
    "bearer ",
    "private key",
    "cf_tunnel_",
)
_REQUEST_KEYS = frozenset({"kind", "target_id", "path"})
_GRANT_KEYS = frozenset(
    {
        "issuer",
        "key_id",
        "audience",
        "workspace_id",
        "operation_id",
        "request_id",
        "gateway_node_id",
        "probe_kind",
        "target_id",
        "request_digest",
        "issued_at",
        "expires_at",
        "jti",
    }
)


class GatewayDelegationContractError(ValueError):
    """Raised when delegated gateway probe language is malformed."""


class GatewayProbeCommandKind(StrEnum):
    """Closed read-only commands a local gateway may execute."""

    HTTP_STATUS = "http-status"
    POSTGRES_SELECT_ONE = "postgres-select-one"


class GatewayProbeAccessPath(StrEnum):
    """Closed graph-derived transport used to reach a runtime-island gateway."""

    RUNTIME_PRIVATE = "runtime-private"
    NAMED_PUBLIC_INGRESS = "named-public-ingress"


@dataclass(frozen=True, order=True)
class GatewayProbeRequestDigest:
    """Canonical digest of the complete bounded gateway probe request."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _DIGEST.fullmatch(self.value):
            raise GatewayDelegationContractError(
                "gateway probe request digest must be 64 lowercase hex characters"
            )


@dataclass(frozen=True, order=True)
class GatewayProbeRequest:
    """One exact read-only probe request; never an arbitrary proxy request."""

    kind: GatewayProbeCommandKind
    target_id: GatewayTargetId
    path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GatewayProbeCommandKind):
            raise GatewayDelegationContractError("gateway probe kind must be closed")
        if not isinstance(self.target_id, GatewayTargetId):
            raise GatewayDelegationContractError(
                "gateway probe target must be GatewayTargetId"
            )
        if self.kind is GatewayProbeCommandKind.HTTP_STATUS:
            _validate_http_path(self.path)
        elif self.path is not None:
            raise GatewayDelegationContractError(
                "Postgres select-one probe does not accept path"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "target_id": self.target_id.value,
            "path": self.path,
        }

    def canonical_digest(self) -> GatewayProbeRequestDigest:
        content = json.dumps(
            self.descriptor(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return GatewayProbeRequestDigest(hashlib.sha256(content).hexdigest())


class GatewayProbeRequestCodec:
    """Strict codec for the canonical request material bound by a grant."""

    def encode(self, request: GatewayProbeRequest) -> dict[str, object]:
        if not isinstance(request, GatewayProbeRequest):
            raise GatewayDelegationContractError("encode requires GatewayProbeRequest")
        return request.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> GatewayProbeRequest:
        mapping = _mapping(descriptor, "gateway probe request")
        _require_keys(mapping, _REQUEST_KEYS, "gateway probe request")
        raw_kind = mapping.get("kind")
        if not isinstance(raw_kind, str):
            raise GatewayDelegationContractError("gateway probe kind must be text")
        try:
            kind = GatewayProbeCommandKind(raw_kind)
        except ValueError as error:
            raise GatewayDelegationContractError(
                "gateway probe kind is unknown"
            ) from error
        raw_target_id = mapping.get("target_id")
        if not isinstance(raw_target_id, str):
            raise GatewayDelegationContractError(
                "gateway probe target_id must be text"
            )
        raw_path = mapping.get("path")
        if raw_path is not None and not isinstance(raw_path, str):
            raise GatewayDelegationContractError("gateway probe path must be text")
        return GatewayProbeRequest(
            kind=kind,
            target_id=GatewayTargetId(raw_target_id),
            path=raw_path,
        )


@dataclass(frozen=True, order=True)
class DelegatedGatewayProbeGrant:
    """Unsigned, exact, short-lived authority for one gateway probe request."""

    issuer: str
    key_id: str
    audience: str
    workspace_id: str
    operation_id: str
    request_id: str
    gateway_node_id: str
    probe_kind: GatewayProbeCommandKind
    target_id: GatewayTargetId
    request_digest: GatewayProbeRequestDigest
    issued_at: int
    expires_at: int
    jti: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.issuer, "grant issuer"),
            (self.audience, "grant audience"),
            (self.workspace_id, "grant workspace_id"),
            (self.operation_id, "grant operation_id"),
            (self.request_id, "grant request_id"),
            (self.gateway_node_id, "grant gateway_node_id"),
            (self.jti, "grant jti"),
        ):
            _validate_reference(value, name)
        if not isinstance(self.key_id, str) or not _KEY_ID.fullmatch(self.key_id):
            raise GatewayDelegationContractError(
                "grant key_id must be a bounded identifier"
            )
        if not isinstance(self.probe_kind, GatewayProbeCommandKind):
            raise GatewayDelegationContractError("grant probe_kind must be closed")
        if not isinstance(self.target_id, GatewayTargetId):
            raise GatewayDelegationContractError(
                "grant target_id must be GatewayTargetId"
            )
        if not isinstance(self.request_digest, GatewayProbeRequestDigest):
            raise GatewayDelegationContractError(
                "grant request digest must be GatewayProbeRequestDigest"
            )
        _validate_epoch(self.issued_at, "grant issued_at")
        _validate_epoch(self.expires_at, "grant expires_at")
        if self.expires_at <= self.issued_at:
            raise GatewayDelegationContractError(
                "grant expires_at must follow issued_at"
            )
        if self.expires_at - self.issued_at > _MAX_GRANT_LIFETIME_SECONDS:
            raise GatewayDelegationContractError(
                "grant lifetime must not exceed 300 seconds"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "issuer": self.issuer,
            "key_id": self.key_id,
            "audience": self.audience,
            "workspace_id": self.workspace_id,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "gateway_node_id": self.gateway_node_id,
            "probe_kind": self.probe_kind.value,
            "target_id": self.target_id.value,
            "request_digest": self.request_digest.value,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "jti": self.jti,
        }


class DelegatedGatewayProbeGrantCodec:
    """Strict descriptor codec for unsigned delegated probe grants."""

    def encode(self, grant: DelegatedGatewayProbeGrant) -> dict[str, object]:
        if not isinstance(grant, DelegatedGatewayProbeGrant):
            raise GatewayDelegationContractError(
                "encode requires DelegatedGatewayProbeGrant"
            )
        return grant.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> DelegatedGatewayProbeGrant:
        mapping = _mapping(descriptor, "delegated gateway probe grant")
        _require_keys(mapping, _GRANT_KEYS, "delegated gateway probe grant")
        raw_kind = _text(mapping, "probe_kind")
        try:
            probe_kind = GatewayProbeCommandKind(raw_kind)
        except ValueError as error:
            raise GatewayDelegationContractError(
                "grant probe_kind is unknown"
            ) from error
        return DelegatedGatewayProbeGrant(
            issuer=_text(mapping, "issuer"),
            key_id=_text(mapping, "key_id"),
            audience=_text(mapping, "audience"),
            workspace_id=_text(mapping, "workspace_id"),
            operation_id=_text(mapping, "operation_id"),
            request_id=_text(mapping, "request_id"),
            gateway_node_id=_text(mapping, "gateway_node_id"),
            probe_kind=probe_kind,
            target_id=GatewayTargetId(_text(mapping, "target_id")),
            request_digest=GatewayProbeRequestDigest(
                _text(mapping, "request_digest")
            ),
            issued_at=_integer(mapping, "issued_at"),
            expires_at=_integer(mapping, "expires_at"),
            jti=_text(mapping, "jti"),
        )


class GatewayHealthAccess(StrEnum):
    """Closed disclosure levels for gateway process health endpoints."""

    PUBLIC_MINIMAL = "public-minimal"
    DELEGATED_CAPABILITY = "delegated-capability"


@dataclass(frozen=True)
class GatewayHealthDisclosurePolicy:
    """Explicit first-pass health disclosure for a runtime-island gateway."""

    liveness: GatewayHealthAccess
    readiness: GatewayHealthAccess
    public_target_count: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.liveness, GatewayHealthAccess):
            raise GatewayDelegationContractError(
                "gateway liveness access must be closed"
            )
        if not isinstance(self.readiness, GatewayHealthAccess):
            raise GatewayDelegationContractError(
                "gateway readiness access must be closed"
            )
        if type(self.public_target_count) is not bool:
            raise GatewayDelegationContractError(
                "gateway public target count policy must be boolean"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "liveness": self.liveness.value,
            "readiness": self.readiness.value,
            "public_target_count": self.public_target_count,
        }


def canonical_gateway_health_disclosure_policy() -> GatewayHealthDisclosurePolicy:
    """Expose minimal liveness while protecting readiness and target metadata."""

    return GatewayHealthDisclosurePolicy(
        liveness=GatewayHealthAccess.PUBLIC_MINIMAL,
        readiness=GatewayHealthAccess.DELEGATED_CAPABILITY,
        public_target_count=False,
    )


class DelegatedGatewayProbeVerificationCode(StrEnum):
    """Bounded reason classes returned by a delegated probe verifier."""

    UNTRUSTED_GRANT = "untrusted-grant"
    TEMPORALLY_INVALID = "temporally-invalid"
    AUDIENCE_MISMATCH = "audience-mismatch"
    REQUEST_MISMATCH = "request-mismatch"
    REPLAYED = "replayed"


@dataclass(frozen=True)
class DelegatedGatewayProbeVerificationResult:
    """Secret-free verifier outcome; never carries compact grant material."""

    is_accepted: bool
    code: DelegatedGatewayProbeVerificationCode | None = None

    def __post_init__(self) -> None:
        if type(self.is_accepted) is not bool:
            raise GatewayDelegationContractError(
                "verification accepted value must be boolean"
            )
        if self.is_accepted and self.code is not None:
            raise GatewayDelegationContractError(
                "accepted verification must not carry rejection code"
            )
        if not self.is_accepted and not isinstance(
            self.code,
            DelegatedGatewayProbeVerificationCode,
        ):
            raise GatewayDelegationContractError(
                "rejected verification requires a bounded code"
            )

    @classmethod
    def allow(cls) -> "DelegatedGatewayProbeVerificationResult":
        return cls(True)

    @classmethod
    def reject(
        cls,
        code: DelegatedGatewayProbeVerificationCode,
    ) -> "DelegatedGatewayProbeVerificationResult":
        return cls(False, code)

    def descriptor(self) -> dict[str, object]:
        return {
            "accepted": self.is_accepted,
            "code": self.code.value if self.code is not None else None,
        }


def _validate_http_path(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) > _MAX_HTTP_PATH
        or "\x00" in value
        or "://" in value
        or "\\" in value
    ):
        raise GatewayDelegationContractError(
            "HTTP gateway probe path must be a bounded absolute path"
        )
    _reject_secret_assignment(value, "HTTP gateway probe path")


def _validate_reference(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_TEXT
        or not _REFERENCE.fullmatch(value)
    ):
        raise GatewayDelegationContractError(
            f"{name} must be a bounded reference"
        )


def _validate_epoch(value: object, name: str) -> None:
    if type(value) is not int or value < 0:
        raise GatewayDelegationContractError(
            f"{name} must be a nonnegative integer epoch second"
        )


def _reject_secret_assignment(value: str, name: str) -> None:
    if any(marker in value.lower() for marker in _SECRET_MARKERS):
        raise GatewayDelegationContractError(f"{name} contains secret-shaped text")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GatewayDelegationContractError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise GatewayDelegationContractError(f"{name} keys must be text")
    return value


def _require_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    name: str,
) -> None:
    unknown = set(mapping) - expected
    missing = expected - set(mapping)
    if unknown:
        raise GatewayDelegationContractError(
            f"{name} has unknown keys: {sorted(unknown)}"
        )
    if missing:
        raise GatewayDelegationContractError(
            f"{name} is missing keys: {sorted(missing)}"
        )


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise GatewayDelegationContractError(f"{key} must be text")
    return value


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise GatewayDelegationContractError(f"{key} must be an integer")
    return value
