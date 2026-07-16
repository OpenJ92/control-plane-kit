"""Pure typed structural differences between validated deployment graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, TypeAlias

from control_plane_kit.algebra import BlockSockets, BlockSpec
from control_plane_kit.graph import (
    DeploymentGraph,
    Edge,
    Endpoint,
    LiteralAddress,
    Node,
    RuntimeRecord,
    SecretReferenceAddress,
)
from control_plane_kit.graph_codec import GraphDescriptorCodec
from control_plane_kit.validation import (
    EdgeSubject,
    GraphSubject,
    NodeSubject,
    RuntimeSubject,
    ValidatedGraph,
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
    ENVIRONMENT = "environment"
    NODE_METADATA = "node-metadata"


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
class EnvironmentValue:
    values: Mapping[str, str]

    def descriptor(self) -> dict[str, str]:
        return {name: _REDACTED for name in sorted(self.values)}


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
            "protocol": self.endpoint.protocol.value,
            "scope": self.endpoint.scope.value,
        }


@dataclass(frozen=True)
class SocketContractValue:
    sockets: BlockSockets

    def descriptor(self) -> dict[str, object]:
        return {
            "providers": [
                {"name": socket.name, "protocol": socket.protocol.value}
                for socket in sorted(self.sockets.providers, key=lambda value: value.name)
            ],
            "requirements": [
                {
                    "name": socket.name,
                    "protocol": socket.protocol.value,
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
            "environment": EnvironmentValue(self.node.environment).descriptor(),
            "metadata": _redact_mapping(self.node.metadata),
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
            "protocol": self.edge.protocol.value,
            "env_assignments": EnvironmentValue(
                self.edge.env_assignments
            ).descriptor(),
        }


DiffValue: TypeAlias = (
    TextValue
    | StringTupleValue
    | MetadataValue
    | EnvironmentValue
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


def diff_graphs(current: ValidatedGraph, desired: ValidatedGraph) -> GraphDiff:
    """Compare two valid typed graphs without persistence or runtime effects."""

    if not isinstance(current, ValidatedGraph) or not isinstance(desired, ValidatedGraph):
        raise TypeError("diff_graphs requires two ValidatedGraph values")
    current_graph = current.require_valid()
    desired_graph = desired.require_valid()
    if not current.codec.supports_same_block_specs_as(desired.codec):
        return GraphDiff(
            current_graph.name,
            desired_graph.name,
            (
                AmbiguousChange(
                    GraphSubject(),
                    AmbiguityReason.BLOCK_SPEC_LANGUAGE_MISMATCH,
                ),
            ),
        )

    changes: list[StructuralChange] = []
    codec = current.codec
    if current_graph.name != desired_graph.name:
        changes.append(
            ModifiedChange(
                FieldSubject(GraphSubject(), StructuralField.GRAPH_NAME),
                TextValue(current_graph.name),
                TextValue(desired_graph.name),
            )
        )
    _diff_runtimes(current_graph, desired_graph, changes)
    _diff_nodes(current_graph, desired_graph, codec, changes)
    _diff_edges(current_graph, desired_graph, changes)
    return GraphDiff(
        current_graph.name,
        desired_graph.name,
        tuple(sorted(changes, key=_change_key)),
    )


def _diff_runtimes(
    current: DeploymentGraph,
    desired: DeploymentGraph,
    changes: list[StructuralChange],
) -> None:
    current_ids = set(current.runtimes)
    desired_ids = set(desired.runtimes)
    for runtime_id in sorted(desired_ids - current_ids):
        changes.append(
            AddedChange(
                RuntimeSubject(runtime_id),
                RuntimeValue(desired.runtimes[runtime_id]),
            )
        )
    for runtime_id in sorted(current_ids - desired_ids):
        changes.append(
            RemovedChange(
                RuntimeSubject(runtime_id),
                RuntimeValue(current.runtimes[runtime_id]),
            )
        )
    for runtime_id in sorted(current_ids & desired_ids):
        before = current.runtimes[runtime_id]
        after = desired.runtimes[runtime_id]
        subject = RuntimeSubject(runtime_id)
        if before.kind is not after.kind:
            changes.append(
                UnsupportedChange(
                    FieldSubject(subject, StructuralField.RUNTIME_KIND),
                    TextValue(before.kind.value),
                    TextValue(after.kind.value),
                    UnsupportedReason.RUNTIME_KIND_TRANSITION,
                )
            )
        if before.children != after.children:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.RUNTIME_CONTAINMENT),
                    StringTupleValue(before.children),
                    StringTupleValue(after.children),
                )
            )
        if before.metadata != after.metadata:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.RUNTIME_METADATA),
                    MetadataValue(before.metadata),
                    MetadataValue(after.metadata),
                )
            )


def _diff_nodes(
    current: DeploymentGraph,
    desired: DeploymentGraph,
    codec: GraphDescriptorCodec,
    changes: list[StructuralChange],
) -> None:
    current_ids = set(current.nodes)
    desired_ids = set(desired.nodes)
    for node_id in sorted(desired_ids - current_ids):
        changes.append(
            AddedChange(NodeSubject(node_id), _node_value(desired.nodes[node_id], codec))
        )
    for node_id in sorted(current_ids - desired_ids):
        changes.append(
            RemovedChange(NodeSubject(node_id), _node_value(current.nodes[node_id], codec))
        )
    for node_id in sorted(current_ids & desired_ids):
        before = current.nodes[node_id]
        after = desired.nodes[node_id]
        subject = NodeSubject(node_id)
        if before.block_family is not after.block_family:
            changes.append(
                AmbiguousChange(
                    subject,
                    AmbiguityReason.NODE_IDENTITY_REUSED,
                    _node_value(before, codec),
                    _node_value(after, codec),
                )
            )
            continue
        if before.kind != after.kind:
            changes.append(
                UnsupportedChange(
                    FieldSubject(subject, StructuralField.IMPLEMENTATION_KIND),
                    TextValue(before.kind),
                    TextValue(after.kind),
                    UnsupportedReason.IMPLEMENTATION_KIND_TRANSITION,
                )
            )
        if before.runtime_id != after.runtime_id:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.RUNTIME_MEMBERSHIP),
                    TextValue(before.runtime_id),
                    TextValue(after.runtime_id),
                )
            )
        if before.block_spec != after.block_spec:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.BLOCK_SPECIFICATION),
                    _block_spec_value(before.block_spec, codec),
                    _block_spec_value(after.block_spec, codec),
                )
            )
        if before.sockets != after.sockets:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.SOCKET_CONTRACT),
                    SocketContractValue(before.sockets),
                    SocketContractValue(after.sockets),
                )
            )
        _diff_endpoints(subject, before, after, changes)
        if before.environment != after.environment:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.ENVIRONMENT),
                    EnvironmentValue(before.environment),
                    EnvironmentValue(after.environment),
                )
            )
        if before.metadata != after.metadata:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.NODE_METADATA),
                    MetadataValue(before.metadata),
                    MetadataValue(after.metadata),
                )
            )


def _diff_endpoints(
    subject: NodeSubject,
    before: Node,
    after: Node,
    changes: list[StructuralChange],
) -> None:
    current_names = set(before.endpoints)
    desired_names = set(after.endpoints)
    for name in sorted(desired_names - current_names):
        changes.append(
            AddedChange(
                FieldSubject(subject, StructuralField.ENDPOINT, name),
                EndpointValue(after.endpoints[name]),
            )
        )
    for name in sorted(current_names - desired_names):
        changes.append(
            RemovedChange(
                FieldSubject(subject, StructuralField.ENDPOINT, name),
                EndpointValue(before.endpoints[name]),
            )
        )
    for name in sorted(current_names & desired_names):
        if before.endpoints[name] != after.endpoints[name]:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.ENDPOINT, name),
                    EndpointValue(before.endpoints[name]),
                    EndpointValue(after.endpoints[name]),
                )
            )


def _diff_edges(
    current: DeploymentGraph,
    desired: DeploymentGraph,
    changes: list[StructuralChange],
) -> None:
    current_ids = set(current.edges)
    desired_ids = set(desired.edges)
    for edge_id in sorted(desired_ids - current_ids):
        changes.append(
            AddedChange(EdgeSubject(edge_id), EdgeValue(desired.edges[edge_id]))
        )
    for edge_id in sorted(current_ids - desired_ids):
        changes.append(
            RemovedChange(EdgeSubject(edge_id), EdgeValue(current.edges[edge_id]))
        )
    for edge_id in sorted(current_ids & desired_ids):
        if current.edges[edge_id] != desired.edges[edge_id]:
            changes.append(
                ModifiedChange(
                    EdgeSubject(edge_id),
                    EdgeValue(current.edges[edge_id]),
                    EdgeValue(desired.edges[edge_id]),
                )
            )


def _node_value(node: Node, codec: GraphDescriptorCodec) -> NodeValue:
    return NodeValue(node, _block_spec_value(node.block_spec, codec))


def _block_spec_value(spec: BlockSpec, codec: GraphDescriptorCodec) -> BlockSpecValue:
    return BlockSpecValue(spec, codec.encode_block_spec(spec))


def _change_key(change: StructuralChange) -> tuple[int, str, str]:
    rank = {
        AddedChange: 0,
        ModifiedChange: 1,
        UnsupportedChange: 2,
        AmbiguousChange: 3,
        RemovedChange: 4,
    }[type(change)]
    descriptor = change.descriptor()
    return (
        rank,
        json.dumps(descriptor["subject"], sort_keys=True),
        json.dumps(descriptor, sort_keys=True),
    )


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
