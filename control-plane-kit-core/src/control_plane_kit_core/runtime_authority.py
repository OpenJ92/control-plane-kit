"""Pure runtime-authority reference language."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


_MAX_TEXT = 512
_RUNTIME_AUTHORITY_REFERENCE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_RUNTIME_AUTHORITY_REFERENCE_KEYS = frozenset({"reference_id"})


class RuntimeEffectContractError(ValueError):
    """Raised when pure runtime-effect material is malformed."""


@dataclass(frozen=True, order=True)
class RuntimeAuthorityReference:
    """Secret-free name for an admitted runtime authority."""

    reference_id: str

    def __post_init__(self) -> None:
        _validate_runtime_authority_reference(self.reference_id)

    def descriptor(self) -> dict[str, object]:
        return {"reference_id": self.reference_id}


class RuntimeAuthorityReferenceCodec:
    """Strict codec for runtime authority references."""

    def encode(self, reference: RuntimeAuthorityReference) -> dict[str, object]:
        if not isinstance(reference, RuntimeAuthorityReference):
            raise RuntimeEffectContractError(
                "encode requires RuntimeAuthorityReference"
            )
        return reference.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> RuntimeAuthorityReference:
        mapping = _authority_mapping(descriptor, "runtime authority reference")
        _require_authority_keys(mapping, _RUNTIME_AUTHORITY_REFERENCE_KEYS)
        reference_id = mapping.get("reference_id")
        if not isinstance(reference_id, str):
            raise RuntimeEffectContractError("reference_id must be text")
        return RuntimeAuthorityReference(reference_id)


def _authority_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeEffectContractError(f"{label} descriptor must be a mapping")
    return value


def _require_authority_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
) -> None:
    keys = frozenset(mapping)
    if keys == expected:
        return
    extra = sorted(keys - expected)
    missing = sorted(expected - keys)
    details: list[str] = []
    if extra:
        details.append(f"unknown keys: {', '.join(extra)}")
    if missing:
        details.append(f"missing keys: {', '.join(missing)}")
    raise RuntimeEffectContractError(
        "invalid runtime authority reference descriptor; " + "; ".join(details)
    )


def _validate_runtime_authority_reference(value: str) -> None:
    if not isinstance(value, str):
        raise RuntimeEffectContractError("runtime authority reference must be text")
    if len(value) > _MAX_TEXT:
        raise RuntimeEffectContractError("runtime authority reference is too long")
    lowered = value.lower()
    if any(
        marker in lowered
        for marker in (
            "password",
            "token",
            "secret",
            "private-key",
            "private_key",
            "begin-private-key",
            "dockerconfigjson",
        )
    ):
        raise RuntimeEffectContractError(
            "runtime authority reference must not contain secret-shaped text"
        )
    if not _RUNTIME_AUTHORITY_REFERENCE.fullmatch(value):
        raise RuntimeEffectContractError(
            "runtime authority reference must be a bounded lowercase identifier"
        )
