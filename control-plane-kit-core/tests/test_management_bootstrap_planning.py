"""#1833: actual graph-pair compiler laws, not a second test compiler."""
from dataclasses import dataclass, fields, replace
import hashlib
import json
import unittest

import rfc8785
import control_plane_kit_core as core
from control_plane_kit_core.secret_selections import UnselectedSecretUse
from control_plane_kit_core.algebra import (
    ApplicationBlock, BlockSockets, BlockSpec, DeploymentTopology, DockerRuntime,
    ProviderSocket, RequirementSocket, SocketConnection,
)
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.secrets import SecretEnvironmentDelivery, SecretReference, SecretUseIntent
from control_plane_kit_core.lifecycle import (
    ResourceLifecycle, ResourceOwnership, ResourcePersistence,
)
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeHealthReadKind,
    WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.planning import (
    ActivityId, ActivityPlan, ActivityPlanDescriptorCodec, AllocatePublicIngress,
    InvalidActivityPlan, NodeTarget, NoCompensationRequired, PlannedActivity,
    ReconcileNode, ReconcileRuntime, RemoveNodeResource, RemovePublicIngress, ReviewChange, ReviewReason,
    StartNode, StartRuntime, StopNode, StopRuntime, WaitForHealthy,
    compile_activity_plan, compensation_for_operation,
)
from control_plane_kit_core.planning.codec import (
    activity_operation_descriptor, activity_operation_from_descriptor,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference, NamedPublicIngress, PublicIngressTarget,
)
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec, ProductIdentity,
    ProductInstanceConfiguration, ProductRuntimeContract, ProviderRuntimePort, instantiate_product,
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
from tests import test_receiver_configuration as receiver_fixtures
from tests import test_node_authority_delivery as authority_fixtures


@dataclass(frozen=True)
class ArtifactImplementation(PureImplementation):
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()

    def materialize(self, block_id, sockets, runtime):
        return replace(
            super().materialize(block_id, sockets, runtime),
            configuration_artifacts=self.configuration_artifacts,
        )


def surface(socket="control", kinds=(NodeHealthReadKind.READINESS,), *, variable=False):
    return WorkloadNodeControlSurfaceDescriptor(
        NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, socket),
        (variable_fixtures.WorkloadNodeControlSurfaceTests().variable("mode"),) if variable else (),
        kinds,
    )


def block(name, surfaces=(), *, gateway=False, checks=(), requirements=(), artifacts=()):
    names = {"control", *(value.provider_socket_name.value for value in surfaces)}
    if gateway:
        names.add("transit")
    capabilities = (CapabilityName.NODE_CONTROLLABLE,) if surfaces else ()
    if any(value.health_reads for value in surfaces):
        capabilities += (CapabilityName.HEALTH_CHECKABLE,)
    spec = BlockSpec(
        name, capabilities=capabilities, control_surfaces=surfaces,
        verification=VerificationContract(checks),
        gateway_transit=GatewayTransitDeclaration("transit", GatewayTransitProtocol.RECEIVER_HEALTH_READ_V2) if gateway else None,
    )
    implementation = ArtifactImplementation(
        "test-service",
        {name_: f"http://{name}.{name_}" for name_ in sorted(names)},
        artifacts,
    )
    return ApplicationBlock(
        spec, implementation,
        BlockSockets(providers=tuple(ProviderSocket(name_, Protocol.HTTP) for name_ in sorted(names)), requirements=requirements),
    )


def topology(*, prefix="", workload=True, workload_surfaces=None, checks=(),
             gateway_surfaces=None, gateway_checks=(), connector_surfaces=(), cycle=False,
             gateway_artifacts=(), added_workload=False):
    runtime_id = prefix + "runtime"
    gateway_id, connector_id, workload_id = (prefix + value for value in ("gateway", "connector", "workload"))
    gateway = block(gateway_id, (surface(),) if gateway_surfaces is None else gateway_surfaces,
                    gateway=True, checks=gateway_checks,
                    requirements=(RequirementSocket("service", Protocol.HTTP, ("SERVICE_URL",)),) if cycle else (),
                    artifacts=gateway_artifacts)
    connector = block(connector_id, connector_surfaces)
    children = [gateway, connector]
    if workload:
        children.append(block(workload_id, (surface(),) if workload_surfaces is None else workload_surfaces, checks=checks))
    if added_workload:
        children.append(block(prefix + "added-workload", (surface(),)))
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


def managed_update_graphs():
    """Generic algebraic A/B/C values modeled on accepted Servers #241/#238."""
    def routes(*node_ids):
        return ConfigurationArtifact(
            "management-routes", "/etc/cpk/gateway/routes.json",
            ConfigurationMediaType.JSON,
            json.dumps({"targets": list(node_ids)}, sort_keys=True, separators=(",", ":")),
        )

    graph_a = graph(gateway_artifacts=(routes("workload"),))
    graph_b = graph(
        gateway_artifacts=(routes("workload", "added-workload"),),
        added_workload=True,
    )
    graph_c = graph(gateway_artifacts=(routes("workload"),))
    return graph_a, graph_b, graph_c


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

    def compile_update(self, current, desired):
        left, right = validate_graph(current), validate_graph(desired)
        self.assertTrue(left.valid, left.descriptor())
        self.assertTrue(right.valid, right.descriptor())
        return self.api("compile_managed_update_activity_plan")(left, right)

    def find(self, plan, operation_type, *, node=None, stage=None, runtime=None):
        found = [value for value in plan.activities if isinstance(value.operation, operation_type)
                 and (node is None or getattr(value.operation, "node_id", getattr(value.operation.target, "node_id", None)) == node)
                 and (stage is None or value.operation.stage.value == stage)
                 and (runtime is None or value.operation.target.runtime_id == runtime)]
        self.assertEqual(len(found), 1, [(type(value.operation).__name__, value.activity_id.value) for value in found])
        return found[0]

    def before(self, plan, first, second):
        self.assertIn(first.activity_id, predecessors(plan, second))

    def fresh_order(self, plan, *, prefix=""):
        bootstrap = self.api("ObserveManagementBootstrap")
        runtime = prefix + "runtime"
        ready = self.find(plan, bootstrap, runtime=runtime, stage="gateway-ingress-ready")
        connected = self.find(plan, bootstrap, runtime=runtime, stage="connector-connected")
        path = self.find(plan, bootstrap, runtime=runtime, stage="authenticated-management-path")
        gateway = self.find(plan, StartNode, node=prefix + "gateway")
        connector = self.find(plan, StartNode, node=prefix + "connector")
        allocation = next(value for value in plan.activities if isinstance(value.operation, AllocatePublicIngress)
                          and value.operation.target.ingress_id == prefix + "management")
        for first, second in ((gateway, allocation), (allocation, connector),
                              (connector, path), (connector, connected), (path, ready)):
            self.before(plan, first, second)
            self.assertNotIn(second.activity_id, predecessors(plan, first))
        for first, second in ((connected, path), (connected, ready)):
            self.assertNotIn(first.activity_id, predecessors(plan, second))
            self.assertNotIn(second.activity_id, predecessors(plan, first))
        requests = [value for value in plan.activities if isinstance(value.operation, bootstrap)
                    and value.operation.target.runtime_id == runtime]
        self.assertEqual({value.operation.stage.value for value in requests},
                         {"gateway-ingress-ready", "connector-connected", "authenticated-management-path"})
        self.assertEqual(len({value.activity_id for value in requests}), 3)
        for health in (value for value in plan.activities if isinstance(value.operation, self.api("ObserveNodeHealth"))
                       and value.operation.target.runtime_id == runtime):
            self.before(plan, ready, health)
            self.before(plan, connected, health)
        return ready, connected, path

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
        _, _, path = self.fresh_order(plan)
        self.assertNotIn(path.activity_id, predecessors(plan, self.find(plan, StartNode, node="workload")))
        self.assertEqual(GraphDescriptorCodec().encode(desired), original)
        self.assertEqual(desired.edges, {})
        self.assertTrue(plan.ready_for_execution)
        self.assertEqual(self.compile(empty(), desired), plan)
        structural = compile_activity_plan(diff_graphs(validate_graph(empty()), validate_graph(desired)))
        for original_activity in structural.activities:
            if isinstance(original_activity.operation, (StartNode, StartRuntime, AllocatePublicIngress)):
                updated = next(value for value in plan.activities if value.activity_id == original_activity.activity_id)
                self.assertEqual(updated.operation, original_activity.operation)
                for dependency in original_activity.dependencies:
                    predecessor = next(value for value in structural.activities if value.activity_id == dependency.predecessor)
                    if isinstance(original_activity.operation, AllocatePublicIngress) and predecessor.operation == WaitForHealthy(NodeTarget("gateway")):
                        continue  # Only the selected ingress's old gateway gate is replaced by startup.
                    self.before(plan, predecessor, updated)

    def test_retained_infrastructure_observes_without_restarting_or_reallocating(self):
        plan = self.compile(graph(workload=False), graph())
        self.assertEqual({value.operation.target.node_id for value in plan.activities if isinstance(value.operation, StartNode)}, {"workload"})
        self.assertFalse(any(isinstance(value.operation, (StartRuntime, AllocatePublicIngress)) for value in plan.activities))
        health = self.find(plan, self.api("ObserveNodeHealth"), node="workload")
        path = self.find(plan, self.api("ObserveManagementBootstrap"), stage="authenticated-management-path")
        self.before(plan, path, health)
        local = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-local-ready")
        connected = self.find(plan, self.api("ObserveManagementBootstrap"), stage="connector-connected")
        self.before(plan, local, connected)
        self.before(plan, connected, path)
        self.assertFalse(any(getattr(value.operation, "stage", None) == "gateway-ingress-ready" for value in plan.activities))

    def test_managed_update_add_orders_route_and_both_sdk_readiness(self):
        graph_a, graph_b, _ = managed_update_graphs()
        plan = self.compile_update(graph_a, graph_b)
        bootstrap = self.api("ObserveManagementBootstrap")
        health_type = self.api("ObserveNodeHealth")
        start = self.find(plan, StartNode, node="added-workload")
        reconcile = self.find(plan, ReconcileNode, node="gateway")
        local = self.find(plan, bootstrap, stage="gateway-local-ready")
        connected = self.find(plan, bootstrap, stage="connector-connected")
        path = self.find(plan, bootstrap, stage="authenticated-management-path")
        retained = self.find(plan, health_type, node="workload")
        added = self.find(plan, health_type, node="added-workload")
        for observation in (retained, added):
            self.assertIs(observation.operation.target.graph_side, self.api("PlanGraphSide").DESIRED_GRAPH)
            self.assertIs(observation.operation.health_kind, NodeHealthReadKind.READINESS)
        for first, second in (
            (start, reconcile), (reconcile, local), (local, connected),
            (connected, path), (path, retained), (path, added), (start, added),
        ):
            self.before(plan, first, second)
        self.assertFalse(any(
            isinstance(value.operation, WaitForHealthy)
            and value.operation.target.node_id in {"workload", "added-workload"}
            for value in plan.activities
        ))
        self.assertTrue(plan.ready_for_execution)

    def test_managed_update_remove_proves_retained_path_before_disposal(self):
        _, graph_b, graph_c = managed_update_graphs()
        plan = self.compile_update(graph_b, graph_c)
        bootstrap = self.api("ObserveManagementBootstrap")
        reconcile = self.find(plan, ReconcileNode, node="gateway")
        local = self.find(plan, bootstrap, stage="gateway-local-ready")
        connected = self.find(plan, bootstrap, stage="connector-connected")
        path = self.find(plan, bootstrap, stage="authenticated-management-path")
        retained = self.find(plan, self.api("ObserveNodeHealth"), node="workload")
        stop = self.find(plan, StopNode, node="added-workload")
        remove = self.find(plan, RemoveNodeResource, node="added-workload")
        for first, second in (
            (reconcile, local), (local, connected), (connected, path),
            (path, retained), (retained, stop), (stop, remove),
        ):
            self.before(plan, first, second)
        self.assertIs(retained.operation.target.graph_side, self.api("PlanGraphSide").DESIRED_GRAPH)
        structural = compile_activity_plan(diff_graphs(validate_graph(graph_b), validate_graph(graph_c)))
        self.assertEqual(stop.activity_id, self.find(structural, StopNode, node="added-workload").activity_id)
        self.assertEqual(remove.activity_id, self.find(structural, RemoveNodeResource, node="added-workload").activity_id)
        self.assertFalse(any(
            isinstance(value.operation, self.api("ObserveNodeHealth"))
            and value.operation.node_id == "added-workload"
            for value in plan.activities
        ))
        self.assertTrue(plan.ready_for_execution)

    def test_managed_update_refuses_material_outside_closed_generic_shape(self):
        graph_a, graph_b, graph_c = managed_update_graphs()
        liveness_a = replace(
            graph_a,
            nodes={**graph_a.nodes, "workload": replace(
                graph_a.node("workload"),
                block_spec=replace(
                    graph_a.node("workload").block_spec,
                    control_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)),),
                ),
            )},
        )
        liveness_b = replace(
            graph_b,
            nodes={**graph_b.nodes, "workload": replace(
                graph_b.node("workload"),
                block_spec=replace(
                    graph_b.node("workload").block_spec,
                    control_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)),),
                ),
            )},
        )
        cases = {
            "retained-workload-material": (graph_a, replace(
                graph_b,
                nodes={**graph_b.nodes, "workload": replace(
                    graph_b.node("workload"),
                    metadata={"unrelated-selected-material": "changed"},
                )},
            )),
            "unpaired-gateway-material": (graph_a, replace(
                graph_b,
                nodes={**graph_b.nodes, "gateway": replace(
                    graph_b.node("gateway"),
                    configuration_artifacts=graph_a.node("gateway").configuration_artifacts,
                )},
            )),
            "independent-new-workload-verification": (graph_a, replace(
                graph_b,
                nodes={**graph_b.nodes, "added-workload": replace(
                    graph_b.node("added-workload"),
                    block_spec=replace(
                        graph_b.node("added-workload").block_spec,
                        verification=VerificationContract((HttpCheck(
                            check_id="independent", provider_socket="control", path="/health",
                        ),)),
                    ),
                )},
            )),
            "liveness-only-new-workload": (graph_a, replace(
                graph_b,
                nodes={**graph_b.nodes, "added-workload": replace(
                    graph_b.node("added-workload"),
                    block_spec=replace(
                        graph_b.node("added-workload").block_spec,
                        control_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)),),
                    ),
                )},
            )),
            "liveness-only-retained-workload": (liveness_a, liveness_b),
            "retained-child-reordering": (graph_a, replace(
                graph_b,
                runtimes={"runtime": replace(
                    graph_b.runtimes["runtime"],
                    children=("connector", "gateway", "workload", "added-workload"),
                )},
            )),
            "retained-compute-removal": (replace(
                graph_b,
                nodes={**graph_b.nodes, "added-workload": replace(
                    graph_b.node("added-workload"),
                    lifecycle=ResourceLifecycle(
                        ResourceOwnership.OWNED, ResourcePersistence.RETAINED,
                    ),
                )},
            ), graph_c),
        }
        for name, (current, desired) in cases.items():
            with self.subTest(name=name):
                plan = self.compile_update(current, desired)
                self.assertFalse(plan.ready_for_execution)
                self.assertTrue(any(isinstance(value.operation, ReviewChange) for value in plan.activities))

    def test_equal_name_only_and_unmanaged_pairs_preserve_exact_legacy_plans(self):
        managed = graph(gateway_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)),))
        for desired in (managed, replace(managed, name="renamed")):
            self.assertEqual(self.compile(managed, desired).activities, ())
        unbound = replace(managed, runtimes={"runtime": replace(managed.runtimes["runtime"], management=None)})
        legacy = compile_activity_plan(diff_graphs(validate_graph(empty()), validate_graph(unbound)))
        self.assertEqual(self.compile(empty(), unbound), legacy)

    def test_retained_connector_reconfiguration_waits_for_gateway_local_readiness(self):
        current = graph()
        for gateway_changes in (False, True):
            with self.subTest(gateway_changes=gateway_changes):
                nodes = dict(current.nodes)
                for node_id in (("connector", "gateway") if gateway_changes else ("connector",)):
                    nodes[node_id] = replace(nodes[node_id], block_spec=replace(nodes[node_id].block_spec,
                        metadata={"configuration_revision": "2"}))
                desired = replace(current, nodes=nodes)
                plan = self.compile(current, desired)
                structural = compile_activity_plan(diff_graphs(validate_graph(current), validate_graph(desired)))
                connector = self.find(plan, ReconcileNode, node="connector")
                self.assertEqual(connector.activity_id, self.find(structural, ReconcileNode, node="connector").activity_id)
                bootstrap = self.api("ObserveManagementBootstrap")
                local = self.find(plan, bootstrap, stage="gateway-local-ready")
                connected = self.find(plan, bootstrap, stage="connector-connected")
                path = self.find(plan, bootstrap, stage="authenticated-management-path")
                self.assertFalse(any(isinstance(value.operation, (StartNode, StartRuntime, AllocatePublicIngress)) for value in plan.activities))
                if gateway_changes:
                    gateway = self.find(plan, ReconcileNode, node="gateway")
                    self.assertEqual(gateway.activity_id, self.find(structural, ReconcileNode, node="gateway").activity_id)
                    self.before(plan, gateway, local)
                self.before(plan, local, connector)
                self.before(plan, connector, connected)
                self.before(plan, connected, path)
                self.assertNotIn(connected.activity_id, predecessors(plan, connector))
                self.assertNotIn(path.activity_id, predecessors(plan, connector))

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

    def test_fresh_bootstrap_preserves_authored_noncyclic_service_prerequisite(self):
        authored = topology()
        gateway, connector, workload = authored.root.children
        gateway = replace(gateway, sockets=replace(gateway.sockets,
            requirements=(RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",)),)))
        service = block("upstream", checks=(HttpCheck(check_id="upstream-health", provider_socket="control", path="/"),))
        service_runtime = DockerRuntime(runtime_id="upstream-runtime", children=(service,))
        connection = SocketConnection("upstream", "control", "gateway", "upstream", edge_id="gateway.upstream")
        desired = compile_topology(DeploymentTopology("bootstrap", DockerRuntime(runtime_id="outer",
            children=(service_runtime, replace(authored.root, children=(gateway, connector, workload)), connection)),
            public_ingresses=authored.public_ingresses))
        plan = self.compile(empty(), desired)
        structural = compile_activity_plan(diff_graphs(validate_graph(empty()), validate_graph(desired)))
        wait = self.find(structural, WaitForHealthy, node="upstream")
        preserved = self.find(plan, WaitForHealthy, node="upstream")
        self.assertEqual(preserved, wait)
        ready, _, _ = self.fresh_order(plan)
        for target in (self.find(plan, StartNode, node="gateway"), self.find(plan, AllocatePublicIngress), ready):
            self.before(plan, preserved, target)
        self.assertTrue(plan.ready_for_execution)

    def test_mixed_retained_and_fresh_runtime_uses_each_own_bootstrap_order(self):
        retained = topology(prefix="old-", workload=False)
        old, new = topology(prefix="old-"), topology(prefix="new-")
        current = compile_topology(DeploymentTopology("bootstrap", DockerRuntime(runtime_id="outer", children=(retained.root,)),
            public_ingresses=retained.public_ingresses))
        desired = compile_topology(DeploymentTopology("bootstrap", DockerRuntime(runtime_id="outer", children=(old.root, new.root)),
            public_ingresses=old.public_ingresses + new.public_ingresses))
        plan = self.compile(current, desired)
        ready, connected, path = self.fresh_order(plan, prefix="new-")
        bootstrap = self.api("ObserveManagementBootstrap")
        old_local = self.find(plan, bootstrap, runtime="old-runtime", stage="gateway-local-ready")
        old_connected = self.find(plan, bootstrap, runtime="old-runtime", stage="connector-connected")
        old_path = self.find(plan, bootstrap, runtime="old-runtime", stage="authenticated-management-path")
        self.before(plan, old_local, old_connected)
        self.before(plan, old_connected, old_path)
        old_health = self.find(plan, self.api("ObserveNodeHealth"), node="old-workload")
        new_health = self.find(plan, self.api("ObserveNodeHealth"), node="new-workload")
        for observation in (ready, connected, path):
            self.assertNotIn(observation.activity_id, predecessors(plan, old_health))
        for observation in (old_local, old_connected, old_path):
            self.assertNotIn(observation.activity_id, predecessors(plan, new_health))

    def test_lifecycle_suppressed_runtime_start_does_not_qualify_as_fresh(self):
        for lifecycle in (ResourceLifecycle.external(), ResourceLifecycle.attached()):
            with self.subTest(lifecycle=lifecycle):
                desired = graph()
                desired = replace(desired, runtimes={"runtime": replace(desired.runtimes["runtime"], lifecycle=lifecycle)})
                plan = self.compile(empty(), desired)
                structural = compile_activity_plan(diff_graphs(validate_graph(empty()), validate_graph(desired)))
                self.assertFalse(any(isinstance(value.operation, StartRuntime) for value in structural.activities))
                self.find(structural, StartNode, node="gateway")
                self.find(structural, StartNode, node="connector")
                self.find(structural, AllocatePublicIngress)
                self.assertFalse(any(getattr(value.operation, "stage", None) == "gateway-ingress-ready" for value in plan.activities))
                self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-local-ready")

    def test_lifecycle_suppressed_node_start_does_not_qualify_as_fresh(self):
        for node_id in ("gateway", "connector"):
            for lifecycle in (ResourceLifecycle.external(), ResourceLifecycle.attached()):
                with self.subTest(node=node_id, lifecycle=lifecycle):
                    desired = graph()
                    desired = replace(desired, nodes={
                        **desired.nodes, node_id: replace(desired.nodes[node_id], lifecycle=lifecycle),
                    })
                    plan = self.compile(empty(), desired)
                    structural = compile_activity_plan(diff_graphs(validate_graph(empty()), validate_graph(desired)))
                    self.find(structural, StartRuntime, runtime="runtime")
                    self.find(structural, StartNode, node="connector" if node_id == "gateway" else "gateway")
                    self.find(structural, AllocatePublicIngress)
                    for result in (structural, plan):
                        self.assertFalse(any(isinstance(value.operation, (StartNode, WaitForHealthy))
                                             and value.operation.target.node_id == node_id
                                             for value in result.activities))
                    bootstrap = self.api("ObserveManagementBootstrap")
                    self.assertFalse(any(isinstance(value.operation, bootstrap)
                                         and value.operation.stage.value == "gateway-ingress-ready"
                                         for value in plan.activities))
                    self.find(plan, bootstrap, stage="gateway-local-ready")
                    self.find(plan, bootstrap, stage="authenticated-management-path")

    def test_adding_management_to_existing_runtime_preserves_whole_structural_review(self):
        desired = graph()
        current = replace(desired, runtimes={"runtime": replace(desired.runtimes["runtime"], management=None)})
        fresh = topology(prefix="new-")
        # The existing whole-plan retarget guard also covers mixed additions.
        for right in (desired, replace(desired, runtimes={**desired.runtimes, **compile_topology(fresh).runtimes},
                      nodes={**desired.nodes, **compile_topology(fresh).nodes},
                      public_ingresses=desired.public_ingresses + fresh.public_ingresses)):
            plan = self.compile(current, right)
            structural = compile_activity_plan(diff_graphs(validate_graph(current), validate_graph(right)))
            self.assertEqual(plan, structural)

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
        self.fresh_order(plan, prefix="new-")

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
        request = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-ingress-ready").operation
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
        ready = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-ingress-ready")
        allocation = self.find(plan, AllocatePublicIngress)
        self.before(plan, allocation, ready)
        self.before(plan, ready, verify)
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
        ready = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-ingress-ready")
        self.before(plan, connected, health)
        self.before(plan, ready, health)
        self.assertNotIn(connected.activity_id, predecessors(plan, path))
        self.assertNotIn(path.activity_id, predecessors(plan, connected))
        self.before(plan, path, health)
        self.assertNotIn(health.activity_id, predecessors(plan, connected))

    def test_request_wire_hashes_and_compensation_commit_exact_graph_relation_and_stage(self):
        desired = graph()
        current = graph(workload=False)
        plan = self.compile(current, desired)
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
        self.assertEqual(self.compile(current, desired), plan)

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

    def receiver_v2_graph(self):
        try:
            declaration = self.api("GatewayTransitDeclarationCodec")().decode({
                "provider_socket_name": "transit",
                "protocol": "gateway-receiver-health-read-transit.v2",
            })
        except ValueError:
            self.fail("the gateway declaration decoder must admit receiver health V2")
        original = graph()
        gateway = original.node("gateway")
        return replace(original, nodes={**original.nodes, "gateway": replace(gateway,
            block_spec=replace(gateway.block_spec, gateway_transit=declaration))})

    def test_receiver_v2_preserves_real_health_ingress_and_own_bootstrap_selection(self):
        desired = self.receiver_v2_graph()
        self.assertTrue(validate_graph(desired).valid)
        plan = self.compile(empty(), desired)
        self.fresh_order(plan)
        operations = (self.api("ObserveNodeHealth"), self.api("ObserveManagementBootstrap"))
        for activity in plan.activities:
            if isinstance(activity.operation, operations):
                operation = activity.operation
                resolved = self.api("resolve_management_observation")(
                    operation, validate_graph(empty()), validate_graph(desired), expected_operation=operation)
                self.assertEqual(resolved.gateway_node.block_spec.gateway_transit,
                                 desired.node("gateway").block_spec.gateway_transit)
                self.assertEqual(resolved.ingress, desired.public_ingresses[0])
                self.assertEqual(compensation_for_operation(operation), NoCompensationRequired())
        self.assertEqual(self.compile(empty(), desired), plan)
        codec = ActivityPlanDescriptorCodec()
        self.assertEqual(codec.decode(codec.encode(plan)), plan)

    def test_receiver_v2_commits_exact_graph_relation_and_refuses_obsolete_descriptors(self):
        desired = self.receiver_v2_graph()
        codec = GraphDescriptorCodec()
        document = codec.encode(desired)
        plan = self.compile(empty(), desired)
        health = self.find(plan, self.api("ObserveNodeHealth"), node="workload")
        expected_graph = hashlib.sha256(
            b"control-plane-kit.management-graph.v1\0" + rfc8785.dumps(document)).hexdigest()
        relation = {"runtime_id": "runtime", "management": desired.runtimes["runtime"].management.descriptor(),
                    "ingress": desired.public_ingresses[0].descriptor(),
                    "gateway_transit": {"provider_socket_name": "transit",
                                        "protocol": "gateway-receiver-health-read-transit.v2"},
                    "gateway_readiness": {"provider_socket_name": "control", "health_kind": "readiness"}}
        expected_relation = hashlib.sha256(
            b"control-plane-kit.management-relation.v1\0" + rfc8785.dumps(relation)).hexdigest()
        self.assertEqual(health.operation.target.graph_digest, expected_graph)
        self.assertEqual(health.operation.target.relation_digest, expected_relation)
        self.assertEqual(self.compile(empty(), desired), plan)
        self.assertEqual(self.compile(empty(), codec.decode(document)), plan)
        document["nodes"]["gateway"]["block_spec"]["gateway_transit"]["protocol"] = "gateway-node-health-read-transit.v1"
        with self.assertRaises(ValueError):
            codec.decode(document)

    def test_ingress_ready_has_distinct_wire_identity_and_bounded_codec_refusals(self):
        stage_type = self.api("ManagementBootstrapStage")
        stage = getattr(stage_type, "GATEWAY_INGRESS_READY", None)
        self.assertIsNotNone(stage, "#1863 ingress-ready stage is missing")
        self.assertEqual(stage.value, "gateway-ingress-ready")
        plan = self.compile(empty(), graph())
        activity = self.find(plan, self.api("ObserveManagementBootstrap"), stage=stage.value)
        request = activity.operation
        wire = activity_operation_descriptor(request)
        self.assertEqual(wire["stage"], "gateway-ingress-ready")
        self.assertEqual(activity_operation_from_descriptor(wire), request)
        identity = lambda value: "observe:" + hashlib.sha256(
            b"control-plane-kit.management-activity.v1\0" + rfc8785.dumps(activity_operation_descriptor(value))).hexdigest()
        self.assertEqual(activity.activity_id.value, identity(request))
        operations = [replace(request, stage=value) for value in stage_type]
        self.assertEqual(len(set(operations)), 4)
        self.assertEqual(len({identity(value) for value in operations}), 4)
        codec = ActivityPlanDescriptorCodec()
        self.assertEqual(codec.decode(codec.encode(plan)), plan)
        for changes in ({"stage": "unknown"}, {"extra": "PRIVATE-CANDIDATE" * 1000}):
            malformed = {**wire, **changes}
            envelope = codec.encode(plan)
            next(value for value in envelope["activities"] if value["activity_id"] == activity.activity_id.value)["operation"] = malformed
            for decode, candidate in ((activity_operation_from_descriptor, malformed), (codec.decode, envelope)):
                with self.assertRaises(ValueError) as caught:
                    decode(candidate)
                self.assertLess(len(str(caught.exception)), 200)
                self.assertNotIn("PRIVATE-CANDIDATE", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_admitted_local_bootstrap_plan_roundtrips_without_recompilation(self):
        target = {"kind": "management", "runtime_id": "runtime", "graph_side": "desired-graph",
                  "graph_digest": "a" * 64, "relation_digest": "b" * 64}
        wire = {"schema": "control-plane-kit.activity-plan", "version": 1, "activities": [
            {"activity_id": "admitted-local", "operation": {"kind": "observe-management-bootstrap", "target": target, "stage": "gateway-local-ready"},
             "dependencies": [], "risk": "low", "impact": "non-destructive", "compensation": {"kind": "not-required"}},
            {"activity_id": "admitted-connected", "operation": {"kind": "observe-management-bootstrap", "target": target, "stage": "connector-connected"},
             "dependencies": ["admitted-local"], "risk": "low", "impact": "non-destructive", "compensation": {"kind": "not-required"}}]}
        codec = ActivityPlanDescriptorCodec()
        admitted = codec.decode(wire)
        self.assertEqual(codec.encode(admitted), wire)
        self.assertEqual(codec.dumps(admitted), json.dumps(wire, sort_keys=True, separators=(",", ":")))
        self.assertEqual(admitted.activities[0].operation.stage.value, "gateway-local-ready")

    def test_resolver_rejects_rebound_connector_socket_relation_runtime_or_graph(self):
        desired = graph()
        plan = self.compile(empty(), desired)
        request = self.find(plan, self.api("ObserveManagementBootstrap"), stage="gateway-ingress-ready").operation
        resolver = self.api("resolve_management_observation")
        error_type = self.api("ManagementObservationError")
        target = request.target
        for candidate in (replace(request, target=replace(target, runtime_id="other")),
                          replace(request, target=replace(target, graph_side=self.api("PlanGraphSide").BASE_GRAPH)),
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
        for candidate in (replace(request, target=replace(target, relation_digest="f" * 64)),
                          replace(request, target=replace(target, graph_digest="f" * 64))):
            with self.assertRaises(error_type):
                resolver(candidate, validate_graph(empty()), validate_graph(desired), expected_operation=candidate)

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


class ManagedConfigurationUpdateTests(unittest.TestCase):
    """#1970: one public file's content is a closed retained-node relation."""

    def pair(self, *, socket_secret=False):
        declaration = topology()
        if socket_secret:
            delivery = SecretEnvironmentDelivery(
                "APPLICATION_TOKEN", SecretReference("secret://workspace/application"),
                SecretUseIntent.APPLICATION_CONTROL_TOKEN,
            )
            runtime = declaration.root
            workload = runtime.children[2]
            workload = replace(workload, sockets=replace(workload.sockets, requirements=(
                RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",),
                                  secret_deliveries=(delivery,)),
            )))
            declaration = replace(declaration, root=replace(runtime, children=(
                *runtime.children[:2], workload,
                SocketConnection("gateway", "control", "workload", "upstream"),
            )))
        current = compile_topology(declaration)
        node = current.node("workload")
        fixture = receiver_fixtures.ReceiverConfigurationTests()
        fixture.setUp()
        declaration = core.WorkloadNodeControlSurfaceDeclaration(
            node.block_spec.control_surfaces[0],
            profile=core.WorkloadNodeControlSurfaceDeclarationProfile.V2,
        )
        document = fixture.document(declaration)
        document["target"].update(runtime_id="runtime", node_id="workload")
        wrapper = fixture.artifact(document=document)
        application = ConfigurationArtifact(
            "application", "/etc/application/settings.json", ConfigurationMediaType.JSON,
            '{"mode":"blue"}',
        )
        current = current.update_node(replace(
            node, configuration_artifacts=(wrapper, application),
            public_environment=(PublicStaticEnvironmentBinding(receiver_fixtures.PATH_KEY, wrapper.target_path),),
        ))
        desired = current.update_node(replace(
            current.node("workload"),
            configuration_artifacts=(wrapper, replace(application, content='{"mode":"green"}', source_digest=None)),
        ))
        return current, desired

    def compile(self, current, desired):
        left, right = validate_graph(current), validate_graph(desired)
        self.assertTrue(left.valid, left.descriptor())
        self.assertTrue(right.valid, right.descriptor())
        return core.compile_managed_update_activity_plan(left, right)

    def refused(self, current, desired):
        plan = self.compile(current, desired)
        self.assertFalse(plan.ready_for_execution)
        self.assertTrue(any(isinstance(value.operation, ReviewChange) for value in plan.activities))

    def test_content_change_reconciles_before_desired_health_without_replacing_infrastructure(self):
        current, desired = self.pair()
        codec = GraphDescriptorCodec()
        originals = (codec.encode(current), codec.encode(desired))
        plan = self.compile(current, desired)
        self.assertTrue(plan.ready_for_execution)
        self.assertEqual(plan, self.compile(current, desired))
        replacements = [a for a in plan.activities if isinstance(a.operation, ReconcileNode)]
        self.assertEqual([a.operation.target.node_id for a in replacements], ["workload"])
        self.assertEqual(replacements[0].impact.value, "disruptive")
        health = [a for a in plan.activities if isinstance(a.operation, core.ObserveNodeHealth)]
        self.assertEqual([a.operation.node_id for a in health], ["workload"])
        self.assertIn(replacements[0].activity_id, predecessors(plan, health[0]))
        stages = {a.operation.stage: a for a in plan.activities
                  if isinstance(a.operation, core.ObserveManagementBootstrap)}
        local = stages[core.ManagementBootstrapStage.GATEWAY_LOCAL_READY]
        connected = stages[core.ManagementBootstrapStage.CONNECTOR_CONNECTED]
        path = stages[core.ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH]
        for first, second in ((local, connected), (connected, path), (path, health[0])):
            self.assertIn(first.activity_id, predecessors(plan, second))
        self.assertEqual(health[0].operation.target.graph_side, core.PlanGraphSide.DESIRED_GRAPH)
        resolved = core.resolve_management_observation(
            health[0].operation, validate_graph(current), validate_graph(desired),
            expected_operation=health[0].operation,
        )
        self.assertEqual(resolved.workload_node, desired.node("workload"))
        self.assertFalse(any(isinstance(a.operation, (StartNode, StartRuntime, AllocatePublicIngress,
                                                     StopNode, RemoveNodeResource, ReconcileRuntime))
                             for a in plan.activities))
        self.assertEqual((codec.encode(current), codec.encode(desired)), originals)

    def test_equal_material_is_empty_and_content_add_remove_share_one_compiler(self):
        current, desired = self.pair()
        self.assertEqual(self.compile(current, current).activities, ())
        self.assertTrue(self.compile(current, desired).ready_for_execution)
        a, b, c = managed_update_graphs()
        self.assertTrue(core.compile_managed_update_activity_plan(validate_graph(a), validate_graph(b)).ready_for_execution)
        self.assertTrue(core.compile_managed_update_activity_plan(validate_graph(b), validate_graph(c)).ready_for_execution)

    def test_other_artifact_changes_and_provenance_only_are_refused(self):
        current, desired = self.pair()
        wrapper, before = current.node("workload").configuration_artifacts
        after = desired.node("workload").configuration_artifacts[1]
        cases = {
            "source-digest-only": (wrapper, replace(before, source_digest="f" * 64)),
            "path": (wrapper, replace(after, target_path="/etc/other.json")),
            "mode": (wrapper, replace(after, file_mode=ConfigurationFileMode.OWNER_READ_ONLY)),
            "media": (wrapper, replace(after, media_type=ConfigurationMediaType.TEXT)),
            "added-artifact": (wrapper, before, replace(after, artifact_id="extra", target_path="/etc/extra.json")),
            "removed-artifact": (wrapper,),
            "wrapper-and-app": (replace(wrapper, source_digest="e" * 64), after),
        }
        for name, artifacts in cases.items():
            with self.subTest(name=name):
                self.refused(current, desired.update_node(replace(desired.node("workload"), configuration_artifacts=artifacts)))

    def test_other_material_and_graph_changes_are_refused(self):
        current, desired = self.pair()
        node = desired.node("workload")
        cases = {
            "image-product": desired.update_node(replace(node, metadata={**node.metadata, "image": "example.invalid/app@sha256:" + "a" * 64})),
            "declaration": desired.update_node(replace(node, block_spec=replace(node.block_spec, control_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)),)))),
            "environment": desired.update_node(replace(node, public_environment=node.public_environment + (PublicStaticEnvironmentBinding("MODE", "other"),))),
            "second-node": desired.update_node(replace(desired.node("connector"), metadata={"changed": True})),
            "graph-name": replace(desired, name="other"),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.refused(current, candidate)

    def test_ineligible_targets_are_refused_even_when_only_app_content_changes(self):
        current, desired = self.pair()
        cases = {
            "external": {"lifecycle": ResourceLifecycle.external()},
            "retained-data": {"lifecycle": ResourceLifecycle.owned_with_retained_data("app-data")},
            "secret": {"secret_deliveries": (SecretEnvironmentDelivery(
                "APPLICATION_TOKEN", SecretReference("secret://workspace/application"), SecretUseIntent.APPLICATION_CONTROL_TOKEN),),
                "secret_selections": (UnselectedSecretUse(SecretReference("secret://workspace/application"),
                                                          SecretUseIntent.APPLICATION_CONTROL_TOKEN),)},
            "runtime-authority": {"runtime_authority_deliveries": (authority_fixtures.DELIVERY,)},
            "independent-verification": {"block_spec": replace(current.node("workload").block_spec,
                verification=VerificationContract((HttpCheck(check_id="own", provider_socket="control", path="/health"),)))},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                self.refused(*(value.update_node(replace(value.node("workload"), **changes)) for value in (current, desired)))

    def test_active_socket_secret_is_effective_material_and_refused(self):
        current, desired = self.pair(socket_secret=True)
        self.assertTrue(current.node("workload").secret_deliveries)
        self.refused(current, desired)

    def test_actual_wrapper_and_exact_target_are_required(self):
        current, desired = self.pair()
        for target_node in ("foreign",):
            wrapper = current.node("workload").configuration_artifacts[0]
            document = json.loads(wrapper.content)
            document["target"]["node_id"] = target_node
            changed = replace(wrapper, content=json.dumps(document), source_digest=None)
            pair = [value.update_node(replace(value.node("workload"), configuration_artifacts=(
                changed, value.node("workload").configuration_artifacts[1],
            ))) for value in (current, desired)]
            self.refused(*pair)
        pair = [value.update_node(replace(value.node("workload"), configuration_artifacts=(
            value.node("workload").configuration_artifacts[1],
        ), public_environment=())) for value in (current, desired)]
        self.refused(*pair)


class ManagedImageUpdateTests(unittest.TestCase):
    """#1972: immutable image successor, with receiving identity unchanged."""

    def pair(self, *, secret_deliveries=()):
        configured, _ = ManagedConfigurationUpdateTests().pair()
        node = configured.node("workload")
        contract = ProductRuntimeContract(sockets=node.sockets,
            provider_ports=(ProviderRuntimePort("control", 8000),),
            capabilities=node.block_spec.capabilities, control_surfaces=node.block_spec.control_surfaces,
            configuration_artifacts=node.configuration_artifacts, public_environment=node.public_environment,
            secret_deliveries=secret_deliveries)
        before = ContainerServerProduct(ProductIdentity("examples", "receiver", 1),
            OciImageReference("registry.example.invalid", "apps/receiver", "sha256:" + "a" * 64), contract)
        after = replace(before, identity=replace(before.identity, contract_revision=2),
            image=replace(before.image, digest="sha256:" + "b" * 64))
        declaration = topology()
        graphs = []
        for product in (before, after):
            workload = instantiate_product(product, "workload", ProductInstanceConfiguration.from_contract(contract))
            graphs.append(compile_topology(replace(declaration, root=replace(declaration.root,
                children=(*declaration.root.children[:2], workload)))))
        return (*graphs, before, after)

    def compile(self, current, desired):
        pair = validate_graph(current), validate_graph(desired)
        for value in pair:
            self.assertTrue(value.valid, value.descriptor())
        return core.compile_managed_update_activity_plan(*pair)

    def refused(self, current, desired):
        plan = self.compile(current, desired)
        self.assertFalse(plan.ready_for_execution)
        self.assertTrue(any(isinstance(item.operation, ReviewChange) for item in plan.activities))

    def test_genuine_image_successor_reconciles_before_desired_health_preserving_receiver(self):
        current, desired, before, after = self.pair()
        codec = GraphDescriptorCodec()
        original = codec.encode(current), codec.encode(desired)
        for graph, product in ((current, before), (desired, after)):
            self.assertEqual({key: graph.node("workload").metadata[key] for key in
                ("product_identity", "product_descriptor_digest", "oci_image")}, {
                "product_identity": product.identity.key,
                "product_descriptor_digest": ProductDescriptorCodec().encode_document(product).content_digest,
                "oci_image": product.image.execution_reference})
        self.assertEqual(current.node("workload").configuration_artifacts,
            desired.node("workload").configuration_artifacts)
        plan = self.compile(current, desired)
        self.assertTrue(plan.ready_for_execution)
        self.assertEqual(plan, self.compile(codec.decode(original[0]), codec.decode(original[1])))
        replacements = [item for item in plan.activities if isinstance(item.operation, ReconcileNode)]
        self.assertEqual([item.operation.target.node_id for item in replacements], ["workload"])
        self.assertEqual(replacements[0].impact.value, "disruptive")
        health = [item for item in plan.activities if isinstance(item.operation, core.ObserveNodeHealth)]
        self.assertEqual([item.operation.node_id for item in health], ["workload"])
        self.assertIn(replacements[0].activity_id, predecessors(plan, health[0]))
        stages = {item.operation.stage: item for item in plan.activities
            if isinstance(item.operation, core.ObserveManagementBootstrap)}
        chain = [stages[kind] for kind in (core.ManagementBootstrapStage.GATEWAY_LOCAL_READY,
            core.ManagementBootstrapStage.CONNECTOR_CONNECTED,
            core.ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)] + health
        for left, right in zip(chain, chain[1:]):
            self.assertIn(left.activity_id, predecessors(plan, right))
        resolved = core.resolve_management_observation(health[0].operation,
            validate_graph(current), validate_graph(desired), expected_operation=health[0].operation)
        self.assertEqual(resolved.workload_node, desired.node("workload"))
        self.assertIs(health[0].operation.target.graph_side, core.PlanGraphSide.DESIRED_GRAPH)
        self.assertFalse(any(isinstance(item.operation, (StartNode, StartRuntime, AllocatePublicIngress,
            StopNode, RemoveNodeResource, ReconcileRuntime)) for item in plan.activities))
        self.assertEqual((codec.encode(current), codec.encode(desired)), original)
        self.assertEqual(self.compile(current, current).activities, ())

    def test_image_metadata_is_exact_canonical_and_an_actual_same_family_digest_transition(self):
        current, desired, _, _ = self.pair()
        old, new = current.node("workload").metadata, desired.node("workload").metadata
        cases = ({**new, "oci_image": old["oci_image"]},
            {**new, "product_descriptor_digest": old["product_descriptor_digest"]},
            {**new, "product_identity": old["product_identity"]},
            {**new, "product_identity": "other/receiver/2"},
            {**new, "product_identity": "examples/other/2"},
            {**new, "product_identity": "examples/receiver/02"},
            {**new, "product_descriptor_digest": "B" * 64},
            {**new, "oci_image": "registry.example.invalid/apps/receiver:latest"},
            {**new, "oci_image": "registry.example.invalid/apps/receiver:tag@sha256:" + "b" * 64},
            {**new, "oci_image": "other.example.invalid/apps/receiver@sha256:" + "b" * 64},
            {**new, "oci_image": "registry.example.invalid/other@sha256:" + "b" * 64},
            {**new, "extra": "changed"}, {key: value for key, value in new.items() if key != "oci_image"})
        for index, metadata in enumerate(cases):
            with self.subTest(case=index):
                self.refused(current, desired.update_node(replace(desired.node("workload"), metadata=metadata)))
        # No monotonic revision policy: explicit revisions differ without changing the receiver.
        self.assertTrue(self.compile(desired, current).ready_for_execution)

    def test_other_graph_and_configuration_changes_are_not_image_only(self):
        current, desired, _, _ = self.pair()
        node = desired.node("workload")
        artifacts = {item.artifact_id: item for item in node.configuration_artifacts}
        wrapper, application = artifacts["receiver"], artifacts["application"]
        cases = (replace(node, configuration_artifacts=(wrapper, replace(application, content='{"mode":"new"}', source_digest=None))),
            replace(node, configuration_artifacts=(replace(wrapper, source_digest="c" * 64), application)),
            replace(node, public_environment=node.public_environment + (PublicStaticEnvironmentBinding("EXTRA", "value"),)),
            replace(node, block_spec=replace(node.block_spec, control_surfaces=(surface(kinds=(NodeHealthReadKind.LIVENESS,)),))))
        for index, changed in enumerate(cases):
            with self.subTest(case=index):
                self.refused(current, desired.update_node(changed))
        self.refused(current, replace(desired, name="different"))
        self.refused(current, desired.update_node(replace(desired.node("connector"), metadata={"extra": True})))

    def test_image_relation_preserves_c1_eligibility_and_exact_wrapper_target(self):
        current, desired, _, _ = self.pair()
        artifacts = {item.artifact_id: item for item in current.node("workload").configuration_artifacts}
        wrapper, application = artifacts["receiver"], artifacts["application"]
        cases = ({"lifecycle": ResourceLifecycle.external()},
            {"lifecycle": ResourceLifecycle.owned_with_retained_data("data")},
            {"secret_deliveries": (SecretEnvironmentDelivery("TOKEN", SecretReference("secret://workspace/app"),
                SecretUseIntent.APPLICATION_CONTROL_TOKEN),),
                "secret_selections": (UnselectedSecretUse(SecretReference("secret://workspace/app"),
                                                          SecretUseIntent.APPLICATION_CONTROL_TOKEN),)},
            {"runtime_authority_deliveries": (authority_fixtures.DELIVERY,)},
            {"block_spec": replace(current.node("workload").block_spec,
                verification=VerificationContract((HttpCheck(check_id="own", provider_socket="control", path="/health"),)))},
            {"configuration_artifacts": (application,), "public_environment": ()})
        for index, changes in enumerate(cases):
            with self.subTest(case=index):
                self.refused(*(graph.update_node(replace(graph.node("workload"), **changes))
                    for graph in (current, desired)))
        document = json.loads(wrapper.content)
        document["target"]["node_id"] = "foreign"
        wrong = replace(wrapper, content=json.dumps(document), source_digest=None)
        self.refused(*(graph.update_node(replace(graph.node("workload"), configuration_artifacts=(wrong, application)))
            for graph in (current, desired)))
        for node_id in ("gateway", "connector"):
            with self.subTest(excluded_target=node_id):
                pair = [current.update_node(replace(current.node(node_id),
                    metadata=graph.node("workload").metadata)) for graph in (current, desired)]
                self.refused(*pair)


class ManagedSelectedConsumerUpdateTests(unittest.TestCase):
    """#1991: three separate retained updates over a complete selected use."""

    def pair(self, family):
        from control_plane_kit_core.secret_selections import SecretVersionSelection
        delivery = SecretEnvironmentDelivery("APPLICATION_TOKEN",
            SecretReference("secret://workspace/application"), SecretUseIntent.APPLICATION_CONTROL_TOKEN)
        current, image, _, _ = ManagedImageUpdateTests().pair(secret_deliveries=(delivery,))
        selection = SecretVersionSelection(delivery.reference, delivery.intent, "version-1")
        current, image = tuple(value.update_node(replace(value.node("workload"),
            secret_selections=(selection,))) for value in (current, image))
        node = current.node("workload")
        if family == "version":
            desired = current.update_node(replace(node,
                secret_selections=(replace(selection, version_id="version-2"),)))
        elif family == "image":
            desired = image
        else:
            desired = current.update_node(replace(node, configuration_artifacts=tuple(
                replace(value, content='{"mode":"green"}', source_digest=None)
                if value.artifact_id == "application" else value
                for value in node.configuration_artifacts)))
        return current, desired

    compile = ManagedImageUpdateTests.compile
    refused = ManagedImageUpdateTests.refused

    def test_each_family_has_one_disruptive_reconcile_then_desired_health(self):
        for family in ("version", "image", "content"):
            with self.subTest(family=family):
                current, desired = self.pair(family)
                codec = GraphDescriptorCodec()
                original = codec.encode(current), codec.encode(desired)
                plan = self.compile(current, desired)
                self.assertTrue(plan.ready_for_execution)
                self.assertEqual(plan, self.compile(*(codec.decode(value) for value in original)))
                reconcile, = (value for value in plan.activities if isinstance(value.operation, ReconcileNode))
                self.assertEqual(reconcile.operation.target.node_id, "workload")
                self.assertEqual(reconcile.impact.value, "disruptive")
                health, = (value for value in plan.activities if isinstance(value.operation, core.ObserveNodeHealth))
                self.assertIn(reconcile.activity_id, predecessors(plan, health))
                self.assertIs(health.operation.target.graph_side, core.PlanGraphSide.DESIRED_GRAPH)
                stages = {value.operation.stage: value for value in plan.activities
                    if isinstance(value.operation, core.ObserveManagementBootstrap)}
                chain = [stages[stage] for stage in (core.ManagementBootstrapStage.GATEWAY_LOCAL_READY,
                    core.ManagementBootstrapStage.CONNECTOR_CONNECTED,
                    core.ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)] + [health]
                for before, after in zip(chain, chain[1:]):
                    self.assertIn(before.activity_id, predecessors(plan, after))
                self.assertFalse(any(isinstance(value.operation, (StartNode, StartRuntime,
                    AllocatePublicIngress, StopNode, RemoveNodeResource, ReconcileRuntime)) for value in plan.activities))
                self.assertEqual(self.compile(desired, desired).activities, ())
                self.assertEqual((codec.encode(current), codec.encode(desired)), original)

    def test_unresolved_before_or_after_is_not_an_eligible_retained_consumer(self):
        for family in ("version", "image", "content"):
            for side in (0, 1):
                with self.subTest(family=family, side=side):
                    pair = list(self.pair(family))
                    node = pair[side].node("workload")
                    selection = node.secret_selections[0]
                    pair[side] = pair[side].update_node(replace(node,
                        secret_selections=(UnselectedSecretUse(selection.reference, selection.intent),)))
                    self.refused(*pair)

    def test_combined_edits_and_delivery_slot_changes_are_refused(self):
        current, version = self.pair("version")
        _, image = self.pair("image")
        _, content = self.pair("content")
        node = version.node("workload")
        for changed in (
            replace(node, metadata=image.node("workload").metadata),
            replace(node, configuration_artifacts=content.node("workload").configuration_artifacts),
            replace(image.node("workload"), configuration_artifacts=content.node("workload").configuration_artifacts),
            replace(node, secret_deliveries=(replace(node.secret_deliveries[0], environment_name="OTHER_TOKEN"),)),
            replace(node, secret_deliveries=(), secret_selections=()),
            replace(node, configuration_artifacts=node.configuration_artifacts[:-1]),
        ):
            with self.subTest(changed=changed.secret_selections):
                self.refused(current, current.update_node(changed))
        self.refused(current, version.update_node(replace(version.node("connector"), metadata={"changed": True})))

    def test_one_version_changes_while_another_use_stays_selected(self):
        from control_plane_kit_core.secret_selections import SecretVersionSelection
        current, desired = self.pair("version")
        second = SecretEnvironmentDelivery("SECOND_TOKEN", SecretReference("secret://workspace/second"),
            SecretUseIntent.APPLICATION_CONTROL_TOKEN)
        selection = SecretVersionSelection(second.reference, second.intent, "second-1")
        current, desired = tuple(graph.update_node(replace(graph.node("workload"),
            secret_deliveries=(*graph.node("workload").secret_deliveries, second),
            secret_selections=(*graph.node("workload").secret_selections, selection)))
            for graph in (current, desired))
        self.assertTrue(self.compile(current, desired).ready_for_execution)
        node = desired.node("workload")
        combined = desired.update_node(replace(node, secret_selections=tuple(
            replace(value, version_id="second-2") if value.reference == second.reference else value
            for value in node.secret_selections)))
        self.refused(current, combined)

    def test_two_retained_consumers_cannot_change_selection_together(self):
        from control_plane_kit_core.secret_selections import SecretVersionSelection
        _, current, _ = managed_update_graphs()
        node_ids = tuple(name for name in current.nodes if name not in ("gateway", "connector"))
        self.assertEqual(len(node_ids), 2)
        configured = self.pair("version")[0].node("workload")
        for name in node_ids:
            delivery = SecretEnvironmentDelivery("TOKEN", SecretReference("secret://workspace/" + name),
                SecretUseIntent.APPLICATION_CONTROL_TOKEN)
            artifacts = {value.artifact_id: value for value in configured.configuration_artifacts}
            wrapper, application = artifacts["receiver"], artifacts["application"]
            document = json.loads(wrapper.content)
            document["target"]["node_id"] = name
            document["target"]["receiver_id"] = ("a" if name == "workload" else "b") * 32
            wrapper = replace(wrapper, content=json.dumps(document), source_digest=None)
            current = current.update_node(replace(current.node(name), secret_deliveries=(delivery,),
                configuration_artifacts=tuple(sorted((wrapper, application))),
                public_environment=configured.public_environment,
                secret_selections=(SecretVersionSelection(delivery.reference, delivery.intent, "version-1"),)))
        desired = current
        for name in node_ids:
            node = desired.node(name)
            updated = replace(node, secret_selections=(replace(node.secret_selections[0], version_id="version-2"),))
            self.assertTrue(self.compile(current, current.update_node(updated)).ready_for_execution)
            desired = desired.update_node(updated)
        self.refused(current, desired)


class ManagedVerifierRefreshTests(unittest.TestCase):
    """#1976: only public verifier values may change on one exact receiver."""

    compile = ManagedImageUpdateTests.compile
    refused = ManagedImageUpdateTests.refused

    def pair(self, *, selected=False):
        if selected:
            current, _ = ManagedSelectedConsumerUpdateTests().pair("version")
        else:
            current, _, _, _ = ManagedImageUpdateTests().pair()
        wrapper = current.node("workload").configuration_artifacts[0]
        document = json.loads(wrapper.content)
        key = document["verifiers"][0]["public_keys"][0]
        document["verifiers"][0]["public_keys"].append({**key,
            "key_id": "public-b", "public_key_pem": key["public_key_pem"].replace("aaaaaaaa", "bbbbbbbb")})
        return current, self.with_wrapper(current, replace(wrapper,
            content=json.dumps(document), source_digest=None))

    def with_wrapper(self, graph, wrapper):
        node = graph.node("workload")
        return graph.update_node(replace(node,
            configuration_artifacts=(wrapper, *node.configuration_artifacts[1:])))

    def test_refresh_reconciles_before_desired_protected_health_without_other_changes(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                current, desired = self.pair(selected=selected)
                codec = GraphDescriptorCodec()
                original = codec.encode(current), codec.encode(desired)
                plan = self.compile(current, desired)
                self.assertTrue(plan.ready_for_execution)
                self.assertEqual(plan, self.compile(*(codec.decode(value) for value in original)))
                plan_codec = ActivityPlanDescriptorCodec()
                self.assertEqual(plan_codec.decode(plan_codec.encode(plan)), plan)
                reconcile, = (a for a in plan.activities if isinstance(a.operation, ReconcileNode))
                self.assertEqual(reconcile.operation.target.node_id, "workload")
                self.assertIs(reconcile.impact, core.ActivityImpact.DISRUPTIVE)
                health, = (a for a in plan.activities if isinstance(a.operation, core.ObserveNodeHealth))
                self.assertIn(reconcile.activity_id, predecessors(plan, health))
                self.assertIs(health.operation.target.graph_side, core.PlanGraphSide.DESIRED_GRAPH)
                resolved = core.resolve_management_observation(health.operation,
                    validate_graph(current), validate_graph(desired), expected_operation=health.operation)
                self.assertEqual(resolved.workload_node, desired.node("workload"))
                stages = {a.operation.stage: a for a in plan.activities
                    if isinstance(a.operation, core.ObserveManagementBootstrap)}
                chain = [stages[stage] for stage in (core.ManagementBootstrapStage.GATEWAY_LOCAL_READY,
                    core.ManagementBootstrapStage.CONNECTOR_CONNECTED,
                    core.ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)] + [health]
                for before, after in zip(chain, chain[1:]):
                    self.assertIn(before.activity_id, predecessors(plan, after))
                self.assertFalse(any(isinstance(a.operation, (StartNode, StartRuntime, AllocatePublicIngress,
                    StopNode, RemoveNodeResource, ReconcileRuntime)) for a in plan.activities))
                self.assertEqual((codec.encode(current), codec.encode(desired)), original)

    def test_equal_graph_is_empty_but_equivalent_wrapper_rewrites_are_not_refresh(self):
        _, current = self.pair()
        self.assertEqual(self.compile(current, current).activities, ())
        wrapper = current.node("workload").configuration_artifacts[0]
        document = json.loads(wrapper.content)
        document["verifiers"].reverse()
        for family in document["verifiers"]:
            family["public_keys"].reverse()
        for changed in (replace(wrapper, content=json.dumps(document), source_digest=None),
                replace(wrapper, content=json.dumps(json.loads(wrapper.content), indent=2), source_digest=None),
                replace(wrapper, source_digest="f" * 64)):
            self.refused(current, self.with_wrapper(current, changed))

    def test_every_receiver_target_field_and_declaration_are_preserved(self):
        current, desired = self.pair()
        wrapper = desired.node("workload").configuration_artifacts[0]
        for field, value in (("workspace_id", "other-workspace"), ("runtime_id", "other-runtime"),
                ("node_id", "other-node"), ("provider_socket_name", "other"), ("receiver_id", "b" * 32)):
            with self.subTest(field=field):
                document = json.loads(wrapper.content)
                document["target"][field] = value
                self.refused(current, self.with_wrapper(desired,
                    replace(wrapper, content=json.dumps(document), source_digest=None)))
        document = json.loads(wrapper.content)
        document["declaration"] = core.WorkloadNodeControlSurfaceDeclaration(
            surface(kinds=(NodeHealthReadKind.LIVENESS,)),
            profile=core.WorkloadNodeControlSurfaceDeclarationProfile.V2).descriptor()
        self.refused(current, self.with_wrapper(desired,
            replace(wrapper, content=json.dumps(document), source_digest=None)))

    def test_both_actual_wrappers_must_be_strict_v2_material(self):
        current, desired = self.pair()
        for side in (0, 1):
            for content in ('{}', '{"profile":"workload-node-control-configuration.v1"}'):
                with self.subTest(side=side, content=content):
                    pair = [current, desired]
                    wrapper = pair[side].node("workload").configuration_artifacts[0]
                    pair[side] = self.with_wrapper(pair[side], replace(wrapper, content=content, source_digest=None))
                    self.refused(*pair)

    def test_wrapper_slot_and_other_graph_deltas_are_not_verifier_only(self):
        current, desired = self.pair(selected=True)
        node = desired.node("workload")
        wrapper, application = node.configuration_artifacts
        for changed in (replace(wrapper, artifact_id="other"), replace(wrapper, target_path="/etc/other.json"),
                replace(wrapper, media_type=ConfigurationMediaType.TEXT),
                replace(wrapper, file_mode=ConfigurationFileMode.OWNER_READ_ONLY)):
            with self.subTest(wrapper=changed.artifact_id, path=changed.target_path,
                    media=changed.media_type, mode=changed.file_mode):
                self.refused(current, self.with_wrapper(desired, changed))
        cases = (
            desired.update_node(replace(node, configuration_artifacts=(wrapper,
                replace(application, content='{"mode":"green"}', source_digest=None)))),
            desired.update_node(replace(node, metadata={**node.metadata, "oci_image":
                "registry.example.invalid/apps/receiver@sha256:" + "c" * 64})),
            desired.update_node(replace(node, secret_selections=(replace(node.secret_selections[0], version_id="version-2"),))),
            desired.update_node(replace(node, configuration_artifacts=(wrapper,))),
            desired.update_node(replace(desired.node("connector"), metadata={"changed": True})),
            replace(desired, name="other"),
        )
        for index, candidate in enumerate(cases):
            with self.subTest(case=index):
                self.refused(current, candidate)

    def test_retained_eligibility_and_complete_selection_still_apply(self):
        current, desired = self.pair(selected=True)
        for lifecycle in (ResourceLifecycle.external(), ResourceLifecycle.owned_with_retained_data("data")):
            self.refused(*(graph.update_node(replace(graph.node("workload"), lifecycle=lifecycle))
                for graph in (current, desired)))
        for side in (0, 1):
            pair = [current, desired]
            node = pair[side].node("workload")
            selection = node.secret_selections[0]
            pair[side] = pair[side].update_node(replace(node,
                secret_selections=(UnselectedSecretUse(selection.reference, selection.intent),)))
            self.refused(*pair)


if __name__ == "__main__":
    unittest.main()
