"""Closed authoring values for process environment bindings."""

from __future__ import annotations

from dataclasses import dataclass
import re


_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_MAX_PUBLIC_VALUE_BYTES = 16_384
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "private_key",
    "api_key",
)


@dataclass(frozen=True, order=True)
class PublicStaticEnvironmentBinding:
    """One bounded, explicitly non-secret process environment literal."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _ENVIRONMENT_NAME.fullmatch(self.name):
            raise ValueError("public environment binding name is malformed")
        if any(marker in self.name.lower() for marker in _SECRET_MARKERS):
            raise ValueError(
                "secret-shaped environment names require SecretEnvironmentDelivery"
            )
        if not isinstance(self.value, str):
            raise TypeError("public environment binding value must be a string")
        if "\x00" in self.value or len(self.value.encode("utf-8")) > _MAX_PUBLIC_VALUE_BYTES:
            raise ValueError("public environment binding value is malformed or unbounded")

    def descriptor(self) -> dict[str, str]:
        return {
            "kind": "public-static",
            "name": self.name,
            "value": self.value,
        }
