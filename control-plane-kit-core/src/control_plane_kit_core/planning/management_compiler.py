"""Pure graph-pair management planning and pinned request resolution.

Requests describe required observations. They neither grant authority nor prove
readiness, successful transport, or freshness of an execution attempt.
"""
from dataclasses import dataclass, replace
import hashlib

from control_plane_kit_core.configuration import ConfigurationMediaType
from control_plane_kit_core.lifecycle import ResourceOwnership, ResourcePersistence
from control_plane_kit_core.node_control import NodeHealthReadKind, WorkloadNodeControlSurfaceDescriptor
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.planning.activity_plan import (
    ActivityDependency, ActivityId, ActivityPlan, AllocatePublicIngress,
    ChangeTarget, InvalidActivityPlan, PlannedActivity, ReconcileNode, RemoveNodeResource, ReviewChange,
    ReviewReason, RiskLevel, StartNode, StartRuntime, StopNode, WaitForHealthy,
)
from control_plane_kit_core.planning.codec import activity_operation_descriptor
from control_plane_kit_core.planning.compiler import compile_activity_plan
from control_plane_kit_core.planning.management_observations import (
    ManagementBootstrapStage, ManagementObservationError, ManagementObservationTarget,
    ObserveManagementBootstrap, ObserveNodeHealth, PlanGraphSide, _bounded_wire,
)
from control_plane_kit_core.public_ingress import NamedPublicIngress
from control_plane_kit_core.products import OciImageReference, ProductDescriptorDigest, ProductIdentity
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRoutesCodec, ReceiverManagementRoutesError
from control_plane_kit_core.secret_selections import SecretVersionSelection, normalize_secret_selections
from control_plane_kit_core.topology.changes import (
    AddedChange, FieldSubject, ModifiedChange, RemovedChange, StructuralField,
)
from control_plane_kit_core.topology.codec import _management_ingress
from control_plane_kit_core.topology.diff import diff_graphs
from control_plane_kit_core.topology.graph import Node
from control_plane_kit_core.topology.validation import (
    NodeSubject, RuntimeSubject, ValidatedGraph, management_ingress_for_health_read,
)


_GRAPH_DOMAIN = b"control-plane-kit.management-graph.v1\0"
_RELATION_DOMAIN = b"control-plane-kit.management-relation.v1\0"
_ACTIVITY_DOMAIN = b"control-plane-kit.management-activity.v1\0"
_REVIEW_DOMAIN = b"control-plane-kit.management-review.v1\0"


@dataclass(frozen=True)
class ResolvedManagementBootstrap:
    operation: ObserveManagementBootstrap
    selected_graph: ValidatedGraph
    gateway_node: Node
    connector_node: Node
    ingress: NamedPublicIngress
    gateway_readiness_socket: str


@dataclass(frozen=True)
class ResolvedNodeHealth:
    operation: ObserveNodeHealth
    selected_graph: ValidatedGraph
    gateway_node: Node
    connector_node: Node
    ingress: NamedPublicIngress
    workload_node: Node
    workload_surface: WorkloadNodeControlSurfaceDescriptor


@dataclass(frozen=True)
class _ManagedUpdateShape:
    mode: str
    runtime_id: str
    gateway_id: str
    connector_id: str
    retained_node_id: str
    changed_node_id: str


def _require_pair(current, desired):
    if (not isinstance(current, ValidatedGraph) or not isinstance(desired, ValidatedGraph)
            or not current.valid or not desired.valid):
        raise ManagementObservationError("management planning requires two valid graphs")


def _selected_surface(node, *, gateway=False):
    eligible = tuple(value for value in node.block_spec.control_surfaces
                     if (NodeHealthReadKind.READINESS in value.health_reads if gateway else bool(value.health_reads)))
    if len(eligible) != 1:
        return None, (ReviewReason.AMBIGUOUS_CHANGE if eligible else ReviewReason.UNSUPPORTED_CHANGE)
    return eligible[0], None


def _health_kind(surface):
    return NodeHealthReadKind.READINESS if NodeHealthReadKind.READINESS in surface.health_reads else NodeHealthReadKind.LIVENESS


def _graph_digest(snapshot):
    material = snapshot.codec.encode(snapshot.graph)
    return hashlib.sha256(_GRAPH_DOMAIN + _bounded_wire(material, 1_048_576)).hexdigest()


def _pin_path(snapshot, runtime_id, side):
    """Re-derive every target from the selected snapshot's own codec/material."""
    graph = snapshot.graph
    ingress = _management_ingress(graph, runtime_id)
    runtime = graph.runtimes[runtime_id]
    gateway = graph.nodes[runtime.management.gateway_node_id]
    connector = graph.nodes[ingress.connector_node_id]
    readiness, _ = _selected_surface(gateway, gateway=True)
    if readiness is None:
        raise ManagementObservationError("gateway local readiness selection is unavailable")
    socket = readiness.provider_socket_name.value
    relation = {"runtime_id": runtime_id, "management": runtime.management.descriptor(),
                "ingress": ingress.descriptor(), "gateway_transit": gateway.block_spec.gateway_transit.descriptor(),
                "gateway_readiness": {"provider_socket_name": socket, "health_kind": "readiness"}}
    target = ManagementObservationTarget(runtime_id, side, _graph_digest(snapshot),
        hashlib.sha256(_RELATION_DOMAIN + _bounded_wire(relation, 4096)).hexdigest())
    return target, gateway, connector, ingress, socket


def resolve_management_observation(candidate, current, desired, *, expected_operation):
    """Resolve semantics against an independently selected accepted plan operation.

The caller supplies expected_operation from its pinned plan, never from a result
or credential. Equality is not authorization or proof of execution freshness.
"""
    result = None
    try:
        _require_pair(current, desired)
        if (type(candidate) not in (ObserveManagementBootstrap, ObserveNodeHealth)
                or type(expected_operation) is not type(candidate)
                or candidate != expected_operation
                or not current.codec.supports_same_block_specs_as(desired.codec)):
            raise ManagementObservationError("management observation context does not match")
        candidate.__post_init__()
        snapshot = current if candidate.target.graph_side is PlanGraphSide.BASE_GRAPH else desired
        target, gateway, connector, ingress, socket = _pin_path(snapshot, candidate.target.runtime_id, candidate.target.graph_side)
        if target != candidate.target:
            raise ManagementObservationError("management observation pins do not match")
        if type(candidate) is ObserveManagementBootstrap:
            result = ResolvedManagementBootstrap(candidate, snapshot, gateway, connector, ingress, socket)
        else:
            workload = snapshot.graph.nodes[candidate.node_id]
            surface, _ = _selected_surface(workload)
            if (surface is None or workload.runtime_id != target.runtime_id
                    or surface.provider_socket_name.value != candidate.provider_socket_name
                    or _health_kind(surface) is not candidate.health_kind):
                raise ManagementObservationError("node health observation selection does not match")
            selected_ingress = management_ingress_for_health_read(snapshot, candidate.node_id,
                candidate.provider_socket_name, candidate.health_kind)
            if selected_ingress != ingress:
                raise ManagementObservationError("node health management relation does not match")
            result = ResolvedNodeHealth(candidate, snapshot, gateway, connector, ingress, workload, surface)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        pass
    if result is None:
        raise ManagementObservationError("management observation does not match pinned graph context")
    return result


def _observation_activity(operation, dependencies=()):
    identity = "observe:" + hashlib.sha256(_ACTIVITY_DOMAIN + _bounded_wire(operation.descriptor(), 1024)).hexdigest()
    return PlannedActivity(ActivityId(identity), operation,
                           tuple(ActivityDependency(value) for value in dependencies), risk=RiskLevel.MEDIUM)


def _review_activity(subject, reason):
    operation = ReviewChange(ChangeTarget(subject), reason)
    identity = "review-management:" + hashlib.sha256(
        _REVIEW_DOMAIN + _bounded_wire(activity_operation_descriptor(operation), 1024)).hexdigest()
    return PlannedActivity(ActivityId(identity), operation, risk=RiskLevel.HIGH)


def _independent_verification(node):
    return bool(node.block_spec.verification.checks) or node.block_spec.health_path is not None


def _managed_update_shape(current, desired, diff):
    """Recognize one generic retained-path workload add/remove graph relation."""
    added = [value.subject.node_id for value in diff.changes
             if isinstance(value, AddedChange) and isinstance(value.subject, NodeSubject)]
    removed = [value.subject.node_id for value in diff.changes
               if isinstance(value, RemovedChange) and isinstance(value.subject, NodeSubject)]
    configurations = [value.subject.owner.node_id for value in diff.changes
                      if isinstance(value, ModifiedChange)
                      and isinstance(value.subject, FieldSubject)
                      and value.subject.field is StructuralField.CONFIGURATION_ARTIFACTS
                      and isinstance(value.subject.owner, NodeSubject)]
    containments = [value.subject.owner.runtime_id for value in diff.changes
                    if isinstance(value, ModifiedChange)
                    and isinstance(value.subject, FieldSubject)
                    and value.subject.field is StructuralField.RUNTIME_CONTAINMENT
                    and isinstance(value.subject.owner, RuntimeSubject)]
    if (len(diff.changes) != 3 or len(configurations) != 1 or len(containments) != 1
            or (len(added), len(removed)) not in ((1, 0), (0, 1))):
        return None
    mode = "add" if added else "remove"
    changed_node_id = (added or removed)[0]
    runtime_id = containments[0]
    if (set(current.graph.runtimes) != set(desired.graph.runtimes)
            or runtime_id not in current.graph.runtimes):
        return None
    current_runtime = current.graph.runtimes[runtime_id]
    desired_runtime = desired.graph.runtimes[runtime_id]
    if (current_runtime.management is None
            or current_runtime.management != desired_runtime.management):
        return None
    gateway_id = current_runtime.management.gateway_node_id
    if configurations[0] != gateway_id:
        return None
    try:
        ingress = _management_ingress(desired.graph, runtime_id)
        connector_id = ingress.connector_node_id
        current_gateway = current.graph.nodes[gateway_id]
        desired_gateway = desired.graph.nodes[gateway_id]
        connector = desired.graph.nodes[connector_id]
        changed = (desired.graph if mode == "add" else current.graph).nodes[changed_node_id]
    except (KeyError, ValueError, AttributeError):
        return None
    if (changed.runtime_id != runtime_id or current_gateway.runtime_id != runtime_id
            or desired_gateway.runtime_id != runtime_id or connector.runtime_id != runtime_id
            or _independent_verification(changed) or _independent_verification(desired_gateway)):
        return None
    changed_surface, _ = _selected_surface(changed)
    gateway_surface, _ = _selected_surface(desired_gateway, gateway=True)
    if (changed_surface is None or gateway_surface is None
            or NodeHealthReadKind.READINESS not in changed_surface.health_reads
            or changed.lifecycle.ownership is not ResourceOwnership.OWNED
            or changed.lifecycle.compute is not ResourcePersistence.EPHEMERAL):
        return None
    current_nodes = {key for key, value in current.graph.nodes.items() if value.runtime_id == runtime_id}
    desired_nodes = {key for key, value in desired.graph.nodes.items() if value.runtime_id == runtime_id}
    retained = sorted((current_nodes & desired_nodes) - {gateway_id, connector_id})
    if len(retained) != 1:
        return None
    retained_id = retained[0]
    retained_node = desired.graph.nodes[retained_id]
    retained_surface, _ = _selected_surface(retained_node)
    if (_independent_verification(retained_node) or retained_surface is None
            or NodeHealthReadKind.READINESS not in retained_surface.health_reads
            or current.graph.nodes[retained_id] != retained_node):
        return None
    if mode == "add":
        if current_nodes != {gateway_id, connector_id, retained_id}:
            return None
        if desired_nodes != current_nodes | {changed_node_id}:
            return None
        if (changed_node_id in current_runtime.children
                or desired_runtime.children.count(changed_node_id) != 1
                or tuple(value for value in desired_runtime.children if value != changed_node_id)
                != current_runtime.children):
            return None
    else:
        if desired_nodes != {gateway_id, connector_id, retained_id}:
            return None
        if current_nodes != desired_nodes | {changed_node_id}:
            return None
        if (changed_node_id in desired_runtime.children
                or current_runtime.children.count(changed_node_id) != 1
                or tuple(value for value in current_runtime.children if value != changed_node_id)
                != desired_runtime.children):
            return None
    return _ManagedUpdateShape(
        mode, runtime_id, gateway_id, connector_id, retained_id, changed_node_id,
    )


def _retained_update_eligible(before, after):
    """Shared replacement posture; relation owners separately constrain changes."""
    # Compiled Node.secret_deliveries includes active socket-derived deliveries.
    if before.secret_deliveries != after.secret_deliveries:
        return False
    try:
        for node in (before, after):
            if (_independent_verification(node)
                    or node.lifecycle.ownership is not ResourceOwnership.OWNED
                    or node.lifecycle.compute is not ResourcePersistence.EPHEMERAL
                    or node.lifecycle.data or node.runtime_authority_deliveries):
                return False
            selections = normalize_secret_selections(node.secret_deliveries, node.secret_selections)
            if any(type(value) is not SecretVersionSelection for value in selections):
                return False
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return False
    return True


def _retained_update_wrapper(current, desired, node_id):
    """Select the exact receiver for an eligible retained workload update."""
    before, after = current.graph.nodes[node_id], desired.graph.nodes[node_id]
    runtime = desired.graph.runtimes[after.runtime_id]
    surface, _ = _selected_surface(after)
    if (runtime.management is None or surface is None
            or NodeHealthReadKind.READINESS not in surface.health_reads
            or not _retained_update_eligible(before, after)):
        return None
    try:
        _, gateway, connector, _, _ = _pin_path(desired, after.runtime_id, PlanGraphSide.DESIRED_GRAPH)
        if node_id in (gateway.node_id, connector.node_id):
            return None
        wrapper = select_receiver_node_control_configuration_artifact(
            artifacts=before.configuration_artifacts,
            environment=before.public_environment + before.socket_environment,
            control_surfaces=before.block_spec.control_surfaces,
        )
        configuration = ReceiverNodeControlConfigurationCodec().decode_bytes(wrapper.content.encode("utf-8"))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return None
    if (configuration.target.node_id.value != node_id
            or configuration.target.runtime_id.value != after.runtime_id
            or configuration.target.provider_socket_name.value != surface.provider_socket_name.value):
        return None
    return wrapper


def _managed_configuration_update_shape(current, desired, diff):
    """Recognize one public app-file content change, preserving all other values."""
    if len(diff.changes) != 1:
        return False
    change = diff.changes[0]
    if (not isinstance(change, ModifiedChange)
            or not isinstance(change.subject, FieldSubject)
            or change.subject.field is not StructuralField.CONFIGURATION_ARTIFACTS
            or not isinstance(change.subject.owner, NodeSubject)):
        return False
    node_id = change.subject.owner.node_id
    before, after = current.graph.nodes[node_id], desired.graph.nodes[node_id]
    # Restore only the allowed field: every other graph value must still agree.
    restored = replace(after, configuration_artifacts=before.configuration_artifacts)
    if desired.graph.update_node(restored) != current.graph:
        return False
    wrapper = _retained_update_wrapper(current, desired, node_id)
    if wrapper is None:
        return False
    old = {value.artifact_id: value for value in before.configuration_artifacts}
    new = {value.artifact_id: value for value in after.configuration_artifacts}
    if old.keys() != new.keys():
        return False
    changed = [key for key in old if old[key] != new[key]]
    if len(changed) != 1 or changed[0] == wrapper.artifact_id:
        return False
    original, successor = old[changed[0]], new[changed[0]]
    return (original.media_type is ConfigurationMediaType.JSON
            and original.content != successor.content
            and replace(successor, content=original.content, source_digest=original.source_digest) == original)


def _managed_verifier_refresh_shape(current, desired, diff):
    """One exact receiver changes only its structurally valid public verifiers."""
    if len(diff.changes) != 1:
        return False
    change = diff.changes[0]
    if (not isinstance(change, ModifiedChange)
            or not isinstance(change.subject, FieldSubject)
            or change.subject.field is not StructuralField.CONFIGURATION_ARTIFACTS
            or not isinstance(change.subject.owner, NodeSubject)):
        return False
    node_id = change.subject.owner.node_id
    before, after = current.graph.nodes[node_id], desired.graph.nodes[node_id]
    if desired.graph.update_node(replace(after, configuration_artifacts=before.configuration_artifacts)) != current.graph:
        return False
    wrapper = _retained_update_wrapper(current, desired, node_id)
    if wrapper is None:
        return False
    old = {value.artifact_id: value for value in before.configuration_artifacts}
    new = {value.artifact_id: value for value in after.configuration_artifacts}
    if old.keys() != new.keys() or [key for key in old if old[key] != new[key]] != [wrapper.artifact_id]:
        return False
    successor = new[wrapper.artifact_id]
    if replace(successor, content=wrapper.content, source_digest=wrapper.source_digest) != wrapper:
        return False
    try:
        selected = select_receiver_node_control_configuration_artifact(
            artifacts=after.configuration_artifacts,
            environment=after.public_environment + after.socket_environment,
            control_surfaces=after.block_spec.control_surfaces,
        )
        codec = ReceiverNodeControlConfigurationCodec()
        original = codec.decode_bytes(wrapper.content.encode("utf-8"))
        refreshed = codec.decode_bytes(selected.content.encode("utf-8"))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return False
    # Full target equality includes workspace and receiver identity, not only placement.
    # Current signer/issuer authority remains an Operations admission obligation.
    return (original.target == refreshed.target
            and original.declaration == refreshed.declaration
            and original.verifiers != refreshed.verifiers)


def _managed_image_update_shape(current, desired, diff):
    """Recognize a pinned image transition; registered contract truth is external."""
    if len(diff.changes) != 1:
        return False
    change = diff.changes[0]
    if (not isinstance(change, ModifiedChange)
            or not isinstance(change.subject, FieldSubject)
            or change.subject.field is not StructuralField.NODE_METADATA
            or not isinstance(change.subject.owner, NodeSubject)):
        return False
    node_id = change.subject.owner.node_id
    before, after = current.graph.nodes[node_id], desired.graph.nodes[node_id]
    keys = {"product_identity", "product_descriptor_digest", "oci_image"}
    if (before.metadata.keys() != after.metadata.keys()
            or not keys <= before.metadata.keys()
            or any(before.metadata[key] == after.metadata[key] for key in keys)):
        return False
    restored = replace(after, metadata={**after.metadata,
        **{key: before.metadata[key] for key in keys}})
    if desired.graph.update_node(restored) != current.graph:
        return False
    references = []
    try:
        for node in (before, after):
            namespace, name, revision = node.metadata["product_identity"].split("/")
            identity = ProductIdentity(namespace, name, int(revision))
            ProductDescriptorDigest(node.metadata["product_descriptor_digest"])
            registry, path = node.metadata["oci_image"].split("/", 1)
            repository, digest = path.split("@")
            image = OciImageReference(registry, repository, digest)
            if (identity.key != node.metadata["product_identity"]
                    or image.execution_reference != node.metadata["oci_image"]):
                return False
            references.append((identity, image))
    except (ValueError, TypeError, AttributeError, OverflowError):
        return False
    (old_identity, old_image), (new_identity, new_image) = references
    if ((old_identity.namespace, old_identity.name) != (new_identity.namespace, new_identity.name)
            or (old_image.registry, old_image.repository) != (new_image.registry, new_image.repository)
            or old_image.digest == new_image.digest):
        return False
    return _retained_update_wrapper(current, desired, node_id) is not None


def _managed_secret_selection_update_shape(current, desired, diff):
    """One existing selected use changes version; all other graph values agree."""
    if len(diff.changes) != 1:
        return False
    change = diff.changes[0]
    if (not isinstance(change, ModifiedChange)
            or not isinstance(change.subject, FieldSubject)
            or change.subject.field is not StructuralField.SECRET_SELECTIONS
            or not isinstance(change.subject.owner, NodeSubject)):
        return False
    node_id = change.subject.owner.node_id
    before, after = current.graph.nodes[node_id], desired.graph.nodes[node_id]
    if desired.graph.update_node(replace(after, secret_selections=before.secret_selections)) != current.graph:
        return False
    if _retained_update_wrapper(current, desired, node_id) is None:
        return False
    # Canonical total coverage and equal deliveries fix the use keys and order.
    return sum(left != right for left, right in zip(
        before.secret_selections, after.secret_selections, strict=True)) == 1


def _same_image_product_successor(before, after):
    """Canonical references only; registered contract authority is external."""
    keys = {"product_identity", "product_descriptor_digest"}
    if (not keys <= before.metadata.keys() or not keys <= after.metadata.keys()
            or any(before.metadata[key] == after.metadata[key] for key in keys)
            or {**after.metadata, **{key: before.metadata[key] for key in keys}} != before.metadata):
        return False
    identities = []
    try:
        for node in (before, after):
            namespace, name, revision = node.metadata["product_identity"].split("/")
            identity = ProductIdentity(namespace, name, int(revision))
            ProductDescriptorDigest(node.metadata["product_descriptor_digest"])
            registry, path = node.metadata["oci_image"].split("/", 1)
            repository, digest = path.split("@")
            image = OciImageReference(registry, repository, digest)
            if (identity.key != node.metadata["product_identity"]
                    or image.execution_reference != node.metadata["oci_image"]):
                return False
            identities.append(identity)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
    return (identities[0].namespace, identities[0].name) == (identities[1].namespace, identities[1].name)


def _unique_receiver_routes(node):
    """Count every strict route candidate, including unchanged decoys."""
    candidates = []
    for artifact in node.configuration_artifacts:
        try:
            routes = ReceiverManagementRoutesCodec().decode_bytes(artifact.content.encode("utf-8"))
        except ReceiverManagementRoutesError:
            continue
        candidates.append((artifact, routes))
    if len(candidates) != 1 or candidates[0][0].media_type is not ConfigurationMediaType.JSON:
        return None
    return candidates[0]


def _managed_declaration_update_shape(current, desired, diff):
    """One health-only workload successor and its exact retained gateway route."""
    if len(diff.changes) != 4 or any(not isinstance(change, ModifiedChange)
            or not isinstance(change.subject, FieldSubject)
            or not isinstance(change.subject.owner, NodeSubject) for change in diff.changes):
        return None
    declarations = [change.subject.owner.node_id for change in diff.changes
                    if change.subject.field is StructuralField.BLOCK_SPECIFICATION]
    if len(declarations) != 1:
        return None
    node_id = declarations[0]
    before, after = current.graph.nodes[node_id], desired.graph.nodes[node_id]
    wrapper = _retained_update_wrapper(current, desired, node_id)
    if wrapper is None or not _same_image_product_successor(before, after):
        return None
    gateway_id = desired.graph.runtimes[after.runtime_id].management.gateway_node_id
    if {(change.subject.owner.node_id, change.subject.field) for change in diff.changes} != {
            (node_id, StructuralField.BLOCK_SPECIFICATION), (node_id, StructuralField.NODE_METADATA),
            (node_id, StructuralField.CONFIGURATION_ARTIFACTS),
            (gateway_id, StructuralField.CONFIGURATION_ARTIFACTS)}:
        return None
    old_gateway, new_gateway = current.graph.nodes[gateway_id], desired.graph.nodes[gateway_id]
    if not _retained_update_eligible(old_gateway, new_gateway):
        return None
    codec = ReceiverNodeControlConfigurationCodec()
    try:
        successor = select_receiver_node_control_configuration_artifact(
            artifacts=after.configuration_artifacts,
            environment=after.public_environment + after.socket_environment,
            control_surfaces=after.block_spec.control_surfaces)
        original = codec.decode_bytes(wrapper.content.encode("utf-8"))
        updated = codec.decode_bytes(successor.content.encode("utf-8"))
        gateway_configurations = []
        for gateway in (old_gateway, new_gateway):
            own_wrapper = select_receiver_node_control_configuration_artifact(
                artifacts=gateway.configuration_artifacts,
                environment=gateway.public_environment + gateway.socket_environment,
                control_surfaces=gateway.block_spec.control_surfaces)
            configured = codec.decode_bytes(own_wrapper.content.encode("utf-8"))
            readiness, _ = _selected_surface(gateway, gateway=True)
            if (readiness is None or configured.target.node_id.value != gateway_id
                    or configured.target.runtime_id.value != gateway.runtime_id
                    or configured.target.provider_socket_name != readiness.provider_socket_name):
                return None
            gateway_configurations.append((own_wrapper, configured))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return None
    if (original.target != updated.target or original.verifiers != updated.verifiers
            or original.declaration == updated.declaration
            or any(value.profile is not WorkloadNodeControlSurfaceDeclarationProfile.V2
                or value.surface.variables or NodeHealthReadKind.READINESS not in value.surface.health_reads
                for value in (original.declaration, updated.declaration))
            or replace(updated.declaration.surface, health_reads=original.declaration.surface.health_reads)
                != original.declaration.surface
            or replace(after.block_spec, control_surfaces=before.block_spec.control_surfaces) != before.block_spec
            or replace(successor, content=wrapper.content, source_digest=wrapper.source_digest) != wrapper
            or tuple(wrapper if item.artifact_id == successor.artifact_id else item
                for item in after.configuration_artifacts) != before.configuration_artifacts
            or gateway_configurations[0] != gateway_configurations[1]):
        return None
    old_routes, new_routes = _unique_receiver_routes(old_gateway), _unique_receiver_routes(new_gateway)
    if old_routes is None or new_routes is None:
        return None
    old_artifact, old_value = old_routes
    new_artifact, new_value = new_routes
    if (old_value.gateway_target != gateway_configurations[0][1].target
            or new_value.gateway_target != gateway_configurations[1][1].target
            or replace(new_artifact, content=old_artifact.content, source_digest=old_artifact.source_digest) != old_artifact):
        return None
    bindings = [value for value in old_value.targets if value.target == original.target]
    if len(bindings) != 1 or bindings[0].declaration != original.declaration:
        return None
    # Compare existing normalized bindings without constructing a hypothetical
    # aggregate that could exceed the route codec's independent byte bound.
    expected = tuple(replace(value, declaration=updated.declaration) if value == bindings[0] else value
        for value in old_value.targets)
    if new_value.targets != expected:
        return None
    restored_gateway = replace(new_gateway, configuration_artifacts=tuple(
        old_artifact if item.artifact_id == new_artifact.artifact_id else item
        for item in new_gateway.configuration_artifacts))
    restored_workload = replace(after, block_spec=before.block_spec, metadata=before.metadata,
        configuration_artifacts=before.configuration_artifacts)
    if desired.graph.update_node(restored_gateway).update_node(restored_workload) != current.graph:
        return None
    return node_id, gateway_id, after.runtime_id


def _declaration_update_plan(base, shape):
    """Add only workload-install -> gateway-install; preserve every base edge."""
    node_id, gateway_id, runtime_id = shape
    workload = _matching_activity(base, ReconcileNode, lambda value: value.target.node_id == node_id)
    gateway = _matching_activity(base, ReconcileNode, lambda value: value.target.node_id == gateway_id)
    stages = tuple(_matching_activity(base, ObserveManagementBootstrap,
        lambda value: value.target.runtime_id == runtime_id
        and value.target.graph_side is PlanGraphSide.DESIRED_GRAPH and value.stage is stage)
        for stage in (ManagementBootstrapStage.GATEWAY_LOCAL_READY,
            ManagementBootstrapStage.CONNECTOR_CONNECTED, ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH))
    health = _matching_activity(base, ObserveNodeHealth, lambda value: value.node_id == node_id
        and value.target.runtime_id == runtime_id and value.target.graph_side is PlanGraphSide.DESIRED_GRAPH)
    if workload is None or gateway is None or health is None or any(value is None for value in stages):
        return None
    try:
        parents = {value.predecessor for value in gateway.dependencies} | {workload.activity_id}
        ordered = ActivityPlan(tuple(replace(value, dependencies=tuple(ActivityDependency(parent)
            for parent in sorted(parents))) if value == gateway else value for value in base.activities))
    except InvalidActivityPlan:
        return None
    ancestors = {}
    for activity in ordered.activities:
        ancestors[activity.activity_id] = {item.predecessor for item in activity.dependencies}
        for item in activity.dependencies:
            ancestors[activity.activity_id].update(ancestors[item.predecessor])
    chain = (workload, gateway, *stages, health)
    if (health.activity_id in ancestors[gateway.activity_id]
            or any(left.activity_id not in ancestors[right.activity_id] for left, right in zip(chain, chain[1:]))):
        return None
    return ordered


def _review_blocked(plan, diff):
    if not diff.changes:
        return plan
    review = _review_activity(diff.changes[0].subject, ReviewReason.UNSUPPORTED_CHANGE)
    if any(value.activity_id == review.activity_id for value in plan.activities):
        return plan
    return ActivityPlan(plan.activities + (review,))


def _matching_activity(plan, operation_type, predicate):
    matches = [value for value in plan.activities
               if isinstance(value.operation, operation_type) and predicate(value.operation)]
    return matches[0] if len(matches) == 1 else None


def compile_managed_update_activity_plan(current: ValidatedGraph, desired: ValidatedGraph) -> ActivityPlan:
    """Derive closed managed updates, including coupled declaration/route changes."""
    _require_pair(current, desired)
    diff = diff_graphs(current, desired)
    base = compile_graph_activity_plan(current, desired)
    if (not diff.changes or _managed_configuration_update_shape(current, desired, diff)
            or _managed_image_update_shape(current, desired, diff)
            or _managed_secret_selection_update_shape(current, desired, diff)
            or _managed_verifier_refresh_shape(current, desired, diff)):
        return base
    declaration_shape = _managed_declaration_update_shape(current, desired, diff)
    if declaration_shape is not None:
        if not base.ready_for_execution:
            return base
        return _declaration_update_plan(base, declaration_shape) or _review_blocked(base, diff)
    shape = _managed_update_shape(current, desired, diff)
    if shape is None or not base.ready_for_execution:
        return _review_blocked(base, diff) if shape is None else base

    activities = {value.activity_id: value for value in base.activities}
    dependencies = {
        value.activity_id: {item.predecessor for item in value.dependencies}
        for value in base.activities
    }
    reconcile = _matching_activity(
        base, ReconcileNode, lambda value: value.target.node_id == shape.gateway_id,
    )
    local = _matching_activity(
        base, ObserveManagementBootstrap,
        lambda value: value.target.runtime_id == shape.runtime_id
        and value.stage is ManagementBootstrapStage.GATEWAY_LOCAL_READY,
    )
    if reconcile is None or local is None:
        return _review_blocked(base, diff)

    try:
        target = _pin_path(desired, shape.runtime_id, PlanGraphSide.DESIRED_GRAPH)[0]
        retained = desired.graph.nodes[shape.retained_node_id]
        retained_surface, _ = _selected_surface(retained)
        if retained_surface is None:
            return _review_blocked(base, diff)

        if shape.mode == "add":
            start = _matching_activity(
                base, StartNode,
                lambda value: value.target.node_id == shape.changed_node_id,
            )
            connected = _matching_activity(
                base, ObserveManagementBootstrap,
                lambda value: value.target.runtime_id == shape.runtime_id
                and value.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED,
            )
            path = _matching_activity(
                base, ObserveManagementBootstrap,
                lambda value: value.target.runtime_id == shape.runtime_id
                and value.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH,
            )
            if start is None or connected is None or path is None:
                return _review_blocked(base, diff)
            dependencies[reconcile.activity_id].add(start.activity_id)
        else:
            stop = _matching_activity(
                base, StopNode,
                lambda value: value.target.node_id == shape.changed_node_id,
            )
            remove = _matching_activity(
                base, RemoveNodeResource,
                lambda value: value.target.node_id == shape.changed_node_id,
            )
            if stop is None or remove is None:
                return _review_blocked(base, diff)
            connected = _observation_activity(
                ObserveManagementBootstrap(target, ManagementBootstrapStage.CONNECTOR_CONNECTED),
                (local.activity_id,),
            )
            path = _observation_activity(
                ObserveManagementBootstrap(target, ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH),
                (connected.activity_id,),
            )
            for activity in (connected, path):
                activities[activity.activity_id] = activity
                dependencies[activity.activity_id] = {
                    item.predecessor for item in activity.dependencies
                }

        retained_health = _observation_activity(
            ObserveNodeHealth(
                target, shape.retained_node_id,
                retained_surface.provider_socket_name.value,
                _health_kind(retained_surface),
            ),
            (path.activity_id,),
        )
        activities[retained_health.activity_id] = retained_health
        dependencies[retained_health.activity_id] = {path.activity_id}
        if shape.mode == "remove":
            dependencies[stop.activity_id].add(retained_health.activity_id)
    except (KeyError, ValueError, TypeError, AttributeError):
        return _review_blocked(base, diff)

    return ActivityPlan(tuple(
        replace(activity, dependencies=tuple(
            ActivityDependency(value) for value in sorted(dependencies[identity])
        ))
        for identity, activity in activities.items()
    ))


def compile_graph_activity_plan(current: ValidatedGraph, desired: ValidatedGraph) -> ActivityPlan:
    """Derive management observation obligations from two complete graph values."""
    _require_pair(current, desired)
    diff = diff_graphs(current, desired)
    structural = compile_activity_plan(diff)
    if not structural.activities or all(isinstance(value.operation, ReviewChange) for value in structural.activities):
        return structural
    # A retained-runtime cutover needs its own accepted policy; do not invent one.
    if any(isinstance(value.subject, FieldSubject) and value.subject.field is StructuralField.RUNTIME_MANAGEMENT
           for value in diff.changes):
        return structural
    graph = desired.graph
    activities = {value.activity_id: value for value in structural.activities}
    dependencies = {value.activity_id: {item.predecessor for item in value.dependencies} for value in structural.activities}
    replacements = {}
    waits = {value.operation.target.node_id: value for value in structural.activities if isinstance(value.operation, WaitForHealthy)}
    starts = {value.operation.target.node_id: value for value in structural.activities if isinstance(value.operation, (StartNode, ReconcileNode))}
    allocations = {value.operation.target.ingress_id: value for value in structural.activities if isinstance(value.operation, AllocatePublicIngress)}
    runtime_starts = {value.operation.target.runtime_id for value in structural.activities if type(value.operation) is StartRuntime}
    node_starts = {value.operation.target.node_id for value in structural.activities if type(value.operation) is StartNode}
    current_ingresses = {value.ingress_id for value in current.graph.public_ingresses}

    def add(activity):
        activities[activity.activity_id] = activity
        dependencies.setdefault(activity.activity_id, set()).update(value.predecessor for value in activity.dependencies)
        return activity

    def refuse(subject, reason=ReviewReason.UNSUPPORTED_CHANGE):
        return add(_review_activity(subject, reason))

    for runtime_id, runtime in sorted(graph.runtimes.items()):
        if runtime.management is None:
            continue
        gateway_id = runtime.management.gateway_node_id
        ingress = next(value for value in graph.public_ingresses if value.ingress_id == runtime.management.management_ingress_id)
        connector_id = ingress.connector_node_id
        # Freshness is a graph-pair/structural-plan property, never a caller mode
        # or inferred execution result. Reconcile cannot substitute for creation.
        fresh = (runtime_id not in current.graph.runtimes
                 and gateway_id not in current.graph.nodes and connector_id not in current.graph.nodes
                 and ingress.ingress_id not in current_ingresses
                 and runtime_id in runtime_starts and gateway_id in node_starts and connector_id in node_starts
                 and ingress.ingress_id in allocations)
        owned_waits = {key: value for key, value in waits.items() if key in graph.nodes and graph.nodes[key].runtime_id == runtime_id}
        sdk = {}
        for node_id, wait in owned_waits.items():
            node = graph.nodes[node_id]
            if _independent_verification(node):
                refuse(NodeSubject(node_id))
            if node_id == gateway_id:
                continue
            eligible = any(value.health_reads for value in node.block_spec.control_surfaces)
            if eligible:
                selected, reason = _selected_surface(node)
                if selected is None:
                    refuse(NodeSubject(node_id), reason)
                else:
                    sdk[node_id] = selected
            elif node_id != connector_id:
                # Keep the ordinary obligation, but do not let empty checks or an
                # unimplemented private verification route imply managed success.
                refuse(NodeSubject(node_id))
        path_needed = bool(sdk) or connector_id in owned_waits or ingress.ingress_id in allocations
        local_needed = path_needed or gateway_id in owned_waits
        if not local_needed:
            continue
        readiness, reason = _selected_surface(graph.nodes[gateway_id], gateway=True)
        if readiness is None:
            refuse(NodeSubject(gateway_id), reason)
            continue
        pinned = None
        try:
            pinned = _pin_path(desired, runtime_id, PlanGraphSide.DESIRED_GRAPH)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            pass
        if pinned is None:
            refuse(RuntimeSubject(runtime_id))
            continue
        target = pinned[0]
        gateway_wait = owned_waits.get(gateway_id)
        gateway_dependencies = dependencies[gateway_wait.activity_id].copy() if gateway_wait else set()
        if gateway_id in starts:
            gateway_dependencies.add(starts[gateway_id].activity_id)
        connected = path = None
        if fresh:
            allocation = allocations[ingress.ingress_id]
            if gateway_wait:
                dependencies[allocation.activity_id].discard(gateway_wait.activity_id)
            dependencies[allocation.activity_id].add(starts[gateway_id].activity_id)
            dependencies[starts[connector_id].activity_id].add(allocation.activity_id)
            connection_dependencies = {starts[connector_id].activity_id, allocation.activity_id}
            connected = add(_observation_activity(ObserveManagementBootstrap(target, ManagementBootstrapStage.CONNECTOR_CONNECTED), connection_dependencies))
            path = add(_observation_activity(ObserveManagementBootstrap(target, ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH), connection_dependencies))
            readiness = add(_observation_activity(ObserveManagementBootstrap(target, ManagementBootstrapStage.GATEWAY_INGRESS_READY),
                gateway_dependencies | {path.activity_id}))
        else:
            readiness = add(_observation_activity(ObserveManagementBootstrap(target, ManagementBootstrapStage.GATEWAY_LOCAL_READY), gateway_dependencies))
            if connector_activity := starts.get(connector_id):
                dependencies[connector_activity.activity_id].add(readiness.activity_id)
            if allocation := allocations.get(ingress.ingress_id):
                # Independent checks remain separate, review-blocked obligations.
                if gateway_wait:
                    dependencies[allocation.activity_id].discard(gateway_wait.activity_id)
                dependencies[allocation.activity_id].add(readiness.activity_id)
            if path_needed:
                connection_dependencies = {readiness.activity_id}
                if connector_id in starts:
                    connection_dependencies.add(starts[connector_id].activity_id)
                if ingress.ingress_id in allocations:
                    connection_dependencies.add(allocations[ingress.ingress_id].activity_id)
                connected = add(_observation_activity(ObserveManagementBootstrap(target, ManagementBootstrapStage.CONNECTOR_CONNECTED), connection_dependencies))
                path = add(_observation_activity(ObserveManagementBootstrap(target, ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH), (connected.activity_id,)))
        for node_id, wait in owned_waits.items():
            node = graph.nodes[node_id]
            replacement = None
            if node_id == gateway_id:
                replacement = readiness
            elif node_id in sdk:
                selected = sdk[node_id]
                replacement = add(_observation_activity(ObserveNodeHealth(target, node_id,
                    selected.provider_socket_name.value, _health_kind(selected)),
                    dependencies[wait.activity_id] | ({readiness.activity_id, connected.activity_id} if fresh else {path.activity_id})))
            elif node_id == connector_id and not any(value.health_reads for value in node.block_spec.control_surfaces):
                replacement = connected
            if _independent_verification(node):
                if replacement:
                    dependencies[wait.activity_id].add(replacement.activity_id)
                if path:
                    dependencies[wait.activity_id].add(path.activity_id)
            elif replacement is not None:
                replacements[wait.activity_id] = replacement.activity_id
                del activities[wait.activity_id]

    finished = []
    for identity, activity in activities.items():
        parents = {replacements.get(value, value) for value in dependencies[identity]}
        finished.append(replace(activity, dependencies=tuple(ActivityDependency(value) for value in sorted(parents))))
    return ActivityPlan(tuple(finished))
