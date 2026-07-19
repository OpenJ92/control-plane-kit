"""Runtime-only secret resolution with no durable secret-value language."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping, Protocol, TypeAlias
from urllib.parse import urlsplit


_PROVIDER_ID = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_REFERENCE_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


class SecretResolutionCode(StrEnum):
    MALFORMED_REFERENCE = "malformed-reference"
    MISSING = "missing"
    DENIED = "denied"
    INVALID_RESOLVER_RESULT = "invalid-resolver-result"


class SecretResolutionError(ValueError):
    """A bounded failure that never includes resolved secret content."""

    def __init__(self, code: SecretResolutionCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, order=True)
class SecretProviderId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _PROVIDER_ID.fullmatch(self.value):
            raise SecretResolutionError(
                SecretResolutionCode.MALFORMED_REFERENCE,
                "secret provider identity is malformed",
            )


@dataclass(frozen=True, order=True)
class SecretReference:
    """Opaque provider-qualified identity safe for durable descriptors."""

    reference_id: str
    provider_id: SecretProviderId = field(init=False, compare=True)
    path: tuple[str, ...] = field(init=False, compare=True, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.reference_id, str):
            raise SecretResolutionError(
                SecretResolutionCode.MALFORMED_REFERENCE,
                "secret reference is malformed",
            )
        parsed = urlsplit(self.reference_id)
        path = tuple(part for part in parsed.path.split("/") if part)
        if (
            parsed.scheme != "secret"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or not path
            or parsed.path != "/" + "/".join(path)
            or any(part in (".", "..") for part in path)
            or any(not _REFERENCE_SEGMENT.fullmatch(part) for part in path)
        ):
            raise SecretResolutionError(
                SecretResolutionCode.MALFORMED_REFERENCE,
                "secret reference is malformed",
            )
        object.__setattr__(self, "provider_id", SecretProviderId(parsed.netloc))
        object.__setattr__(self, "path", path)


CredentialReference = SecretReference


@dataclass(frozen=True, repr=False)
class SecretValue:
    """Ephemeral resolved text with a deliberately redacted representation."""

    _value: str

    def __post_init__(self) -> None:
        if not isinstance(self._value, str) or not self._value:
            raise SecretResolutionError(
                SecretResolutionCode.INVALID_RESOLVER_RESULT,
                "secret resolver returned an invalid value",
            )

    def reveal(self) -> str:
        """Release the value only to the bounded runtime transport boundary."""

        return self._value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"


@dataclass(frozen=True)
class SecretResolved:
    reference: SecretReference
    value: SecretValue = field(repr=False)


@dataclass(frozen=True)
class SecretMissing:
    reference: SecretReference


@dataclass(frozen=True)
class SecretDenied:
    reference: SecretReference


SecretResolution: TypeAlias = SecretResolved | SecretMissing | SecretDenied


@dataclass(frozen=True)
class SecretProviderAuthority:
    """Process-bootstrap authority for one provider and path subset."""

    provider_id: SecretProviderId
    allowed_prefixes: tuple[tuple[str, ...], ...] = ((),)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, SecretProviderId):
            raise TypeError("secret provider authority requires SecretProviderId")
        if not self.allowed_prefixes or any(
            not isinstance(prefix, tuple)
            or any(not _REFERENCE_SEGMENT.fullmatch(part) for part in prefix)
            for prefix in self.allowed_prefixes
        ):
            raise TypeError("secret provider authority prefixes are malformed")

    def permits(self, reference: SecretReference) -> bool:
        return (
            reference.provider_id == self.provider_id
            and any(reference.path[: len(prefix)] == prefix for prefix in self.allowed_prefixes)
        )


class SecretResolver(Protocol):
    """Runtime authority supplied outside the deployment graph."""

    @property
    def authority(self) -> SecretProviderAuthority: ...

    def resolve(self, reference: SecretReference) -> SecretResolution: ...


@dataclass(frozen=True, repr=False)
class LocalDevelopmentSecretResolver:
    """Explicit process-memory resolver for local development and tests."""

    authority: SecretProviderAuthority
    _values: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        copied = dict(self._values)
        for reference_id, value in copied.items():
            reference = SecretReference(reference_id)
            if not self.authority.permits(reference):
                raise SecretResolutionError(
                    SecretResolutionCode.DENIED,
                    "local secret configuration exceeds bootstrap authority",
                )
            if not isinstance(value, str) or not value:
                raise SecretResolutionError(
                    SecretResolutionCode.INVALID_RESOLVER_RESULT,
                    "local secret configuration contains an invalid value",
                )
        object.__setattr__(self, "_values", MappingProxyType(copied))

    def resolve(self, reference: SecretReference) -> SecretResolution:
        if not isinstance(reference, SecretReference):
            raise TypeError("secret resolver requires SecretReference")
        if not self.authority.permits(reference):
            return SecretDenied(reference)
        value = self._values.get(reference.reference_id)
        if value is None:
            return SecretMissing(reference)
        return SecretResolved(reference, SecretValue(value))

    def __repr__(self) -> str:
        return (
            "LocalDevelopmentSecretResolver("
            f"authority={self.authority!r}, values=<redacted>)"
        )


def require_resolved_secret(
    resolver: SecretResolver,
    reference: SecretReference,
) -> SecretValue:
    """Interpret a resolver outcome without exposing secret material."""

    result = resolver.resolve(reference)
    match result:
        case SecretResolved(reference=resolved_reference, value=value) if resolved_reference == reference:
            return value
        case SecretMissing():
            raise SecretResolutionError(
                SecretResolutionCode.MISSING,
                "secret reference could not be resolved",
            )
        case SecretDenied():
            raise SecretResolutionError(
                SecretResolutionCode.DENIED,
                "secret reference is outside bootstrap authority",
            )
        case _:
            raise SecretResolutionError(
                SecretResolutionCode.INVALID_RESOLVER_RESULT,
                "secret resolver returned an invalid result",
            )
