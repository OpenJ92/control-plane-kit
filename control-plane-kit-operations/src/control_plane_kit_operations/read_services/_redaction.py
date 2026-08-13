"""Private recursive redaction for read-model descriptors."""

from __future__ import annotations

from typing import Mapping


_REDACTED = "<redacted>"
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "private_key",
    "credential",
    "api_key",
)
_ADDRESS_KEYS = ("address", "url", "environment", "env_assignments")


def _redact_descriptor_value(key: str, value: object) -> object:
    if key.lower().replace("-", "_") == "environment_bindings":
        return _redact_environment_bindings(value)
    if _looks_sensitive_key(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_descriptor_value(str(child_key), child_value)
            for child_key, child_value in sorted(value.items())
        }
    if isinstance(value, list):
        return [_redact_descriptor_value(key, child) for child in value]
    if isinstance(value, tuple):
        return tuple(_redact_descriptor_value(key, child) for child in value)
    return value


def _redact_environment_bindings(value: object) -> object:
    if not isinstance(value, (list, tuple)):
        return _REDACTED
    redacted: list[object] = []
    for binding in value:
        if not isinstance(binding, Mapping):
            redacted.append(_REDACTED)
            continue
        redacted.append(
            {
                str(child_key): (
                    _REDACTED
                    if str(child_key) in {"value", "reference", "reference_id"}
                    else _redact_descriptor_value(str(child_key), child_value)
                )
                for child_key, child_value in sorted(binding.items())
            }
        )
    return redacted


def _looks_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _ADDRESS_KEYS
        or ("." not in normalized and normalized.endswith("_url"))
        or any(marker in normalized for marker in _SECRET_MARKERS)
    )
