"""Pure pinned-plan health target selection; no observation or dispatch authority."""

from dataclasses import dataclass

from control_plane_kit_core.node_control import WorkloadNodeControlSurfaceDescriptor
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, ManagementObservationError, ObserveNodeHealth,
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
    operation: ObserveNodeHealth
    gateway_node_id: str
    gateway_transit_provider_socket_name: str
    ingress: NamedPublicIngress
    workload_surface: WorkloadNodeControlSurfaceDescriptor


_MISMATCH = "management health target does not match pinned plan context"


def project_management_health_target(
    plan: ActivityPlan,
    activity_id: ActivityId,
    candidate: ObserveNodeHealth,
    current: ValidatedGraph,
    desired: ValidatedGraph,
) -> ManagementHealthTargetProjection:
    """Select one health activity from an accepted plan and its exact snapshots.

    The caller supplies the accepted record's plan value and pinned graph pair.
    This function does not authenticate acceptance, record identity, freshness,
    request/run/attempt authority or a worker fence. It neither observes health
    nor authorizes dispatch. Bootstrap stages are outside this health projection.
    """
    if (type(plan) is not ActivityPlan or type(activity_id) is not ActivityId
            or type(candidate) is not ObserveNodeHealth):
        raise ManagementHealthTargetProjectionError(_MISMATCH)

    expected = None
    try:
        expected = plan.activity(activity_id).operation
    except KeyError:
        pass
    if type(expected) is not ObserveNodeHealth:
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

    return ManagementHealthTargetProjection(
        activity_id=activity_id,
        operation=resolved.operation,
        gateway_node_id=resolved.gateway_node.node_id,
        gateway_transit_provider_socket_name=(
            resolved.gateway_node.block_spec.gateway_transit.provider_socket_name
        ),
        ingress=resolved.ingress,
        workload_surface=resolved.workload_surface,
    )
