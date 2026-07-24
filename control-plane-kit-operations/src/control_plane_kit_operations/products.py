"""Durable product registration language for operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any, Mapping
from urllib.parse import urlsplit

from control_plane_kit_core.products import (
    ProductDescriptorDigest,
    ProductDescriptorDocument,
    ProductIdentity,
    ProductIdentityCodec,
    ProductReference,
    ProductReferenceCodec,
)
from control_plane_kit_core.runtime_effects import ImagePullAuthority


class ProductRegistrationError(ValueError):
    """Raised when product registration data is malformed."""


class ProductRegistrationConflict(ProductRegistrationError):
    """Raised when an import requires an explicit replacement decision."""


class ProductRegistrationNotFound(ProductRegistrationError):
    """Raised when a registered product cannot be found."""


class RegisteredProductStatus(StrEnum):
    """Closed durable status for workspace product registration."""

    ACTIVE = "active"
    REVOKED = "revoked"


class RegisteredImagePullAuthorityStatus(StrEnum):
    """Closed durable status for workspace image-pull authority."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class InlineDescriptorSource:
    """Evidence that descriptor bytes were supplied directly by the operator."""

    def descriptor(self) -> dict[str, object]:
        return {"kind": "inline_descriptor"}


@dataclass(frozen=True)
class RemoteDescriptorSource:
    """Evidence that a descriptor was acquired from a remote descriptor URL."""

    url: str
    expected_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_source_url(self.url)
        if self.expected_sha256 is not None:
            ProductDescriptorDigest(self.expected_sha256)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "remote_descriptor_url",
            "url": self.url,
            "expected_sha256": self.expected_sha256,
        }


@dataclass(frozen=True)
class CatalogueDescriptorSource:
    """Evidence that a descriptor was selected from a product catalogue URL."""

    url: str
    product_identity: ProductIdentity
    expected_catalogue_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_source_url(self.url)
        if not isinstance(self.product_identity, ProductIdentity):
            raise ProductRegistrationError("catalogue source requires ProductIdentity")
        if self.expected_catalogue_sha256 is not None:
            ProductDescriptorDigest(self.expected_catalogue_sha256)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "catalogue_url",
            "url": self.url,
            "product_identity": ProductIdentityCodec().encode(self.product_identity),
            "expected_catalogue_sha256": self.expected_catalogue_sha256,
        }


DescriptorSourceEvidence = (
    InlineDescriptorSource | RemoteDescriptorSource | CatalogueDescriptorSource
)


class DescriptorSourceCodec:
    """Strict codec for product descriptor acquisition evidence."""

    def encode(self, source: DescriptorSourceEvidence) -> dict[str, object]:
        if not isinstance(
            source,
            (InlineDescriptorSource, RemoteDescriptorSource, CatalogueDescriptorSource),
        ):
            raise ProductRegistrationError("unsupported descriptor source")
        return source.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> DescriptorSourceEvidence:
        mapping = _mapping(descriptor, "descriptor source")
        kind = mapping.get("kind")
        if kind == "inline_descriptor":
            _require_keys(mapping, frozenset({"kind"}), "inline descriptor source")
            return InlineDescriptorSource()
        if kind == "remote_descriptor_url":
            _require_keys(
                mapping,
                frozenset({"kind", "url", "expected_sha256"}),
                "remote descriptor source",
            )
            return RemoteDescriptorSource(
                url=_text(mapping, "url"),
                expected_sha256=_optional_text(mapping, "expected_sha256"),
            )
        if kind == "catalogue_url":
            _require_keys(
                mapping,
                frozenset(
                    {
                        "kind",
                        "url",
                        "product_identity",
                        "expected_catalogue_sha256",
                    }
                ),
                "catalogue descriptor source",
            )
            return CatalogueDescriptorSource(
                url=_text(mapping, "url"),
                product_identity=ProductIdentityCodec().decode(
                    _mapping(mapping["product_identity"], "product_identity")
                ),
                expected_catalogue_sha256=_optional_text(
                    mapping,
                    "expected_catalogue_sha256",
                ),
            )
        raise ProductRegistrationError("unsupported descriptor source")


@dataclass(frozen=True)
class RegisteredProduct:
    """A descriptor admitted as durable workspace product truth."""

    registration_id: str
    workspace_id: str
    reference: ProductReference
    descriptor_document: ProductDescriptorDocument
    source: DescriptorSourceEvidence
    imported_by: str
    imported_at: str
    status: RegisteredProductStatus = RegisteredProductStatus.ACTIVE
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.registration_id, "registration_id")
        _validate_identifier(self.workspace_id, "workspace_id")
        _validate_identifier(self.imported_by, "imported_by")
        _validate_identifier(self.imported_at, "imported_at")
        if not isinstance(self.reference, ProductReference):
            raise ProductRegistrationError("registered product requires ProductReference")
        if not isinstance(self.descriptor_document, ProductDescriptorDocument):
            raise ProductRegistrationError(
                "registered product requires ProductDescriptorDocument"
            )
        if self.reference != ProductReference.from_document(self.descriptor_document):
            raise ProductRegistrationError(
                "registered product reference must match descriptor document"
            )
        if not isinstance(
            self.source,
            (InlineDescriptorSource, RemoteDescriptorSource, CatalogueDescriptorSource),
        ):
            raise ProductRegistrationError("registered product source is unsupported")
        if not isinstance(self.status, RegisteredProductStatus):
            raise ProductRegistrationError("registered product status is unsupported")
        if not isinstance(self.metadata, Mapping):
            raise ProductRegistrationError("registered product metadata must be mapping")

    @classmethod
    def from_document(
        cls,
        *,
        workspace_id: str,
        descriptor_document: ProductDescriptorDocument,
        source: DescriptorSourceEvidence,
        imported_by: str,
        imported_at: str,
    ) -> "RegisteredProduct":
        reference = ProductReference.from_document(descriptor_document)
        return cls(
            registration_id=registration_id_for(workspace_id, reference),
            workspace_id=workspace_id,
            reference=reference,
            descriptor_document=descriptor_document,
            source=source,
            imported_by=imported_by,
            imported_at=imported_at,
        )


@dataclass(frozen=True)
class RegisteredImagePullAuthority:
    """Workspace-scoped authority reference for pulling OCI images."""

    authority_id: str
    workspace_id: str
    authority: ImagePullAuthority
    admitted_by: str
    admitted_at: str
    status: RegisteredImagePullAuthorityStatus = (
        RegisteredImagePullAuthorityStatus.ACTIVE
    )
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.authority_id, "authority_id")
        _validate_identifier(self.workspace_id, "workspace_id")
        _validate_identifier(self.admitted_by, "admitted_by")
        _validate_identifier(self.admitted_at, "admitted_at")
        if not isinstance(self.authority, ImagePullAuthority):
            raise ProductRegistrationError(
                "registered image pull authority requires ImagePullAuthority"
            )
        if not isinstance(self.status, RegisteredImagePullAuthorityStatus):
            raise ProductRegistrationError(
                "registered image pull authority status is unsupported"
            )
        if not isinstance(self.metadata, Mapping):
            raise ProductRegistrationError(
                "registered image pull authority metadata must be mapping"
            )

    @classmethod
    def from_authority(
        cls,
        *,
        workspace_id: str,
        authority: ImagePullAuthority,
        admitted_by: str,
        admitted_at: str,
    ) -> "RegisteredImagePullAuthority":
        return cls(
            authority_id=image_pull_authority_id_for(workspace_id, authority),
            workspace_id=workspace_id,
            authority=authority,
            admitted_by=admitted_by,
            admitted_at=admitted_at,
        )


@dataclass(frozen=True)
class ImportProductDescriptorCommand:
    """Application command to admit one product descriptor into a workspace."""

    workspace_id: str
    descriptor_document: ProductDescriptorDocument
    source: DescriptorSourceEvidence
    imported_by: str
    imported_at: str

    def __post_init__(self) -> None:
        RegisteredProduct.from_document(
            workspace_id=self.workspace_id,
            descriptor_document=self.descriptor_document,
            source=self.source,
            imported_by=self.imported_by,
            imported_at=self.imported_at,
        )


@dataclass(frozen=True)
class RegisterImagePullAuthorityCommand:
    """Application command to admit OCI pull authority for one workspace."""

    workspace_id: str
    authority: ImagePullAuthority
    admitted_by: str
    admitted_at: str

    def __post_init__(self) -> None:
        RegisteredImagePullAuthority.from_authority(
            workspace_id=self.workspace_id,
            authority=self.authority,
            admitted_by=self.admitted_by,
            admitted_at=self.admitted_at,
        )


class ProductRegistrationService:
    """Application service owning product-registration transaction boundaries."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def import_descriptor(
        self,
        command: ImportProductDescriptorCommand,
    ) -> RegisteredProduct:
        if not isinstance(command, ImportProductDescriptorCommand):
            raise ProductRegistrationError(
                "import_descriptor requires ImportProductDescriptorCommand"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.registered_products.register(
                workspace_id=command.workspace_id,
                descriptor_document=command.descriptor_document,
                source=command.source,
                imported_by=command.imported_by,
                imported_at=command.imported_at,
            )
            unit_of_work.commit()
            return registered

    def revoke(self, *, workspace_id: str, reference: ProductReference) -> RegisteredProduct:
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.registered_products.revoke(
                workspace_id,
                reference,
            )
            unit_of_work.commit()
            return registered


class ImagePullAuthorityRegistrationService:
    """Application service owning image pull authority transaction boundaries."""

    def __init__(self, unit_of_work_factory: Any) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register(
        self,
        command: RegisterImagePullAuthorityCommand,
    ) -> RegisteredImagePullAuthority:
        if not isinstance(command, RegisterImagePullAuthorityCommand):
            raise ProductRegistrationError(
                "register requires RegisterImagePullAuthorityCommand"
            )
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.image_pull_authorities.register(
                workspace_id=command.workspace_id,
                authority=command.authority,
                admitted_by=command.admitted_by,
                admitted_at=command.admitted_at,
            )
            unit_of_work.commit()
            return registered

    def revoke(
        self,
        *,
        workspace_id: str,
        authority_id: str,
    ) -> RegisteredImagePullAuthority:
        with self._unit_of_work_factory() as unit_of_work:
            registered = unit_of_work.stores.image_pull_authorities.revoke(
                workspace_id,
                authority_id,
            )
            unit_of_work.commit()
            return registered


def registration_id_for(workspace_id: str, reference: ProductReference) -> str:
    """Return deterministic registration identity for workspace plus digest."""

    _validate_identifier(workspace_id, "workspace_id")
    if not isinstance(reference, ProductReference):
        raise ProductRegistrationError("registration id requires ProductReference")
    digest = sha256(
        f"{workspace_id}\0{reference.descriptor_sha256.value}".encode("utf-8")
    ).hexdigest()
    return f"rprod_{digest}"


def image_pull_authority_id_for(
    workspace_id: str,
    authority: ImagePullAuthority,
) -> str:
    """Return deterministic identity for workspace plus pull-authority reference."""

    _validate_identifier(workspace_id, "workspace_id")
    if not isinstance(authority, ImagePullAuthority):
        raise ProductRegistrationError(
            "image pull authority id requires ImagePullAuthority"
        )
    digest = sha256(
        (
            f"{workspace_id}\0"
            f"{authority.registry}\0"
            f"{authority.repository or ''}\0"
            f"{authority.credential_reference.reference_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"ipull_{digest}"


def _validate_source_url(url: str) -> None:
    _validate_identifier(url, "source url")
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ProductRegistrationError("descriptor source URL must use https")
    if not parsed.netloc:
        raise ProductRegistrationError("descriptor source URL requires host")
    if parsed.username is not None or parsed.password is not None:
        raise ProductRegistrationError("descriptor source URL must not contain credentials")
    if parsed.query:
        raise ProductRegistrationError("descriptor source URL must not contain query")
    if parsed.fragment:
        raise ProductRegistrationError("descriptor source URL must not contain fragment")


def _validate_identifier(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise ProductRegistrationError(f"{field} must be a string")
    if not value or len(value) > 512:
        raise ProductRegistrationError(f"{field} must be nonempty and bounded")
    if any(ord(character) < 32 for character in value):
        raise ProductRegistrationError(f"{field} must not contain control characters")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProductRegistrationError(f"{field} must be a mapping")
    return value


def _require_keys(
    mapping: Mapping[str, object],
    expected: frozenset[str],
    field: str,
) -> None:
    keys = frozenset(mapping)
    if keys != expected:
        extra = sorted(keys - expected)
        missing = sorted(expected - keys)
        details: list[str] = []
        if extra:
            details.append(f"unknown keys: {', '.join(extra)}")
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        raise ProductRegistrationError(f"invalid {field}; " + "; ".join(details))


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ProductRegistrationError(f"{key} must be a string")
    return value


def _optional_text(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProductRegistrationError(f"{key} must be a string")
    return value
