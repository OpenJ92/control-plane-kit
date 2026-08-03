from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit_core.configuration import (
    ConfigurationArtifact,
    ConfigurationMediaType,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductCatalog,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProductInstantiationError,
    ProductRuntimeContract,
    ProviderRuntimePort,
    RequirementSecretDelivery,
    instantiate_catalog_product,
    instantiate_product,
)
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
    SecretReferenceEnvironmentDelivery,
    SecretUseIntent,
)
from control_plane_kit_core.topology import GraphDescriptorCodec, compile_topology
from control_plane_kit_core.types import BlockFamily, Protocol, SocketBinding


VALID_DIGEST = "sha256:" + "e" * 64


class ProductInstantiationTests(unittest.TestCase):
    def product(self) -> ContainerServerProduct:
        return ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "hello", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/hello",
                VALID_DIGEST,
                tag="v1",
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(
                    providers=(
                        ProviderSocket("http", Protocol.HTTP),
                    )
                ),
                provider_ports=(ProviderRuntimePort("http", 8000),),
                public_environment=(PublicStaticEnvironmentBinding("LOG_LEVEL", "info"),),
                configuration_artifacts=(
                    ConfigurationArtifact(
                        "settings",
                        "/etc/hello/settings.json",
                        ConfigurationMediaType.JSON,
                        '{"message":"hello"}',
                    ),
                ),
                secret_deliveries=(
                    SecretReferenceEnvironmentDelivery(
                        "API_KEY_REF",
                        SecretReference("secret://local/hello/api-key"),
                    ),
                ),
            ),
            display_name="Hello server",
        )

    def configuration(self) -> ProductInstanceConfiguration:
        return ProductInstanceConfiguration.from_contract(
            self.product().runtime_contract
        )

    def test_instantiates_catalogued_product_into_ordinary_application_block(self) -> None:
        document = ProductDescriptorCodec().encode_document(self.product())
        catalog = ProductCatalog.from_documents((document,))

        block = instantiate_catalog_product(
            catalog,
            ProductIdentity("cpk-servers", "hello", 1),
            role_id="hello-blue",
            configuration=self.configuration(),
        )

        self.assertEqual(block.block_id, "hello-blue")
        self.assertEqual(block.sockets.provider_names(), ("http",))
        self.assertEqual(block.spec.display_name, "Hello server")
        self.assertEqual(block.implementation.kind, "oci-container")

        graph = compile_topology(
            DeploymentTopology("hello", DockerRuntime(children=(block,)))
        )
        node = graph.node("hello-blue")

        self.assertEqual(node.block_family, BlockFamily.APPLICATION)
        self.assertEqual(node.kind, "oci-container")
        self.assertEqual(node.endpoint("http").url, "http://hello-blue:8000")
        self.assertEqual(node.metadata["product_identity"], "cpk-servers/hello/1")
        self.assertEqual(node.metadata["product_descriptor_digest"], document.content_digest)
        self.assertEqual(node.metadata["oci_image"], self.product().image.execution_reference)
        self.assertEqual(node.non_secret_environment()["LOG_LEVEL"], "info")
        self.assertEqual(node.configuration_artifacts[0].artifact_id, "settings")
        self.assertEqual(node.secret_deliveries[0].descriptor()["reference_id"], "secret://local/hello/api-key")

    def test_instantiated_product_graph_round_trips_through_existing_codec(self) -> None:
        block = instantiate_product(
            self.product(),
            role_id="hello-blue",
            configuration=self.configuration(),
        )
        graph = compile_topology(
            DeploymentTopology("hello", DockerRuntime(children=(block,)))
        )
        codec = GraphDescriptorCodec()

        self.assertEqual(codec.decode(codec.encode(graph)), graph)

    def test_configuration_must_match_product_contract_exactly(self) -> None:
        product = self.product()
        missing = ProductInstanceConfiguration()
        extra = ProductInstanceConfiguration(
            public_environment=(
                *self.configuration().public_environment,
                PublicStaticEnvironmentBinding("EXTRA_VALUE", "no"),
            ),
            configuration_artifacts=self.configuration().configuration_artifacts,
            secret_deliveries=self.configuration().secret_deliveries,
        )

        with self.assertRaisesRegex(ProductInstantiationError, "public environment"):
            instantiate_product(product, "hello-blue", missing)
        with self.assertRaisesRegex(ProductInstantiationError, "public environment"):
            instantiate_product(product, "hello-blue", extra)

    def test_configuration_values_may_change_without_changing_product_contract(self) -> None:
        product = self.product()
        configured = ProductInstanceConfiguration(
            public_environment=(PublicStaticEnvironmentBinding("LOG_LEVEL", "debug"),),
            configuration_artifacts=self.configuration().configuration_artifacts,
            secret_deliveries=self.configuration().secret_deliveries,
        )

        block = instantiate_product(product, "hello-blue", configured)
        graph = compile_topology(
            DeploymentTopology("hello", DockerRuntime(children=(block,)))
        )

        self.assertEqual(graph.node("hello-blue").non_secret_environment()["LOG_LEVEL"], "debug")

    def test_requirement_secret_reference_is_configured_but_inactive_without_edge(
        self,
    ) -> None:
        default_delivery = SecretEnvironmentDelivery(
            "DATABASE_PASSWORD",
            SecretReference("secret://local/default/password"),
            SecretUseIntent.POSTGRES_PASSWORD,
        )
        product = ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "database-client", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/database-client",
                VALID_DIGEST,
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(
                    requirements=(
                        RequirementSocket(
                            "database",
                            Protocol.POSTGRES,
                            (),
                            required=False,
                            binding=SocketBinding.RUNTIME_CONTROL,
                            secret_deliveries=(default_delivery,),
                        ),
                    ),
                ),
            ),
            display_name="Database client",
        )
        configured_delivery = SecretEnvironmentDelivery(
            "DATABASE_PASSWORD",
            SecretReference("secret://workspace-a/database/password"),
            SecretUseIntent.POSTGRES_PASSWORD,
        )
        configuration = ProductInstanceConfiguration(
            requirement_secret_deliveries=(
                RequirementSecretDelivery("database", configured_delivery),
            ),
        )

        block = instantiate_product(product, "client", configuration)
        graph = compile_topology(
            DeploymentTopology("client", DockerRuntime(children=(block,)))
        )

        self.assertEqual(
            block.sockets.requirement("database").secret_deliveries,
            (configured_delivery,),
        )
        self.assertEqual(graph.node("client").secret_deliveries, ())
        self.assertEqual(
            graph.node("client").requirement_socket("database").secret_deliveries,
            (configured_delivery,),
        )
        codec = GraphDescriptorCodec()
        self.assertEqual(codec.decode(codec.encode(graph)), graph)

    def test_requirement_secret_configuration_must_match_contract_exactly(self) -> None:
        delivery = SecretEnvironmentDelivery(
            "DATABASE_PASSWORD",
            SecretReference("secret://local/default/password"),
            SecretUseIntent.POSTGRES_PASSWORD,
        )
        product = ContainerServerProduct(
            identity=ProductIdentity("cpk-servers", "database-client", 1),
            image=OciImageReference(
                "ghcr.io",
                "openj92/control-plane-kit-servers/database-client",
                VALID_DIGEST,
            ),
            runtime_contract=ProductRuntimeContract(
                sockets=BlockSockets(
                    requirements=(
                        RequirementSocket(
                            "database",
                            Protocol.POSTGRES,
                            (),
                            required=False,
                            binding=SocketBinding.RUNTIME_CONTROL,
                            secret_deliveries=(delivery,),
                        ),
                    ),
                ),
            ),
            display_name="Database client",
        )

        with self.assertRaisesRegex(
            ProductInstantiationError,
            "requirement secret deliveries",
        ):
            instantiate_product(product, "client", ProductInstanceConfiguration())

        wrong_intent = SecretEnvironmentDelivery(
            "DATABASE_PASSWORD",
            SecretReference("secret://workspace-a/database/password"),
            SecretUseIntent.APPLICATION_CONTROL_TOKEN,
        )
        with self.assertRaisesRegex(
            ProductInstantiationError,
            "requirement secret deliveries",
        ):
            instantiate_product(
                product,
                "client",
                ProductInstanceConfiguration(
                    requirement_secret_deliveries=(
                        RequirementSecretDelivery("database", wrong_intent),
                    ),
                ),
            )

        with self.assertRaisesRegex(
            ProductInstantiationError,
            "requirement secret deliveries must be unique",
        ):
            ProductInstanceConfiguration(
                requirement_secret_deliveries=(
                    RequirementSecretDelivery("database", delivery),
                    RequirementSecretDelivery(
                        "database",
                        SecretEnvironmentDelivery(
                            "DATABASE_PASSWORD",
                            SecretReference("secret://workspace-b/database/password"),
                            SecretUseIntent.POSTGRES_PASSWORD,
                        ),
                    ),
                ),
            )

    def test_unconditional_secret_reference_may_be_configured_per_instance(self) -> None:
        product = self.product()
        configured = ProductInstanceConfiguration(
            public_environment=self.configuration().public_environment,
            configuration_artifacts=self.configuration().configuration_artifacts,
            secret_deliveries=(
                SecretReferenceEnvironmentDelivery(
                    "API_KEY_REF",
                    SecretReference("secret://workspace-a/hello/api-key"),
                ),
            ),
        )

        block = instantiate_product(product, "hello-blue", configured)
        graph = compile_topology(
            DeploymentTopology("hello", DockerRuntime(children=(block,)))
        )

        self.assertEqual(
            graph.node("hello-blue").secret_deliveries[0].reference.reference_id,
            "secret://workspace-a/hello/api-key",
        )

    def test_invalid_role_identity_fails_before_topology_compilation(self) -> None:
        with self.assertRaisesRegex(ProductInstantiationError, "role_id"):
            instantiate_product(self.product(), "../hello", self.configuration())

    def test_product_instantiation_module_has_no_effect_loading(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden_calls = {"open", "__import__"}
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)

        self.assertEqual(calls & forbidden_calls, set())


if __name__ == "__main__":
    unittest.main()
