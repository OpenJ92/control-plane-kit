"""Shared pure receiver configuration; SDK owns loading and runtime setup."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes, reference_violation
from control_plane_kit_core.configuration import (
    ConfigurationArtifact, ConfigurationFileMode, ConfigurationMediaType,
    validate_configuration_target_path,
)
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget,
    WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec,
)

MAX_WRAPPER_CONFIGURATION_BYTES = 65_536
WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT = "CPK_WRAPPER_CONFIGURATION_FILE"
_PROFILE = "workload-node-control-configuration.v1"
_ERROR = "wrapper configuration is invalid"
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, RecursionError)
_PURPOSES = (
    DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
    DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
    DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
)
_TARGET_ROLES = (
    ("workspace_id", NodeControlGraphReferenceRole.WORKSPACE),
    ("graph_revision", NodeControlGraphReferenceRole.GRAPH_REVISION),
    ("node_id", NodeControlGraphReferenceRole.NODE),
    ("provider_socket_name", NodeControlGraphReferenceRole.PROVIDER_SOCKET),
)


class WrapperConfigurationError(ValueError):
    """Fixed refusal; no input material or exception chain is retained."""


def _require(condition: bool) -> None:
    if condition is not True:
        raise ValueError


def _object(value: object, keys: set[str]) -> dict:
    _require(type(value) is dict and set(value) == keys)
    return value


def _reference(value: object, role: NodeControlGraphReferenceRole) -> None:
    _require(type(value) is NodeControlGraphReference and value.role is role and type(value.value) is str)
    _require(NodeControlGraphReference(role, value.value) == value)


@dataclass(frozen=True, slots=True, repr=False)
class NodeControlVerificationConfiguration:
    """Public verification facts for one existing workload protocol purpose."""

    purpose: DelegationKeyPurpose
    issuer: str
    public_keys: tuple[DelegationPublicKey, ...]

    def __post_init__(self) -> None:
        try:
            _require(type(self.purpose) is DelegationKeyPurpose and self.purpose in _PURPOSES)
            _require(type(self.issuer) is str and reference_violation(self.issuer) is None)
            _require(type(self.public_keys) is tuple and 1 <= len(self.public_keys) <= 16)
            for key in self.public_keys:
                _require(type(key) is DelegationPublicKey and type(key.key_id) is str
                         and key.algorithm is DelegationKeyAlgorithm.ED25519
                         and type(key.public_key_pem) is str and type(key.fingerprint_sha256) is str)
                _require(replace(key) == key)
            _require(len({key.key_id for key in self.public_keys}) == len(self.public_keys))
            _require(len({key.fingerprint_sha256 for key in self.public_keys}) == len(self.public_keys))
            object.__setattr__(self, "public_keys", tuple(sorted(self.public_keys, key=lambda key: key.key_id)))
            return
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure


def _validated_declaration_verifiers(provider_socket_name, declaration, verifiers) -> tuple[NodeControlVerificationConfiguration, ...]:
    """Shared structural facts; configuration profiles keep separate codecs."""
    _require(type(declaration) is WorkloadNodeControlSurfaceDeclaration)
    codec = WorkloadNodeControlSurfaceDeclarationCodec()
    _require(codec.decode(codec.encode(declaration)) == declaration)
    _require(provider_socket_name == declaration.surface.provider_socket_name)
    _require(type(verifiers) is tuple and 1 <= len(verifiers) <= len(_PURPOSES))
    for family in verifiers:
        _require(type(family) is NodeControlVerificationConfiguration and replace(family) == family)
    required = {DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ}
    if declaration.surface.variables:
        required.add(DelegationKeyPurpose.WORKLOAD_NODE_CONTROL)
    if declaration.surface.health_reads:
        required.add(DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
    _require({family.purpose for family in verifiers} == required and len(verifiers) == len(required))
    return tuple(sorted(verifiers, key=lambda family: family.purpose))


def _document(configuration: WorkloadNodeControlConfiguration) -> dict:
    return {
        "profile": _PROFILE,
        "target": configuration.target.descriptor(),
        "runtime_id": configuration.runtime_id.value,
        "declaration": configuration.declaration.descriptor(),
        "verifiers": [{
            "purpose": family.purpose.value,
            "issuer": family.issuer,
            "public_keys": [{"key_id": key.key_id, "algorithm": key.algorithm.value,
                             "public_key_pem": key.public_key_pem} for key in family.public_keys],
        } for family in configuration.verifiers],
    }


@dataclass(frozen=True, slots=True, repr=False)
class WorkloadNodeControlConfiguration:
    """One management receiver, with many declared variables/health reads."""

    target: NodeControlTarget
    runtime_id: NodeControlGraphReference
    declaration: WorkloadNodeControlSurfaceDeclaration
    verifiers: tuple[NodeControlVerificationConfiguration, ...]

    def __post_init__(self) -> None:
        try:
            _require(type(self.target) is NodeControlTarget)
            for name, role in _TARGET_ROLES:
                _reference(getattr(self.target, name), role)
            _reference(self.runtime_id, NodeControlGraphReferenceRole.RUNTIME)
            object.__setattr__(self, "verifiers", _validated_declaration_verifiers(
                self.target.provider_socket_name, self.declaration, self.verifiers))
            _require(len(canonical_json_bytes(_document(self))) <= MAX_WRAPPER_CONFIGURATION_BYTES)
            return
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError


def _decode_family(value: object) -> NodeControlVerificationConfiguration:
    family = _object(value, {"purpose", "issuer", "public_keys"})
    keys = family["public_keys"]
    _require(type(keys) is list and 1 <= len(keys) <= 16)
    public_keys = []
    for candidate in keys:
        key = _object(candidate, {"key_id", "algorithm", "public_key_pem"})
        public_keys.append(DelegationPublicKey(key["key_id"], DelegationKeyAlgorithm(key["algorithm"]), key["public_key_pem"]))
    return NodeControlVerificationConfiguration(DelegationKeyPurpose(family["purpose"]), family["issuer"], tuple(public_keys))


class WorkloadNodeControlConfigurationCodec:
    """Closed shared wire codec; no file access, signature checks or defaults."""

    def encode(self, configuration: WorkloadNodeControlConfiguration) -> dict[str, object]:
        try:
            _require(type(configuration) is WorkloadNodeControlConfiguration)
            checked = replace(configuration)
            return _document(checked)
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure

    def decode(self, document: object) -> WorkloadNodeControlConfiguration:
        try:
            value = _object(document, {"profile", "target", "runtime_id", "declaration", "verifiers"})
            _require(value["profile"] == _PROFILE and type(value["profile"]) is str)
            target = _object(value["target"], {name for name, _ in _TARGET_ROLES})
            _require(type(value["verifiers"]) is list and 1 <= len(value["verifiers"]) <= len(_PURPOSES))
            return WorkloadNodeControlConfiguration(
                NodeControlTarget(*(NodeControlGraphReference(role, target[name]) for name, role in _TARGET_ROLES)),
                NodeControlGraphReference(NodeControlGraphReferenceRole.RUNTIME, value["runtime_id"]),
                WorkloadNodeControlSurfaceDeclarationCodec().decode(value["declaration"]),
                tuple(_decode_family(family) for family in value["verifiers"]),
            )
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure

    def encode_bytes(self, configuration: WorkloadNodeControlConfiguration) -> bytes:
        return canonical_json_bytes(self.encode(configuration))

    def decode_bytes(self, raw: bytes) -> WorkloadNodeControlConfiguration:
        try:
            _require(type(raw) is bytes and 1 <= len(raw) <= MAX_WRAPPER_CONFIGURATION_BYTES)
            return self.decode(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                                          parse_constant=_reject_constant))
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure


def _select_wrapper_configuration_slot(
    *, artifacts: tuple[ConfigurationArtifact, ...],
    environment: tuple[PublicStaticEnvironmentBinding | SocketDerivedEnvironmentBinding, ...],
    control_surfaces: tuple[WorkloadNodeControlSurfaceDescriptor, ...],
) -> ConfigurationArtifact:
    """Select an exact structural slot without choosing a configuration profile."""
    _require(type(control_surfaces) is tuple and len(control_surfaces) == 1
             and type(control_surfaces[0]) is WorkloadNodeControlSurfaceDescriptor)
    _require(type(environment) is tuple and all(type(item) in (
        PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding) for item in environment))
    bindings = tuple(item for item in environment if item.name == WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT)
    _require(len(bindings) == 1 and type(bindings[0]) is PublicStaticEnvironmentBinding)
    validate_configuration_target_path(bindings[0].value)
    _require(type(artifacts) is tuple and all(type(item) is ConfigurationArtifact for item in artifacts))
    selected = tuple(item for item in artifacts if item.target_path == bindings[0].value)
    _require(len(selected) == 1)
    artifact = selected[0]
    _require(ConfigurationArtifact.from_descriptor(artifact.descriptor()) == artifact)
    _require(artifact.media_type is ConfigurationMediaType.JSON and artifact.file_mode is ConfigurationFileMode.READ_ONLY)
    return artifact


def select_workload_node_control_configuration_artifact(
    *, artifacts: tuple[ConfigurationArtifact, ...],
    environment: tuple[PublicStaticEnvironmentBinding | SocketDerivedEnvironmentBinding, ...],
    control_surfaces: tuple[WorkloadNodeControlSurfaceDescriptor, ...],
) -> ConfigurationArtifact:
    """Resolve one supplied protocol binding; caller owns provenance and I/O.

    Supply effective public and socket-derived assignments, not a filtered view
    that could hide a competing assignment. Operations separately compares the
    registered and selected slots. Descriptor default bytes are not deployed truth.
    """
    try:
        artifact = _select_wrapper_configuration_slot(
            artifacts=artifacts, environment=environment, control_surfaces=control_surfaces)
        configuration = WorkloadNodeControlConfigurationCodec().decode_bytes(artifact.content.encode("utf-8"))
        _require(configuration.declaration.surface == control_surfaces[0])
        return artifact
    except _INPUT_ERRORS:
        failure = WrapperConfigurationError(_ERROR)
    raise failure


__all__ = [
    "MAX_WRAPPER_CONFIGURATION_BYTES", "WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT",
    "WrapperConfigurationError", "NodeControlVerificationConfiguration",
    "WorkloadNodeControlConfiguration", "WorkloadNodeControlConfigurationCodec",
    "select_workload_node_control_configuration_artifact",
]
