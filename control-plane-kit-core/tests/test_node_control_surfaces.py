from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

import control_plane_kit_core as core
import control_plane_kit_core.node_control as node_control
from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableKind,
    ControlPlaneVariableOperationContract,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductRuntimeContract,
    ProductRuntimeContractCodec,
    ProductRuntimeContractError,
    instantiate_product,
)
from control_plane_kit_core.topology import (
    FieldSubject,
    GraphDescriptorCodec,
    ModifiedChange,
    StructuralField,
    ValidationCode,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import Protocol

from tests.test_graph_codec import PureImplementation


VALID_DIGEST = "sha256:" + "4" * 64
LEGACY_PRODUCT_SHA256 = (
    "47b035629b76ebcb2c8f8ad5aff8b4b566bf7b79cfed0902836414cd27691027"
)


class WorkloadNodeControlSurfaceTests(unittest.TestCase):
    def surface_type(self):
        value = getattr(
            node_control,
            "WorkloadNodeControlSurfaceDescriptor",
            None,
        )
        self.assertIsNotNone(
            value,
            "WorkloadNodeControlSurfaceDescriptor is not implemented",
        )
        return value

    def surface_codec(self):
        value = getattr(
            node_control,
            "WorkloadNodeControlSurfaceDescriptorCodec",
            None,
        )
        self.assertIsNotNone(
            value,
            "WorkloadNodeControlSurfaceDescriptorCodec is not implemented",
        )
        return value()

    def variable(self, name: str) -> ControlPlaneVariableDescriptor:
        return ControlPlaneVariableDescriptor(
            variable_name=NodeControlGraphReference(
                NodeControlGraphReferenceRole.VARIABLE,
                name,
            ),
            kind=ControlPlaneVariableKind.SCALAR,
            state_codec=ControlPlaneStateCodec.SCALAR_V1,
            operation_contracts=(
                ControlPlaneVariableOperationContract(
                    operation=NodeControlOperation.READ_STATE,
                    command_codec=None,
                    result_codec=ControlPlaneResultCodec.STATE_V1,
                ),
                ControlPlaneVariableOperationContract(
                    operation=NodeControlOperation.APPLY_COMMAND,
                    command_codec=ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                    result_codec=ControlPlaneResultCodec.TRANSITION_V1,
                ),
            ),
            description=f"Public {name} control variable.",
        )

    def surface(self, socket: str, *variables: str):
        return self.surface_type()(
            provider_socket_name=NodeControlGraphReference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                socket,
            ),
            variables=tuple(self.variable(name) for name in variables),
        )

    def product(self, *surfaces) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "controlled-router", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/controlled-router",
                VALID_DIGEST,
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(
                    providers=tuple(
                        ProviderSocket(name, Protocol.HTTP)
                        for name in sorted(
                            surface.provider_socket_name.value
                            for surface in surfaces
                        )
                    )
                ),
                capabilities=(CapabilityName.NODE_CONTROLLABLE,),
                control_surfaces=tuple(surfaces),
            ),
        )

    def graph(self, *surfaces):
        product = self.product(*surfaces)
        block = instantiate_product(
            product,
            "router",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )
        return compile_topology(
            DeploymentTopology("controlled", DockerRuntime(children=(block,)))
        )

    def test_surface_codec_is_bounded_strict_and_canonical(self) -> None:
        surface = self.surface("control", "zeta", "alpha")
        codec = self.surface_codec()

        encoded = codec.encode(surface)

        self.assertEqual(
            [item["variable_name"] for item in encoded["variables"]],
            ["alpha", "zeta"],
        )
        self.assertEqual(codec.decode(encoded), surface)
        self.assertEqual(surface.provider_socket_name.role, NodeControlGraphReferenceRole.PROVIDER_SOCKET)
        self.assertNotIn("http://", repr(surface))
        self.assertNotIn("token", repr(surface))

        with self.assertRaisesRegex(ValueError, "at least one"):
            self.surface("control")
        with self.assertRaisesRegex(ValueError, "unique"):
            self.surface("control", "routing", "routing")
        with self.assertRaises(ValueError):
            codec.decode({**encoded, "future": True})

    def test_product_contract_binds_surfaces_and_capability_biconditionally(self) -> None:
        surface = self.surface("control", "routing")
        contract = self.product(surface).runtime_contract

        self.assertEqual(contract.control_surfaces, (surface,))

        with self.assertRaises(ProductRuntimeContractError):
            ProductRuntimeContract(
                sockets=BlockSockets(
                    providers=(ProviderSocket("control", Protocol.HTTP),)
                ),
                control_surfaces=(surface,),
            )
        with self.assertRaises(ProductRuntimeContractError):
            ProductRuntimeContract(
                capabilities=(CapabilityName.NODE_CONTROLLABLE,),
            )
        with self.assertRaises(ProductRuntimeContractError):
            ProductRuntimeContract(
                sockets=BlockSockets(),
                capabilities=(CapabilityName.NODE_CONTROLLABLE,),
                control_surfaces=(surface,),
            )
        with self.assertRaises(ProductRuntimeContractError):
            ProductRuntimeContract(
                sockets=BlockSockets(
                    providers=(ProviderSocket("control", Protocol.POSTGRES),)
                ),
                capabilities=(CapabilityName.NODE_CONTROLLABLE,),
                control_surfaces=(surface,),
            )

    def test_surface_identity_is_socket_plus_variable_and_order_is_canonical(self) -> None:
        alpha = self.surface("alpha-control", "routing")
        beta = self.surface("beta-control", "routing")

        contract = self.product(beta, alpha).runtime_contract

        self.assertEqual(
            tuple(
                surface.provider_socket_name.value
                for surface in contract.control_surfaces
            ),
            ("alpha-control", "beta-control"),
        )
        with self.assertRaises(ProductRuntimeContractError):
            replace(contract, control_surfaces=(alpha, alpha))

    def test_product_codec_accepts_only_legacy_or_nonempty_surface_shape(self) -> None:
        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "external-products"
            / "proxy"
            / "product.cpk.json"
        ).read_bytes()
        legacy = ProductDescriptorCodec().decode_document(fixture)

        self.assertEqual(legacy.content, fixture)
        self.assertEqual(hashlib.sha256(fixture).hexdigest(), LEGACY_PRODUCT_SHA256)
        self.assertNotIn(
            "control_surfaces",
            legacy.product.runtime_contract.descriptor(),
        )

        surface = self.surface("control", "routing")
        contract = self.product(surface).runtime_contract
        codec = ProductRuntimeContractCodec()
        encoded = codec.encode(contract)
        self.assertEqual(codec.decode(encoded), contract)
        self.assertEqual(len(encoded["control_surfaces"]), 1)

        explicit_empty = dict(codec.encode(ProductRuntimeContract()))
        explicit_empty["control_surfaces"] = []
        with self.assertRaises(ProductRuntimeContractError):
            codec.decode(explicit_empty)
        with self.assertRaises(ProductRuntimeContractError):
            codec.decode({**encoded, "future": []})

    def test_product_surface_survives_instantiation_graph_round_trip_and_diff(self) -> None:
        before = self.graph(self.surface("control", "routing"))
        after = self.graph(self.surface("control", "routing", "mode"))
        codec = GraphDescriptorCodec()

        restored = codec.decode(codec.encode(before))
        self.assertEqual(
            restored.node("router").block_spec.control_surfaces,
            before.node("router").block_spec.control_surfaces,
        )

        diff = diff_graphs(validate_graph(before), validate_graph(after))
        block_changes = [
            change
            for change in diff.changes
            if isinstance(change, ModifiedChange)
            and isinstance(change.subject, FieldSubject)
            and change.subject.field is StructuralField.BLOCK_SPECIFICATION
        ]
        self.assertEqual(len(block_changes), 1)

    def test_graph_validation_proves_surface_socket_exists_and_is_http(self) -> None:
        surface = self.surface("control", "routing")

        for providers in (
            (),
            (ProviderSocket("control", Protocol.POSTGRES),),
        ):
            with self.subTest(providers=providers):
                block = ApplicationBlock(
                    spec=BlockSpec(
                        role_id="router",
                        capabilities=(CapabilityName.NODE_CONTROLLABLE,),
                        control_surfaces=(surface,),
                    ),
                    implementation=PureImplementation("test", {}),
                    sockets=BlockSockets(providers=providers),
                )
                graph = compile_topology(
                    DeploymentTopology(
                        "invalid-control",
                        DockerRuntime(children=(block,)),
                    )
                )

                result = validate_graph(graph)

                self.assertFalse(result.valid)
                self.assertIn(
                    ValidationCode.NODE_CONTROL_SURFACE,
                    {finding.code for finding in result.errors},
                )

    def test_legacy_graph_descriptor_bytes_and_checksum_do_not_drift(self) -> None:
        block = ApplicationBlock(
            spec=BlockSpec(role_id="hello"),
            implementation=PureImplementation(
                "test",
                {"http": "http://hello:8000"},
            ),
            sockets=BlockSockets(
                providers=(ProviderSocket("http", Protocol.HTTP),)
            ),
        )
        graph = compile_topology(
            DeploymentTopology("legacy", DockerRuntime(children=(block,)))
        )
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(graph)
        canonical = json.dumps(descriptor, separators=(",", ":")).encode("utf-8")

        self.assertNotIn(
            "control_surfaces",
            descriptor["nodes"]["hello"]["block_spec"],
        )
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            "1df54197127bb66cb0bcb166e0ff8ba70f4e975465f7d8c125eb1beba0ab93d8",
        )

        explicit_empty = json.loads(canonical)
        explicit_empty["nodes"]["hello"]["block_spec"]["control_surfaces"] = []
        with self.assertRaises(ValueError):
            codec.decode(explicit_empty)

    def test_surface_contract_is_root_exported(self) -> None:
        self.assertIs(
            getattr(core, "WorkloadNodeControlSurfaceDescriptor", None),
            self.surface_type(),
        )
        self.assertIsNotNone(
            getattr(core, "WorkloadNodeControlSurfaceDescriptorCodec", None)
        )


if __name__ == "__main__":
    unittest.main()
