"""Pure external product identity language."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re


_IDENTITY_PART = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_REGISTRY = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?$")
_REPOSITORY_PART = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_PART_LENGTH = 96
_MAX_REPOSITORY_LENGTH = 255
_MAX_PROVENANCE_FIELDS = 16
_MAX_PROVENANCE_VALUE_LENGTH = 256
_DESCRIPTOR_KEYS = frozenset({"namespace", "name", "contract_revision"})
_OCI_DESCRIPTOR_KEYS = frozenset(
    {"registry", "repository", "digest", "tag", "platforms", "provenance"}
)
_PLATFORM_DESCRIPTOR_KEYS = frozenset({"os", "architecture", "variant"})
_SECRET_FIELD_HINTS = ("secret", "token", "password", "credential", "key")


class ProductIdentityError(ValueError):
    """Raised when an external product identity is not in the closed language."""


class DuplicateProductIdentity(ProductIdentityError):
    """Raised when a product identity appears more than once in one catalogue."""


class OciImageReferenceError(ValueError):
    """Raised when an OCI image reference is not in the closed language."""


class PlatformMismatch(OciImageReferenceError):
    """Raised when an admitted image cannot run on the requested platform."""


@dataclass(frozen=True, order=True)
class ProductIdentity:
    """Language-neutral identity for an externally supplied product contract."""

    namespace: str
    name: str
    contract_revision: int

    def __post_init__(self) -> None:
        _validate_identity_part(self.namespace, "namespace")
        _validate_identity_part(self.name, "name")
        if type(self.contract_revision) is not int or self.contract_revision < 1:
            raise ProductIdentityError("contract_revision must be a positive integer")

    @property
    def key(self) -> str:
        """Return the stable human-readable product identity key."""

        return f"{self.namespace}/{self.name}/{self.contract_revision}"

    def descriptor(self) -> dict[str, object]:
        """Return the deterministic durable descriptor form."""

        return {
            "namespace": self.namespace,
            "name": self.name,
            "contract_revision": self.contract_revision,
        }


class ProductIdentityCodec:
    """Strict codec for the current product identity descriptor language."""

    def encode(self, identity: ProductIdentity) -> dict[str, object]:
        if not isinstance(identity, ProductIdentity):
            raise ProductIdentityError("encode requires ProductIdentity")
        return identity.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> ProductIdentity:
        mapping = _mapping(descriptor, "product_identity")
        keys = frozenset(mapping)
        if keys != _DESCRIPTOR_KEYS:
            extra = sorted(keys - _DESCRIPTOR_KEYS)
            missing = sorted(_DESCRIPTOR_KEYS - keys)
            details: list[str] = []
            if extra:
                details.append(f"unknown keys: {', '.join(extra)}")
            if missing:
                details.append(f"missing keys: {', '.join(missing)}")
            raise ProductIdentityError(
                "invalid product identity descriptor; " + "; ".join(details)
            )
        return ProductIdentity(
            namespace=_text(mapping, "namespace"),
            name=_text(mapping, "name"),
            contract_revision=_integer(mapping, "contract_revision"),
        )


def require_unique_product_identities(
    identities: Iterable[ProductIdentity],
) -> tuple[ProductIdentity, ...]:
    """Return identities sorted after proving there are no duplicates."""

    values = tuple(identities)
    for identity in values:
        if not isinstance(identity, ProductIdentity):
            raise ProductIdentityError("catalogue identity must be ProductIdentity")
    ordered = tuple(sorted(values))
    seen: set[ProductIdentity] = set()
    for identity in ordered:
        if identity in seen:
            raise DuplicateProductIdentity(f"duplicate product identity {identity.key}")
        seen.add(identity)
    return ordered


@dataclass(frozen=True, order=True)
class OciPlatform:
    """A bounded OCI platform constraint."""

    os: str
    architecture: str
    variant: str | None = None

    def __post_init__(self) -> None:
        _validate_platform_part(self.os, "platform.os")
        _validate_platform_part(self.architecture, "platform.architecture")
        if self.variant is not None:
            _validate_platform_part(self.variant, "platform.variant")

    @property
    def label(self) -> str:
        if self.variant is None:
            return f"{self.os}/{self.architecture}"
        return f"{self.os}/{self.architecture}/{self.variant}"

    def descriptor(self) -> dict[str, object]:
        return {
            "os": self.os,
            "architecture": self.architecture,
            "variant": self.variant,
        }


@dataclass(frozen=True)
class OciImageReference:
    """Digest-pinned OCI workload artifact reference."""

    registry: str
    repository: str
    digest: str
    tag: str | None = None
    platforms: tuple[OciPlatform, ...] = ()
    provenance: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        _validate_registry(self.registry)
        _validate_repository(self.repository)
        _validate_digest(self.digest)
        if self.tag is not None:
            _validate_tag(self.tag)
        platforms = tuple(self.platforms)
        for platform in platforms:
            if not isinstance(platform, OciPlatform):
                raise OciImageReferenceError("platforms must contain OciPlatform values")
        provenance = _provenance_mapping(self.provenance or {})
        object.__setattr__(self, "platforms", tuple(sorted(platforms)))
        object.__setattr__(self, "provenance", provenance)

    @property
    def execution_reference(self) -> str:
        """Return the immutable image reference used for execution."""

        return f"{self.registry}/{self.repository}@{self.digest}"

    @property
    def human_reference(self) -> str:
        """Return a display reference that may include a mutable human tag."""

        if self.tag is None:
            return self.execution_reference
        return f"{self.registry}/{self.repository}:{self.tag}@{self.digest}"

    def require_platform(self, requested: OciPlatform) -> None:
        """Fail before runtime effects if constraints exclude the requested platform."""

        if not isinstance(requested, OciPlatform):
            raise OciImageReferenceError("requested platform must be OciPlatform")
        if self.platforms and requested not in self.platforms:
            available = ", ".join(platform.label for platform in self.platforms)
            raise PlatformMismatch(
                f"image does not support {requested.label}; available platforms: {available}"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "registry": self.registry,
            "repository": self.repository,
            "digest": self.digest,
            "tag": self.tag,
            "platforms": [platform.descriptor() for platform in self.platforms],
            "provenance": dict(sorted((self.provenance or {}).items())),
        }


class OciImageReferenceCodec:
    """Strict codec for the current OCI image reference descriptor language."""

    def encode(self, image: OciImageReference) -> dict[str, object]:
        if not isinstance(image, OciImageReference):
            raise OciImageReferenceError("encode requires OciImageReference")
        return image.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> OciImageReference:
        mapping = _oci_mapping(descriptor, "oci_image_reference")
        _require_exact_keys(mapping, _OCI_DESCRIPTOR_KEYS, "OCI image reference")
        return OciImageReference(
            registry=_oci_text(mapping, "registry"),
            repository=_oci_text(mapping, "repository"),
            digest=_oci_text(mapping, "digest"),
            tag=_optional_text(mapping, "tag"),
            platforms=tuple(_platform(value) for value in _list(mapping, "platforms")),
            provenance=_string_mapping(mapping["provenance"], "provenance"),
        )


def _validate_identity_part(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise ProductIdentityError(f"{field} must be a string")
    if len(value) > _MAX_PART_LENGTH:
        raise ProductIdentityError(f"{field} is too long")
    if not _IDENTITY_PART.fullmatch(value):
        raise ProductIdentityError(
            f"{field} must contain lowercase ASCII letters, digits, dots, or hyphens"
        )


def _validate_platform_part(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise OciImageReferenceError(f"{field} must be a string")
    if len(value) > _MAX_PART_LENGTH:
        raise OciImageReferenceError(f"{field} is too long")
    if not _IDENTITY_PART.fullmatch(value):
        raise OciImageReferenceError(
            f"{field} must contain lowercase ASCII letters, digits, dots, or hyphens"
        )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProductIdentityError(f"{field} must be a mapping")
    return value


def _oci_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise OciImageReferenceError(f"{field} must be a mapping")
    return value


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str):
        raise ProductIdentityError(f"{key} must be a string")
    return value


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping[key]
    if type(value) is not int:
        raise ProductIdentityError(f"{key} must be an integer")
    return value


def _oci_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str):
        raise OciImageReferenceError(f"{key} must be a string")
    return value


def _require_exact_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    label: str,
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
    raise OciImageReferenceError(f"invalid {label} descriptor; " + "; ".join(details))


def _validate_registry(value: str) -> None:
    if not isinstance(value, str):
        raise OciImageReferenceError("registry must be a string")
    if "@" in value:
        raise OciImageReferenceError("registry must not contain credentials")
    if not _REGISTRY.fullmatch(value):
        raise OciImageReferenceError("registry must be a bounded OCI registry host")


def _validate_repository(value: str) -> None:
    if not isinstance(value, str):
        raise OciImageReferenceError("repository must be a string")
    if len(value) > _MAX_REPOSITORY_LENGTH:
        raise OciImageReferenceError("repository is too long")
    parts = value.split("/")
    if not parts or any(not _REPOSITORY_PART.fullmatch(part) for part in parts):
        raise OciImageReferenceError("repository must be a bounded lowercase OCI path")


def _validate_digest(value: str) -> None:
    if not isinstance(value, str):
        raise OciImageReferenceError("digest must be a string")
    if not _SHA256_DIGEST.fullmatch(value):
        raise OciImageReferenceError("digest must be an immutable sha256 digest")


def _validate_tag(value: str) -> None:
    if not isinstance(value, str):
        raise OciImageReferenceError("tag must be a string")
    if not _TAG.fullmatch(value):
        raise OciImageReferenceError("tag must be a bounded OCI tag")


def _optional_text(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise OciImageReferenceError(f"{key} must be a string or null")
    return value


def _list(mapping: Mapping[str, object], key: str) -> tuple[object, ...]:
    value = mapping[key]
    if not isinstance(value, list):
        raise OciImageReferenceError(f"{key} must be a list")
    return tuple(value)


def _string_mapping(value: object, field: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise OciImageReferenceError(f"{field} must be a mapping")
    if len(value) > _MAX_PROVENANCE_FIELDS:
        raise OciImageReferenceError(f"{field} contains too many fields")
    return _provenance_mapping(value)


def _provenance_mapping(value: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _IDENTITY_PART.fullmatch(key):
            raise OciImageReferenceError("provenance keys must be bounded identity parts")
        if any(secret in key for secret in _SECRET_FIELD_HINTS):
            raise OciImageReferenceError("provenance must not contain secret fields")
        if not isinstance(item, str):
            raise OciImageReferenceError("provenance values must be strings")
        if len(item) > _MAX_PROVENANCE_VALUE_LENGTH:
            raise OciImageReferenceError("provenance value is too long")
        result[key] = item
    return dict(sorted(result.items()))


def _platform(value: object) -> OciPlatform:
    mapping = _oci_mapping(value, "platform")
    _require_exact_keys(mapping, _PLATFORM_DESCRIPTOR_KEYS, "platform")
    return OciPlatform(
        os=_oci_text(mapping, "os"),
        architecture=_oci_text(mapping, "architecture"),
        variant=_optional_text(mapping, "variant"),
    )
