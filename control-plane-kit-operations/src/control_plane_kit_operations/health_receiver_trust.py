"""Configured public receiver facts and a trusted, effect-free decoder port."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from types import MappingProxyType
from typing import Protocol

from control_plane_kit_core._node_control_public_wire import reference_violation
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget, workload_node_control_audience
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec
from control_plane_kit_core.planning import PlanGraphSide
from control_plane_kit_core.products import ProductDescriptorCodec, ProductDescriptorDocument, ProductReference, ProductReferenceCodec


class HealthReceiverTrustError(ValueError):
    """A bounded refusal; arbitrary adapter/owner exceptions are not this contract."""


_UNAVAILABLE = "health receiver trust is unavailable"
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, RecursionError)
_PURPOSES = (DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
             DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)


def _require(value: bool) -> None:
    if value is not True:
        raise HealthReceiverTrustError(_UNAVAILABLE)


def _same(value: object, rebuilt: object) -> bool:
    if type(value) is not type(rebuilt):
        return False
    if is_dataclass(rebuilt):
        return all(_same(getattr(value, item.name), getattr(rebuilt, item.name)) for item in fields(rebuilt))
    if isinstance(rebuilt, tuple):
        return len(value) == len(rebuilt) and all(_same(left, right) for left, right in zip(value, rebuilt))
    if isinstance(rebuilt, Mapping):
        return value.keys() == rebuilt.keys() and all(_same(value[key], rebuilt[key]) for key in rebuilt)
    return value == rebuilt


def _reference(value: object, role: NodeControlGraphReferenceRole) -> None:
    _require(type(value) is NodeControlGraphReference and value.role is role and type(value.value) is str)
    _require(_same(value, NodeControlGraphReference(role, value.value)))


def _public_keys(keys: object) -> None:
    _require(type(keys) is tuple and 1 <= len(keys) <= 16)
    for key in keys:
        _require(type(key) is DelegationPublicKey and key.algorithm is DelegationKeyAlgorithm.ED25519)
        _require(type(key.key_id) is str and type(key.public_key_pem) is str
                 and type(key.fingerprint_sha256) is str)
        _require(_same(key, replace(key)))
    _require(len({key.key_id for key in keys}) == len(keys)
             and len({key.fingerprint_sha256 for key in keys}) == len(keys))


def _common(value: object, purpose: DelegationKeyPurpose) -> None:
    _require(value.purpose is purpose and type(value.issuer) is str and reference_violation(value.issuer) is None)
    _reference(value.runtime_id, NodeControlGraphReferenceRole.RUNTIME)
    _public_keys(value.public_keys)
    _require(type(value.audience) is str)


@dataclass(frozen=True, slots=True, repr=False)
class GatewayHealthReceiverTrust:
    workspace_id: NodeControlGraphReference
    gateway_node_id: NodeControlGraphReference
    runtime_id: NodeControlGraphReference
    purpose: DelegationKeyPurpose
    issuer: str
    audience: str
    public_keys: tuple[DelegationPublicKey, ...]

    def __post_init__(self) -> None:
        try:
            _require(type(self) is GatewayHealthReceiverTrust)
            _common(self, _PURPOSES[0])
            _reference(self.workspace_id, NodeControlGraphReferenceRole.WORKSPACE)
            _reference(self.gateway_node_id, NodeControlGraphReferenceRole.NODE)
            _require(self.audience == f"gateway:{self.workspace_id.value}:{self.gateway_node_id.value}"
                     and len(self.audience) <= 265)
            return
        except _INPUT_ERRORS:
            failure = HealthReceiverTrustError(_UNAVAILABLE)
        raise failure


@dataclass(frozen=True, slots=True, repr=False)
class WorkloadHealthReceiverTrust:
    target: NodeControlTarget
    runtime_id: NodeControlGraphReference
    declaration: WorkloadNodeControlSurfaceDeclaration
    purpose: DelegationKeyPurpose
    issuer: str
    audience: str
    public_keys: tuple[DelegationPublicKey, ...]

    def __post_init__(self) -> None:
        try:
            _require(type(self) is WorkloadHealthReceiverTrust and type(self.target) is NodeControlTarget)
            _common(self, _PURPOSES[1])
            for name, role in (("workspace_id", NodeControlGraphReferenceRole.WORKSPACE),
                    ("graph_revision", NodeControlGraphReferenceRole.GRAPH_REVISION),
                    ("node_id", NodeControlGraphReferenceRole.NODE),
                    ("provider_socket_name", NodeControlGraphReferenceRole.PROVIDER_SOCKET)):
                _reference(getattr(self.target, name), role)
            _require(_same(self.target, replace(self.target)))
            _require(type(self.declaration) is WorkloadNodeControlSurfaceDeclaration)
            codec = WorkloadNodeControlSurfaceDeclarationCodec()
            _require(_same(self.declaration, codec.decode(codec.encode(self.declaration))))
            _require(self.target.provider_socket_name == self.declaration.surface.provider_socket_name
                     and self.audience == workload_node_control_audience(self.target))
            return
        except _INPUT_ERRORS:
            failure = HealthReceiverTrustError(_UNAVAILABLE)
        raise failure


@dataclass(frozen=True, slots=True, repr=False)
class HealthReceiverSelection:
    """Original approved pins, canonical product provenance and selected bytes."""

    workspace_id: str
    authored_graph_id: str
    realized_projection_id: str
    graph_side: PlanGraphSide
    receiver_node_id: str
    runtime_id: str
    provider_socket_name: str
    product_reference: ProductReference
    descriptor_document: ProductDescriptorDocument
    artifact: ConfigurationArtifact

    def __post_init__(self) -> None:
        try:
            _require(type(self) is HealthReceiverSelection and type(self.graph_side) is PlanGraphSide)
            for name in ("workspace_id", "authored_graph_id", "realized_projection_id",
                         "receiver_node_id", "runtime_id", "provider_socket_name"):
                value = getattr(self, name)
                _require(type(value) is str and reference_violation(value) is None)
            _require(type(self.product_reference) is ProductReference)
            reference_codec = ProductReferenceCodec()
            _require(_same(self.product_reference, reference_codec.decode(reference_codec.encode(self.product_reference))))
            _require(type(self.descriptor_document) is ProductDescriptorDocument)
            document = ProductDescriptorCodec().decode_document(self.descriptor_document.content)
            _require(_same(document, self.descriptor_document)
                     and _same(self.product_reference, ProductReference.from_document(document)))
            _require(type(self.artifact) is ConfigurationArtifact and _same(self.artifact, replace(self.artifact)))
            return
        except _INPUT_ERRORS:
            failure = HealthReceiverTrustError(_UNAVAILABLE)
        raise failure


class HealthReceiverDecoder(Protocol):
    """Trusted deterministic interpretation of supplied bytes, with no I/O.

    Implementations report configured facts, not expected selection identities.
    Unsupported/malformed profiles raise HealthReceiverTrustError; unrelated
    programming errors propagate. Bindings must keep historical semantics stable.
    """

    def decode(self, selection: HealthReceiverSelection) -> GatewayHealthReceiverTrust | WorkloadHealthReceiverTrust: ...


@dataclass(frozen=True, slots=True, repr=False)
class HealthReceiverDecoderBinding:
    """One exact product/purpose, declared artifact slot and parser profile."""

    product_reference: ProductReference
    purpose: DelegationKeyPurpose
    configuration_profile: str
    artifact_id: str
    target_path: str
    media_type: ConfigurationMediaType
    file_mode: ConfigurationFileMode
    decoder: HealthReceiverDecoder

    def __post_init__(self) -> None:
        try:
            _require(type(self) is HealthReceiverDecoderBinding and type(self.purpose) is DelegationKeyPurpose
                     and self.purpose in _PURPOSES and type(self.product_reference) is ProductReference)
            codec = ProductReferenceCodec()
            _require(_same(self.product_reference, codec.decode(codec.encode(self.product_reference))))
            _require(type(self.configuration_profile) is str and reference_violation(self.configuration_profile) is None)
            _require(type(self.artifact_id) is str and type(self.target_path) is str
                     and type(self.media_type) is ConfigurationMediaType and type(self.file_mode) is ConfigurationFileMode)
            # Core owns slot spelling/path admissibility; this is not configuration parsing.
            ConfigurationArtifact(self.artifact_id, self.target_path, self.media_type, "{}", self.file_mode)
            _require(callable(self.decoder.decode))
            return
        except _INPUT_ERRORS:
            failure = HealthReceiverTrustError(_UNAVAILABLE)
        raise failure


@dataclass(frozen=True, slots=True, repr=False)
class HealthReceiverDecoders:
    """An immutable trusted composition table; an empty table fails closed."""

    bindings: tuple[HealthReceiverDecoderBinding, ...]
    _index: Mapping = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            _require(type(self) is HealthReceiverDecoders and type(self.bindings) is tuple)
            index = {}
            for binding in self.bindings:
                _require(type(binding) is HealthReceiverDecoderBinding)
                replace(binding)
                key = (binding.product_reference, binding.purpose)
                _require(key not in index)
                index[key] = binding
            object.__setattr__(self, "_index", MappingProxyType(index))
            return
        except _INPUT_ERRORS:
            failure = HealthReceiverTrustError(_UNAVAILABLE)
        raise failure

    def binding_for(self, reference: ProductReference, purpose: DelegationKeyPurpose) -> HealthReceiverDecoderBinding:
        binding = self._index.get((reference, purpose))
        _require(binding is not None)
        return binding


__all__ = ["GatewayHealthReceiverTrust", "WorkloadHealthReceiverTrust", "HealthReceiverSelection",
           "HealthReceiverDecoder", "HealthReceiverDecoderBinding", "HealthReceiverDecoders", "HealthReceiverTrustError"]
