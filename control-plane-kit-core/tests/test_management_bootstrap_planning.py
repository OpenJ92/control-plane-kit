"""#1833: actual graph-pair compiler laws, not a second test compiler."""
from dataclasses import dataclass, fields, replace
import hashlib
import json
import unittest

import rfc8785
import control_plane_kit_core as core
from control_plane_kit_core.algebra import (
    ApplicationBlock, BlockSockets, BlockSpec, DeploymentTopology, DockerRuntime,
    ProviderSocket, RequirementSocket, SocketConnection,
)
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeHealthReadKind,
    WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, ActivityPlanDescriptorCodec, AllocatePublicIngress,
    InvalidActivityPlan, NodeTarget, NoCompensationRequired, PlannedActivity,
    ReconcileNode, ReconcileRuntime, RemovePublicIngress, ReviewChange, ReviewReason,
    StartNode, StartRuntime, StopNode, StopRuntime, WaitForHealthy,
    compile_activity_plan, compensation_for_operation,
)
from control_plane_kit_core.planning.codec import (
    activity_operation_descriptor, activity_operation_from_descriptor,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference, NamedPublicIngress, PublicIngressTarget,
)
from control_plane_kit_core.runtime_management import (
    GatewayTransitDeclaration, GatewayTransitProtocol, RuntimeManagement,
)
from control_plane_kit_core.topology import (
    DeploymentGraph, GraphDescriptorCodec, NodeSubject, RuntimeSubject,
    compile_topology, diff_graphs, validate_graph,
)
from control_plane_kit_core.topology.codec import GenericBlockSpecCodec
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from tests.test_graph_codec import PureImplementation
from tests import test_node_control_surfaces as variable_fixtures


def surface(socket="control", kinds=(NodeHealthReadKind.READINESS,), *, variable=False):
    return WorkloadNodeControlSurfaceDescriptor(
        NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, socket),
        (variable_fixtures.WorkloadNodeControlSurfaceTests().variable("mode"),) if variable else (),
        kinds,
    )


def block(name, surfaces=(), *, gateway=False, checks=(), requirements=()):
    names = {"control", *(value.provider_socket_name.value for value in surfaces)}
    if gateway:
        names.add("transit")
    capabilities = (CapabilityName.NODE_CONTROLLABLE,) if surfaces else ()
    if any(value.health_reads for value in surfaces):
        capabilities += (CapabilityName.HEALTH_CHECKABLE,)
    spec = BlockSpec(
        name, capabilities=capabilities, control_surfaces=surfaces,
        verification=VerificationContract(checks),
        gateway_transit=GatewayTransitDeclaration("transit", GatewayTransitProtocol.NODE_HEALTH_READ_V1) if gateway else None,
    )
    return ApplicationBlock(
        spec, PureImplementation("test-service", {name_: f"http://{name}.{name_}" for name_ in sorted(names)}),
        BlockSockets(providers=tuple(ProviderSocket(name_, Protocol.HTTP) for name_ in sorted(names)), requirements=requirements),
    )


def topology(*, prefix="", workload=True, workload_surfaces=None, checks=(),
             gateway_surfaces=None, gateway_checks=(), connector_surfaces=(), cycle=False):
    runtime_id = prefix + "runtime"
    gateway_id, connector_id, workload_id = (prefix + value for value in ("gateway", "connector", "workload"))
    gateway = block(gateway_id, (surface(),) if gateway_surfaces is None else gateway_surfaces,
                    gateway=True, checks=gateway_checks,
                    requirements=(RequirementSocket("service", Protocol.HTTP, ("SERVICE_URL",)),) if cycle else ())
    connector = block(connector_id, connector_surfaces)
    children = [gateway, connector]
    if workload:
        children.append(block(workload_id, (surface(),) if workload_surfaces is None else workload_surfaces, checks=checks))
    if cycle:
        children.append(SocketConnection(workload_id, "control", gateway_id, "service", edge_id=prefix + "actual-service-dependency"))
    ingress = NamedPublicIngress(prefix + "management", IngressAuthorityReference("management-authority"),
        PublicIngressTarget(gateway_id, "transit"), connector_id, prefix + "management.example.invalid")
    return DeploymentTopology("bootstrap", DockerRuntime(runtime_id=runtime_id, children=tuple(children),
        management=RuntimeManagement(gateway_id, ingress.ingress_id)), public_ingresses=(ingress,))


def graph(**options):
    return compile_topology(topology(**options))


def empty():
    return DeploymentGraph("bootstrap")


def predecessors(plan, activity):
    """Read the compiler's existing DAG; never construct expected activities."""
    by_id = {value.activity_id: value for value in plan.activities}
    pending = [value.predecessor for value in activity.dependencies]
    result = set()
    while pending:
        key = pending.pop()
        if key not in result:
            result.add(key)
            pending.extend(value.predecessor for value in by_id[key].dependencies)
    return result


@dataclass(frozen=True)
class CustomSpec(BlockSpec):
    marker: str = "custom"


class CustomCodec:
    variant = "management-test-custom"
    spec_type = CustomSpec

    def encode(self, spec):
        return {**GenericBlockSpecCodec().encode(spec), "variant": self.variant, "marker": spec.marker}

    def decode(self, descriptor):
        generic = GenericBlockSpecCodec().decode({key: value for key, value in descriptor.items() if key != "marker"})
        return CustomSpec(**{item.name: getattr(generic, item.name) for item in fields(BlockSpec)}, marker=descriptor["marker"])


class ManagementBootstrapPlanningTests(unittest.TestCase):
    def api(self, name):
        value = getattr(core, name, None)
        self.assertIsNotNone(value, f"#1833 public contract {name} is missing")
        return value

    def compile(self, current, desired, *, codec=None):
        options = {} if codec is None else {"codec": codec}
        left, right = validate_graph(current, **options), validate_graph(desired, **options)
        self.assertTrue(left.valid, left.descriptor())
        self.assertTrue(right.valid, right.descriptor())
        return self.api("compile_graph_activity_plan")(left, right)

    def find(self, plan, operation_type, *, node=None, stage=None, runtime=None):
        found = [value for value in plan.activities if isinstance(value.operation, operation_type)
                 and (node is None or getattr(value.operation, "node_id", getattr(value.operation.target, "node_id", None)) == node)
                 and (stage is None or value.operation.stage.value == stage)
                 and (runtime is None or value.operation.target.runtime_id == runtime)]
        self.assertEqual(len(found), 1, [(type(value.operation).__name__, value.activity_id.value) for value in found])
        return found[0]

    def before(self, plan, first, second):
        self.assertIn(first.activity_id, predecessors(plan, second))

    def review(self, plan, subject, reason):
        matching = [value for value in plan.activities if isinstance(value.operation, ReviewChange)
                    and value.operation.target.subject == subject and value.operation.reason is reason]
        self.assertEqual(len(matching), 1)
        expected = "review-management:" + hashlib.sha256(
            b"control-plane-kit.management-review.v1\0" + rfc8785.dumps(activity_operation_descriptor(matching[0].operation))).hexdigest()
        self.assertEqual(matching[0].activity_id.value, expected)
        self.assertFalse(plan.ready_for_execution)
        return matching[0]

    def test_actual_initial_compiler_orders_distinct_stages_and_preserves_graph(self):
        desired = graph()
        original = GraphDescriptorCodec().encode(desired)
        plan = self.compile(empty(), desired)
        bootstrap = self.api("ObserveManagementBootstrap")
        local = self.find(plan, bootstrap, stage="gateway-local-ready")
        connected = self.find(plan, bootstrap, stage="connector-connected")
        path = self.find(plan, bootstrap, stage="authenticated-management-path")
        health = self.find(plan, self.api("ObserveNodeHealth"), node="workload")
        sequence = (self.find(plan, StartNode, node="gateway"), local,
                    self.find(plan, AllocatePublicIngress), self.find(plan, StartNode, node="connector"), connected, path, health)
        for first, second in zip(sequence, sequence[1:]):
            self.before(plan, first, second)
            self.assertNotIn(second.activity_id, predecessors(plan, first))
        self.assertNotIn(path.activity_id, predecessors(plan, self.find(plan, StartNode, node="workload")))
        self.assertEqual(GraphDescriptorCodec().encode(desired), original)
        self.assertEqual(desired.edges, {})
        self.assertTrue(plan.ready_for_execution)

    def test_retained_infrastructure_observes_without_restarting_or_reallocating(self):
        plan = self.compile(graph(workload=False), graph())
        self.assertEqual({value.operation.target.node_id for value in plan.activities if isinstance(value.operation, StartNode)}, {"workload"})
        self.assertFalse(any(isinstance(value.operation, (StartRuntime, AllocatePublicIngress)) for value in plan.activities))
        health = self.find(plan, self.api("ObserveNodeHealth"), node="workload")
        path = self.find(plan, self.api("ObserveManagementBootstrap"), stage="authenticated-management-path")
        self.before(plan, path, health)

    def test_equal_name_only_and_unmanaged_pairs_preserve_exact_legacy_plans(self):
        managed = graph(gateway_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)),))
        for desired in (managed, replace(managed, name="renamed")):
            self.assertEqual(self.compile(managed, desired).activities, ())
        unbound = replace(managed, runtimes={"runtime": replace(managed.runtimes["runtime"], management=None)})
        legacy = compile_activity_plan(diff_graphs(validate_graph(empty()), validate_graph(unbound)))
        self.assertEqual(self.compile(empty(), unbound), legacy)

    def test_independent_runtimes_have_no_synthetic_cross_runtime_barrier(self):
        left, right = topology(prefix="left-"), topology(prefix="right-")
        desired = compile_topology(DeploymentTopology("bootstrap", DockerRuntime(runtime_id="outer", children=(left.root, right.root)),
                                  public_ingresses=left.public_ingresses + right.public_ingresses))
        plan = self.compile(empty(), desired)
        bootstrap = self.api("ObserveManagementBootstrap")
        left_path = self.find(plan, bootstrap, runtime="left-runtime", stage="authenticated-management-path")
        right_path = self.find(plan, bootstrap, runtime="right-runtime", stage="authenticated-management-path")
        left_health = self.find(plan, self.api("ObserveNodeHealth"), node="left-workload")
        self.assertNotIn(right_path.activity_id, predecessors(plan, left_health))
        self.before(plan, left_path, left_health)

    def test_real_service_dependency_cycle_is_rejected_by_actual_compiler(self):
        desired = graph(cycle=True)
        self.assertTrue(validate_graph(desired).valid)
        compiler = self.api("compile_graph_activity_plan")
        with self.assertRaises(InvalidActivityPlan):
            compiler(validate_graph(empty()), validate_graph(desired))

    def test_complete_replacement_keeps_old_teardown_and_desired_checks(self):
        current, desired = graph(prefix="old-"), graph(prefix="new-")
        plan = self.compile(current, desired)
        connector_stop = self.find(plan, StopNode, node="old-connector")
        removal = self.find(plan, RemovePublicIngress)
        gateway_stop = self.find(plan, StopNode, node="old-gateway")
        self.before(plan, connector_stop, removal)
        self.before(plan, removal, gateway_stop)
        bootstrap = self.api("ObserveManagementBootstrap")
        for value in plan.activities:
            if isinstance(value.operation, (bootstrap, self.api("ObserveNodeHealth"))):
                self.assertEqual(value.operation.target.runtime_id, "new-runtime")
                self.assertEqual(value.operation.target.graph_side.value, "desired-graph")
        self.assertEqual(removal.operation.target.ingress_id, "old-management")

    def test_retained_management_retarget_preserves_structural_review_without_restart(self):
        current = graph()
        alternate = replace(current.public_ingresses[0], ingress_id="alternate", hostname="alternate.example.invalid")
        current = replace(current, public_ingresses=current.public_ingresses + (alternate,))
        desired = replace(current, runtimes={"runtime": replace(current.runtimes["runtime"], management=RuntimeManagement("gateway", "alternate"))})
        structural = compile_activity_plan(diff_graphs(validate_graph(current), validate_graph(desired)))
        plan = self.compile(current, desired)
        self.assertEqual(plan, structural)
        self.assertFalse(any(isinstance(value.operation, (StartRuntime, StopRuntime, ReconcileRuntime)) for value in plan.activities))
        self.assertFalse(plan.ready_for_execution)

    def test_sdk_health_kind_is_selected_once_without_socket_or_kind_downgrade(self):
        for kinds, expected in (((NodeHealthReadKind.LIVENESS,), NodeHealthReadKind.LIVENESS),
                                ((NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS), NodeHealthReadKind.READINESS)):
            with self.subTest(kinds=kinds):
                plan = self.compile(empty(), graph(workload_surfaces=(surface(kinds=kinds),)))
                request = self.find(plan, self.api("ObserveNodeHealth"), node="workload").operation
                self.assertIs(request.health_kind, expected)
                self.assertEqual(request.provider_socket_name, "control")
                self.assertEqual(request.transport.value, "runtime-gateway")
        ambiguous = graph(workload_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)), surface("other")))
        plan = self.compile(empty(), ambiguous)
        self.review(plan, NodeSubject("workload"), ReviewReason.AMBIGUOUS_CHANGE)
        self.assertFalse(any(isinstance(value.operation, self.api("ObserveNodeHealth")) and value.operation.node_id == "workload" for value in plan.activities))

    def test_gateway_own_readiness_is_independent_from_transit_and_never_upgraded(self):
        for surfaces, reason in (((surface(kinds=(NodeHealthReadKind.LIVENESS,)),), ReviewReason.UNSUPPORTED_CHANGE),
                                 ((surface(), surface("other")), ReviewReason.AMBIGUOUS_CHANGE)):
            desired = graph(gateway_surfaces=surfaces)
            self.assertTrue(validate_graph(desired).valid)
            self.review(self.compile(empty(), desired), NodeSubject("gateway"), reason)
        current, desired = empty(), graph()
        plan = self.compile(current, desired)
        request = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-local-ready").operation
        resolved = self.api("resolve_management_observation")(request, validate_graph(current), validate_graph(desired), expected_operation=request)
        self.assertEqual(resolved.gateway_readiness_socket, "control")
        self.assertEqual(resolved.ingress.target.provider_socket, "transit")
        self.assertEqual(resolved.gateway_node.node_id, "gateway")

    def test_mixed_sdk_and_verification_keeps_both_obligations_and_refuses_private_fallback(self):
        check = HttpCheck(check_id="expected-body", provider_socket="control", path="/", expected_body_sha256="a" * 64)
        current = graph(checks=(check,))
        desired_topology = topology(checks=(replace(check, expected_body_sha256="b" * 64),))
        consumer = block("consumer", (surface(),), requirements=(RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",)),))
        connection = SocketConnection("workload", "control", "consumer", "upstream", edge_id="consumer.upstream")
        desired = compile_topology(replace(desired_topology, root=replace(desired_topology.root,
            children=desired_topology.root.children + (consumer, connection))))
        plan = self.compile(current, desired)
        verify = self.find(plan, WaitForHealthy, node="workload")
        sdk = self.find(plan, self.api("ObserveNodeHealth"), node="workload")
        reconcile = self.find(plan, ReconcileNode, node="workload")
        self.before(plan, reconcile, verify)
        self.before(plan, reconcile, sdk)
        consumer_start = self.find(plan, StartNode, node="consumer")
        self.before(plan, verify, consumer_start)
        self.before(plan, sdk, consumer_start)
        structural = compile_activity_plan(diff_graphs(validate_graph(current), validate_graph(desired)))
        self.assertEqual(verify.activity_id, self.find(structural, WaitForHealthy, node="workload").activity_id)
        self.review(plan, NodeSubject("workload"), ReviewReason.UNSUPPORTED_CHANGE)
        self.assertEqual(desired.node("workload").block_spec.verification.checks[0].expected_body_sha256, "b" * 64)
        self.assertIsNot(verify.operation, sdk.operation)

    def test_gateway_independent_verification_does_not_gate_its_own_ingress(self):
        desired = graph(gateway_checks=(HttpCheck(check_id="gateway-body", provider_socket="control", path="/", expected_body_sha256="a" * 64),))
        plan = self.compile(empty(), desired)
        verify = self.find(plan, WaitForHealthy, node="gateway")
        local = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-local-ready")
        allocation = self.find(plan, AllocatePublicIngress)
        self.before(plan, local, allocation)
        self.assertNotIn(verify.activity_id, predecessors(plan, allocation))
        self.review(plan, NodeSubject("gateway"), ReviewReason.UNSUPPORTED_CHANGE)
        structural = compile_activity_plan(diff_graphs(validate_graph(empty()), validate_graph(desired)))
        self.assertEqual(verify.activity_id, self.find(structural, WaitForHealthy, node="gateway").activity_id)

    def test_non_sdk_and_variable_only_nodes_keep_verification_without_invented_sdk_requests(self):
        check = HttpCheck(check_id="independent-check", provider_socket="control", path="/")
        for surfaces in ((), (surface(kinds=(), variable=True),)):
            with self.subTest(variable=bool(surfaces)):
                desired = graph(workload_surfaces=surfaces, checks=(check,))
                self.assertTrue(validate_graph(desired).valid)
                plan = self.compile(empty(), desired)
                self.find(plan, WaitForHealthy, node="workload")
                self.review(plan, NodeSubject("workload"), ReviewReason.UNSUPPORTED_CHANGE)
                self.assertFalse(any(isinstance(value.operation, self.api("ObserveNodeHealth")) and value.operation.node_id == "workload" for value in plan.activities))

    def test_empty_non_sdk_health_is_not_fabricated_success(self):
        plan = self.compile(empty(), graph(workload_surfaces=()))
        self.review(plan, NodeSubject("workload"), ReviewReason.UNSUPPORTED_CHANGE)
        self.assertFalse(plan.ready_for_execution)

    def test_connector_sdk_health_follows_connection_and_path_not_the_reverse(self):
        plan = self.compile(empty(), graph(connector_surfaces=(surface(),)))
        health = self.find(plan, self.api("ObserveNodeHealth"), node="connector")
        connected = self.find(plan, self.api("ObserveManagementBootstrap"), stage="connector-connected")
        path = self.find(plan, self.api("ObserveManagementBootstrap"), stage="authenticated-management-path")
        self.before(plan, connected, path)
        self.before(plan, path, health)
        self.assertNotIn(health.activity_id, predecessors(plan, connected))

    def test_request_wire_hashes_and_compensation_commit_exact_graph_relation_and_stage(self):
        desired = graph()
        plan = self.compile(empty(), desired)
        request_activity = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-local-ready")
        request = request_activity.operation
        expected_graph = hashlib.sha256(b"control-plane-kit.management-graph.v1\0" + rfc8785.dumps(GraphDescriptorCodec().encode(desired))).hexdigest()
        relation = {"runtime_id": "runtime", "management": desired.runtimes["runtime"].management.descriptor(),
                    "ingress": desired.public_ingresses[0].descriptor(), "gateway_transit": desired.node("gateway").block_spec.gateway_transit.descriptor(),
                    "gateway_readiness": {"provider_socket_name": "control", "health_kind": "readiness"}}
        expected_relation = hashlib.sha256(b"control-plane-kit.management-relation.v1\0" + rfc8785.dumps(relation)).hexdigest()
        wire = {"kind": "observe-management-bootstrap", "target": {"kind": "management", "runtime_id": "runtime", "graph_side": "desired-graph",
                "graph_digest": expected_graph, "relation_digest": expected_relation}, "stage": "gateway-local-ready"}
        self.assertEqual(activity_operation_descriptor(request), wire)
        self.assertEqual(activity_operation_from_descriptor(wire), request)
        self.assertEqual(request_activity.activity_id.value, "observe:" + hashlib.sha256(b"control-plane-kit.management-activity.v1\0" + rfc8785.dumps(wire)).hexdigest())
        self.assertEqual(compensation_for_operation(request), NoCompensationRequired())
        codec = ActivityPlanDescriptorCodec()
        self.assertEqual(codec.decode(codec.encode(plan)), plan)
        self.assertEqual(codec.encode(plan)["version"], 1)
        self.assertEqual(self.compile(empty(), desired), plan)

    def test_stage_side_kind_and_transport_are_exact_semantic_identity_not_freshness(self):
        desired = graph(workload_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS)),))
        plan = self.compile(empty(), desired)
        request = self.find(plan, self.api("ObserveNodeHealth"), node="workload").operation
        wire = activity_operation_descriptor(request)
        self.assertEqual(set(wire), {"kind", "target", "node_id", "provider_socket_name", "health_kind", "transport"})
        self.assertEqual(wire["transport"], "runtime-gateway")
        side = self.api("PlanGraphSide")
        base = replace(request, target=replace(request.target, graph_side=side.BASE_GRAPH))
        liveness = replace(request, health_kind=NodeHealthReadKind.LIVENESS)
        self.assertNotEqual(request, base)
        self.assertNotEqual(activity_operation_descriptor(request), activity_operation_descriptor(base))
        self.assertNotEqual(request, liveness)
        resolver = self.api("resolve_management_observation")
        for candidate in (base, liveness):
            with self.assertRaises(ValueError):
                resolver(candidate, validate_graph(desired), validate_graph(desired), expected_operation=request)
        resolved = resolver(base, validate_graph(desired), validate_graph(desired), expected_operation=base)
        self.assertEqual(resolved.workload_node.node_id, "workload")
        self.assertEqual(resolved.workload_surface.provider_socket_name.value, "control")

    def test_resolver_rejects_rebound_connector_socket_relation_runtime_or_graph(self):
        desired = graph()
        plan = self.compile(empty(), desired)
        request = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-local-ready").operation
        resolver = self.api("resolve_management_observation")
        error_type = self.api("ManagementObservationError")
        target = request.target
        for candidate in (replace(request, target=replace(target, runtime_id="other")),
                          replace(request, target=replace(target, relation_digest="f" * 64)),
                          replace(request, target=replace(target, graph_digest="f" * 64)),
                          replace(request, stage=self.api("ManagementBootstrapStage").CONNECTOR_CONNECTED)):
            with self.subTest(candidate=type(candidate).__name__), self.assertRaises(error_type) as caught:
                resolver(candidate, validate_graph(empty()), validate_graph(desired), expected_operation=request)
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)
        for changed in (replace(desired, name="different"),
                        graph(gateway_surfaces=(surface("own-readiness"),)),
                        replace(desired, public_ingresses=(replace(desired.public_ingresses[0], hostname="changed.example.invalid"),))):
            with self.assertRaises(error_type):
                resolver(request, validate_graph(empty()), validate_graph(changed), expected_operation=request)

    def test_self_matching_candidates_still_require_graph_derived_relation_and_health_selection(self):
        desired = graph()
        plan = self.compile(empty(), desired)
        request = self.find(plan, self.api("ObserveNodeHealth"), node="workload").operation
        resolver = self.api("resolve_management_observation")
        error_type = self.api("ManagementObservationError")
        for candidate in (replace(request, target=replace(request.target, relation_digest="f" * 64)),
                          replace(request, target=replace(request.target, runtime_id="other")),
                          replace(request, health_kind=NodeHealthReadKind.LIVENESS),
                          replace(request, provider_socket_name="missing")):
            with self.subTest(field=activity_operation_descriptor(candidate)), self.assertRaises(error_type) as caught:
                resolver(candidate, validate_graph(empty()), validate_graph(desired), expected_operation=candidate)
            self.assertLess(len(str(caught.exception)), 200)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
        # Rebind the ingress to a different, genuinely present connector. Update
        # the graph pin so rejection must reach the still-stale relation pin.
        authored = topology()
        changed = compile_topology(replace(authored,
            root=replace(authored.root, children=authored.root.children + (block("replacement-connector"),)),
            public_ingresses=(replace(authored.public_ingresses[0], connector_node_id="replacement-connector"),)))
        self.assertTrue(validate_graph(changed).valid)
        digest = hashlib.sha256(b"control-plane-kit.management-graph.v1\0" + rfc8785.dumps(GraphDescriptorCodec().encode(changed))).hexdigest()
        stale_relation = replace(request, target=replace(request.target, graph_digest=digest))
        with self.assertRaises(error_type) as caught:
            resolver(stale_relation, validate_graph(empty()), validate_graph(changed), expected_operation=stale_relation)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_public_outer_and_operation_codecs_refuse_bad_observation_without_candidate_context(self):
        plan = self.compile(empty(), graph())
        request = self.find(plan, self.api("ObserveNodeHealth"), node="workload").operation
        codec = ActivityPlanDescriptorCodec()
        public = codec.encode(ActivityPlan((PlannedActivity(ActivityId("check"), request),)))
        canary = "PRIVATE-CANDIDATE-" * 1000
        for key, value in (("health_kind", canary), ("transport", canary), ("provider_socket_name", canary), ("extra", canary)):
            malformed = json.loads(json.dumps(public))
            malformed["activities"][0]["operation"][key] = value
            for decode, data in ((codec.decode, malformed), (activity_operation_from_descriptor, malformed["activities"][0]["operation"])):
                with self.subTest(key=key), self.assertRaises(ValueError) as caught:
                    decode(data)
                self.assertLess(len(str(caught.exception)), 200)
                self.assertNotIn("PRIVATE-CANDIDATE", str(caught.exception) + repr(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
        with self.assertRaises((TypeError, ValueError)):
            replace(request, health_kind="readiness")
        with self.assertRaises((TypeError, ValueError)):
            replace(request.target, graph_side="desired-graph")
        for changes in ({"runtime_id": "x" * 129}, {"graph_digest": "F" * 64}, {"relation_digest": "f" * 63}, {"runtime_id": "cf_tunnel_do_not_store"}):
            with self.assertRaises((TypeError, ValueError)):
                replace(request.target, **changes)

    def test_supplied_custom_codec_pins_exact_material_and_incompatible_language_refuses(self):
        desired = graph()
        original = desired.node("workload").block_spec
        custom = CustomSpec(**{item.name: getattr(original, item.name) for item in fields(BlockSpec)})
        desired = replace(desired, nodes={**desired.nodes, "workload": replace(desired.node("workload"), block_spec=custom)})
        codec = GraphDescriptorCodec((CustomCodec(),))
        plan = self.compile(empty(), desired, codec=codec)
        request = self.find(plan, self.api("ObserveNodeHealth"), node="workload").operation
        expected = hashlib.sha256(b"control-plane-kit.management-graph.v1\0" + rfc8785.dumps(codec.encode(desired))).hexdigest()
        self.assertEqual(request.target.graph_digest, expected)
        resolver = self.api("resolve_management_observation")
        resolved = resolver(request, validate_graph(empty(), codec=codec), validate_graph(desired, codec=codec), expected_operation=request)
        self.assertIsInstance(resolved.workload_node.block_spec, CustomSpec)
        incompatible = self.api("compile_graph_activity_plan")(validate_graph(empty()), validate_graph(desired, codec=codec))
        self.assertFalse(incompatible.ready_for_execution)
        self.assertTrue(all(isinstance(value.operation, ReviewChange) for value in incompatible.activities))

    def test_observation_graph_byte_limit_is_planning_refusal_not_graph_invalidity(self):
        desired = graph()
        desired = replace(desired, runtimes={"runtime": replace(desired.runtimes["runtime"], metadata={"padding": "x" * 1_048_576})})
        self.assertTrue(validate_graph(desired).valid)
        plan = self.compile(empty(), desired)
        self.review(plan, RuntimeSubject("runtime"), ReviewReason.UNSUPPORTED_CHANGE)
        self.assertFalse(any(isinstance(value.operation, (self.api("ObserveNodeHealth"), self.api("ObserveManagementBootstrap"))) for value in plan.activities))

    def test_legacy_descriptor_bytes_and_meaning_are_unchanged(self):
        # Literal old wire protects compatibility, independent of the new encoder.
        wire = {"schema": "control-plane-kit.activity-plan", "version": 1, "activities": [
            {"activity_id": "legacy-health", "operation": {"kind": "wait-for-healthy", "target": {"kind": "node", "node_id": "workload"}},
             "dependencies": [], "risk": "low", "impact": "non-destructive", "compensation": {"kind": "not-required"}}]}
        codec = ActivityPlanDescriptorCodec()
        plan = codec.decode(wire)
        self.assertEqual(plan.activities[0].operation, WaitForHealthy(NodeTarget("workload")))
        self.assertEqual(codec.dumps(plan), json.dumps(wire, sort_keys=True, separators=(",", ":")))
        self.assertEqual(codec.encode(plan), wire)


if __name__ == "__main__":
    unittest.main()
