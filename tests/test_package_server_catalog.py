from __future__ import annotations

import unittest

from control_plane_kit import (
    CapabilityImplementation,
    CapabilityName,
    ControlRouteSetName,
    DockerRuntime,
    DeploymentRecipe,
    GraphDescriptorCodec,
    ExecutableCapability,
    PACKAGE_SERVER_CONTRACTS,
    PackageServerContract,
    PackageServerProduct,
    ProductMaturity,
    UnsupportedCapability,
    UnknownGraphVariant,
    compile_recipe,
    package_server_contract,
)


class PackageServerCatalogTests(unittest.TestCase):
    def test_catalogue_contains_each_closed_product_exactly_once(self) -> None:
        self.assertEqual(
            {contract.product for contract in PACKAGE_SERVER_CONTRACTS},
            set(PackageServerProduct),
        )
        self.assertEqual(len(PACKAGE_SERVER_CONTRACTS), len(PackageServerProduct))

    def test_teaching_servers_advertise_only_executable_evidence(self) -> None:
        teaching = tuple(
            contract
            for contract in PACKAGE_SERVER_CONTRACTS
            if contract.maturity is ProductMaturity.TEACHING
        )
        expected_probe_paths = {
            PackageServerProduct.HELLO: "/",
            PackageServerProduct.HTTP_PROXY: "/",
            PackageServerProduct.HTTP_ACTIVE_ROUTER: "/",
            PackageServerProduct.HTTP_CIRCUIT_BREAKER: "/health",
            PackageServerProduct.HTTP_MULTIPLEXER: "/",
            PackageServerProduct.HTTP_RATE_LIMITER: "/",
            PackageServerProduct.HTTP_RETRY: "/health",
            PackageServerProduct.HTTP_WEIGHTED_LOAD_BALANCER: "/",
            PackageServerProduct.REQUEST_OBSERVER: "/health",
        }
        self.assertEqual(
            {contract.product for contract in teaching},
            set(expected_probe_paths),
        )

        for contract in teaching:
            with self.subTest(product=contract.product.value):
                self.assertEqual(
                    contract.block.spec.capabilities,
                    tuple(value.capability for value in contract.capabilities),
                )
                self.assertEqual(
                    contract.capabilities[0].implementation,
                    CapabilityImplementation.APPLICATION_PROBE,
                )
                self.assertEqual(
                    contract.capabilities[0].path,
                    expected_probe_paths[contract.product],
                )
                self.assertIsInstance(
                    contract.resolve(CapabilityName.TARGET_MUTABLE),
                    UnsupportedCapability,
                )

        observer = package_server_contract(PackageServerProduct.REQUEST_OBSERVER)
        self.assertEqual(
            observer.block.spec.capabilities,
            (CapabilityName.HEALTH_CHECKABLE, CapabilityName.METRICS_READABLE),
        )
        self.assertEqual(observer.capabilities[0].path, "/health")
        self.assertEqual(
            observer.capabilities[1].route_set,
            ControlRouteSetName.METRICS,
        )

    def test_managed_router_capabilities_are_control_route_backed(self) -> None:
        contract = package_server_contract(PackageServerProduct.MANAGED_HTTP_ROUTER)

        self.assertEqual(contract.maturity, ProductMaturity.OPERATIONAL)
        self.assertEqual(
            tuple(value.capability for value in contract.capabilities),
            (
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityName.TARGET_MUTABLE,
                CapabilityName.SWITCHABLE,
                CapabilityName.DRAINABLE,
            ),
        )
        self.assertTrue(
            all(
                value.implementation is CapabilityImplementation.CONTROL_ROUTE
                for value in contract.capabilities
            )
        )
        self.assertEqual(
            contract.resolve(CapabilityName.SWITCHABLE).route_set,
            ControlRouteSetName.TARGETS,
        )

    def test_contract_rejects_capability_claim_without_evidence(self) -> None:
        block = package_server_contract(PackageServerProduct.HTTP_PROXY).block

        with self.assertRaisesRegex(ValueError, "exactly match"):
            PackageServerContract(
                PackageServerProduct.HTTP_PROXY,
                ProductMaturity.TEACHING,
                block,
                (),
            )

    def test_executable_evidence_shape_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires an absolute path"):
            ExecutableCapability(
                CapabilityName.HEALTH_CHECKABLE,
                CapabilityImplementation.APPLICATION_PROBE,
            )
        with self.assertRaisesRegex(ValueError, "requires a route set"):
            ExecutableCapability(
                CapabilityName.SWITCHABLE,
                CapabilityImplementation.CONTROL_ROUTE,
            )

    def test_matrix_exposes_socket_binding_without_metadata_inference(self) -> None:
        descriptor = package_server_contract(
            PackageServerProduct.MANAGED_HTTP_ROUTER
        ).descriptor()

        self.assertEqual(descriptor["product"], "managed-http-router")
        requirements = {
            value["name"]: value for value in descriptor["requirements"]
        }
        self.assertEqual(requirements["target-blue"]["binding"], "environment")
        self.assertEqual(requirements["active"]["binding"], "runtime-control")
        self.assertEqual(requirements["active"]["env_bindings"], [])
        self.assertEqual(
            requirements["active"]["protocol"],
            {"transport": "tcp", "application": "http"},
        )

    def test_package_product_identity_round_trips_through_default_codec(self) -> None:
        block = package_server_contract(PackageServerProduct.HTTP_MULTIPLEXER).block
        graph = compile_recipe(
            DeploymentRecipe("package-product", DockerRuntime(children=(block,)))
        )
        codec = GraphDescriptorCodec()

        descriptor = codec.encode(graph)
        restored = codec.decode(descriptor)

        self.assertEqual(
            descriptor["nodes"][block.block_id]["block_spec"]["variant"],
            "package-server",
        )
        self.assertEqual(
            descriptor["nodes"][block.block_id]["block_spec"]["product"],
            "http-multiplexer",
        )
        self.assertIs(
            restored.node(block.block_id).block_spec.product,
            PackageServerProduct.HTTP_MULTIPLEXER,
        )

    def test_unknown_persisted_product_fails_closed(self) -> None:
        block = package_server_contract(PackageServerProduct.HTTP_MULTIPLEXER).block
        graph = compile_recipe(
            DeploymentRecipe("unknown-package-product", DockerRuntime(children=(block,)))
        )
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph)
        descriptor["nodes"][block.block_id]["block_spec"]["product"] = "unknown"

        with self.assertRaisesRegex(UnknownGraphVariant, "unknown package server value"):
            codec.decode(descriptor)

    def test_free_form_metadata_cannot_change_typed_product_identity(self) -> None:
        block = package_server_contract(PackageServerProduct.HTTP_PROXY).block
        graph = compile_recipe(
            DeploymentRecipe("typed-product", DockerRuntime(children=(block,)))
        )
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph)
        descriptor["nodes"][block.block_id]["block_spec"]["metadata"][
            "product"
        ] = PackageServerProduct.MANAGED_HTTP_ROUTER.value

        restored = codec.decode(descriptor)

        self.assertIs(
            restored.node(block.block_id).block_spec.product,
            PackageServerProduct.HTTP_PROXY,
        )


if __name__ == "__main__":
    unittest.main()
