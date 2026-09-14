"""#1843: pinned plan selection and symbolic health targets, never authority."""

from dataclasses import FrozenInstanceError, dataclass, fields, replace
import importlib
import importlib.util
import unittest

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeHealthReadKind,
)
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, ObserveManagementBootstrap, ObserveNodeHealth,
    PlanGraphSide, StartNode, compile_graph_activity_plan,
)
from control_plane_kit_core.topology import DeploymentGraph, GraphDescriptorCodec, validate_graph
from control_plane_kit_core.topology.codec import GenericBlockSpecCodec
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.types import Protocol
from control_plane_kit_operations.runtime_management_admission import (
    runtime_management_execution_is_unsupported,
)
from tests.runtime_management_fixtures import bootstrap_management_graph


@dataclass(frozen=True)
class MarkedSpec(BlockSpec):
    marker: str = "pinned-custom-language"


class MarkedSpecCodec:
    variant = "operations-management-target-test"
    spec_type = MarkedSpec

    def encode(self, spec):
        return {**GenericBlockSpecCodec().encode(spec), "variant": self.variant, "marker": spec.marker}

    def decode(self, descriptor):
        generic = GenericBlockSpecCodec().decode({key: value for key, value in descriptor.items() if key != "marker"})
        return MarkedSpec(**{item.name: getattr(generic, item.name) for item in fields(BlockSpec)}, marker=descriptor["marker"])


class RuntimeManagementTargetTests(unittest.TestCase):
    def api(self):
        # Assert missing behavior after collection and valid fixture construction.
        name = "control_plane_kit_operations.runtime_management_targets"
        self.assertIsNotNone(importlib.util.find_spec(name), "#1843 management health target projection is missing")
        module = importlib.import_module(name)
        return module.project_management_health_target, module.ManagementHealthTargetProjectionError

    def graph(self, *, peer=False):
        graph = bootstrap_management_graph(self)
        gateway, workload = graph.node("gateway"), graph.node("api")
        gateway_surface = replace(gateway.block_spec.control_surfaces[0], provider_socket_name=
            NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, "gateway-ready"))
        workload_surface = replace(workload.block_spec.control_surfaces[0], provider_socket_name=
            NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, "sdk-health"),
            health_reads=(NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS))

        def sockets(node, names, spec):
            return replace(node, block_spec=spec,
                sockets=BlockSockets(providers=tuple(ProviderSocket(name, Protocol.HTTP) for name in names)),
                endpoints={name: Endpoint(LiteralAddress(f"http://{node.node_id}-{name}:8000"), Protocol.HTTP) for name in names})

        gateway = sockets(gateway, ("transit-health", "gateway-ready", "data"), replace(gateway.block_spec,
            gateway_transit=replace(gateway.block_spec.gateway_transit, provider_socket_name="transit-health"),
            control_surfaces=(gateway_surface,)))
        workload = sockets(workload, ("sdk-health", "data"), replace(workload.block_spec, control_surfaces=(workload_surface,)))
        ingress = replace(graph.public_ingresses[0], target=replace(graph.public_ingresses[0].target,
            provider_socket="transit-health"))
        nodes = {**graph.nodes, "gateway": gateway, "api": workload}
        runtime = graph.runtimes["docker"]
        if peer:
            nodes["peer"] = sockets(replace(workload, node_id="peer"), ("sdk-health", "data"),
                replace(workload.block_spec, role_id="peer"))
            runtime = replace(runtime, children=(*runtime.children, "peer"))
        graph = replace(graph, nodes=nodes, runtimes={"docker": runtime}, public_ingresses=(ingress,))
        validate_graph(graph).require_valid()
        return graph

    def context(self, *, peer=False, codec=None, graph=None):
        graph = self.graph(peer=peer) if graph is None else graph
        options = {} if codec is None else {"codec": codec}
        current = validate_graph(DeploymentGraph(graph.name), **options)
        desired = validate_graph(graph, **options)
        current.require_valid()
        desired.require_valid()
        plan = compile_graph_activity_plan(current, desired)
        self.assertTrue(plan.ready_for_execution)
        activity = next(value for value in plan.activities
            if type(value.operation) is ObserveNodeHealth and value.operation.node_id == "api")
        return plan, activity, current, desired

    def with_operation(self, plan, activity, operation):
        # Deliberately forged plan material tests Core's rederivation, not approval.
        return ActivityPlan(tuple(replace(value, operation=operation) if value.activity_id == activity.activity_id
            else value for value in plan.activities))

    def refuse(self, project, error, plan, identity, candidate, current, desired):
        with self.assertRaises(error) as caught:
            project(plan, identity, candidate, current, desired)
        self.assertEqual(str(caught.exception), "management health target does not match pinned plan context")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_edge_free_nondefault_sockets_are_exact_frozen_references(self):
        plan, activity, current, desired = self.context()
        self.assertFalse(desired.graph.edges)
        project, _ = self.api()
        result = project(plan, activity.activity_id, activity.operation, current, desired)
        self.assertEqual(result.activity_id, activity.activity_id)
        self.assertEqual(result.operation, activity.operation)
        self.assertEqual(result.gateway_node_id, "gateway")
        self.assertEqual(result.gateway_transit_provider_socket_name, "transit-health")
        self.assertEqual(result.ingress, desired.graph.public_ingresses[0])
        self.assertEqual(result.ingress.connector_node_id, "connector")
        self.assertEqual(result.workload_surface, desired.graph.node("api").block_spec.control_surfaces[0])
        self.assertEqual(result.workload_surface.provider_socket_name.value, "sdk-health")
        self.assertIs(result.operation.health_kind, NodeHealthReadKind.READINESS)
        with self.assertRaises(FrozenInstanceError):
            result.gateway_node_id = "peer"
        # Projection does not grant the pending management execution capability.
        self.assertTrue(runtime_management_execution_is_unsupported(current.graph, desired.graph, plan))

    def test_activity_selection_rejects_missing_nonhealth_bootstrap_and_same_runtime_peer(self):
        plan, activity, current, desired = self.context(peer=True)
        peer = next(value for value in plan.activities
            if type(value.operation) is ObserveNodeHealth and value.operation.node_id == "peer")
        bootstrap = next(value for value in plan.activities if type(value.operation) is ObserveManagementBootstrap)
        start = next(value for value in plan.activities if type(value.operation) is StartNode)
        project, error = self.api()
        for identity, candidate in (
            (ActivityId("missing-health-activity"), activity.operation),
            (start.activity_id, activity.operation),
            (bootstrap.activity_id, activity.operation),
            (bootstrap.activity_id, bootstrap.operation),
            (peer.activity_id, activity.operation),
            (activity.activity_id, peer.operation),
        ):
            with self.subTest(identity=identity.value, kind=type(candidate).__name__):
                self.refuse(project, error, plan, identity, candidate, current, desired)

    def test_candidate_cannot_substitute_side_digest_relation_runtime_socket_or_kind(self):
        plan, activity, current, desired = self.context()
        operation = activity.operation
        candidates = (
            replace(operation, target=replace(operation.target, graph_side=PlanGraphSide.BASE_GRAPH)),
            replace(operation, target=replace(operation.target, graph_digest="0" * 64)),
            replace(operation, target=replace(operation.target, relation_digest="0" * 64)),
            replace(operation, target=replace(operation.target, runtime_id="other")),
            replace(operation, provider_socket_name="data"),
            replace(operation, health_kind=NodeHealthReadKind.LIVENESS),
        )
        project, error = self.api()
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.refuse(project, error, plan, activity.activity_id, candidate, current, desired)

    def test_self_matching_plan_material_still_requires_exact_graph_pins_and_health_surface(self):
        plan, activity, current, desired = self.context()
        operation = activity.operation
        candidates = (
            replace(operation, target=replace(operation.target, graph_digest="0" * 64)),
            replace(operation, target=replace(operation.target, relation_digest="0" * 64)),
            replace(operation, target=replace(operation.target, runtime_id="other")),
            replace(operation, node_id="missing-node"),
            replace(operation, provider_socket_name="data"),
            replace(operation, health_kind=NodeHealthReadKind.LIVENESS),
        )
        forged_plans = tuple(self.with_operation(plan, activity, value) for value in candidates)
        project, error = self.api()
        for candidate, forged in zip(candidates, forged_plans):
            with self.subTest(candidate=candidate):
                self.refuse(project, error, forged, activity.activity_id, candidate, current, desired)

    def test_equal_graphs_preserve_explicit_side_and_do_not_use_node_presence(self):
        plan, activity, _, desired = self.context()
        base_operation = replace(activity.operation, target=replace(activity.operation.target, graph_side=PlanGraphSide.BASE_GRAPH))
        base_plan = self.with_operation(plan, activity, base_operation)
        project, error = self.api()
        self.refuse(project, error, plan, activity.activity_id, base_operation, desired, desired)
        base = project(base_plan, activity.activity_id, base_operation, desired, desired)
        wanted = project(plan, activity.activity_id, activity.operation, desired, desired)
        self.assertIs(base.operation.target.graph_side, PlanGraphSide.BASE_GRAPH)
        self.assertIs(wanted.operation.target.graph_side, PlanGraphSide.DESIRED_GRAPH)
        self.assertNotEqual(base.operation.target, wanted.operation.target)
        # Both graphs contain api; changed selected material must not select current.
        changed = validate_graph(replace(desired.graph, name="changed-material"))
        changed.require_valid()
        self.refuse(project, error, plan, activity.activity_id, activity.operation, desired, changed)

    def test_connector_rebinding_cannot_reuse_stale_relation_with_new_graph_digest(self):
        plan, activity, current, desired = self.context()
        old = desired.graph.node("connector")
        graph = replace(desired.graph,
            nodes={key: value for key, value in desired.graph.nodes.items() if key != "connector"} | {
                "connector-two": replace(old, node_id="connector-two", block_spec=replace(old.block_spec, role_id="connector-two"))},
            runtimes={"docker": replace(desired.graph.runtimes["docker"], children=("api", "gateway", "connector-two"))},
            public_ingresses=(replace(desired.graph.public_ingresses[0], connector_node_id="connector-two"),))
        rebound_plan, rebound, _, rebound_graph = self.context(graph=graph)
        self.assertNotEqual(activity.operation.target.relation_digest, rebound.operation.target.relation_digest)
        stale = replace(activity.operation, target=replace(activity.operation.target, graph_digest=rebound.operation.target.graph_digest))
        forged = self.with_operation(plan, activity, stale)
        project, error = self.api()
        self.refuse(project, error, forged, activity.activity_id, stale, current, rebound_graph)
        result = project(rebound_plan, rebound.activity_id, rebound.operation, current, rebound_graph)
        self.assertEqual(result.ingress.connector_node_id, "connector-two")

    def test_own_codec_is_preserved_and_changed_custom_material_or_incompatible_pair_refuses(self):
        graph = self.graph()
        spec = graph.node("api").block_spec
        custom = MarkedSpec(**{item.name: getattr(spec, item.name) for item in fields(BlockSpec)})
        graph = replace(graph, nodes={**graph.nodes, "api": replace(graph.node("api"), block_spec=custom)})
        codec = GraphDescriptorCodec((MarkedSpecCodec(),))
        plan, activity, current, desired = self.context(graph=graph, codec=codec)
        changed = validate_graph(replace(graph, nodes={**graph.nodes, "api": replace(graph.node("api"), block_spec=replace(custom, marker="changed"))}), codec=codec)
        changed.require_valid()
        project, error = self.api()
        self.assertEqual(project(plan, activity.activity_id, activity.operation, current, desired).operation, activity.operation)
        self.refuse(project, error, plan, activity.activity_id, activity.operation, current, changed)
        self.refuse(project, error, plan, activity.activity_id, activity.operation,
            validate_graph(DeploymentGraph(graph.name)), desired)

    def test_malformed_nominal_inputs_and_snapshot_failure_are_bounded(self):
        plan, activity, current, desired = self.context()
        project, error = self.api()
        for args in (
            ("candidate-secret-plan", activity.activity_id, activity.operation, current, desired),
            (plan, "candidate-secret-activity", activity.operation, current, desired),
            (plan, activity.activity_id, "candidate-secret-operation", current, desired),
            (plan, activity.activity_id, activity.operation, "candidate-secret-snapshot", desired),
            (plan, activity.activity_id, activity.operation, current, None),
        ):
            with self.subTest(kind=tuple(type(value).__name__ for value in args)):
                self.refuse(project, error, *args)
