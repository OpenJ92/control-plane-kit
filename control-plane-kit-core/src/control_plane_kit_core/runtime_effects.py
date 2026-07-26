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
from urllib.parse import urlsplit

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
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryCodec,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityDeliverySecretReference,
    RuntimeAuthorityDeliverySecretReferenceCodec,
    RuntimeAuthorityReference,
    RuntimeAuthorityReferenceCodec,
    RuntimeEffectContractError,
)
from control_plane_kit_core.secrets import CredentialReference, SecretResolutionError
from control_plane_kit_core.types import Protocol, RuntimeKind


_MAX_TEXT = 512
_MAX_EVIDENCE_FIELDS = 32
_MAX_EVIDENCE_DEPTH = 4
_MAX_EVIDENCE_ITEMS = 32
_REGISTRY = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?$")
_REPOSITORY_PART = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_MAX_REPOSITORY_LENGTH = 255
_GATEWAY_TARGET_PART = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_GATEWAY_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)


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


@dataclass(frozen=True, order=True)
class GatewayTargetId:
    """Stable target identity derived from node id x provider socket."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise RuntimeEffectContractError("gateway target id must be text")
        _reject_gateway_secret_text(self.value, "gateway target id")
        parts = self.value.split(".")
        if len(parts) != 2 or any(
            not _GATEWAY_TARGET_PART.fullmatch(part) for part in parts
        ):
            raise RuntimeEffectContractError(
                "gateway target id must be node_id.provider_socket"
            )


@dataclass(frozen=True)
class GatewayHttpTarget:
    """Secret-free runtime-private HTTP target for gateway probe dispatch."""

    target_id: GatewayTargetId
    node_id: str
    provider_socket: str
    url: str
    source_edges: tuple[str, ...] = ()
    protocol: Protocol = Protocol.HTTP

    def __post_init__(self) -> None:
        _validate_gateway_identity(self.node_id, "gateway target node id")
        _validate_gateway_identity(
            self.provider_socket,
            "gateway target provider socket",
        )
        _validate_gateway_target_id(self.target_id, self.node_id, self.provider_socket)
        if self.protocol != Protocol.HTTP:
            raise RuntimeEffectContractError("gateway HTTP target protocol is invalid")
        _validate_gateway_url(self.url)
        source_edges = _gateway_source_edges(self.source_edges)
        object.__setattr__(self, "source_edges", source_edges)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "http",
            "target_id": self.target_id.value,
            "node_id": self.node_id,
            "provider_socket": self.provider_socket,
            "protocol": self.protocol.descriptor(),
            "url": self.url,
            "source_edges": list(self.source_edges),
        }


@dataclass(frozen=True)
class GatewayPostgresTarget:
    """Secret-free runtime-private Postgres target for semantic probes."""

    target_id: GatewayTargetId
    node_id: str
    provider_socket: str
    host: str
    port: int
    source_edges: tuple[str, ...] = ()
    protocol: Protocol = Protocol.POSTGRES

    def __post_init__(self) -> None:
        _validate_gateway_identity(self.node_id, "gateway target node id")
        _validate_gateway_identity(
            self.provider_socket,
            "gateway target provider socket",
        )
        _validate_gateway_target_id(self.target_id, self.node_id, self.provider_socket)
        if self.protocol != Protocol.POSTGRES:
            raise RuntimeEffectContractError(
                "gateway Postgres target protocol is invalid"
            )
        _validate_gateway_host(self.host)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise RuntimeEffectContractError("gateway target port is invalid")
        source_edges = _gateway_source_edges(self.source_edges)
        object.__setattr__(self, "source_edges", source_edges)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "postgres",
            "target_id": self.target_id.value,
            "node_id": self.node_id,
            "provider_socket": self.provider_socket,
            "protocol": self.protocol.descriptor(),
            "host": self.host,
            "port": self.port,
            "source_edges": list(self.source_edges),
        }


GatewayTarget = GatewayHttpTarget | GatewayPostgresTarget


@dataclass(frozen=True)
class GatewayTargetMap:
    """Closed graph-derived target material for a local runtime-island gateway."""

    targets: tuple[GatewayTarget, ...] = ()

    def __post_init__(self) -> None:
        targets = tuple(sorted(self.targets, key=lambda value: value.target_id.value))
        if len(targets) > _MAX_EVIDENCE_ITEMS:
            raise RuntimeEffectContractError("gateway target map has too many targets")
        if not all(
            isinstance(value, (GatewayHttpTarget, GatewayPostgresTarget))
            for value in targets
        ):
            raise RuntimeEffectContractError("gateway target map contains unknown target")
        target_ids = tuple(value.target_id for value in targets)
        if len(set(target_ids)) != len(target_ids):
            raise RuntimeEffectContractError("gateway target ids must be unique")
        object.__setattr__(self, "targets", targets)

    def descriptor(self) -> dict[str, object]:
        return {"targets": [value.descriptor() for value in self.targets]}


class GatewayTargetMapCodec:
    """Strict codec for local gateway target-map material."""

    def encode(self, target_map: GatewayTargetMap) -> dict[str, object]:
        if not isinstance(target_map, GatewayTargetMap):
            raise RuntimeEffectContractError("encode requires GatewayTargetMap")
        return target_map.descriptor()

    def decode(self, descriptor: Mapping[str, object]) -> GatewayTargetMap:
        _require_keys(descriptor, _GATEWAY_TARGET_MAP_KEYS, "gateway target map")
        targets = descriptor["targets"]
        if not isinstance(targets, list):
            raise RuntimeEffectContractError("gateway target map targets must be a list")
        return GatewayTargetMap(
            tuple(_gateway_target_from_descriptor(item) for item in targets)
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
    authority_ref: RuntimeAuthorityReference | None = None
    authority_deliveries: tuple[RuntimeAuthorityAccessDelivery, ...] = ()
    products: tuple[RuntimeProductMaterial, ...] = ()

    def __post_init__(self) -> None:
        _required_text(self.effect_id, "effect_id")
        if not isinstance(self.kind, RuntimeEffectKind):
            raise RuntimeEffectContractError("runtime effect kind must be closed")
        if not isinstance(self.runtime_kind, RuntimeKind):
            raise RuntimeEffectContractError("runtime kind must be RuntimeKind")
        if self.authority_ref is not None and not isinstance(
            self.authority_ref,
            RuntimeAuthorityReference,
        ):
            raise RuntimeEffectContractError(
                "runtime authority reference must be RuntimeAuthorityReference"
            )
        authority_deliveries = tuple(sorted(self.authority_deliveries))
        if not all(
            isinstance(value, RuntimeAuthorityAccessDelivery)
            for value in authority_deliveries
        ):
            raise RuntimeEffectContractError(
                "runtime authority deliveries must be RuntimeAuthorityAccessDelivery"
            )
        authority_refs = tuple(
            value.authority_ref for value in authority_deliveries
        )
        if len(set(authority_refs)) != len(authority_refs):
            raise RuntimeEffectContractError(
                "runtime authority deliveries must be unique by authority reference"
            )
        if self.authority_ref is None and authority_deliveries:
            raise RuntimeEffectContractError(
                "runtime authority deliveries require runtime authority reference"
            )
        if self.authority_ref is not None:
            for delivery in authority_deliveries:
                if delivery.authority_ref != self.authority_ref:
                    raise RuntimeEffectContractError(
                        "runtime authority delivery reference must match request authority"
                    )
        object.__setattr__(self, "authority_deliveries", authority_deliveries)
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
            "authority_ref": None
            if self.authority_ref is None
            else self.authority_ref.descriptor(),
            "authority_deliveries": [
                value.descriptor() for value in self.authority_deliveries
            ],
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
_GATEWAY_TARGET_MAP_KEYS = frozenset({"targets"})
_GATEWAY_HTTP_TARGET_KEYS = frozenset(
    {
        "kind",
        "target_id",
        "node_id",
        "provider_socket",
        "protocol",
        "url",
        "source_edges",
    }
)
_GATEWAY_POSTGRES_TARGET_KEYS = frozenset(
    {
        "kind",
        "target_id",
        "node_id",
        "provider_socket",
        "protocol",
        "host",
        "port",
        "source_edges",
    }
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


def _gateway_target_from_descriptor(value: object) -> GatewayTarget:
    if not isinstance(value, Mapping):
        raise RuntimeEffectContractError("gateway target descriptor must be a mapping")
    kind = value.get("kind")
    if kind == "http":
        _require_gateway_keys(value, _GATEWAY_HTTP_TARGET_KEYS, "gateway HTTP target")
        protocol = _gateway_protocol(value, Protocol.HTTP)
        return GatewayHttpTarget(
            target_id=GatewayTargetId(_text(value, "target_id")),
            node_id=_text(value, "node_id"),
            provider_socket=_text(value, "provider_socket"),
            protocol=protocol,
            url=_text(value, "url"),
            source_edges=_source_edges_from_descriptor(value.get("source_edges")),
        )
    if kind == "postgres":
        _require_gateway_keys(
            value,
            _GATEWAY_POSTGRES_TARGET_KEYS,
            "gateway Postgres target",
        )
        protocol = _gateway_protocol(value, Protocol.POSTGRES)
        port = value.get("port")
        if type(port) is not int:
            raise RuntimeEffectContractError("gateway target port is invalid")
        return GatewayPostgresTarget(
            target_id=GatewayTargetId(_text(value, "target_id")),
            node_id=_text(value, "node_id"),
            provider_socket=_text(value, "provider_socket"),
            protocol=protocol,
            host=_text(value, "host"),
            port=port,
            source_edges=_source_edges_from_descriptor(value.get("source_edges")),
        )
    raise RuntimeEffectContractError("gateway target kind is unsupported")


def _require_gateway_keys(
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
    raise RuntimeEffectContractError(f"invalid {label}; " + "; ".join(details))


def _gateway_protocol(
    value: Mapping[str, object],
    expected: Protocol,
) -> Protocol:
    protocol_descriptor = value.get("protocol")
    if not isinstance(protocol_descriptor, Mapping):
        raise RuntimeEffectContractError("gateway target protocol is malformed")
    try:
        protocol = Protocol.from_descriptor(protocol_descriptor)
    except ValueError as error:
        raise RuntimeEffectContractError("gateway target protocol is malformed") from error
    if protocol != expected:
        raise RuntimeEffectContractError("gateway target protocol is unsupported")
    return protocol


def _source_edges_from_descriptor(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeEffectContractError("gateway target source_edges must be a list")
    return tuple(value)


def _validate_gateway_target_id(
    target_id: GatewayTargetId,
    node_id: str,
    provider_socket: str,
) -> None:
    if not isinstance(target_id, GatewayTargetId):
        raise RuntimeEffectContractError("gateway target id must be GatewayTargetId")
    if target_id.value != f"{node_id}.{provider_socket}":
        raise RuntimeEffectContractError(
            "gateway target id must match node id and provider socket"
        )


def _validate_gateway_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not _GATEWAY_TARGET_PART.fullmatch(value):
        raise RuntimeEffectContractError(f"{label} is invalid")
    _reject_gateway_secret_text(value, label)


def _gateway_source_edges(value: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > _MAX_EVIDENCE_ITEMS:
        raise RuntimeEffectContractError("gateway target source edges are malformed")
    edges: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > _MAX_TEXT:
            raise RuntimeEffectContractError("gateway target source edge is invalid")
        _reject_gateway_secret_text(item, "gateway target source edge")
        edges.append(item)
    if len(set(edges)) != len(edges):
        raise RuntimeEffectContractError("gateway target source edges must be unique")
    return tuple(sorted(edges))


def _validate_gateway_url(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise RuntimeEffectContractError("gateway HTTP target url must be text")
    _reject_gateway_secret_text(value, "gateway HTTP target url")
    if len(value) > _MAX_TEXT:
        raise RuntimeEffectContractError("gateway HTTP target url is too long")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeEffectContractError("gateway HTTP target url is invalid")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeEffectContractError(
            "gateway HTTP target url must not contain credentials"
        )
    if parsed.query or parsed.fragment:
        raise RuntimeEffectContractError(
            "gateway HTTP target url must not contain query or fragment"
        )


def _validate_gateway_host(value: str) -> None:
    if not isinstance(value, str):
        raise RuntimeEffectContractError("gateway target host must be text")
    _reject_gateway_secret_text(value, "gateway target host")
    if len(value) > _MAX_TEXT or not _GATEWAY_HOST.fullmatch(value):
        raise RuntimeEffectContractError("gateway target host is invalid")


def _reject_gateway_secret_text(value: str, name: str) -> None:
    lowered = value.lower()
    secret_markers = (
        "password=",
        "token=",
        "secret=",
        "credential=",
        "secret://",
        "begin-private-key",
        "private key",
    )
    if any(marker in lowered for marker in secret_markers):
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
