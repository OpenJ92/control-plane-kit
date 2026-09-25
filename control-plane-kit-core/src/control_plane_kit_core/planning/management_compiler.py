"""Pure graph-pair management planning and pinned request resolution.

Requests describe required observations. They neither grant authority nor prove
readiness, successful transport, or freshness of an execution attempt.
"""
from dataclasses import dataclass, replace
import hashlib

from control_plane_kit_core.node_control import NodeHealthReadKind, WorkloadNodeControlSurfaceDescriptor
from control_plane_kit_core.planning.activity_plan import (
    ActivityDependency, ActivityId, ActivityPlan, AllocatePublicIngress,
    ChangeTarget, PlannedActivity, ReconcileNode, ReviewChange, ReviewReason,
    RiskLevel, StartNode, StartRuntime, WaitForHealthy,
)
from control_plane_kit_core.planning.codec import activity_operation_descriptor
from control_plane_kit_core.planning.compiler import compile_activity_plan
from control_plane_kit_core.planning.management_observations import (
    ManagementBootstrapStage, ManagementObservationError, ManagementObservationTarget,
    ObserveManagementBootstrap, ObserveNodeHealth, PlanGraphSide, _bounded_wire,
)
from control_plane_kit_core.public_ingress import NamedPublicIngress
from control_plane_kit_core.topology.changes import FieldSubject, StructuralField
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
