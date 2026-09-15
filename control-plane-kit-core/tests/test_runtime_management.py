from dataclasses import replace
import unittest

import control_plane_kit_core as core
from control_plane_kit_core.algebra import BlockSockets, BlockSpec, DeploymentTopology, DockerRuntime, ProviderSocket
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlOperation,
    NodeHealthReadKind, WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.planning import (
    ReconcileRuntime, ReviewChange, StartRuntime, StopRuntime, compile_activity_plan,
)
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductIdentity, ProductInstanceConfiguration,
    ProductRuntimeContract, ProductRuntimeContractCodec, ProviderRuntimePort, instantiate_product,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference, NamedPublicIngress, PublicIngressTarget,
)
from control_plane_kit_core.topology import (
    DeploymentGraph, GraphDescriptorCodec, compile_topology, diff_graphs, validate_graph,
)
from control_plane_kit_core.types import ApplicationProtocol, Protocol, Transport
from control_plane_kit_core.topology.codec import GenericBlockSpecCodec
from control_plane_kit_core.topology.changes import FieldSubject, ModifiedChange, StructuralField
from control_plane_kit_core.topology.validation import RuntimeSubject
import control_plane_kit_core.topology.changes as changes
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL


class RuntimeManagementTests(unittest.TestCase):
    def api(self, name):
        value = getattr(core, name, None)
        self.assertIsNotNone(value, f"public runtime management contract {name} is missing")
        return value

    def surface(self, socket, kind):
        return WorkloadNodeControlSurfaceDescriptor(
            NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, socket),
            (), (kind,),
        )

    def contract(self, *, gateway=False, health=False, two_sockets=False):
        providers = [ProviderSocket("control", Protocol.HTTP)]
        ports = [ProviderRuntimePort("control", 8000)]
        surfaces = (self.surface("control", NodeHealthReadKind.LIVENESS),) if health else ()
        if two_sockets:
            providers.append(ProviderSocket("other", Protocol.HTTP))
            ports.append(ProviderRuntimePort("other", 8001))
            surfaces += (self.surface("other", NodeHealthReadKind.READINESS),)
        options = {}
        if gateway:
            options["gateway_transit"] = self.api("GatewayTransitDeclaration")(
                "control", self.api("GatewayTransitProtocol").NODE_HEALTH_READ_V1,
            )
        return ProductRuntimeContract(
            sockets=BlockSockets(providers=tuple(providers)), provider_ports=tuple(ports),
            capabilities=(CapabilityName.NODE_CONTROLLABLE, CapabilityName.HEALTH_CHECKABLE) if surfaces else (),
            control_surfaces=surfaces, **options,
        )

    def block(self, name, contract):
        product = ContainerServerProduct(
            ProductIdentity("example", name, 1),
            OciImageReference("example.invalid", name, "sha256:" + "1" * 64), contract,
        )
        return instantiate_product(product, name, ProductInstanceConfiguration.from_contract(contract))

    def topology(self, *, management=True, transit=True, two_sockets=False):
        gateway = self.block("gateway", self.contract(gateway=transit, health=True))
        connector = self.block("connector", ProductRuntimeContract())
        workload = self.block("workload", self.contract(health=True, two_sockets=two_sockets))
        ingress = NamedPublicIngress(
            "management", IngressAuthorityReference("ingress-authority"),
            PublicIngressTarget("gateway", "control"), "connector", "management.example.invalid",
        )
        options = {}
        if management:
            options["management"] = self.api("RuntimeManagement")("gateway", "management")
        return DeploymentTopology(
            "explicit", DockerRuntime(runtime_id="runtime", children=(gateway, connector, workload), **options),
            public_ingresses=(ingress,),
        )

    def graph(self, **options):
        return compile_topology(self.topology(**options))

    def select(self, graph, socket="control", kind=NodeHealthReadKind.LIVENESS):
        return self.api("management_ingress_for_health_read")(
            validate_graph(graph), "workload", socket, kind,
        )

    def test_complete_authoring_compiles_and_round_trips_without_inserting_resources(self):
        topology = self.topology()
        graph = compile_topology(topology)
        self.assertTrue(validate_graph(graph).valid)
        self.assertEqual(set(graph.nodes), {"gateway", "connector", "workload"})
        self.assertEqual(graph.edges, {})
        self.assertEqual(graph.public_ingresses, topology.public_ingresses)
        self.assertEqual(graph.runtimes["runtime"].management, topology.root.management)
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph)
        self.assertEqual(descriptor["runtimes"]["runtime"]["management"], {
            "gateway_node_id": "gateway", "management_ingress_id": "management",
        })
        restored = codec.decode(descriptor)
        self.assertEqual(codec.encode(restored), descriptor)
        self.assertEqual(self.select(restored), graph.public_ingresses[0])

    def test_product_transit_declaration_reaches_block_and_graph_without_grant_material(self):
        contract = self.contract(gateway=True)
        codec = ProductRuntimeContractCodec()
        encoded = codec.encode(contract)
        self.assertEqual(encoded["gateway_transit"], {
            "provider_socket_name": "control", "protocol": "gateway-node-health-read-transit.v1",
        })
        self.assertEqual(codec.decode(encoded), contract)
        block = self.block("gateway", contract)
        self.assertEqual(block.spec.gateway_transit, contract.gateway_transit)
        graph = compile_topology(DeploymentTopology("role", DockerRuntime(children=(block,))))
        self.assertEqual(graph.node("gateway").block_spec.gateway_transit, contract.gateway_transit)
        self.assertEqual(contract.secret_deliveries, ())

    def test_absent_management_preserves_legacy_sdk_graph_and_descriptor(self):
        graph = self.graph(management=False, transit=False)
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph)
        self.assertNotIn("management", descriptor["runtimes"]["runtime"])
        self.assertNotIn("gateway_transit", descriptor["nodes"]["gateway"]["block_spec"])
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)
        self.assertTrue(validate_graph(graph).valid)
        self.assertEqual(descriptor["runtimes"]["runtime"], {
            "kind": "docker", "children": ["gateway", "connector", "workload"],
            "authority_ref": None, "metadata": {"network_name": "control-plane-kit-network"},
            "lifecycle": OWNED_EPHEMERAL.descriptor(),
        })
        self.assertEqual(GenericBlockSpecCodec().encode(BlockSpec("plain")), {
            "variant": "block", "role_id": "plain", "display_name": None,
            "health_path": None, "capabilities": [], "verification": {"checks": []}, "metadata": {},
        })

    def test_missing_selection_rejects_health_request_without_invalidating_legacy_graph(self):
        graph = self.graph(management=False, transit=False)
        self.assertTrue(validate_graph(graph).valid)
        with self.assertRaises(ValueError):
            self.select(graph)

    def test_exact_workload_socket_and_kind_are_required_without_readiness_downgrade(self):
        graph = self.graph(two_sockets=True)
        self.assertEqual(self.select(graph), graph.public_ingresses[0])
        self.assertEqual(self.select(graph, "other", NodeHealthReadKind.READINESS), graph.public_ingresses[0])
        for socket, kind in (("control", NodeHealthReadKind.READINESS), ("other", NodeHealthReadKind.LIVENESS), ("missing", NodeHealthReadKind.LIVENESS)):
            with self.subTest(socket=socket, kind=kind), self.assertRaises(ValueError):
                self.select(graph, socket, kind)

    def test_health_transit_does_not_admit_mutation_or_an_unknown_profile(self):
        graph = self.graph()
        with self.assertRaises((TypeError, ValueError)):
            self.select(graph, kind=NodeControlOperation.APPLY_COMMAND)
        descriptor = ProductRuntimeContractCodec().encode(self.contract(gateway=True))
        descriptor["gateway_transit"]["protocol"] = "arbitrary-control-transit"
        with self.assertRaises(ValueError):
            ProductRuntimeContractCodec().decode(descriptor)

    def test_own_sdk_surface_is_not_a_gateway_transit_declaration(self):
        graph = self.graph(transit=False)
        self.assertFalse(validate_graph(graph).valid)
        with self.assertRaises(ValueError):
            GraphDescriptorCodec().encode(graph)

    def test_missing_wrong_runtime_and_application_ingress_targets_are_rejected(self):
        graph = self.graph()
        runtime = graph.runtimes["runtime"]
        variants = (
            replace(graph, nodes={key: node for key, node in graph.nodes.items() if key != "connector"}),
            replace(graph, public_ingresses=()),
            replace(graph, runtimes={"runtime": replace(runtime, management=self.api("RuntimeManagement")("missing", "management"))}),
            replace(graph, public_ingresses=(replace(graph.public_ingresses[0], target=PublicIngressTarget("workload", "control")),)),
        )
        for index, candidate in enumerate(variants):
            with self.subTest(case=index):
                self.assertFalse(validate_graph(candidate).valid)
                with self.assertRaises(ValueError):
                    GraphDescriptorCodec().encode(candidate)

    def test_management_cannot_select_an_otherwise_valid_path_in_another_runtime(self):
        graph = self.graph()
        runtime = graph.runtimes["runtime"]
        candidate = replace(graph,
            nodes={**graph.nodes, **{name: replace(graph.node(name), runtime_id="other") for name in ("gateway", "connector")}},
            runtimes={"runtime": replace(runtime, children=("workload",)), "other": replace(runtime, runtime_id="other", children=("gateway", "connector"), management=None)},
        )
        unbound = replace(candidate, runtimes={**candidate.runtimes, "runtime": replace(candidate.runtimes["runtime"], management=None)})
        self.assertTrue(validate_graph(unbound).valid)
        self.assertFalse(validate_graph(candidate).valid)

    def test_management_ingress_must_target_exact_transit_socket(self):
        graph = self.graph()
        gateway = self.block("gateway", self.contract(gateway=True, health=True, two_sockets=True))
        expanded = compile_topology(DeploymentTopology("gateway", DockerRuntime(runtime_id="runtime", children=(gateway,))))
        candidate = replace(graph, nodes={**graph.nodes, "gateway": expanded.node("gateway")},
            public_ingresses=(replace(graph.public_ingresses[0], target=PublicIngressTarget("gateway", "other")),))
        unbound = replace(candidate, runtimes={"runtime": replace(candidate.runtimes["runtime"], management=None)})
        self.assertTrue(validate_graph(unbound).valid)
        self.assertFalse(validate_graph(candidate).valid)

    def test_transit_role_requires_http_even_without_sdk_control_surface(self):
        declaration = self.api("GatewayTransitDeclaration")("data", self.api("GatewayTransitProtocol").NODE_HEALTH_READ_V1)
        with self.assertRaises(ValueError):
            ProductRuntimeContract(sockets=BlockSockets(providers=(ProviderSocket("data", Protocol.POSTGRES),)),
                provider_ports=(ProviderRuntimePort("data", 5432),), gateway_transit=declaration)

    def test_transit_accepts_value_equivalent_authored_http_protocol(self):
        protocol = Protocol(Transport.TCP, ApplicationProtocol.HTTP)
        self.assertEqual(protocol, Protocol.HTTP)
        self.assertIsNot(protocol, Protocol.HTTP)
        contract = replace(self.contract(gateway=True), sockets=BlockSockets(providers=(ProviderSocket("control", protocol),)))
        gateway = self.block("gateway", contract)
        compiled = compile_topology(DeploymentTopology("gateway", DockerRuntime(runtime_id="runtime", children=(gateway,))))
        graph = self.graph()
        graph = replace(graph, nodes={**graph.nodes, "gateway": compiled.node("gateway")})
        self.assertTrue(validate_graph(graph).valid)
        codec = GraphDescriptorCodec()
        self.assertEqual(self.select(graph), self.select(codec.decode(codec.encode(graph))))

    def test_invalid_graph_selector_error_does_not_echo_graph_label(self):
        graph = replace(self.graph(), name="PRIVATE-LABEL-" * 1000, public_ingresses=())
        invalid = validate_graph(graph)
        self.assertFalse(invalid.valid)
        with self.assertRaises(ValueError) as caught:
            self.api("management_ingress_for_health_read")(invalid, "workload", "control", NodeHealthReadKind.LIVENESS)
        self.assertLess(len(str(caught.exception)), 200)
        self.assertNotIn("PRIVATE-LABEL", str(caught.exception) + repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_nested_management_and_transit_codecs_reject_unknown_fields(self):
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(self.graph())
        descriptor["runtimes"]["runtime"]["management"]["extra"] = "ignored"
        with self.assertRaises(ValueError):
            codec.decode(descriptor)
        contract_codec = ProductRuntimeContractCodec()
        contract = contract_codec.encode(self.contract(gateway=True))
        contract["gateway_transit"]["extra"] = "ignored"
        with self.assertRaises(ValueError):
            contract_codec.decode(contract)

    def assert_management_diff(self, diff, before, after):
        value_type = getattr(changes, "RuntimeManagementValue", None)
        self.assertIsNotNone(value_type, "typed management diff value is missing")
        field = getattr(StructuralField, "RUNTIME_MANAGEMENT", None)
        self.assertIsNotNone(field, "typed management diff field is missing")
        self.assertEqual(diff.changes, (ModifiedChange(FieldSubject(RuntimeSubject("runtime"), field), value_type(before), value_type(after)),))

    def test_runtime_management_change_is_reviewed_not_physical_runtime_reconciliation(self):
        graph = self.graph()
        alternate = replace(graph.public_ingresses[0], ingress_id="alternate", hostname="alternate.example.invalid")
        current = replace(graph, public_ingresses=(*graph.public_ingresses, alternate))
        runtime = current.runtimes["runtime"]
        desired = replace(current, runtimes={"runtime": replace(runtime, management=self.api("RuntimeManagement")("gateway", "alternate"))})
        diff = diff_graphs(validate_graph(current), validate_graph(desired))
        self.assertFalse(diff.empty)
        self.assert_management_diff(diff, runtime.management, desired.runtimes["runtime"].management)
        plan = compile_activity_plan(diff)
        self.assertTrue(any(isinstance(value.operation, ReviewChange) for value in plan.activities))
        self.assertFalse(any(isinstance(value.operation, (ReconcileRuntime, StartRuntime, StopRuntime)) for value in plan.activities))

    def test_management_addition_and_removal_are_typed_review_changes(self):
        managed = self.graph()
        unbound = replace(managed, runtimes={"runtime": replace(managed.runtimes["runtime"], management=None)})
        for current, desired in ((unbound, managed), (managed, unbound)):
            diff = diff_graphs(validate_graph(current), validate_graph(desired))
            self.assert_management_diff(diff, current.runtimes["runtime"].management, desired.runtimes["runtime"].management)
            plan = compile_activity_plan(diff)
            self.assertTrue(plan.activities)
            self.assertTrue(all(isinstance(value.operation, ReviewChange) for value in plan.activities))

    def test_management_references_are_bounded_canonical_identifiers(self):
        management = self.api("RuntimeManagement")
        for invalid in ("", " gateway", "gateway\n", "x" * 4096, "private-key", "cf_tunnel", "eyjexample"):
            for gateway, ingress in ((invalid, "management"), ("gateway", invalid)):
                with self.subTest(gateway=gateway[:20], ingress=ingress[:20]), self.assertRaises(ValueError):
                    management(gateway, ingress)

    def test_transit_socket_retains_ingress_secret_reference_rejection(self):
        declaration = self.api("GatewayTransitDeclaration")
        protocol = self.api("GatewayTransitProtocol").NODE_HEALTH_READ_V1
        for socket in ("private-key", "eyjexample"):
            with self.subTest(socket=socket), self.assertRaises(ValueError) as caught:
                declaration(socket, protocol)
            self.assertNotIn(socket, str(caught.exception))
            self.assertIsNone(caught.exception.__context__)

    def test_direct_nested_codecs_reject_existing_secret_marker_canaries(self):
        management_codec = self.api("RuntimeManagementCodec")()
        for field in ("gateway_node_id", "management_ingress_id"):
            for canary in ("cf_tunnel_do_not_store", "begin-private-key"):
                descriptor = {"gateway_node_id": "gateway", "management_ingress_id": "management"}
                descriptor[field] = canary
                with self.subTest(field=field, canary=canary), self.assertRaises(ValueError) as caught:
                    management_codec.decode(descriptor)
                self.assertNotIn(canary, str(caught.exception) + repr(caught.exception))
                self.assertIsNone(caught.exception.__context__)
        with self.assertRaises(ValueError) as caught:
            self.api("GatewayTransitDeclarationCodec")().decode({
                "provider_socket_name": "private-key",
                "protocol": "gateway-node-health-read-transit.v1",
            })
        self.assertNotIn("private-key", str(caught.exception) + repr(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    def test_unknown_transit_profile_error_has_no_candidate_exception_context(self):
        with self.assertRaises(ValueError) as caught:
            self.api("GatewayTransitDeclarationCodec")().decode({
                "provider_socket_name": "control", "protocol": "PRIVATE-PROFILE-" * 1000,
            })
        self.assertLess(len(str(caught.exception)), 200)
        self.assertNotIn("PRIVATE-PROFILE", str(caught.exception) + repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_equal_and_name_only_management_graphs_remain_no_activity(self):
        graph = self.graph()
        for desired in (graph, replace(graph, name="renamed")):
            plan = compile_activity_plan(diff_graphs(validate_graph(graph), validate_graph(desired)))
            self.assertEqual(plan.activities, ())


if __name__ == "__main__":
    unittest.main()
