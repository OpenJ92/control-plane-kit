"""Capability verification for the local runtime-island gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import jwt

from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    DelegatedGatewayProbeGrantCodec,
    DelegatedGatewayProbeVerificationCode,
    GatewayDelegationContractError,
    GatewayProbeRequest,
    GatewayProbeRequestCodec,
)


_AUTHORIZATION_SCHEME = "CPK-Gateway"
_TOKEN_TYPE = "CPK-GATEWAY-PROBE+JWT"
_MAX_AUTHORIZATION_HEADER_BYTES = 12_288
_MAX_REQUEST_BODY_BYTES = 4_096
_MAX_PUBLIC_KEYS = 16
_MAX_REPLAY_ENTRIES = 4_096
_CLOCK_SKEW_SECONDS = 5
_CLAIM_KEYS = frozenset({"iss", "aud", "iat", "exp", "jti", "gateway_probe"})
_HEADER_KEYS = frozenset({"alg", "kid", "typ"})


class GatewayProbeVerifier(Protocol):
    """Verifies one inbound gateway probe capability before target IO."""

    def verify(
        self,
        authorization_header: str | None,
        body: bytes,
    ) -> GatewayProbeRequest:
        """Return the exact request authorized by the capability."""


class GatewayProbeVerificationError(RuntimeError):
    """Bounded verification failure; never contains compact token material."""

    def __init__(
        self,
        code: DelegatedGatewayProbeVerificationCode,
        *,
        status_code: int,
    ) -> None:
        super().__init__("gateway capability rejected")
        self.code = code
        self.status_code = status_code


@dataclass(repr=False)
class GatewayProbeReplayCache:
    """Small in-memory replay cache for delegated gateway probe JTIs."""

    clock: Callable[[], float] = time.time
    max_entries: int = _MAX_REPLAY_ENTRIES
    _entries: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.max_entries) is not int
            or self.max_entries < 1
            or self.max_entries > _MAX_REPLAY_ENTRIES
        ):
            raise ValueError("gateway replay cache size is outside supported bounds")

    def remember_once(self, jti: str, expires_at: int) -> None:
        now = int(self.clock())
        with self._lock:
            expired = [key for key, value in self._entries.items() if value <= now]
            for key in expired:
                self._entries.pop(key, None)
            if jti in self._entries:
                raise GatewayProbeVerificationError(
                    DelegatedGatewayProbeVerificationCode.REPLAYED,
                    status_code=409,
                )
            if len(self._entries) >= self.max_entries:
                raise GatewayProbeVerificationError(
                    DelegatedGatewayProbeVerificationCode.REPLAYED,
                    status_code=409,
                )
            self._entries[jti] = expires_at

    def __repr__(self) -> str:
        return "GatewayProbeReplayCache(<redacted>)"


@dataclass(frozen=True, repr=False)
class Ed25519GatewayProbeVerifier:
    """Verifies Ed25519 JWT capabilities for one gateway process."""

    issuer: str
    audience: str
    gateway_node_id: str
    public_keys: Mapping[str, str]
    replay_cache: GatewayProbeReplayCache
    clock: Callable[[], float] = time.time
    clock_skew_seconds: int = _CLOCK_SKEW_SECONDS

    def __post_init__(self) -> None:
        keys = _validated_public_keys(self.public_keys)
        if not isinstance(self.replay_cache, GatewayProbeReplayCache):
            raise TypeError("gateway verifier requires replay cache")
        if (
            type(self.clock_skew_seconds) is not int
            or self.clock_skew_seconds < 0
            or self.clock_skew_seconds > 30
        ):
            raise ValueError("gateway verifier clock skew is outside supported bounds")
        object.__setattr__(self, "public_keys", MappingProxyType(keys))

    def verify(
        self,
        authorization_header: str | None,
        body: bytes,
    ) -> GatewayProbeRequest:
        request = _decode_request_body(body)
        token = _extract_token(authorization_header)
        key = self._select_key(token)
        claims = self._decode_claims(token, key)
        grant = self._decode_grant(claims)
        self._require_exact_claim_binding(claims, grant)
        self._require_exact_authority(grant)
        self._require_temporal_validity(grant)
        self._require_exact_request(grant, request)
        self.replay_cache.remember_once(grant.jti, grant.expires_at)
        return request

    def _select_key(self, token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except Exception:
            raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
        if set(header) != _HEADER_KEYS:
            raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
        if header.get("alg") != "EdDSA" or header.get("typ") != _TOKEN_TYPE:
            raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self.public_keys:
            raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
        return self.public_keys[kid]

    def _decode_claims(self, token: str, public_key: str) -> Mapping[str, object]:
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["EdDSA"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": ["iss", "aud", "iat", "exp", "jti"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.InvalidAudienceError:
            raise _rejected(DelegatedGatewayProbeVerificationCode.AUDIENCE_MISMATCH)
        except jwt.InvalidIssuerError:
            raise _rejected(DelegatedGatewayProbeVerificationCode.AUDIENCE_MISMATCH)
        except Exception:
            raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
        if not isinstance(claims, Mapping) or set(claims) != _CLAIM_KEYS:
            raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
        return claims

    def _decode_grant(
        self,
        claims: Mapping[str, object],
    ) -> DelegatedGatewayProbeGrant:
        try:
            grant_descriptor = claims["gateway_probe"]
            if not isinstance(grant_descriptor, Mapping):
                raise GatewayDelegationContractError("gateway grant must be object")
            return DelegatedGatewayProbeGrantCodec().decode(grant_descriptor)
        except GatewayDelegationContractError:
            raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)

    def _require_exact_claim_binding(
        self,
        claims: Mapping[str, object],
        grant: DelegatedGatewayProbeGrant,
    ) -> None:
        if (
            claims.get("iss") != grant.issuer
            or claims.get("aud") != grant.audience
            or claims.get("iat") != grant.issued_at
            or claims.get("exp") != grant.expires_at
            or claims.get("jti") != grant.jti
        ):
            raise _rejected(DelegatedGatewayProbeVerificationCode.REQUEST_MISMATCH)

    def _require_exact_authority(self, grant: DelegatedGatewayProbeGrant) -> None:
        if (
            grant.issuer != self.issuer
            or grant.audience != self.audience
            or grant.gateway_node_id != self.gateway_node_id
        ):
            raise _rejected(DelegatedGatewayProbeVerificationCode.AUDIENCE_MISMATCH)

    def _require_temporal_validity(self, grant: DelegatedGatewayProbeGrant) -> None:
        now = int(self.clock())
        if (
            grant.issued_at > now + self.clock_skew_seconds
            or grant.expires_at <= now - self.clock_skew_seconds
        ):
            raise _rejected(DelegatedGatewayProbeVerificationCode.TEMPORALLY_INVALID)

    def _require_exact_request(
        self,
        grant: DelegatedGatewayProbeGrant,
        request: GatewayProbeRequest,
    ) -> None:
        if (
            grant.probe_kind is not request.kind
            or grant.target_id != request.target_id
            or grant.request_digest != request.canonical_digest()
        ):
            raise _rejected(DelegatedGatewayProbeVerificationCode.REQUEST_MISMATCH)

    def __repr__(self) -> str:
        return "Ed25519GatewayProbeVerifier(<redacted>)"


def _decode_request_body(body: bytes) -> GatewayProbeRequest:
    if not isinstance(body, bytes) or len(body) > _MAX_REQUEST_BODY_BYTES:
        raise _rejected(DelegatedGatewayProbeVerificationCode.REQUEST_MISMATCH)
    try:
        descriptor = json.loads(body.decode("utf-8"))
        if not isinstance(descriptor, Mapping):
            raise GatewayDelegationContractError("gateway request must be object")
        return GatewayProbeRequestCodec().decode(descriptor)
    except (UnicodeDecodeError, json.JSONDecodeError, GatewayDelegationContractError):
        raise _rejected(DelegatedGatewayProbeVerificationCode.REQUEST_MISMATCH)


def _extract_token(authorization_header: str | None) -> str:
    if (
        not isinstance(authorization_header, str)
        or not authorization_header
        or len(authorization_header.encode("utf-8")) > _MAX_AUTHORIZATION_HEADER_BYTES
    ):
        raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
    parts = authorization_header.split(" ")
    if len(parts) != 2 or parts[0] != _AUTHORIZATION_SCHEME or not parts[1]:
        raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
    try:
        parts[1].encode("ascii")
    except UnicodeEncodeError:
        raise _rejected(DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT)
    return parts[1]


def _validated_public_keys(public_keys: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(public_keys, Mapping) or not public_keys:
        raise ValueError("gateway verifier requires at least one public key")
    if len(public_keys) > _MAX_PUBLIC_KEYS:
        raise ValueError("gateway verifier has too many public keys")
    validated: dict[str, str] = {}
    for key_id, public_key in public_keys.items():
        if not isinstance(key_id, str) or not key_id:
            raise ValueError("gateway verifier key id must be text")
        if not isinstance(public_key, str) or "PRIVATE KEY" in public_key:
            raise ValueError("gateway verifier public key must be public PEM")
        try:
            loaded = serialization.load_pem_public_key(public_key.encode("ascii"))
        except Exception as exc:
            raise ValueError("gateway verifier public key is invalid") from exc
        if not isinstance(loaded, Ed25519PublicKey):
            raise ValueError("gateway verifier public key must be Ed25519")
        validated[key_id] = public_key
    return dict(sorted(validated.items()))


def _rejected(
    code: DelegatedGatewayProbeVerificationCode,
) -> GatewayProbeVerificationError:
    status = {
        DelegatedGatewayProbeVerificationCode.UNTRUSTED_GRANT: 401,
        DelegatedGatewayProbeVerificationCode.TEMPORALLY_INVALID: 401,
        DelegatedGatewayProbeVerificationCode.AUDIENCE_MISMATCH: 403,
        DelegatedGatewayProbeVerificationCode.REQUEST_MISMATCH: 403,
        DelegatedGatewayProbeVerificationCode.REPLAYED: 409,
    }[code]
    return GatewayProbeVerificationError(code, status_code=status)
