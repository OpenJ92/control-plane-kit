"""Pure runtime-effect request and result language.

This module describes the value boundary between durable operations and concrete
runtime interpreters. It never imports Docker, stores, cpk-server process code,
or interpreter packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Mapping

from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
    environment_binding_from_descriptor,
)
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.planning import ActivityId, ActivityOperation
from control_plane_kit_core.planning.codec import activity_operation_descriptor
from control_plane_kit_core.probe_intents import RuntimeEndpointObservation
from control_plane_kit_core.products import (
    ContainerServerProduct,
    ContainerServerProductCodec,
    OciImageReference,
    ProductReference,
    ProductReferenceCodec,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityReference,
    RuntimeAuthorityReferenceCodec,
    RuntimeEffectContractError,
)
from control_plane_kit_core.secrets import CredentialReference, SecretResolutionError
from control_plane_kit_core.types import RuntimeKind


_MAX_TEXT = 512
_MAX_EVIDENCE_FIELDS = 32
_MAX_EVIDENCE_DEPTH = 4
_MAX_EVIDENCE_ITEMS = 32
_REGISTRY = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?$")
_REPOSITORY_PART = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_MAX_REPOSITORY_LENGTH = 255


class RuntimeEffectKind(StrEnum):
    """Closed runtime-effect intents interpreters can execute."""

    REALIZE_ACTIVITY = "realize-activity"


@dataclass(frozen=True, order=True)
class ImagePullAuthority:
    """Secret-free authority reference for pulling an OCI image."""

    registry: str
    repository: str | None
    credential_reference: CredentialReference

    def __post_init__(self) -> None:
        _validate_registry_scope(self.registry)
        if self.repository is not None:
            _validate_repository_scope(self.repository)
        reference = self.credential_reference
        if isinstance(reference, str):
            try:
                reference = CredentialReference(reference)
            except SecretResolutionError as error:
                raise RuntimeEffectContractError(
                    "image pull authority credential_reference is malformed"
                ) from error
        if not isinstance(reference, CredentialReference):
            raise RuntimeEffectContractError(
                "image pull authority credential_reference must be CredentialReference"
            )
        object.__setattr__(self, "credential_reference", reference)

    def permits(self, image: OciImageReference) -> bool:
        """Return whether this authority scope covers an immutable image reference."""

        if not isinstance(image, OciImageReference):
            raise RuntimeEffectContractError("image pull authority requires OCI image")
        if image.registry != self.registry:
            return False
        if self.repository is None:
            return True
        return image.repository == self.repository or image.repository.startswith(
            f"{self.repository}/"
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "registry": self.registry,
            "repository": self.repository,
            "credential_reference": self.credential_reference.reference_id,
        }


class ImagePullAuthorityCodec:
    """Strict codec for secret-free image pull authority references."""

    def encode(self, authority: ImagePullAuthority) -> dict[str, object]:
        if not isinstance(authority, ImagePullAuthority):
            raise RuntimeEffectContractError("encode requires ImagePullAuthority")
        return authority.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> ImagePullAuthority:
        mapping = _authority_mapping(descriptor, "image pull authority")
        _require_authority_keys(mapping, _IMAGE_PULL_AUTHORITY_KEYS)
        credential = mapping.get("credential_reference")
        if not isinstance(credential, str):
            raise RuntimeEffectContractError("credential_reference must be text")
        return ImagePullAuthority(
            registry=_authority_text(mapping, "registry"),
            repository=_authority_optional_text(mapping, "repository"),
            credential_reference=CredentialReference(credential),
        )


@dataclass(frozen=True)
class RuntimeEffectSource:
    """Pinned durable source identities for one runtime effect."""

    workspace_id: str
    request_id: str
    run_id: str
    plan_id: str
    base_graph_id: str
    desired_graph_id: str
    intent_event_id: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.workspace_id, "workspace_id"),
            (self.request_id, "request_id"),
            (self.run_id, "run_id"),
            (self.plan_id, "plan_id"),
            (self.base_graph_id, "base_graph_id"),
            (self.desired_graph_id, "desired_graph_id"),
            (self.intent_event_id, "intent_event_id"),
        ):
            _required_text(value, name)

    def descriptor(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "base_graph_id": self.base_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "intent_event_id": self.intent_event_id,
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "RuntimeEffectSource":
        _require_keys(value, _SOURCE_KEYS, "runtime effect source")
        return cls(
            workspace_id=_text(value, "workspace_id"),
            request_id=_text(value, "request_id"),
            run_id=_text(value, "run_id"),
            plan_id=_text(value, "plan_id"),
            base_graph_id=_text(value, "base_graph_id"),
            desired_graph_id=_text(value, "desired_graph_id"),
            intent_event_id=_text(value, "intent_event_id"),
        )


@dataclass(frozen=True)
class RuntimeProductMaterial:
    """Pure product material selected from registered descriptor truth."""

    node_id: str
    runtime_id: str
    reference: ProductReference
    product: ContainerServerProduct
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    socket_environment: tuple[SocketDerivedEnvironmentBinding, ...] = ()
    pull_authority: ImagePullAuthority | None = None

    def __post_init__(self) -> None:
        _required_text(self.node_id, "node_id")
        _required_text(self.runtime_id, "runtime_id")
        if not isinstance(self.reference, ProductReference):
            raise RuntimeEffectContractError("product reference must be ProductReference")
        if not isinstance(self.product, ContainerServerProduct):
            raise RuntimeEffectContractError("product must be ContainerServerProduct")
        if self.reference.identity != self.product.identity:
            raise RuntimeEffectContractError("product material identity mismatch")
        public_environment = tuple(sorted(self.public_environment))
        if not all(
            isinstance(value, PublicStaticEnvironmentBinding)
            for value in public_environment
        ):
            raise RuntimeEffectContractError(
                "runtime product public environment must use public-static bindings"
            )
        public_names = tuple(value.name for value in public_environment)
        if len(set(public_names)) != len(public_names):
            raise RuntimeEffectContractError(
                "runtime product public environment names must be unique"
            )
        socket_environment = tuple(sorted(self.socket_environment))
        if not all(
            isinstance(value, SocketDerivedEnvironmentBinding)
            for value in socket_environment
        ):
            raise RuntimeEffectContractError(
                "runtime product socket environment must use socket-derived bindings"
            )
        names = tuple(value.name for value in socket_environment)
        if len(set(names)) != len(names):
            raise RuntimeEffectContractError(
                "runtime product socket environment names must be unique"
            )
        if self.pull_authority is not None and not isinstance(
            self.pull_authority,
            ImagePullAuthority,
        ):
            raise RuntimeEffectContractError(
                "runtime product pull_authority must be ImagePullAuthority"
            )
        object.__setattr__(self, "public_environment", public_environment)
        object.__setattr__(self, "socket_environment", socket_environment)

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "runtime_id": self.runtime_id,
            "reference": ProductReferenceCodec().encode(self.reference),
            "product": ContainerServerProductCodec().encode(self.product),
            "public_environment": [
                value.descriptor() for value in self.public_environment
            ],
            "socket_environment": [
                value.descriptor() for value in self.socket_environment
            ],
            "pull_authority": None
            if self.pull_authority is None
            else ImagePullAuthorityCodec().encode(self.pull_authority),
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "RuntimeProductMaterial":
        _require_keys(value, _PRODUCT_MATERIAL_KEYS, "runtime product material")
        return cls(
            node_id=_text(value, "node_id"),
            runtime_id=_text(value, "runtime_id"),
            reference=ProductReferenceCodec().decode(
                _mapping(value, "reference", "runtime product material")
            ),
            product=ContainerServerProductCodec().decode(
                _mapping(value, "product", "runtime product material")
            ),
            public_environment=_public_environment(
                value.get("public_environment"),
                "runtime product material",
            ),
            socket_environment=_socket_environment(
                value.get("socket_environment"),
                "runtime product material",
            ),
            pull_authority=_pull_authority(value.get("pull_authority")),
        )


@dataclass(frozen=True)
class RuntimeEffectRequest:
    """Pure request operations hands to a runtime interpreter."""

    effect_id: str
    kind: RuntimeEffectKind
    runtime_kind: RuntimeKind
    source: RuntimeEffectSource
    activity_id: ActivityId
    operation: ActivityOperation
    products: tuple[RuntimeProductMaterial, ...] = ()

    def __post_init__(self) -> None:
        _required_text(self.effect_id, "effect_id")
        if not isinstance(self.kind, RuntimeEffectKind):
            raise RuntimeEffectContractError("runtime effect kind must be closed")
        if not isinstance(self.runtime_kind, RuntimeKind):
            raise RuntimeEffectContractError("runtime kind must be RuntimeKind")
        if not isinstance(self.source, RuntimeEffectSource):
            raise RuntimeEffectContractError("runtime effect source is malformed")
        if not isinstance(self.activity_id, ActivityId):
            raise RuntimeEffectContractError("activity_id must be ActivityId")
        try:
            activity_operation_descriptor(self.operation)
        except Exception as error:
            raise RuntimeEffectContractError("activity operation is malformed") from error
        products = tuple(sorted(self.products, key=lambda value: value.node_id))
        if not all(isinstance(value, RuntimeProductMaterial) for value in products):
            raise RuntimeEffectContractError(
                "runtime products must contain RuntimeProductMaterial"
            )
        node_ids = tuple(value.node_id for value in products)
        if len(set(node_ids)) != len(node_ids):
            raise RuntimeEffectContractError("runtime product node ids must be unique")
        object.__setattr__(self, "products", products)

    def descriptor(self) -> dict[str, object]:
        return {
            "effect_id": self.effect_id,
            "kind": self.kind.value,
            "runtime_kind": self.runtime_kind.value,
            "source": self.source.descriptor(),
            "activity_id": self.activity_id.value,
            "operation": activity_operation_descriptor(self.operation),
            "products": [value.descriptor() for value in self.products],
        }


@dataclass(frozen=True)
class RuntimeEffectFailure:
    """Bounded interpreter failure evidence with no secret values."""

    code: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_text(self.code, "failure code")
        _bounded_text(self.message, "failure message")
        details = _evidence_mapping(self.details, "failure details")
        object.__setattr__(self, "details", details)

    def descriptor(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class RuntimeEffectResult:
    """Pure result a runtime interpreter returns to operations."""

    effect_id: str
    kind: EffectResultKind
    evidence: Mapping[str, object] = field(default_factory=dict)
    failure: RuntimeEffectFailure | None = None
    observations: tuple[RuntimeEndpointObservation, ...] = ()

    @classmethod
    def succeeded(
        cls,
        effect_id: str,
        *,
        evidence: Mapping[str, object] | None = None,
        observations: tuple[RuntimeEndpointObservation, ...] = (),
    ) -> "RuntimeEffectResult":
        return cls(
            effect_id,
            EffectResultKind.SUCCEEDED,
            {} if evidence is None else evidence,
            observations=observations,
        )

    @classmethod
    def failed(
        cls,
        effect_id: str,
        failure: RuntimeEffectFailure,
    ) -> "RuntimeEffectResult":
        return cls(effect_id, EffectResultKind.FAILED, failure=failure)

    @classmethod
    def unsupported(
        cls,
        effect_id: str,
        failure: RuntimeEffectFailure,
    ) -> "RuntimeEffectResult":
        return cls(effect_id, EffectResultKind.UNSUPPORTED, failure=failure)

    @classmethod
    def uncertain(
        cls,
        effect_id: str,
        failure: RuntimeEffectFailure,
    ) -> "RuntimeEffectResult":
        return cls(effect_id, EffectResultKind.UNCERTAIN, failure=failure)

    def __post_init__(self) -> None:
        _required_text(self.effect_id, "effect_id")
        if not isinstance(self.kind, EffectResultKind):
            raise RuntimeEffectContractError("runtime effect result kind is malformed")
        if self.kind not in {
            EffectResultKind.SUCCEEDED,
            EffectResultKind.FAILED,
            EffectResultKind.UNSUPPORTED,
            EffectResultKind.UNCERTAIN,
        }:
            raise RuntimeEffectContractError("runtime effect result kind is not executable")
        evidence = _evidence_mapping(self.evidence, "runtime effect evidence")
        object.__setattr__(self, "evidence", evidence)
        if self.failure is not None and not isinstance(self.failure, RuntimeEffectFailure):
            raise RuntimeEffectContractError("runtime effect failure is malformed")
        observations = tuple(self.observations)
        if not all(isinstance(value, RuntimeEndpointObservation) for value in observations):
            raise RuntimeEffectContractError(
                "runtime effect observations must be RuntimeEndpointObservation"
            )
        object.__setattr__(self, "observations", observations)
        if self.kind is EffectResultKind.SUCCEEDED and self.failure is not None:
            raise RuntimeEffectContractError("successful runtime effect cannot fail")
        if self.kind is not EffectResultKind.SUCCEEDED and self.failure is None:
            raise RuntimeEffectContractError("non-success runtime effect requires failure")

    def descriptor(self) -> dict[str, object]:
        return {
            "effect_id": self.effect_id,
            "kind": self.kind.value,
            "evidence": dict(self.evidence),
            "failure": None if self.failure is None else self.failure.descriptor(),
            "observations": [
                value.descriptor()
                for value in sorted(
                    self.observations,
                    key=lambda item: (
                        item.subject_id,
                        item.socket_name,
                        item.graph_id,
                        item.context.value,
                    ),
                )
            ],
        }


_SOURCE_KEYS = frozenset(
    {
        "workspace_id",
        "request_id",
        "run_id",
        "plan_id",
        "base_graph_id",
        "desired_graph_id",
        "intent_event_id",
    }
)
_PRODUCT_MATERIAL_KEYS = frozenset(
    {
        "node_id",
        "runtime_id",
        "reference",
        "product",
        "public_environment",
        "socket_environment",
        "pull_authority",
    }
)
_IMAGE_PULL_AUTHORITY_KEYS = frozenset(
    {"registry", "repository", "credential_reference"}
)


def _require_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
    label: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RuntimeEffectContractError(f"{label} descriptor is malformed")


def _mapping(
    value: Mapping[str, object],
    key: str,
    label: str,
) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise RuntimeEffectContractError(f"{label} {key} must be a mapping")
    return item


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise RuntimeEffectContractError(f"{key} must be text")
    return item


def _socket_environment(
    value: object,
    label: str,
) -> tuple[SocketDerivedEnvironmentBinding, ...]:
    if not isinstance(value, list):
        raise RuntimeEffectContractError(f"{label} socket_environment must be a list")
    if len(value) > _MAX_EVIDENCE_ITEMS:
        raise RuntimeEffectContractError(
            f"{label} socket_environment has too many bindings"
        )
    bindings: list[SocketDerivedEnvironmentBinding] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RuntimeEffectContractError(
                f"{label} socket_environment binding is malformed"
            )
        try:
            binding = environment_binding_from_descriptor(item)
        except ValueError as error:
            raise RuntimeEffectContractError(
                f"{label} socket_environment binding is malformed"
            ) from error
        if not isinstance(binding, SocketDerivedEnvironmentBinding):
            raise RuntimeEffectContractError(
                f"{label} socket_environment must be socket-derived"
            )
        bindings.append(binding)
    return tuple(bindings)


def _public_environment(
    value: object,
    label: str,
) -> tuple[PublicStaticEnvironmentBinding, ...]:
    if not isinstance(value, list):
        raise RuntimeEffectContractError(f"{label} public_environment must be a list")
    if len(value) > _MAX_EVIDENCE_ITEMS:
        raise RuntimeEffectContractError(
            f"{label} public_environment has too many bindings"
        )
    bindings: list[PublicStaticEnvironmentBinding] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RuntimeEffectContractError(
                f"{label} public_environment binding is malformed"
            )
        try:
            binding = environment_binding_from_descriptor(item)
        except ValueError as error:
            raise RuntimeEffectContractError(
                f"{label} public_environment binding is malformed"
            ) from error
        if not isinstance(binding, PublicStaticEnvironmentBinding):
            raise RuntimeEffectContractError(
                f"{label} public_environment must be public-static"
            )
        bindings.append(binding)
    return tuple(bindings)


def _pull_authority(value: object) -> ImagePullAuthority | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RuntimeEffectContractError(
            "runtime product material pull_authority must be a mapping or null"
        )
    return ImagePullAuthorityCodec().decode(value)


def _required_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise RuntimeEffectContractError(f"{name} must be bounded nonempty text")
    _reject_secret_text(value, name)


def _bounded_text(value: str, name: str) -> None:
    if not isinstance(value, str) or "\x00" in value or len(value) > _MAX_TEXT:
        raise RuntimeEffectContractError(f"{name} must be bounded text")
    _reject_secret_text(value, name)


def _evidence_mapping(value: Mapping[str, object], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeEffectContractError(f"{label} must be a mapping")
    if len(value) > _MAX_EVIDENCE_FIELDS:
        raise RuntimeEffectContractError(f"{label} has too many fields")
    result = {
        _evidence_key(key, label): _evidence_value(item, label, depth=0)
        for key, item in value.items()
    }
    return dict(sorted(result.items()))


def _evidence_key(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise RuntimeEffectContractError(f"{label} keys must be bounded text")
    _reject_secret_text(value, label)
    return value


def _evidence_value(value: object, label: str, *, depth: int) -> object:
    if depth > _MAX_EVIDENCE_DEPTH:
        raise RuntimeEffectContractError(f"{label} is too deeply nested")
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str):
        _bounded_text(value, label)
        return value
    if isinstance(value, list):
        if len(value) > _MAX_EVIDENCE_ITEMS:
            raise RuntimeEffectContractError(f"{label} has too many items")
        return [_evidence_value(item, label, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        if len(value) > _MAX_EVIDENCE_FIELDS:
            raise RuntimeEffectContractError(f"{label} has too many fields")
        result = {
            _evidence_key(key, label): _evidence_value(item, label, depth=depth + 1)
            for key, item in value.items()
        }
        return dict(sorted(result.items()))
    raise RuntimeEffectContractError(
        f"{label} contains unsupported value {type(value).__name__}"
    )


def _reject_secret_text(value: str, name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in ("password=", "token=", "secret=")):
        raise RuntimeEffectContractError(f"{name} contains secret-shaped text")


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
        "invalid image pull authority descriptor; " + "; ".join(details)
    )


def _authority_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise RuntimeEffectContractError(f"{key} must be text")
    return value


def _authority_optional_text(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeEffectContractError(f"{key} must be text or null")
    return value


def _validate_registry_scope(value: str) -> None:
    if not isinstance(value, str):
        raise RuntimeEffectContractError("registry must be text")
    if "@" in value:
        raise RuntimeEffectContractError("registry must not contain credentials")
    if not _REGISTRY.fullmatch(value):
        raise RuntimeEffectContractError("registry must be a bounded OCI registry host")


def _validate_repository_scope(value: str) -> None:
    if not isinstance(value, str):
        raise RuntimeEffectContractError("repository must be text or null")
    if len(value) > _MAX_REPOSITORY_LENGTH:
        raise RuntimeEffectContractError("repository is too long")
    parts = value.split("/")
    if not parts or any(not _REPOSITORY_PART.fullmatch(part) for part in parts):
        raise RuntimeEffectContractError(
            "repository must be a bounded lowercase OCI path"
        )
