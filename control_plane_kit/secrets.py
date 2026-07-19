"""Runtime-only secret resolution with no durable secret-value language."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from pathlib import PurePosixPath
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


class SecretFileMode(StrEnum):
    OWNER_READ_ONLY = "0400"


@dataclass(frozen=True, order=True)
class SecretFilePathBinding:
    """Expose a mounted secret path through one non-secret environment slot."""

    environment_name: str

    def __post_init__(self) -> None:
        _validate_environment_name(self.environment_name)

    def descriptor(self) -> dict[str, str]:
        return {"environment_name": self.environment_name}


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


@dataclass(frozen=True, order=True)
class SecretEnvironmentDelivery:
    """Inject one opaque reference into a named process environment slot."""

    environment_name: str
    reference: SecretReference

    def __post_init__(self) -> None:
        _validate_environment_name(self.environment_name)
        if not isinstance(self.reference, SecretReference):
            raise TypeError("secret environment delivery requires SecretReference")

    def descriptor(self) -> dict[str, str]:
        return {
            "kind": "environment",
            "environment_name": self.environment_name,
            "reference_id": self.reference.reference_id,
        }


@dataclass(frozen=True, order=True)
class SecretFileDelivery:
    """Mount one opaque reference as a protected runtime-only file."""

    target_path: str
    reference: SecretReference
    file_mode: SecretFileMode = SecretFileMode.OWNER_READ_ONLY
    path_binding: SecretFilePathBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference, SecretReference):
            raise TypeError("secret file delivery requires SecretReference")
        if not isinstance(self.file_mode, SecretFileMode):
            raise TypeError("secret file mode must be SecretFileMode")
        if self.path_binding is not None and not isinstance(
            self.path_binding, SecretFilePathBinding
        ):
            raise TypeError("secret file path binding must be SecretFilePathBinding")
        _validate_secret_target_path(self.target_path)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "file",
            "target_path": self.target_path,
            "reference_id": self.reference.reference_id,
            "file_mode": self.file_mode.value,
            "path_binding": (
                None if self.path_binding is None else self.path_binding.descriptor()
            ),
        }


SecretDelivery: TypeAlias = SecretEnvironmentDelivery | SecretFileDelivery


def secret_delivery_sort_key(value: SecretDelivery) -> tuple[str, str, str, str, str]:
    """Interpret either delivery constructor into one deterministic order."""

    match value:
        case SecretEnvironmentDelivery(environment_name=name, reference=reference):
            return ("environment", name, reference.reference_id, "", "")
        case SecretFileDelivery(
            target_path=path,
            reference=reference,
            file_mode=file_mode,
            path_binding=path_binding,
        ):
            return (
                "file",
                path,
                reference.reference_id,
                file_mode.value,
                "" if path_binding is None else path_binding.environment_name,
            )


def secret_delivery_from_descriptor(value: Mapping[str, object]) -> SecretDelivery:
    kind = value.get("kind")
    try:
        match kind:
            case "environment" if set(value) == {
                "kind",
                "environment_name",
                "reference_id",
            }:
                return SecretEnvironmentDelivery(
                    _descriptor_text(value, "environment_name"),
                    SecretReference(_descriptor_text(value, "reference_id")),
                )
            case "file" if set(value) == {
                "kind",
                "target_path",
                "reference_id",
                "file_mode",
                "path_binding",
            }:
                return SecretFileDelivery(
                    _descriptor_text(value, "target_path"),
                    SecretReference(_descriptor_text(value, "reference_id")),
                    SecretFileMode(_descriptor_text(value, "file_mode")),
                    _path_binding_from_descriptor(value.get("path_binding")),
                )
            case _:
                raise SecretResolutionError(
                    SecretResolutionCode.MALFORMED_REFERENCE,
                    "secret delivery descriptor is malformed",
                )
    except (TypeError, ValueError) as error:
        if isinstance(error, SecretResolutionError):
            raise
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret delivery descriptor is malformed",
        ) from error


def _descriptor_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret delivery descriptor is malformed",
        )
    return item


def _path_binding_from_descriptor(value: object) -> SecretFilePathBinding | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"environment_name"}:
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret delivery descriptor is malformed",
        )
    return SecretFilePathBinding(_descriptor_text(value, "environment_name"))


def _validate_environment_name(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Z][A-Z0-9_]{0,127}", value
    ):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret environment name is malformed",
        )


def _validate_secret_target_path(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("/run/secrets/"):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret file target must use the protected secret namespace",
        )
    path = PurePosixPath(value)
    if (
        str(path) != value
        or value.endswith("/")
        or any(part in (".", "..") for part in path.parts)
        or any(not _REFERENCE_SEGMENT.fullmatch(part) for part in path.parts[3:])
    ):
        raise SecretResolutionError(
            SecretResolutionCode.MALFORMED_REFERENCE,
            "secret file target is malformed",
        )


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
