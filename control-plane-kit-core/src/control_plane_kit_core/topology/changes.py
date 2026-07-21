"""Closed data algebra for structural deployment-graph changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from typing import Mapping, TypeAlias

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.configuration import ConfigurationArtifact
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
)
from control_plane_kit_core.secrets import SecretDelivery, secret_delivery_sort_key
from control_plane_kit_core.topology.graph import (
    Edge,
    Endpoint,
    LiteralAddress,
    Node,
    RuntimeRecord,
    SecretReferenceAddress,
)
from control_plane_kit_core.topology.validation import (
    EdgeSubject,
    GraphSubject,
    NodeSubject,
    RuntimeSubject,
)

_REDACTED = "<redacted>"
_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "private_key",
    "credential",
    "api_key",
)


class StructuralField(StrEnum):
    GRAPH_NAME = "graph-name"
    RUNTIME_KIND = "runtime-kind"
    RUNTIME_CONTAINMENT = "runtime-containment"
    RUNTIME_METADATA = "runtime-metadata"
    BLOCK_FAMILY = "block-family"
    BLOCK_SPECIFICATION = "block-specification"
    IMPLEMENTATION_KIND = "implementation-kind"
    RUNTIME_MEMBERSHIP = "runtime-membership"
    SOCKET_CONTRACT = "socket-contract"
    ENDPOINT = "endpoint"
    PUBLIC_ENVIRONMENT = "public-environment"
    SOCKET_ENVIRONMENT = "socket-environment"
    NODE_METADATA = "node-metadata"
    CONFIGURATION_ARTIFACTS = "configuration-artifacts"
    SECRET_DELIVERIES = "secret-deliveries"
    RESOURCE_LIFECYCLE = "resource-lifecycle"


DiffOwner: TypeAlias = GraphSubject | RuntimeSubject | NodeSubject


@dataclass(frozen=True)
class FieldSubject:
    owner: DiffOwner
    field: StructuralField
    key: str | None = None

    def descriptor(self) -> dict[str, object]:
        value: dict[str, object] = {
            "owner": self.owner.descriptor(),
            "field": self.field.value,
        }
        if self.key is not None:
            value["key"] = self.key
        return value


DiffSubject: TypeAlias = (
    GraphSubject | RuntimeSubject | NodeSubject | EdgeSubject | FieldSubject
)


@dataclass(frozen=True)
class TextValue:
    value: str

    def descriptor(self) -> str:
        return self.value


@dataclass(frozen=True)
class StringTupleValue:
    values: tuple[str, ...]

    def descriptor(self) -> list[str]:
        return list(self.values)


@dataclass(frozen=True)
class MetadataValue:
    values: Mapping[str, object]

    def descriptor(self) -> dict[str, object]:
        return _redact_mapping(self.values)


@dataclass(frozen=True)
class EnvironmentBindingsValue:
    values: tuple[
        PublicStaticEnvironmentBinding | SocketDerivedEnvironmentBinding,
        ...,
    ]

    def descriptor(self) -> list[dict[str, str]]:
        return [
            {
                "kind": value.descriptor()["kind"],
                "name": value.name,
                "value": _REDACTED,
                **(
                    {"edge_id": value.edge_id}
                    if isinstance(value, SocketDerivedEnvironmentBinding)
                    else {}
                ),
            }
            for value in sorted(self.values, key=lambda item: item.name)
        ]


@dataclass(frozen=True)
class ConfigurationArtifactsValue:
    values: tuple[ConfigurationArtifact, ...]

    def descriptor(self) -> list[dict[str, str]]:
        return [value.descriptor() for value in sorted(self.values)]


@dataclass(frozen=True)
class SecretDeliveriesValue:
    values: tuple[SecretDelivery, ...]

    def descriptor(self) -> list[dict[str, object]]:
        descriptors: list[dict[str, object]] = []
        for value in sorted(self.values, key=secret_delivery_sort_key):
            descriptor = dict(value.descriptor())
            reference_id = descriptor.get("reference_id")
            if isinstance(reference_id, str):
                descriptor["reference_fingerprint"] = hashlib.sha256(
                    reference_id.encode("utf-8")
                ).hexdigest()
            descriptor["reference_id"] = _REDACTED
            descriptors.append(descriptor)
        return descriptors


@dataclass(frozen=True)
class EndpointValue:
    endpoint: Endpoint

    def descriptor(self) -> dict[str, object]:
        match self.endpoint.address:
            case LiteralAddress(value=value):
                address: dict[str, str] = {"kind": "literal", "value": value}
            case SecretReferenceAddress():
                address = {"kind": "secret-reference", "secret_ref": _REDACTED}
        return {
            "address": address,
            "protocol": self.endpoint.protocol.descriptor(),
            "scope": self.endpoint.scope.value,
        }


@dataclass(frozen=True)
class SocketContractValue:
    sockets: BlockSockets

    def descriptor(self) -> dict[str, object]:
        return {
            "providers": [
                {"name": socket.name, "protocol": socket.protocol.descriptor()}
                for socket in sorted(self.sockets.providers, key=lambda value: value.name)
            ],
            "requirements": [
                {
                    "name": socket.name,
                    "protocol": socket.protocol.descriptor(),
                    "binding": socket.binding.value,
                    "env_bindings": list(socket.env_bindings),
                    "required": socket.required,
                }
                for socket in sorted(
                    self.sockets.requirements,
                    key=lambda value: value.name,
                )
            ],
        }


@dataclass(frozen=True)
class BlockSpecValue:
    spec: BlockSpec
    encoded: Mapping[str, object]

    def descriptor(self) -> dict[str, object]:
        return _redact_mapping(self.encoded)


@dataclass(frozen=True)
class RuntimeValue:
    runtime: RuntimeRecord

    def descriptor(self) -> dict[str, object]:
        return {
            "runtime_id": self.runtime.runtime_id,
            "kind": self.runtime.kind.value,
            "children": list(self.runtime.children),
            "metadata": _redact_mapping(self.runtime.metadata),
            "lifecycle": self.runtime.lifecycle.descriptor(),
        }


@dataclass(frozen=True)
class NodeValue:
    node: Node
    block_spec: BlockSpecValue

    def descriptor(self) -> dict[str, object]:
        return {
            "node_id": self.node.node_id,
            "block_family": self.node.block_family.value,
            "block_spec": self.block_spec.descriptor(),
            "kind": self.node.kind,
            "runtime_id": self.node.runtime_id,
            "providers": SocketContractValue(self.node.sockets).descriptor()["providers"],
            "requirements": SocketContractValue(self.node.sockets).descriptor()[
                "requirements"
            ],
            "endpoints": {
                name: EndpointValue(endpoint).descriptor()
                for name, endpoint in sorted(self.node.endpoints.items())
            },
            "environment_bindings": EnvironmentBindingsValue(
                self.node.public_environment + self.node.socket_environment
            ).descriptor(),
            "metadata": _redact_mapping(self.node.metadata),
            "lifecycle": self.node.lifecycle.descriptor(),
            "configuration_artifacts": ConfigurationArtifactsValue(
                self.node.configuration_artifacts
            ).descriptor(),
            "secret_deliveries": SecretDeliveriesValue(
                self.node.secret_deliveries
            ).descriptor(),
        }


@dataclass(frozen=True)
class EdgeValue:
    edge: Edge

    def descriptor(self) -> dict[str, object]:
        return {
            "edge_id": self.edge.edge_id,
            "provider": {
                "node_id": self.edge.provider_role,
                "socket": self.edge.provider_socket,
            },
            "consumer": {
                "node_id": self.edge.consumer_role,
                "socket": self.edge.requirement_socket,
            },
            "protocol": self.edge.protocol.descriptor(),
            "binding": self.edge.binding.value,
            "env_assignments": {
                name: _REDACTED for name in sorted(self.edge.env_assignments)
            },
        }


DiffValue: TypeAlias = (
    TextValue
    | StringTupleValue
    | MetadataValue
    | EnvironmentBindingsValue
    | ConfigurationArtifactsValue
    | SecretDeliveriesValue
    | EndpointValue
    | SocketContractValue
    | BlockSpecValue
    | RuntimeValue
    | NodeValue
    | EdgeValue
)


class UnsupportedReason(StrEnum):
    RUNTIME_KIND_TRANSITION = "runtime-kind-transition"
    IMPLEMENTATION_KIND_TRANSITION = "implementation-kind-transition"


class AmbiguityReason(StrEnum):
    BLOCK_SPEC_LANGUAGE_MISMATCH = "block-spec-language-mismatch"
    NODE_IDENTITY_REUSED = "node-identity-reused"


@dataclass(frozen=True)
class AddedChange:
    subject: DiffSubject
    after: DiffValue

    def descriptor(self) -> dict[str, object]:
        return {
            "form": "added",
            "subject": self.subject.descriptor(),
            "after": self.after.descriptor(),
        }


@dataclass(frozen=True)
class RemovedChange:
    subject: DiffSubject
    before: DiffValue

    def descriptor(self) -> dict[str, object]:
        return {
            "form": "removed",
            "subject": self.subject.descriptor(),
            "before": self.before.descriptor(),
        }


@dataclass(frozen=True)
class ModifiedChange:
    subject: DiffSubject
    before: DiffValue
    after: DiffValue

    def descriptor(self) -> dict[str, object]:
        return {
            "form": "modified",
            "subject": self.subject.descriptor(),
            "before": self.before.descriptor(),
            "after": self.after.descriptor(),
        }


@dataclass(frozen=True)
class UnsupportedChange:
    subject: DiffSubject
    before: DiffValue
    after: DiffValue
    reason: UnsupportedReason

    def descriptor(self) -> dict[str, object]:
        return {
            "form": "unsupported",
            "subject": self.subject.descriptor(),
            "reason": self.reason.value,
            "before": self.before.descriptor(),
            "after": self.after.descriptor(),
        }


@dataclass(frozen=True)
class AmbiguousChange:
    subject: DiffSubject
    reason: AmbiguityReason
    before: DiffValue | None = None
    after: DiffValue | None = None

    def descriptor(self) -> dict[str, object]:
        value: dict[str, object] = {
            "form": "ambiguous",
            "subject": self.subject.descriptor(),
            "reason": self.reason.value,
        }
        if self.before is not None:
            value["before"] = self.before.descriptor()
        if self.after is not None:
            value["after"] = self.after.descriptor()
        return value


StructuralChange: TypeAlias = (
    AddedChange
    | RemovedChange
    | ModifiedChange
    | UnsupportedChange
    | AmbiguousChange
)


@dataclass(frozen=True)
class GraphDiff:
    """Deterministic structural data from current topology to desired topology."""

    current_graph_name: str
    desired_graph_name: str
    changes: tuple[StructuralChange, ...]

    @property
    def empty(self) -> bool:
        return not self.changes

    def descriptor(self) -> dict[str, object]:
        return {
            "current_graph_name": self.current_graph_name,
            "desired_graph_name": self.desired_graph_name,
            "changes": [change.descriptor() for change in self.changes],
        }

    def summary(self) -> str:
        if self.empty:
            return "no changes"
        counts: dict[str, int] = {}
        for change in self.changes:
            form = str(change.descriptor()["form"])
            counts[form] = counts.get(form, 0) + 1
        return ", ".join(f"{form}: {counts[form]}" for form in sorted(counts))




def _redact_mapping(mapping: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _redact_value(str(key), value)
        for key, value in sorted(mapping.items(), key=lambda item: str(item[0]))
    }


def _redact_value(key: str, value: object) -> object:
    if any(marker in key.lower() for marker in _SECRET_MARKERS):
        return _REDACTED
    if isinstance(value, Mapping):
        return _redact_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, child) for child in value]
    return value
