"""Pure external product identity language."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re


_IDENTITY_PART = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_MAX_PART_LENGTH = 96
_DESCRIPTOR_KEYS = frozenset({"namespace", "name", "contract_revision"})


class ProductIdentityError(ValueError):
    """Raised when an external product identity is not in the closed language."""


class DuplicateProductIdentity(ProductIdentityError):
    """Raised when a product identity appears more than once in one catalogue."""


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


def _validate_identity_part(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise ProductIdentityError(f"{field} must be a string")
    if len(value) > _MAX_PART_LENGTH:
        raise ProductIdentityError(f"{field} is too long")
    if not _IDENTITY_PART.fullmatch(value):
        raise ProductIdentityError(
            f"{field} must contain lowercase ASCII letters, digits, dots, or hyphens"
        )


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProductIdentityError(f"{field} must be a mapping")
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
