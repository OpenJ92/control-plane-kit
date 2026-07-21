"""Pure external product identity language."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket, RequirementSocket
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.configuration import ConfigurationArtifact
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    environment_binding_from_descriptor,
)
from control_plane_kit_core.lifecycle import (
    DataResourceSpec,
    ResourceLifecycle,
    ResourceOwnership,
    ResourcePersistence,
)
from control_plane_kit_core.secrets import (
    SecretDelivery,
    secret_delivery_from_descriptor,
    secret_delivery_sort_key,
)
from control_plane_kit_core.types import Protocol, SocketBinding
from control_plane_kit_core.verification import VerificationContract, expected_protocols


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
_PRODUCT_CONTRACT_DESCRIPTOR_KEYS = frozenset(
    {
        "sockets",
        "public_environment",
        "configuration_artifacts",
        "secret_deliveries",
        "capabilities",
        "verification",
        "lifecycle",
    }
)
_CONTAINER_PRODUCT_DESCRIPTOR_KEYS = frozenset(
    {"kind", "identity", "image", "runtime_contract", "display_name", "description"}
)
_PRODUCT_DOCUMENT_KEYS = frozenset({"schema", "product"})
_PRODUCT_DOCUMENT_SCHEMA = "control-plane-kit.product"
_PRODUCT_DOCUMENT_MEDIA_TYPE = "application/vnd.cpk.product+json"
_PRODUCT_DOCUMENT_FILENAME = "product.cpk.json"
_MAX_PRODUCT_DOCUMENT_BYTES = 262_144
_SOCKETS_DESCRIPTOR_KEYS = frozenset({"requirements", "providers"})
_REQUIREMENT_DESCRIPTOR_KEYS = frozenset(
    {"protocol", "env_bindings", "required", "binding"}
)
_PROVIDER_DESCRIPTOR_KEYS = frozenset({"protocol"})
_LIFECYCLE_DESCRIPTOR_KEYS = frozenset({"ownership", "compute", "data"})
_DATA_RESOURCE_DESCRIPTOR_KEYS = frozenset({"resource_id", "persistence"})
_SECRET_FIELD_HINTS = ("secret", "token", "password", "credential", "key")
_MAX_DISPLAY_TEXT_LENGTH = 128
_MAX_DESCRIPTION_TEXT_LENGTH = 1024
_FORBIDDEN_PRODUCT_TEXT = (
    "`",
    "$(",
    "&&",
    "||",
    ";",
    "/var/run/",
    "docker.sock",
    "/proc/",
    "/sys/",
)


class ProductIdentityError(ValueError):
    """Raised when an external product identity is not in the closed language."""


class DuplicateProductIdentity(ProductIdentityError):
    """Raised when a product identity appears more than once in one catalogue."""


class OciImageReferenceError(ValueError):
    """Raised when an OCI image reference is not in the closed language."""


class PlatformMismatch(OciImageReferenceError):
    """Raised when an admitted image cannot run on the requested platform."""


class ProductRuntimeContractError(ValueError):
    """Raised when runtime contract material is not descriptor-safe."""


class ContainerServerProductError(ValueError):
    """Raised when an external container product is not descriptor-safe."""


class ProductDescriptorError(ValueError):
    """Raised when product.cpk.json content is not the current product language."""


class ProductCatalogError(ValueError):
    """Raised when an immutable product catalogue cannot be constructed."""


class ProductCatalogConflict(ProductCatalogError):
    """Raised when one identity is associated with conflicting product content."""


class UnknownProductIdentity(ProductCatalogError):
    """Raised when a catalogue lookup names an absent product identity."""


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
    provenance: Mapping[str, str] | tuple[tuple[str, str], ...] | None = None

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
            "provenance": dict(self.provenance or ()),
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


@dataclass(frozen=True)
class ProductRuntimeContract:
    """Descriptor-safe runtime contract material for an external product."""

    sockets: BlockSockets = field(default_factory=BlockSockets)
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    secret_deliveries: tuple[SecretDelivery, ...] = ()
    capabilities: tuple[CapabilityName, ...] = ()
    verification: VerificationContract = field(default_factory=VerificationContract)
    lifecycle: ResourceLifecycle = field(default_factory=ResourceLifecycle.owned_ephemeral)

    def __post_init__(self) -> None:
        if not isinstance(self.sockets, BlockSockets):
            raise ProductRuntimeContractError("product sockets must be BlockSockets")
        _validate_socket_names(self.sockets)
        public_environment = tuple(self.public_environment)
        if not all(
            isinstance(value, PublicStaticEnvironmentBinding)
            for value in public_environment
        ):
            raise ProductRuntimeContractError(
                "public environment must contain PublicStaticEnvironmentBinding values"
            )
        _require_unique(
            tuple(value.name for value in public_environment),
            "public environment names",
        )
        configuration_artifacts = tuple(sorted(self.configuration_artifacts))
        if not all(
            isinstance(value, ConfigurationArtifact)
            for value in configuration_artifacts
        ):
            raise ProductRuntimeContractError(
                "configuration artifacts must contain ConfigurationArtifact values"
            )
        _require_unique(
            tuple(value.artifact_id for value in configuration_artifacts),
            "configuration artifact identities",
        )
        _require_unique(
            tuple(value.target_path for value in configuration_artifacts),
            "configuration artifact target paths",
        )
        secret_deliveries = tuple(
            sorted(self.secret_deliveries, key=secret_delivery_sort_key)
        )
        if not all(isinstance(value, SecretDelivery) for value in secret_deliveries):
            raise ProductRuntimeContractError(
                "secret deliveries must contain SecretDelivery values"
            )
        capabilities = tuple(sorted(self.capabilities, key=lambda value: value.value))
        if not all(isinstance(value, CapabilityName) for value in capabilities):
            raise ProductRuntimeContractError("capabilities must be CapabilityName values")
        _require_unique(tuple(value.value for value in capabilities), "capabilities")
        if not isinstance(self.verification, VerificationContract):
            raise ProductRuntimeContractError(
                "verification must be VerificationContract"
            )
        _validate_verification(self.sockets, self.verification)
        if not isinstance(self.lifecycle, ResourceLifecycle):
            raise ProductRuntimeContractError("lifecycle must be ResourceLifecycle")
        _validate_lifecycle_distinctions(self.lifecycle, configuration_artifacts)
        object.__setattr__(self, "public_environment", tuple(sorted(public_environment)))
        object.__setattr__(self, "configuration_artifacts", configuration_artifacts)
        object.__setattr__(self, "secret_deliveries", secret_deliveries)
        object.__setattr__(self, "capabilities", capabilities)

    def descriptor(self) -> dict[str, object]:
        return {
            "sockets": _sockets_descriptor(self.sockets),
            "public_environment": [
                value.descriptor() for value in self.public_environment
            ],
            "configuration_artifacts": [
                value.descriptor() for value in self.configuration_artifacts
            ],
            "secret_deliveries": [value.descriptor() for value in self.secret_deliveries],
            "capabilities": [value.value for value in self.capabilities],
            "verification": self.verification.descriptor(),
            "lifecycle": self.lifecycle.descriptor(),
        }


class ProductRuntimeContractCodec:
    """Strict codec for descriptor-safe runtime contract material."""

    def encode(self, contract: ProductRuntimeContract) -> dict[str, object]:
        if not isinstance(contract, ProductRuntimeContract):
            raise ProductRuntimeContractError("encode requires ProductRuntimeContract")
        return contract.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> ProductRuntimeContract:
        try:
            mapping = _product_mapping(descriptor, "product runtime contract")
            _require_product_keys(
                mapping,
                _PRODUCT_CONTRACT_DESCRIPTOR_KEYS,
                "product runtime contract",
            )
            return ProductRuntimeContract(
                sockets=_sockets_from_descriptor(mapping["sockets"]),
                public_environment=tuple(
                    _public_environment_binding(value)
                    for value in _product_list(mapping, "public_environment")
                ),
                configuration_artifacts=tuple(
                    ConfigurationArtifact.from_descriptor(
                        _product_mapping(value, "configuration artifact")
                    )
                    for value in _product_list(mapping, "configuration_artifacts")
                ),
                secret_deliveries=tuple(
                    secret_delivery_from_descriptor(
                        _product_mapping(value, "secret delivery")
                    )
                    for value in _product_list(mapping, "secret_deliveries")
                ),
                capabilities=tuple(
                    CapabilityName(_product_text_value(value, "capability"))
                    for value in _product_list(mapping, "capabilities")
                ),
                verification=VerificationContract.from_descriptor(mapping["verification"]),
                lifecycle=_lifecycle_from_descriptor(mapping["lifecycle"]),
            )
        except ProductRuntimeContractError:
            raise
        except Exception as error:
            raise ProductRuntimeContractError(
                "product runtime contract descriptor is malformed"
            ) from error


@dataclass(frozen=True)
class ContainerServerProduct:
    """Pure external OCI server product value."""

    identity: ProductIdentity
    image: OciImageReference
    runtime_contract: ProductRuntimeContract
    display_name: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ProductIdentity):
            raise ContainerServerProductError("product identity must be ProductIdentity")
        if not isinstance(self.image, OciImageReference):
            raise ContainerServerProductError("product image must be OciImageReference")
        if not isinstance(self.runtime_contract, ProductRuntimeContract):
            raise ContainerServerProductError(
                "runtime contract must be ProductRuntimeContract"
            )
        if self.display_name is not None:
            _validate_product_text(
                self.display_name,
                "display_name",
                max_length=_MAX_DISPLAY_TEXT_LENGTH,
            )
        if self.description is not None:
            _validate_product_text(
                self.description,
                "description",
                max_length=_MAX_DESCRIPTION_TEXT_LENGTH,
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "container-server",
            "identity": ProductIdentityCodec().encode(self.identity),
            "image": OciImageReferenceCodec().encode(self.image),
            "runtime_contract": ProductRuntimeContractCodec().encode(
                self.runtime_contract
            ),
            "display_name": self.display_name,
            "description": self.description,
        }


class ContainerServerProductCodec:
    """Strict codec for the first external product form."""

    variant = "container-server"

    def encode(self, product: ContainerServerProduct) -> dict[str, object]:
        if not isinstance(product, ContainerServerProduct):
            raise ContainerServerProductError("encode requires ContainerServerProduct")
        return product.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> ContainerServerProduct:
        try:
            mapping = _container_mapping(descriptor, "container server product")
            _require_container_keys(
                mapping,
                _CONTAINER_PRODUCT_DESCRIPTOR_KEYS,
                "container server product",
            )
            kind = _container_text(mapping, "kind")
            if kind != self.variant:
                raise ContainerServerProductError(
                    f"unsupported product descriptor variant {kind!r}"
                )
            return ContainerServerProduct(
                identity=ProductIdentityCodec().decode(
                    _container_mapping(mapping["identity"], "identity")
                ),
                image=OciImageReferenceCodec().decode(
                    _container_mapping(mapping["image"], "image")
                ),
                runtime_contract=ProductRuntimeContractCodec().decode(
                    _container_mapping(mapping["runtime_contract"], "runtime contract")
                ),
                display_name=_container_optional_text(mapping, "display_name"),
                description=_container_optional_text(mapping, "description"),
            )
        except ContainerServerProductError:
            raise
        except Exception as error:
            raise ContainerServerProductError(
                "container server product descriptor is malformed"
            ) from error


@dataclass(frozen=True)
class ProductDescriptorDocument:
    """Canonical product.cpk.json bytes and the product value they encode."""

    product: ContainerServerProduct
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.product, ContainerServerProduct):
            raise ProductDescriptorError("product document requires ContainerServerProduct")
        if not isinstance(self.content, bytes):
            raise ProductDescriptorError("product document content must be bytes")

    @property
    def filename(self) -> str:
        return _PRODUCT_DOCUMENT_FILENAME

    @property
    def media_type(self) -> str:
        return _PRODUCT_DOCUMENT_MEDIA_TYPE

    @property
    def size_bytes(self) -> int:
        return len(self.content)

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class ProductDescriptorCodec:
    """Strict codec for the language-neutral product.cpk.json boundary."""

    def __init__(self, *, max_bytes: int = _MAX_PRODUCT_DOCUMENT_BYTES) -> None:
        if type(max_bytes) is not int or max_bytes < 1:
            raise ProductDescriptorError("max_bytes must be a positive integer")
        self.max_bytes = max_bytes

    def encode_document(self, product: ContainerServerProduct) -> ProductDescriptorDocument:
        if not isinstance(product, ContainerServerProduct):
            raise ProductDescriptorError("encode requires ContainerServerProduct")
        content = self._canonical_bytes(self._document_mapping(product))
        self._validate_size(content)
        return ProductDescriptorDocument(product=product, content=content)

    def decode_document(
        self,
        document: bytes | str | Mapping[str, object],
    ) -> ProductDescriptorDocument:
        if isinstance(document, bytes):
            self._validate_size(document)
            mapping = self._json_mapping(document)
            canonical = self._canonical_bytes(mapping)
            if document != canonical:
                raise ProductDescriptorError("product descriptor JSON is not canonical")
        elif isinstance(document, str):
            try:
                content = document.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ProductDescriptorError("product descriptor text is not UTF-8") from error
            self._validate_size(content)
            mapping = self._json_mapping(content)
            canonical = self._canonical_bytes(mapping)
            if content != canonical:
                raise ProductDescriptorError("product descriptor JSON is not canonical")
        elif isinstance(document, Mapping):
            mapping = document
            canonical = self._canonical_bytes(mapping)
            self._validate_size(canonical)
        else:
            raise ProductDescriptorError(
                "product descriptor must be bytes, text, or mapping"
            )

        product = self._product_from_mapping(mapping)
        expected = self._canonical_bytes(self._document_mapping(product))
        if canonical != expected:
            raise ProductDescriptorError("product descriptor JSON is not canonical")
        return ProductDescriptorDocument(product=product, content=canonical)

    def _document_mapping(
        self,
        product: ContainerServerProduct,
    ) -> dict[str, object]:
        return {
            "schema": _PRODUCT_DOCUMENT_SCHEMA,
            "product": ContainerServerProductCodec().encode(product),
        }

    def _product_from_mapping(
        self,
        document: Mapping[str, object],
    ) -> ContainerServerProduct:
        mapping = _descriptor_mapping(document, "product descriptor")
        _require_descriptor_keys(mapping, _PRODUCT_DOCUMENT_KEYS, "product descriptor")
        schema = mapping["schema"]
        if schema != _PRODUCT_DOCUMENT_SCHEMA:
            raise ProductDescriptorError("product descriptor schema is unsupported")
        try:
            return ContainerServerProductCodec().decode(
                _container_mapping(mapping["product"], "product")
            )
        except Exception as error:
            raise ProductDescriptorError("product descriptor product is malformed") from error

    def _json_mapping(self, content: bytes) -> Mapping[str, object]:
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProductDescriptorError("product descriptor is malformed JSON") from error
        return _descriptor_mapping(value, "product descriptor")

    def _canonical_bytes(self, mapping: Mapping[str, object]) -> bytes:
        try:
            text = json.dumps(
                mapping,
                ensure_ascii=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ProductDescriptorError("product descriptor is not JSON shaped") from error
        return text.encode("utf-8")

    def _validate_size(self, content: bytes) -> None:
        if not content or len(content) > self.max_bytes:
            raise ProductDescriptorError("product descriptor is empty or exceeds its bound")


@dataclass(frozen=True)
class ProductCatalog:
    """Immutable catalogue assembled from admitted external product documents."""

    products: tuple[ProductDescriptorDocument, ...] = ()

    def __post_init__(self) -> None:
        documents = tuple(self.products)
        for document in documents:
            if not isinstance(document, ProductDescriptorDocument):
                raise ProductCatalogError(
                    "catalogue products must contain ProductDescriptorDocument values"
                )
        by_identity: dict[ProductIdentity, ProductDescriptorDocument] = {}
        for document in documents:
            identity = document.product.identity
            existing = by_identity.get(identity)
            if existing is None:
                by_identity[identity] = document
            elif existing.content_digest != document.content_digest:
                raise ProductCatalogConflict(
                    f"conflicting product descriptor for {identity.key}"
                )
        ordered = tuple(by_identity[identity] for identity in sorted(by_identity))
        object.__setattr__(self, "products", ordered)

    @classmethod
    def empty(cls) -> "ProductCatalog":
        return cls(())

    @classmethod
    def from_documents(
        cls,
        documents: Iterable[ProductDescriptorDocument],
    ) -> "ProductCatalog":
        return cls(tuple(documents))

    def lookup(self, identity: ProductIdentity) -> ProductDescriptorDocument:
        if not isinstance(identity, ProductIdentity):
            raise ProductCatalogError("lookup requires ProductIdentity")
        for document in self.products:
            if document.product.identity == identity:
                return document
        raise UnknownProductIdentity(f"unknown product identity {identity.key}")

    def add(self, document: ProductDescriptorDocument) -> "ProductCatalog":
        if not isinstance(document, ProductDescriptorDocument):
            raise ProductCatalogError("add requires ProductDescriptorDocument")
        try:
            existing = self.lookup(document.product.identity)
        except UnknownProductIdentity:
            return ProductCatalog((*self.products, document))
        if existing.content_digest != document.content_digest:
            raise ProductCatalogConflict(
                f"conflicting product descriptor for {document.product.identity.key}"
            )
        return self

    def merge(self, other: "ProductCatalog") -> "ProductCatalog":
        if not isinstance(other, ProductCatalog):
            raise ProductCatalogError("merge requires ProductCatalog")
        catalog = self
        for document in other.products:
            catalog = catalog.add(document)
        return catalog

    def descriptor(self) -> dict[str, object]:
        return {
            "products": [
                json.loads(document.content.decode("utf-8"))
                for document in self.products
            ]
        }

    @property
    def content(self) -> bytes:
        return json.dumps(
            self.descriptor(),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


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


def _provenance_mapping(
    value: Mapping[str, object] | tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    result: dict[str, str] = {}
    items = value.items() if isinstance(value, Mapping) else value
    for key, item in items:
        if not isinstance(key, str) or not _IDENTITY_PART.fullmatch(key):
            raise OciImageReferenceError("provenance keys must be bounded identity parts")
        if any(secret in key for secret in _SECRET_FIELD_HINTS):
            raise OciImageReferenceError("provenance must not contain secret fields")
        if not isinstance(item, str):
            raise OciImageReferenceError("provenance values must be strings")
        if len(item) > _MAX_PROVENANCE_VALUE_LENGTH:
            raise OciImageReferenceError("provenance value is too long")
        result[key] = item
    return tuple(sorted(result.items()))


def _platform(value: object) -> OciPlatform:
    mapping = _oci_mapping(value, "platform")
    _require_exact_keys(mapping, _PLATFORM_DESCRIPTOR_KEYS, "platform")
    return OciPlatform(
        os=_oci_text(mapping, "os"),
        architecture=_oci_text(mapping, "architecture"),
        variant=_optional_text(mapping, "variant"),
    )


def _validate_socket_names(sockets: BlockSockets) -> None:
    _require_unique(sockets.requirement_names(), "requirement socket names")
    _require_unique(sockets.provider_names(), "provider socket names")


def _validate_verification(
    sockets: BlockSockets,
    verification: VerificationContract,
) -> None:
    providers = {provider.name: provider for provider in sockets.providers}
    for check in verification.checks:
        try:
            provider = providers[check.provider_socket]
        except KeyError as error:
            raise ProductRuntimeContractError(
                f"verification check {check.check_id!r} references unknown provider socket"
            ) from error
        if provider.protocol not in expected_protocols(check):
            raise ProductRuntimeContractError(
                f"verification check {check.check_id!r} is incompatible with provider protocol"
            )


def _validate_lifecycle_distinctions(
    lifecycle: ResourceLifecycle,
    configuration_artifacts: tuple[ConfigurationArtifact, ...],
) -> None:
    retained_data_ids = {value.resource_id for value in lifecycle.data}
    artifact_ids = {value.artifact_id for value in configuration_artifacts}
    overlap = sorted(retained_data_ids & artifact_ids)
    if overlap:
        raise ProductRuntimeContractError(
            "retained data resources must be distinct from configuration artifacts: "
            + ", ".join(overlap)
        )


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ProductRuntimeContractError(f"{label} must be unique")


def _sockets_descriptor(sockets: BlockSockets) -> dict[str, object]:
    return {
        "requirements": {
            socket.name: {
                "protocol": socket.protocol.descriptor(),
                "env_bindings": list(socket.env_bindings),
                "required": socket.required,
                "binding": socket.binding.value,
            }
            for socket in sorted(sockets.requirements, key=lambda value: value.name)
        },
        "providers": {
            socket.name: {"protocol": socket.protocol.descriptor()}
            for socket in sorted(sockets.providers, key=lambda value: value.name)
        },
    }


def _sockets_from_descriptor(value: object) -> BlockSockets:
    descriptor = _product_mapping(value, "sockets")
    _require_product_keys(descriptor, _SOCKETS_DESCRIPTOR_KEYS, "sockets")
    requirements: list[RequirementSocket] = []
    for name, item in sorted(
        _product_mapping(descriptor["requirements"], "requirements").items()
    ):
        if not isinstance(name, str):
            raise ProductRuntimeContractError("requirement socket names must be strings")
        requirement = _product_mapping(item, "requirement")
        _require_product_keys(
            requirement,
            _REQUIREMENT_DESCRIPTOR_KEYS,
            "requirement",
        )
        requirements.append(
            RequirementSocket(
                name=name,
                protocol=_protocol(requirement, "requirement protocol"),
                env_bindings=tuple(
                    _product_text_value(binding, "requirement environment binding")
                    for binding in _product_list(requirement, "env_bindings")
                ),
                required=_product_bool(requirement, "required"),
                binding=SocketBinding(_product_text(requirement, "binding")),
            )
        )
    providers: list[ProviderSocket] = []
    for name, item in sorted(
        _product_mapping(descriptor["providers"], "providers").items()
    ):
        if not isinstance(name, str):
            raise ProductRuntimeContractError("provider socket names must be strings")
        provider = _product_mapping(item, "provider")
        _require_product_keys(provider, _PROVIDER_DESCRIPTOR_KEYS, "provider")
        providers.append(
            ProviderSocket(
                name=name,
                protocol=_protocol(provider, "provider protocol"),
            )
        )
    return BlockSockets(requirements=tuple(requirements), providers=tuple(providers))


def _protocol(value: object, label: str) -> Protocol:
    descriptor = _product_mapping(_product_mapping(value, label)["protocol"], label)
    return Protocol.from_descriptor(descriptor)


def _public_environment_binding(value: object) -> PublicStaticEnvironmentBinding:
    binding = environment_binding_from_descriptor(
        _product_mapping(value, "public environment binding")
    )
    if not isinstance(binding, PublicStaticEnvironmentBinding):
        raise ProductRuntimeContractError(
            "product public environment cannot contain derived bindings"
        )
    return binding


def _lifecycle_from_descriptor(value: object) -> ResourceLifecycle:
    descriptor = _product_mapping(value, "lifecycle")
    _require_product_keys(descriptor, _LIFECYCLE_DESCRIPTOR_KEYS, "lifecycle")
    data = tuple(
        _data_resource_from_descriptor(item)
        for item in _product_list(descriptor, "data")
    )
    return ResourceLifecycle(
        ownership=ResourceOwnership(_product_text(descriptor, "ownership")),
        compute=ResourcePersistence(_product_text(descriptor, "compute")),
        data=data,
    )


def _data_resource_from_descriptor(value: object) -> DataResourceSpec:
    descriptor = _product_mapping(value, "data resource")
    _require_product_keys(descriptor, _DATA_RESOURCE_DESCRIPTOR_KEYS, "data resource")
    return DataResourceSpec(
        resource_id=_product_text(descriptor, "resource_id"),
        persistence=ResourcePersistence(_product_text(descriptor, "persistence")),
    )


def _product_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProductRuntimeContractError(f"{field} must be a mapping")
    return value


def _product_list(mapping: Mapping[str, object], key: str) -> tuple[object, ...]:
    value = mapping[key]
    if not isinstance(value, list):
        raise ProductRuntimeContractError(f"{key} must be a list")
    return tuple(value)


def _product_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping[key]
    return _product_text_value(value, key)


def _product_text_value(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ProductRuntimeContractError(f"{field} must be a string")
    return value


def _product_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = mapping[key]
    if type(value) is not bool:
        raise ProductRuntimeContractError(f"{key} must be a boolean")
    return value


def _require_product_keys(
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
    raise ProductRuntimeContractError(
        f"invalid {label} descriptor; " + "; ".join(details)
    )


def _validate_product_text(value: str, field: str, *, max_length: int) -> None:
    if not isinstance(value, str):
        raise ContainerServerProductError(f"{field} must be a string")
    if not value.strip() or len(value) > max_length or "\x00" in value:
        raise ContainerServerProductError(f"{field} is empty or exceeds its bound")
    lowered = value.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_PRODUCT_TEXT):
        raise ContainerServerProductError(f"{field} contains executable or host path text")


def _container_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContainerServerProductError(f"{field} must be a mapping")
    return value


def _container_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str):
        raise ContainerServerProductError(f"{key} must be a string")
    return value


def _container_optional_text(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContainerServerProductError(f"{key} must be a string or null")
    return value


def _require_container_keys(
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
    raise ContainerServerProductError(
        f"invalid {label} descriptor; " + "; ".join(details)
    )


def _descriptor_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProductDescriptorError(f"{field} must be a mapping")
    return value


def _require_descriptor_keys(
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
    raise ProductDescriptorError(f"invalid {label}; " + "; ".join(details))
