"""Real Core health values for the separate immutable preparation store laws."""
from dataclasses import replace
import hashlib
import importlib
import importlib.util

import rfc8785

from control_plane_kit_core.node_control import (
    NodeControlCanonicalization, NodeControlGraphReference,
    NodeControlGraphReferenceRole, NodeControlTarget, workload_node_control_audience,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.node_health_reads import (
    NodeHealthReadRequest, DelegatedWorkloadNodeHealthReadGrant,
    DelegatedWorkloadNodeHealthReadGrantProfile,
)
from control_plane_kit_core.node_health_transit import (
    DelegatedGatewayNodeHealthReadTransitGrant,
    DelegatedGatewayNodeHealthReadTransitGrantProfile,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import (
    ActivityPlan, ObserveNodeHealth, PlanGraphSide, compile_graph_activity_plan,
)
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.runtime_management_targets import project_management_health_target
from tests.runtime_management_fixtures import bootstrap_management_graph

VALUE_MODULE = "control_plane_kit_operations.health_effect_preparations"
STORE_MODULE = "control_plane_kit_operations.postgres.health_effect_preparation_store"
RELATION = "cpk_health_effect_preparations"


def wire_identity(identity):
    # Independent contract oracle, not a call to the implementation under test.
    return "health_" + hashlib.sha256(rfc8785.dumps({
        "domain": "cpk.health-effect-attempt.v1", "identity": identity.descriptor(),
    })).hexdigest()


def ref(role, value):
    return NodeControlGraphReference(role, value)


class HealthEffectPreparationFixture:
    def api(self, name=VALUE_MODULE):
        self.assertIsNotNone(importlib.util.find_spec(name), "#1851 immutable health preparation is missing")
        return importlib.import_module(name)

    def health_context(self, side=PlanGraphSide.DESIRED_GRAPH):
        graph = bootstrap_management_graph(self)
        desired = validate_graph(graph)
        empty = validate_graph(DeploymentGraph(graph.name))
        plan = compile_graph_activity_plan(empty, desired)
        activity = next(item for item in plan.activities
            if type(item.operation) is ObserveNodeHealth and item.operation.node_id == "api")
        if side is PlanGraphSide.BASE_GRAPH:
            # Equal content deliberately cannot decide authored side. This retained
            # plan fixture tests storage identity, not full admission/scheduling.
            operation = replace(activity.operation, target=replace(activity.operation.target, graph_side=side))
            plan = ActivityPlan(tuple(replace(item, operation=operation) if item.activity_id == activity.activity_id
                else item for item in plan.activities))
            activity = plan.activity(activity.activity_id)
            current = desired
        else:
            current = empty
        current.require_valid()
        desired.require_valid()
        projection = project_management_health_target(plan, activity.activity_id, activity.operation, current, desired)
        return plan, activity, current, desired, projection

    def material(self, *, identity=None, side=PlanGraphSide.DESIRED_GRAPH, request_id="health-request-a"):
        plan, activity, current, desired, projection = self.health_context(side)
        identity = identity or EffectAttemptIdentity(RunId("run-a"), activity.activity_id.value, 1)
        target = NodeControlTarget(
            ref(NodeControlGraphReferenceRole.WORKSPACE, "workspace-a"),
            ref(NodeControlGraphReferenceRole.GRAPH_REVISION,
                "health-base" if side is PlanGraphSide.BASE_GRAPH else "health-desired"),
            ref(NodeControlGraphReferenceRole.NODE, projection.target_node_id),
            ref(NodeControlGraphReferenceRole.PROVIDER_SOCKET, projection.target_provider_socket_name),
        )
        declaration = WorkloadNodeControlSurfaceDeclaration(
            projection.target_surface, WorkloadNodeControlSurfaceDeclarationProfile.V2)
        request = NodeHealthReadRequest(target,
            ref(NodeControlGraphReferenceRole.RUNTIME, activity.operation.target.runtime_id),
            projection.target_health_kind, declaration.identity(), request_id)
        common = dict(canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1,
            issuer="cpk-server", target=request.target, runtime_id=request.runtime_id,
            kind=request.kind, declaration_identity=request.declaration_identity,
            request_id=request.request_id, request_digest=request.canonical_digest(),
            issued_at=1_700_000_000, not_before=1_700_000_000, expires_at=1_700_000_060)
        transit = DelegatedGatewayNodeHealthReadTransitGrant(
            profile=DelegatedGatewayNodeHealthReadTransitGrantProfile.V1,
            purpose=DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
            key_id="health-transit", attempt_id=wire_identity(identity),
            gateway_node_id=ref(NodeControlGraphReferenceRole.NODE, projection.gateway_node_id),
            jti="transit-jti-a", **common)
        workload = DelegatedWorkloadNodeHealthReadGrant(
            profile=DelegatedWorkloadNodeHealthReadGrantProfile.V1,
            purpose=DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
            key_id="health-workload", audience=workload_node_control_audience(target),
            jti="workload-jti-a", **common)
        self.assertEqual(request.canonical_digest(), transit.request_digest)
        self.assertEqual(request.canonical_digest(), workload.request_digest)
        return dict(identity=identity, request_fingerprint="a" * 64,
            original_event_id="health-original-event", base_realized_projection_id="health-base-projection",
            desired_realized_projection_id="health-desired-projection",
            transit_key_registration_id="dkey_" + "a" * 64,
            workload_key_registration_id="dkey_" + "b" * 64,
            transit_authorization_id="suse_" + "a" * 64,
            workload_authorization_id="suse_" + "b" * 64,
            request=request, transit_grant=transit, workload_grant=workload)

    def preparation(self, **options):
        values = self.material(**options)  # valid existing Core context before missing-feature guard
        return self.api().HealthEffectPreparationRecord(**values)

    def assert_safe(self, error, *canaries):
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = str(error) + repr(error)
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)


def pair_for_request(record, request):
    """Keep the unsigned pair internally lawful while changing its target."""
    shared = dict(target=request.target, runtime_id=request.runtime_id, kind=request.kind,
        declaration_identity=request.declaration_identity, request_id=request.request_id,
        request_digest=request.canonical_digest())
    return replace(record, request=request,
        transit_grant=replace(record.transit_grant, **shared),
        workload_grant=replace(record.workload_grant,
            audience=workload_node_control_audience(request.target), **shared))


def forged_copy(value, *, subclass=False, **changes):
    from dataclasses import fields
    target = type("Derived" + type(value).__name__, (type(value),), {}) if subclass else type(value)
    copy = object.__new__(target)
    for item in fields(value):
        object.__setattr__(copy, item.name, changes.get(item.name, getattr(value, item.name)))
    return copy
