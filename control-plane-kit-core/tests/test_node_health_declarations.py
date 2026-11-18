from __future__ import annotations

from dataclasses import replace
import hashlib
import unittest

import rfc8785

import control_plane_kit_core as core
import control_plane_kit_core.control_routes as routes
import control_plane_kit_core.node_control as control
import control_plane_kit_core.node_control_surface_reads as reads
import control_plane_kit_core.node_control_surface_read_results as results
from control_plane_kit_core.algebra import (
    ApplicationBlock, BlockSockets, BlockSpec, DeploymentTopology, DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.capabilities import CapabilityName, HEALTH_CHECKABLE
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductIdentity,
    ProductInstanceConfiguration, ProductRuntimeContract, ProductRuntimeContractCodec,
    ProviderRuntimePort, instantiate_product,
)
from control_plane_kit_core.topology import (
    GraphDescriptorCodec, compile_topology, diff_graphs, validate_graph,
)
from control_plane_kit_core.types import Protocol
from tests import test_node_control_surface_read_results as result_fixtures
from tests import test_node_control_surfaces as surface_fixtures
from tests.test_graph_codec import PureImplementation


class NodeHealthDeclarationTests(unittest.TestCase):
    def kind(self):
        kind = getattr(control, "NodeHealthReadKind", None)
        self.assertIsNotNone(kind, "NodeHealthReadKind declaration is missing")
        return kind

    def surface(self, *variables: str, kinds=None, socket="control"):
        kind = self.kind()
        return control.WorkloadNodeControlSurfaceDescriptor(
            provider_socket_name=control.NodeControlGraphReference(
                control.NodeControlGraphReferenceRole.PROVIDER_SOCKET, socket,
            ),
            variables=tuple(
                surface_fixtures.WorkloadNodeControlSurfaceTests().variable(name)
                for name in variables
            ),
            health_reads=(kind.LIVENESS, kind.READINESS) if kinds is None else kinds,
        )

    def declaration(self, *variables: str):
        surface = self.surface(*variables)
        profile = getattr(reads.WorkloadNodeControlSurfaceDeclarationProfile, "V2", None)
        self.assertIsNotNone(profile, "health declaration v2 is missing")
        return reads.WorkloadNodeControlSurfaceDeclaration(surface, profile=profile)

    def product(self, surface):
        return ContainerServerProduct(
            ProductIdentity("example", "health-service", 1),
            OciImageReference("example.invalid", "health-service", "sha256:" + "4" * 64),
            ProductRuntimeContract(
                sockets=BlockSockets(providers=(
                    ProviderSocket("sql", Protocol.POSTGRES),
                    ProviderSocket("control", Protocol.HTTP),
                )),
                provider_ports=(ProviderRuntimePort("sql", 5432), ProviderRuntimePort("control", 8001)),
                capabilities=(CapabilityName.NODE_CONTROLLABLE, CapabilityName.HEALTH_CHECKABLE),
                control_surfaces=(surface,),
            ),
        )

    def graph(self, surface):
        product = self.product(surface)
        block = instantiate_product(
            product, "service", ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )
        return compile_topology(DeploymentTopology("health", DockerRuntime(children=(block,))))

    def test_health_union_is_canonical_strict_and_explicit(self):
        kind = self.kind()
        surface = self.surface(kinds=(kind.READINESS, kind.LIVENESS))
        self.assertEqual(surface.health_reads, (kind.LIVENESS, kind.READINESS))
        self.assertEqual(surface.descriptor(), {
            "provider_socket_name": "control", "variables": [],
            "health_reads": ["liveness", "readiness"],
        })
        codec = control.WorkloadNodeControlSurfaceDescriptorCodec()
        self.assertEqual(codec.decode(codec.encode(surface)), surface)
        for candidate in ((), (kind.LIVENESS, kind.LIVENESS), [kind.LIVENESS], ("readiness",), (None,)):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.surface(kinds=candidate)
        for candidate in ([], ["unknown"], ["readiness", "readiness"], "readiness", [None]):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                codec.decode({**surface.descriptor(), "health_reads": candidate})
        with self.assertRaises(ValueError):
            codec.decode({**surface.descriptor(), "endpoint": "https://unexpected.invalid"})

    def test_legacy_variable_wire_and_operations_are_preserved(self):
        self.kind()
        legacy = surface_fixtures.WorkloadNodeControlSurfaceTests().surface("control", "mode")
        mixed = replace(legacy, health_reads=(self.kind().READINESS,))
        self.assertNotIn("health_reads", legacy.descriptor())
        self.assertEqual(mixed.variables, legacy.variables)
        self.assertEqual(tuple(c.operation for c in mixed.variables[0].operation_contracts), (
            control.NodeControlOperation.READ_STATE, control.NodeControlOperation.APPLY_COMMAND,
        ))
        with self.assertRaises(ValueError):
            control.WorkloadNodeControlSurfaceDescriptorCodec().decode({**legacy.descriptor(), "health_reads": []})
        restored = replace(mixed, health_reads=())
        self.assertEqual(rfc8785.dumps(restored.descriptor()), rfc8785.dumps(legacy.descriptor()))

    def test_declaration_profiles_are_disjoint_and_canonical(self):
        declaration = self.declaration()
        expected = (
            b'{"profile":"workload-node-control-surface-declaration.v2","surface":'
            b'{"health_reads":["liveness","readiness"],"provider_socket_name":"control","variables":[]}}'
        )
        self.assertEqual(declaration.canonical_bytes(), expected)
        self.assertEqual(declaration.identity().value, hashlib.sha256(expected).hexdigest())
        codec = reads.WorkloadNodeControlSurfaceDeclarationCodec()
        self.assertEqual(codec.decode(codec.encode(declaration)), declaration)
        legacy = reads.WorkloadNodeControlSurfaceDeclaration(
            surface_fixtures.WorkloadNodeControlSurfaceTests().surface("control", "mode"),
        )
        for surface, profile in (
            (declaration.surface, reads.WorkloadNodeControlSurfaceDeclarationProfile.V1),
            (legacy.surface, reads.WorkloadNodeControlSurfaceDeclarationProfile.V2),
        ):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                reads.WorkloadNodeControlSurfaceDeclaration(surface, profile=profile)
            with self.assertRaises(ValueError):
                codec.decode({"profile": profile.value, "surface": surface.descriptor()})
        self.assertNotEqual(legacy.identity(), self.declaration("mode").identity())

    def test_health_product_and_edge_free_graph_preserve_control_socket(self):
        surface = self.surface()
        product = self.product(surface)
        contract_codec = ProductRuntimeContractCodec()
        self.assertEqual(contract_codec.decode(contract_codec.encode(product.runtime_contract)), product.runtime_contract)
        graph = self.graph(surface)
        self.assertFalse(graph.edges)
        self.assertTrue(validate_graph(graph).valid)
        codec = GraphDescriptorCodec()
        restored = codec.decode(codec.encode(graph))
        self.assertEqual(restored.node("service").block_spec.control_surfaces, (surface,))
        self.assertEqual({p.provider_socket: p.container_port for p in product.runtime_contract.provider_ports}, {
            "sql": 5432, "control": 8001,
        })
        changed = self.graph(replace(surface, health_reads=(self.kind().READINESS,)))
        self.assertTrue(diff_graphs(validate_graph(graph), validate_graph(changed)).changes)

    def test_capability_and_http_provider_invariants_reject_invalid_composition(self):
        surface = self.surface()
        product = self.product(surface)
        with self.assertRaises(ValueError):
            replace(product.runtime_contract, capabilities=(CapabilityName.NODE_CONTROLLABLE,))
        with self.assertRaises(ValueError):
            BlockSpec("service", capabilities=(CapabilityName.NODE_CONTROLLABLE,), control_surfaces=(surface,))
        for socket in ("sql", "missing"):
            invalid = replace(surface, provider_socket_name=control.NodeControlGraphReference(
                control.NodeControlGraphReferenceRole.PROVIDER_SOCKET, socket,
            ))
            with self.subTest(socket=socket), self.assertRaises(ValueError):
                self.product(invalid)
            block = ApplicationBlock(
                spec=BlockSpec("service", capabilities=product.runtime_contract.capabilities, control_surfaces=(invalid,)),
                implementation=PureImplementation("test", {}), sockets=product.runtime_contract.sockets,
            )
            graph = compile_topology(DeploymentTopology("invalid", DockerRuntime(children=(block,))))
            self.assertFalse(validate_graph(graph).valid)
        # Existing health-checkable products without the SDK retain their meaning.
        self.assertFalse(ProductRuntimeContract(capabilities=(CapabilityName.HEALTH_CHECKABLE,)).control_surfaces)

    def test_static_health_only_status_is_variable_coverage_not_health(self):
        declaration = self.declaration()
        fixture = result_fixtures.NodeControlSurfaceReadResultTests()
        request = fixture.request(declaration, reads.NodeControlSurfaceReadKind.STATUS)
        codec = results.NodeControlSurfaceReadResultCodec(request, declaration)
        result = codec.status_result(())
        wire = codec.encode(result)
        self.assertEqual(wire["profile"], "workload-node-control-surface-read-result.v2")
        self.assertEqual(wire["installed_variable_names"], [])
        self.assertEqual(wire["variable_registry_coverage"], "none")
        self.assertNotIn("registry_coverage", wire)
        self.assertNotIn("health", wire)
        self.assertEqual(codec.decode(wire), result)
        for change in ({"variable_registry_coverage": "complete"}, {"health": "healthy"}, {"registry_coverage": "complete"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                codec.decode({**wire, **change})

    def test_declared_health_mapping_does_not_reinterpret_legacy_capability(self):
        kind = self.kind()
        surface = self.surface(kinds=(kind.READINESS,))
        mapping = getattr(surface, "health_read_path", None)
        self.assertIsNotNone(mapping, "declared health route mapping is missing")
        self.assertEqual(mapping(kind.READINESS), "/__control/health/readiness")
        self.assertEqual(self.surface().health_read_path(kind.LIVENESS), "/__control/health/liveness")
        for absent in (kind.LIVENESS, "readiness", "../status", None):
            with self.subTest(absent=absent), self.assertRaises(ValueError):
                mapping(absent)
        legacy = surface_fixtures.WorkloadNodeControlSurfaceTests().surface("control", "mode")
        with self.assertRaises(ValueError):
            legacy.health_read_path(kind.READINESS)
        self.assertEqual(HEALTH_CHECKABLE.route_set, routes.ControlRouteSetName.COMMON_STATUS)
        self.assertEqual(next(route.path for route in routes.COMMON_STATUS_ROUTES.routes if route.name == "health"), "/__deploy/health")
        health_routes = getattr(routes, "NODE_HEALTH_ROUTES", None)
        self.assertIsNotNone(health_routes, "closed health route set is missing")
        self.assertEqual(routes.route_set_named("node-health"), health_routes)
        self.assertEqual([(r.name, r.method.value, r.path, r.scope.value) for r in health_routes.routes], [
            ("health-read", "GET", "/__control/health/{health_kind}", "node-health:read"),
        ])
        self.assertIs(core.NodeHealthReadKind, kind)

    def test_mixed_static_disclosure_retains_kind_and_declaration_binding(self):
        declaration = self.declaration("alpha", "beta")
        fixture = result_fixtures.NodeControlSurfaceReadResultTests()
        for read_kind in reads.NodeControlSurfaceReadKind:
            request = fixture.request(declaration, read_kind)
            codec = results.NodeControlSurfaceReadResultCodec(request, declaration)
            if read_kind is reads.NodeControlSurfaceReadKind.CAPABILITIES:
                result = codec.capabilities_result()
                self.assertEqual(result.descriptor()["declaration"], declaration.descriptor())
            else:
                installed = (declaration.surface.variables[0].variable_name,)
                result = codec.status_result(installed)
                self.assertEqual(result.descriptor()["variable_registry_coverage"], "partial")
            wire = codec.encode(result)
            self.assertEqual(codec.decode(wire), result)
            for change in (
                {"profile": "workload-node-control-surface-read-result.v1"},
                {"declaration_identity": "f" * 64}, {"request_digest": "f" * 64},
                {"request_id": "other-read"},
            ):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    codec.decode({**wire, **change})

    def test_v2_status_maximum_is_reachable_and_plus_one_is_rejected(self):
        kind = self.kind()
        fixture = result_fixtures.NodeControlSurfaceReadResultTests()
        legacy = fixture.maximum_status_declaration()
        declaration = reads.WorkloadNodeControlSurfaceDeclaration(
            replace(legacy.surface, health_reads=(kind.LIVENESS, kind.READINESS)),
            profile=reads.WorkloadNodeControlSurfaceDeclarationProfile.V2,
        )
        request = fixture.request(declaration, reads.NodeControlSurfaceReadKind.STATUS, request_id="r" * 128)
        codec = results.NodeControlSurfaceReadResultCodec(request, declaration)
        result = codec.status_result(tuple(v.variable_name for v in declaration.surface.variables))
        self.assertEqual(len(result.canonical_bytes()), 4820)
        self.assertEqual(len(rfc8785.dumps(declaration.surface.descriptor())), 16186)
        candidate = fixture.padded_candidate(codec.encode(result), "installed_variable_names", 4820)
        with self.assertRaisesRegex(ValueError, "aggregate exceeds.*bound"):
            codec.decode(candidate)

    def test_health_capability_bound_and_context_limit_precede_nested_decoding(self):
        kind = self.kind()
        fixture = result_fixtures.NodeControlSurfaceReadResultTests()
        legacy = fixture.maximum_capability_declaration()
        variables = list(legacy.surface.variables)
        # Make room for exactly the forty-byte nonempty health extension.
        variables[0] = replace(variables[0], description=variables[0].description[:-40])
        surface = replace(legacy.surface, variables=tuple(variables), health_reads=(kind.LIVENESS, kind.READINESS))
        declaration = reads.WorkloadNodeControlSurfaceDeclaration(surface, profile=reads.WorkloadNodeControlSurfaceDeclarationProfile.V2)
        request = fixture.request(declaration, reads.NodeControlSurfaceReadKind.CAPABILITIES, request_id="r" * 128)
        codec = results.NodeControlSurfaceReadResultCodec(request, declaration)
        result = codec.capabilities_result()
        self.assertEqual(len(rfc8785.dumps(surface.descriptor())), 16384)
        self.assertEqual(len(declaration.canonical_bytes()), 16453)
        self.assertEqual(len(result.canonical_bytes()), 16902)
        self.assertEqual(codec.decode(codec.encode(result)), result)
        candidate = fixture.padded_candidate(codec.encode(result), "declaration", 16902)
        with self.assertRaisesRegex(ValueError, "aggregate exceeds.*bound"):
            codec.decode(candidate)
        variables[0] = replace(variables[0], description=variables[0].description + "x")
        with self.assertRaises(ValueError):
            replace(surface, variables=tuple(variables))
        small = self.declaration()
        request = fixture.request(small, reads.NodeControlSurfaceReadKind.STATUS)
        codec = results.NodeControlSurfaceReadResultCodec(request, small)
        wire = codec.encode(codec.status_result(()))
        with self.assertRaisesRegex(ValueError, "context.*bound"):
            codec.decode({**wire, "request_id": wire["request_id"] + "x"})


if __name__ == "__main__":
    unittest.main()
