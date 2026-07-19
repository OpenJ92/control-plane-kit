"""Pure interpreter from two validated graphs to structural change data."""

from __future__ import annotations

import json

from control_plane_kit.algebra import BlockSpec
from control_plane_kit.topology.graph import DeploymentGraph, Node
from control_plane_kit.topology.changes import (
    AddedChange,
    AmbiguityReason,
    AmbiguousChange,
    BlockSpecValue,
    EdgeValue,
    EndpointValue,
    ConfigurationArtifactsValue,
    SecretDeliveriesValue,
    EnvironmentValue,
    FieldSubject,
    GraphDiff,
    MetadataValue,
    ModifiedChange,
    NodeValue,
    RemovedChange,
    RuntimeValue,
    SocketContractValue,
    StringTupleValue,
    StructuralChange,
    StructuralField,
    TextValue,
    UnsupportedChange,
    UnsupportedReason,
)
from control_plane_kit.topology.codec import GraphDescriptorCodec
from control_plane_kit.topology.validation import (
    EdgeSubject,
    GraphSubject,
    NodeSubject,
    RuntimeSubject,
    ValidatedGraph,
)


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
        if before.lifecycle != after.lifecycle:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.RESOURCE_LIFECYCLE),
                    MetadataValue(before.lifecycle.descriptor()),
                    MetadataValue(after.lifecycle.descriptor()),
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
        if before.configuration_artifacts != after.configuration_artifacts:
            changes.append(
                ModifiedChange(
                    FieldSubject(
                        subject,
                        StructuralField.CONFIGURATION_ARTIFACTS,
                    ),
                    ConfigurationArtifactsValue(before.configuration_artifacts),
                    ConfigurationArtifactsValue(after.configuration_artifacts),
                )
            )
        if before.secret_deliveries != after.secret_deliveries:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.SECRET_DELIVERIES),
                    SecretDeliveriesValue(before.secret_deliveries),
                    SecretDeliveriesValue(after.secret_deliveries),
                )
            )
        if before.lifecycle != after.lifecycle:
            changes.append(
                ModifiedChange(
                    FieldSubject(subject, StructuralField.RESOURCE_LIFECYCLE),
                    MetadataValue(before.lifecycle.descriptor()),
                    MetadataValue(after.lifecycle.descriptor()),
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
