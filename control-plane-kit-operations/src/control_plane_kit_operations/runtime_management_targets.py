"""Pure pinned-plan health target selection; no observation or dispatch authority."""

from dataclasses import dataclass

from control_plane_kit_core.node_control import NodeHealthReadKind, WorkloadNodeControlSurfaceDescriptor
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, ManagementBootstrapStage, ManagementObservationError,
    ObserveManagementBootstrap, ObserveNodeHealth,
    resolve_management_observation,
)
from control_plane_kit_core.public_ingress import NamedPublicIngress
from control_plane_kit_core.topology import ValidatedGraph


class ManagementHealthTargetProjectionError(ValueError):
    """The selected plan activity and graph-pinned candidate do not agree."""


@dataclass(frozen=True)
class ManagementHealthTargetProjection:
    """Symbolic references only; constructing this value never grants authority."""

    activity_id: ActivityId
    operation: ObserveNodeHealth | ObserveManagementBootstrap
    gateway_node_id: str
    gateway_transit_provider_socket_name: str
    ingress: NamedPublicIngress
    target_node_id: str
    target_provider_socket_name: str
    target_health_kind: NodeHealthReadKind
    target_surface: WorkloadNodeControlSurfaceDescriptor


_MISMATCH = "management health target does not match pinned plan context"


def is_signed_management_health_operation(operation: object) -> bool:
    """Classify the signed transport family; this supplies no authority."""
    return (type(operation) is ObserveNodeHealth
        or (type(operation) is ObserveManagementBootstrap and operation.stage in (
            ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH,
            ManagementBootstrapStage.GATEWAY_INGRESS_READY,
        )))


def project_management_health_target(
    plan: ActivityPlan,
    activity_id: ActivityId,
    candidate: ObserveNodeHealth | ObserveManagementBootstrap,
    current: ValidatedGraph,
    desired: ValidatedGraph,
) -> ManagementHealthTargetProjection:
    """Select one health activity from an accepted plan and its exact snapshots.

    The caller supplies the accepted record's plan value and pinned graph pair.
    This function does not authenticate acceptance, record identity, freshness,
    request/run/attempt authority or a worker fence. It neither observes health
    nor authorizes dispatch. Native connector reads have no signed target.
    """
    if (type(plan) is not ActivityPlan or type(activity_id) is not ActivityId
            or not is_signed_management_health_operation(candidate)):
        raise ManagementHealthTargetProjectionError(_MISMATCH)

    expected = None
    try:
        expected = plan.activity(activity_id).operation
    except KeyError:
        pass
    if not is_signed_management_health_operation(expected):
        raise ManagementHealthTargetProjectionError(_MISMATCH)

    resolved = None
    try:
        resolved = resolve_management_observation(
            candidate, current, desired, expected_operation=expected,
        )
    except ManagementObservationError:
        pass
    if resolved is None:
        # Raise outside the candidate-bearing lookup/resolution exception context.
        raise ManagementHealthTargetProjectionError(_MISMATCH)

    if type(resolved.operation) is ObserveNodeHealth:
        node_id = resolved.workload_node.node_id
        socket = resolved.operation.provider_socket_name
        health_kind = resolved.operation.health_kind
        surface = resolved.workload_surface
    else:
        node_id = resolved.gateway_node.node_id
        socket = resolved.gateway_readiness_socket
        health_kind = NodeHealthReadKind.READINESS
        surfaces = tuple(surface for surface in resolved.gateway_node.block_spec.control_surfaces
            if surface.provider_socket_name.value == socket and health_kind in surface.health_reads)
        if len(surfaces) != 1:
            raise ManagementHealthTargetProjectionError(_MISMATCH)
        surface = surfaces[0]

    return ManagementHealthTargetProjection(
        activity_id=activity_id,
        operation=resolved.operation,
        gateway_node_id=resolved.gateway_node.node_id,
        gateway_transit_provider_socket_name=(
            resolved.gateway_node.block_spec.gateway_transit.provider_socket_name
        ),
        ingress=resolved.ingress,
        target_node_id=node_id,
        target_provider_socket_name=socket,
        target_health_kind=health_kind,
        target_surface=surface,
    )
