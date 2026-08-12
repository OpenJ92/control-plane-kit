"""Pure provider-neutral delegation signing-key identity language."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import re


_KEY_ID = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_PUBLIC_KEY_BEGIN = "-----BEGIN PUBLIC KEY-----"
_PUBLIC_KEY_END = "-----END PUBLIC KEY-----"


class DelegationKeyPurpose(StrEnum):
    """Closed purposes for keys that delegate bounded control-plane authority."""

    GATEWAY_PROBE = "gateway-probe"
    WORKLOAD_NODE_CONTROL = "workload-node-control"
    WORKLOAD_NODE_CONTROL_SURFACE_READ = "workload-node-control-surface-read"
    GATEWAY_NODE_CONTROL_TRANSIT = "gateway-node-control-transit"


class DelegationKeyAlgorithm(StrEnum):
    """Maintained asymmetric algorithms admitted by the language."""

    ED25519 = "ed25519"


@dataclass(frozen=True)
class DelegationPublicKey:
    """One immutable public verification identity, never private material."""

    key_id: str
    algorithm: DelegationKeyAlgorithm
    public_key_pem: str = field(repr=False)
    fingerprint_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not _KEY_ID.fullmatch(self.key_id):
            raise ValueError("delegation key_id is malformed")
        if not isinstance(self.algorithm, DelegationKeyAlgorithm):
            raise TypeError("delegation key algorithm is unsupported")
        if not isinstance(self.public_key_pem, str):
            raise TypeError("delegation public key must be text")
        normalized = _normalize_public_key(self.public_key_pem)
        object.__setattr__(self, "public_key_pem", normalized)
        object.__setattr__(
            self,
            "fingerprint_sha256",
            sha256(normalized.encode("ascii")).hexdigest(),
        )

    def descriptor(self) -> dict[str, str]:
        """Return bounded public identity metadata without key material."""

        return {
            "key_id": self.key_id,
            "algorithm": self.algorithm.value,
            "fingerprint_sha256": self.fingerprint_sha256,
        }


def _normalize_public_key(value: str) -> str:
    if len(value) > 8192:
        raise ValueError("delegation public key is too large")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("delegation public key must be ASCII") from error
    normalized = value.replace("\r\n", "\n").strip() + "\n"
    lines = normalized.splitlines()
    if (
        len(lines) < 3
        or lines[0] != _PUBLIC_KEY_BEGIN
        or lines[-1] != _PUBLIC_KEY_END
        or "PRIVATE" in normalized.upper()
    ):
        raise ValueError("delegation public key must be public PEM material")
    if any(not line for line in lines[1:-1]):
        raise ValueError("delegation public key PEM body is malformed")
    return normalized


__all__ = [
    "DelegationKeyAlgorithm",
    "DelegationKeyPurpose",
    "DelegationPublicKey",
]
