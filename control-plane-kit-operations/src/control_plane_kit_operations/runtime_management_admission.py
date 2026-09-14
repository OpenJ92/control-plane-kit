"""Pure refusal policy until managed runtime execution has an accepted transport."""

from control_plane_kit_core.planning import ActivityPlan, compile_activity_plan
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    GraphDescriptorCodec,
    diff_graphs,
    validate_graph,
)


def runtime_management_execution_is_unsupported(
    current: DeploymentGraph,
    desired: DeploymentGraph,
    plan: ActivityPlan | None = None,
    *,
    codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
) -> bool:
    """Check both snapshots; only a proven, congruent empty plan is exempt.

    Omitting ``plan`` means an activity is being attempted directly. Such an
    attempt never inherits the planning no-op exception.
    """

    if not any(_has_management_material(graph) for graph in (current, desired)):
        return False
    if plan is None or plan.activities:
        return True
    validated_current = validate_graph(current, codec=codec)
    validated_desired = validate_graph(desired, codec=codec)
    if not validated_current.valid or not validated_desired.valid:
        return True
    canonical_plan = compile_activity_plan(
        diff_graphs(validated_current, validated_desired)
    )
    return bool(canonical_plan.activities)


def _has_management_material(graph: DeploymentGraph) -> bool:
    return any(runtime.management is not None for runtime in graph.runtimes.values()) or any(
        node.block_spec.gateway_transit is not None or node.block_spec.control_surfaces
        for node in graph.nodes.values()
    )
