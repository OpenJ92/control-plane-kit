"""Explicit successor wrapper configuration for a logical receiver target."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes
from control_plane_kit_core.configuration import ConfigurationArtifact
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.node_control import WorkloadNodeControlSurfaceDescriptor
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec,
)
from control_plane_kit_core.receiver_identity import NodeControlReceiverTarget, NodeControlReceiverTargetCodec
from control_plane_kit_core.wrapper_configuration import (
    MAX_WRAPPER_CONFIGURATION_BYTES, WrapperConfigurationError, NodeControlVerificationConfiguration,
    _decode_family, _INPUT_ERRORS, _object, _reject_constant, _require,
    _select_wrapper_configuration_slot, _unique_object, _validated_declaration_verifiers,
)

_PROFILE = "workload-node-control-configuration.v2"
_ERROR = "wrapper configuration is invalid"


def _document(configuration: ReceiverNodeControlConfiguration) -> dict:
    return {
        "profile": _PROFILE,
        "target": configuration.target.descriptor(),
        "declaration": configuration.declaration.descriptor(),
        "verifiers": [{
            "purpose": family.purpose.value, "issuer": family.issuer,
            "public_keys": [{"key_id": key.key_id, "algorithm": key.algorithm.value,
                             "public_key_pem": key.public_key_pem} for key in family.public_keys],
        } for family in configuration.verifiers],
    }


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverNodeControlConfiguration:
    """Installed scope/declaration/public trust, without changing graph authority."""

    target: NodeControlReceiverTarget
    declaration: WorkloadNodeControlSurfaceDeclaration
    verifiers: tuple[NodeControlVerificationConfiguration, ...]

    def __post_init__(self) -> None:
        try:
            NodeControlReceiverTargetCodec().encode(self.target)
            object.__setattr__(self, "verifiers", _validated_declaration_verifiers(
                self.target.provider_socket_name, self.declaration, self.verifiers))
            _require(len(canonical_json_bytes(_document(self))) <= MAX_WRAPPER_CONFIGURATION_BYTES)
            return
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure


class ReceiverNodeControlConfigurationCodec:
    """Closed V2 decoding only; never fall back to the historical profile."""

    def encode(self, configuration: ReceiverNodeControlConfiguration) -> dict[str, object]:
        try:
            _require(type(configuration) is ReceiverNodeControlConfiguration)
            return _document(replace(configuration))
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure

    def decode(self, document: object) -> ReceiverNodeControlConfiguration:
        try:
            value = _object(document, {"profile", "target", "declaration", "verifiers"})
            _require(type(value["profile"]) is str and value["profile"] == _PROFILE)
            _require(type(value["verifiers"]) is list and 1 <= len(value["verifiers"]) <= 3)
            return ReceiverNodeControlConfiguration(
                NodeControlReceiverTargetCodec().decode(value["target"]),
                WorkloadNodeControlSurfaceDeclarationCodec().decode(value["declaration"]),
                tuple(_decode_family(family) for family in value["verifiers"]),
            )
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure

    def encode_bytes(self, configuration: ReceiverNodeControlConfiguration) -> bytes:
        return canonical_json_bytes(self.encode(configuration))

    def decode_bytes(self, raw: bytes) -> ReceiverNodeControlConfiguration:
        try:
            _require(type(raw) is bytes and 1 <= len(raw) <= MAX_WRAPPER_CONFIGURATION_BYTES)
            return self.decode(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                                          parse_constant=_reject_constant))
        except _INPUT_ERRORS:
            failure = WrapperConfigurationError(_ERROR)
        raise failure


def select_receiver_node_control_configuration_artifact(
    *, artifacts: tuple[ConfigurationArtifact, ...],
    environment: tuple[PublicStaticEnvironmentBinding | SocketDerivedEnvironmentBinding, ...],
    control_surfaces: tuple[WorkloadNodeControlSurfaceDescriptor, ...],
) -> ConfigurationArtifact:
    """Select exact V2 material; the caller owns provenance, admission and I/O."""
    try:
        artifact = _select_wrapper_configuration_slot(
            artifacts=artifacts, environment=environment, control_surfaces=control_surfaces)
        configuration = ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode("utf-8"))
        _require(configuration.declaration.surface == control_surfaces[0])
        return artifact
    except _INPUT_ERRORS:
        failure = WrapperConfigurationError(_ERROR)
    raise failure


__all__ = [
    "ReceiverNodeControlConfiguration", "ReceiverNodeControlConfigurationCodec",
    "select_receiver_node_control_configuration_artifact",
]
