from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import (
    BlockSockets,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
)
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.configuration import (
    ConfigurationArtifact,
    ConfigurationMediaType,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.lifecycle import ResourceLifecycle
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductFamily,
    ProductIdentity,
    ProductInstanceConfiguration,
    ProviderRuntimePort,
    ProductRuntimeContract,
    ProductRuntimeContractCodec,
    ProductRuntimeContractError,
    RetainedDataMount,
    instantiate_product,
)
import control_plane_kit_core.products as products_language
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.types import Protocol, SocketBinding
from control_plane_kit_core.topology import ValidationCode, compile_topology, validate_graph
from control_plane_kit_core.verification import HttpCheck, VerificationContract


def _socket_values() -> tuple[
    tuple[RequirementSocket, RequirementSocket],
    tuple[ProviderSocket, ProviderSocket],
]:
    return (
        (
            RequirementSocket("alpha-input", Protocol.HTTP, ("ALPHA_URL",)),
            RequirementSocket("zeta-input", Protocol.HTTP, ("ZETA_URL",)),
        ),
        (
            ProviderSocket("alpha-output", Protocol.HTTP),
            ProviderSocket("zeta-output", Protocol.HTTP),
        ),
    )


def _permuted_sockets(
    *,
    reverse_requirements: bool,
    reverse_providers: bool,
) -> BlockSockets:
    requirements, providers = _socket_values()
    return BlockSockets(
        requirements=tuple(reversed(requirements)) if reverse_requirements else requirements,
        providers=tuple(reversed(providers)) if reverse_providers else providers,
    )


class ProductRuntimeContractTests(unittest.TestCase):
    def test_contract_canonicalizes_each_socket_direction_by_exact_name(self) -> None:
        canonical = ProductRuntimeContract(
            sockets=_permuted_sockets(
                reverse_requirements=False,
                reverse_providers=False,
            )
        )
        cases = (
            ("requirements", True, False),
            ("providers", False, True),
            ("both", True, True),
        )

        for name, reverse_requirements, reverse_providers in cases:
            with self.subTest(name=name):
                authored = _permuted_sockets(
                    reverse_requirements=reverse_requirements,
                    reverse_providers=reverse_providers,
                )
                contract = ProductRuntimeContract(sockets=authored)

                self.assertIs(type(contract.sockets), BlockSockets)
                self.assertIsNot(contract.sockets, authored)
                self.assertEqual(
                    contract.sockets.requirement_names(),
                    ("alpha-input", "zeta-input"),
                )
                self.assertEqual(
                    contract.sockets.provider_names(),
                    ("alpha-output", "zeta-output"),
                )
                self.assertEqual(contract, canonical)
                self.assertEqual(contract.descriptor(), canonical.descriptor())

    def test_block_sockets_remains_an_author_order_value(self) -> None:
        authored = _permuted_sockets(
            reverse_requirements=True,
            reverse_providers=True,
        )

        self.assertEqual(
            authored.requirement_names(),
            ("zeta-input", "alpha-input"),
        )
        self.assertEqual(
            authored.provider_names(),
            ("zeta-output", "alpha-output"),
        )

    def test_reverse_multi_socket_contract_has_an_exact_codec_inverse(self) -> None:
        codec = ProductRuntimeContractCodec()
        cases = (
            ("requirements", True, False),
            ("providers", False, True),
            ("both", True, True),
        )

        for name, reverse_requirements, reverse_providers in cases:
            with self.subTest(name=name):
                contract = ProductRuntimeContract(
                    sockets=_permuted_sockets(
                        reverse_requirements=reverse_requirements,
                        reverse_providers=reverse_providers,
                    )
                )
                descriptor = codec.encode(contract)

                self.assertEqual(codec.decode(descriptor), contract)
                self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)

    def test_socket_admission_is_exact_and_precedes_virtual_dispatch(self) -> None:
        dispatches: list[str] = []

        class HostileBlockSockets(BlockSockets):
            def __getattribute__(self, name):
                if name in {
                    "requirements",
                    "providers",
                    "requirement_names",
                    "provider_names",
                }:
                    dispatches.append(f"outer.{name}")
                    raise RuntimeError("outer socket dispatch canary")
                return super().__getattribute__(name)

        class HostileTuple(tuple):
            def __iter__(self):
                dispatches.append("tuple.__iter__")
                raise RuntimeError("socket tuple dispatch canary")

            def __len__(self):
                dispatches.append("tuple.__len__")
                raise RuntimeError("socket tuple length canary")

        class HostileRequirementSocket(RequirementSocket):
            def __getattribute__(self, name):
                if name == "name":
                    dispatches.append("requirement.name")
                    raise RuntimeError("requirement member dispatch canary")
                return super().__getattribute__(name)

        class HostileProviderSocket(ProviderSocket):
            def __getattribute__(self, name):
                if name == "name":
                    dispatches.append("provider.name")
                    raise RuntimeError("provider member dispatch canary")
                return super().__getattribute__(name)

        class HostileText(str):
            def __hash__(self):
                dispatches.append("name.__hash__")
                raise RuntimeError("socket name hash canary")

            def __eq__(self, other):
                dispatches.append("name.__eq__")
                raise RuntimeError("socket name equality canary")

            def __lt__(self, other):
                dispatches.append("name.__lt__")
                raise RuntimeError("socket name comparison canary")

        requirement = RequirementSocket("candidate-input", Protocol.HTTP, ("INPUT_URL",))
        provider = ProviderSocket("candidate-output", Protocol.HTTP)
        candidates = (
            (
                "outer",
                HostileBlockSockets(),
                "product sockets must be BlockSockets",
            ),
            (
                "requirement-container",
                BlockSockets(requirements=HostileTuple((requirement,))),
                "product requirement sockets are malformed",
            ),
            (
                "provider-container",
                BlockSockets(providers=HostileTuple((provider,))),
                "product provider sockets are malformed",
            ),
            (
                "requirement-member",
                BlockSockets(
                    requirements=(
                        HostileRequirementSocket(
                            "candidate-input",
                            Protocol.HTTP,
                            ("INPUT_URL",),
                        ),
                    )
                ),
                "product requirement sockets are malformed",
            ),
            (
                "provider-member",
                BlockSockets(
                    providers=(
                        HostileProviderSocket("candidate-output", Protocol.HTTP),
                    )
                ),
                "product provider sockets are malformed",
            ),
            (
                "requirement-name",
                BlockSockets(
                    requirements=(
                        RequirementSocket(
                            HostileText("candidate-input"),
                            Protocol.HTTP,
                            ("INPUT_URL",),
                        ),
                    )
                ),
                "product requirement sockets are malformed",
            ),
            (
                "provider-name",
                BlockSockets(
                    providers=(
                        ProviderSocket(
                            HostileText("candidate-output"),
                            Protocol.HTTP,
                        ),
                    )
                ),
                "product provider sockets are malformed",
            ),
        )

        for name, candidate, expected_message in candidates:
            with self.subTest(name=name):
                dispatches.clear()
                caught = None
                try:
                    ProductRuntimeContract(sockets=candidate)
                except Exception as error:  # noqa: BLE001 - classify the public boundary
                    caught = error

                self.assertIsNotNone(caught)
                self.assertIs(type(caught), ProductRuntimeContractError)
                self.assertEqual(str(caught), expected_message)
                self.assertIsNone(caught.__cause__)
                self.assertIsNone(caught.__context__)
                self.assertNotIn("candidate", str(caught))
                self.assertEqual(dispatches, [])

    def test_socket_uniqueness_stays_per_direction_without_new_name_grammar(self) -> None:
        requirement = RequirementSocket("shared", Protocol.HTTP, ("SHARED_URL",))
        provider = ProviderSocket("shared", Protocol.HTTP)
        contract = ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=(
                    RequirementSocket("Mixed name/?", Protocol.HTTP, ("MIXED_URL",)),
                    RequirementSocket("", Protocol.HTTP, ("EMPTY_URL",)),
                    requirement,
                ),
                providers=(
                    ProviderSocket("Mixed name/?", Protocol.HTTP),
                    ProviderSocket("", Protocol.HTTP),
                    provider,
                ),
            )
        )

        self.assertEqual(
            contract.sockets.requirement_names(),
            ("", "Mixed name/?", "shared"),
        )
        self.assertEqual(
            contract.sockets.provider_names(),
            ("", "Mixed name/?", "shared"),
        )
        self.assertEqual(contract.sockets.requirement("shared"), requirement)
        self.assertEqual(contract.sockets.provider("shared"), provider)

        for name, sockets, message in (
            (
                "requirements",
                BlockSockets(requirements=(requirement, requirement)),
                "requirement socket names must be unique",
            ),
            (
                "providers",
                BlockSockets(providers=(provider, provider)),
                "provider socket names must be unique",
            ),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ProductRuntimeContractError) as raised:
                    ProductRuntimeContract(sockets=sockets)
                self.assertEqual(str(raised.exception), message)

    def test_canonical_socket_order_reaches_instantiation_endpoints_and_findings(
        self,
    ) -> None:
        contract = ProductRuntimeContract(
            sockets=_permuted_sockets(
                reverse_requirements=True,
                reverse_providers=True,
            )
        )
        product = ContainerServerProduct(
            identity=ProductIdentity("example", "ordered-sockets", 1),
            image=OciImageReference(
                "ghcr.io",
                "example/ordered-sockets",
                "sha256:" + "a" * 64,
            ),
            runtime_contract=contract,
        )
        block = instantiate_product(
            product,
            "ordered",
            ProductInstanceConfiguration.from_contract(contract),
        )

        self.assertEqual(
            block.sockets.requirement_names(),
            ("alpha-input", "zeta-input"),
        )
        self.assertEqual(
            block.sockets.provider_names(),
            ("alpha-output", "zeta-output"),
        )
        graph = compile_topology(
            DeploymentTopology("ordered", DockerRuntime(children=(block,)))
        )
        node = graph.node("ordered")
        self.assertEqual(tuple(node.endpoints), ("alpha-output", "zeta-output"))

        without_endpoints = graph.update_node(replace(node, endpoints={}))
        findings = validate_graph(without_endpoints).findings
        self.assertEqual(
            tuple(
                finding.subject.socket_name
                for finding in findings
                if finding.code is ValidationCode.MISSING_PROVIDER_ENDPOINT
            ),
            ("alpha-output", "zeta-output"),
        )
        self.assertEqual(
            tuple(
                finding.subject.socket_name
                for finding in findings
                if finding.code is ValidationCode.MISSING_REQUIRED_CONNECTION
            ),
            ("alpha-input", "zeta-input"),
        )

    def test_unexpected_downstream_validation_faults_remain_raw(self) -> None:
        injected = RuntimeError("internal validation canary")
        original = products_language._validate_verification

        def fail_if_called(*_args):
            raise injected

        products_language._validate_verification = fail_if_called
        try:
            with self.assertRaises(RuntimeError) as raised:
                ProductRuntimeContract()
            self.assertIs(raised.exception, injected)
        finally:
            products_language._validate_verification = original

    def test_composes_closed_runtime_material_into_descriptor(self) -> None:
        contract = ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=(RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),),
                providers=(ProviderSocket("http", Protocol.HTTP),),
            ),
            provider_ports=(ProviderRuntimePort("http", 8000),),
            public_environment=(PublicStaticEnvironmentBinding("MODE", "demo"),),
            configuration_artifacts=(
                ConfigurationArtifact(
                    "router-config",
                    "/etc/cpk/router.json",
                    ConfigurationMediaType.JSON,
                    '{"mode":"demo"}',
                ),
            ),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "API_TOKEN",
                    SecretReference("secret://local/api/token"),
                    SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                ),
            ),
            retained_data_mounts=(RetainedDataMount("orders-db", "/var/lib/postgresql/data"),),
            capabilities=(CapabilityName.HEALTH_CHECKABLE,),
            verification=VerificationContract(
                (HttpCheck(check_id="ready", provider_socket="http", path="/health"),)
            ),
            lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
        )

        descriptor = contract.descriptor()

        self.assertEqual(
            descriptor["sockets"]["providers"]["http"]["protocol"],
            {"transport": "tcp", "application": "http"},
        )
        self.assertEqual(descriptor["capabilities"], ["health-checkable"])
        self.assertEqual(
            descriptor["provider_ports"],
            [{"provider_socket": "http", "container_port": 8000}],
        )
        self.assertEqual(
            descriptor["retained_data_mounts"],
            [
                {
                    "resource_id": "orders-db",
                    "target_path": "/var/lib/postgresql/data",
                }
            ],
        )
        self.assertEqual(
            descriptor["secret_deliveries"][0],
            {
                "kind": "environment",
                "environment_name": "API_TOKEN",
                "reference_id": "secret://local/api/token",
                "intent": "application.control-token",
            },
        )

    def test_codec_round_trips_strict_descriptor(self) -> None:
        contract = ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            provider_ports=(ProviderRuntimePort("http", 8000),),
            verification=VerificationContract(
                (HttpCheck(check_id="ready", provider_socket="http", path="/health"),)
            ),
        )
        codec = ProductRuntimeContractCodec()

        descriptor = codec.encode(contract)
        restored = codec.decode(descriptor)

        self.assertEqual(restored, contract)
        self.assertEqual(codec.encode(restored), descriptor)

    def test_requirement_socket_declares_reference_only_secret_deliveries(self) -> None:
        delivery = SecretEnvironmentDelivery(
            "DATABASE_PASSWORD",
            SecretReference("secret://local/database/password"),
            SecretUseIntent.POSTGRES_PASSWORD,
        )
        contract = ProductRuntimeContract(
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
        )
        codec = ProductRuntimeContractCodec()

        descriptor = codec.encode(contract)

        self.assertEqual(
            descriptor["sockets"]["requirements"]["database"]["secret_deliveries"],
            [delivery.descriptor()],
        )
        self.assertEqual(codec.decode(descriptor), contract)
        self.assertNotIn("do-not-disclose", repr(descriptor))

    def test_empty_requirement_secret_deliveries_preserve_existing_descriptor_shape(
        self,
    ) -> None:
        contract = ProductRuntimeContract(
            sockets=BlockSockets(
                requirements=(
                    RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),
                ),
            ),
        )

        descriptor = ProductRuntimeContractCodec().encode(contract)

        self.assertNotIn(
            "secret_deliveries",
            descriptor["sockets"]["requirements"]["database"],
        )

    def test_requirement_socket_rejects_conflicting_secret_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "secret environment targets"):
            RequirementSocket(
                "database",
                Protocol.POSTGRES,
                (),
                required=False,
                binding=SocketBinding.RUNTIME_CONTROL,
                secret_deliveries=(
                    SecretEnvironmentDelivery(
                        "DATABASE_PASSWORD",
                        SecretReference("secret://local/database/first"),
                        SecretUseIntent.POSTGRES_PASSWORD,
                    ),
                    SecretEnvironmentDelivery(
                        "DATABASE_PASSWORD",
                        SecretReference("secret://local/database/second"),
                        SecretUseIntent.POSTGRES_PASSWORD,
                    ),
                ),
            )

    def test_provider_runtime_ports_are_closed_descriptor_material(self) -> None:
        contract = ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            provider_ports=(ProviderRuntimePort("http", 8000),),
        )
        descriptor = ProductRuntimeContractCodec().encode(contract)

        self.assertEqual(
            descriptor["provider_ports"],
            [{"provider_socket": "http", "container_port": 8000}],
        )
        self.assertEqual(
            ProductRuntimeContractCodec().decode(descriptor).provider_ports,
            (ProviderRuntimePort("http", 8000),),
        )

    def test_provider_runtime_ports_reject_unknown_socket_and_bad_port(self) -> None:
        with self.assertRaisesRegex(ProductRuntimeContractError, "provider runtime port"):
            ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
                provider_ports=(ProviderRuntimePort("admin", 8000),),
            )
        for port in (0, 65536, True):
            with self.subTest(port=port):
                with self.assertRaises(ProductRuntimeContractError):
                    ProviderRuntimePort("http", port)  # type: ignore[arg-type]

    def test_retained_data_mounts_are_closed_descriptor_material(self) -> None:
        mount = RetainedDataMount("orders-db", "/var/lib/postgresql/data")
        contract = ProductRuntimeContract(
            retained_data_mounts=(mount,),
            lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
        )
        descriptor = ProductRuntimeContractCodec().encode(contract)

        self.assertEqual(
            descriptor["retained_data_mounts"],
            [{"resource_id": "orders-db", "target_path": "/var/lib/postgresql/data"}],
        )
        self.assertEqual(
            ProductRuntimeContractCodec().decode(descriptor).retained_data_mounts,
            (mount,),
        )

    def test_retained_data_mounts_reject_host_paths_and_unknown_resources(self) -> None:
        for target_path in (
            "var/lib/postgresql/data",
            "/var/run/docker.sock",
            "/proc/self",
            "/sys/kernel",
            "/var/lib/../postgresql/data",
        ):
            with self.subTest(target_path=target_path):
                with self.assertRaises(ProductRuntimeContractError):
                    ProductRuntimeContract(
                        retained_data_mounts=(
                            RetainedDataMount("orders-db", target_path),
                        ),
                        lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
                    )

        with self.assertRaisesRegex(ProductRuntimeContractError, "retained data mount"):
            ProductRuntimeContract(
                retained_data_mounts=(
                    RetainedDataMount("unknown", "/var/lib/postgresql/data"),
                ),
                lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
            )

    def test_verification_protocol_mismatch_fails_before_runtime_effects(self) -> None:
        with self.assertRaisesRegex(ProductRuntimeContractError, "verification"):
            ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("db", Protocol.POSTGRES),)),
                verification=VerificationContract(
                    (HttpCheck(check_id="ready", provider_socket="db", path="/health"),)
                ),
            )

    def test_secret_values_cannot_enter_public_environment_or_configuration(self) -> None:
        with self.assertRaises(ValueError):
            ProductRuntimeContract(
                sockets=BlockSockets(),
                public_environment=(PublicStaticEnvironmentBinding("API_TOKEN", "do-not-disclose"),),
            )
        with self.assertRaises(ValueError):
            ProductRuntimeContract(
                sockets=BlockSockets(),
                configuration_artifacts=(
                    ConfigurationArtifact(
                        "app-config",
                        "/etc/cpk/app.json",
                        ConfigurationMediaType.JSON,
                        '{"password":"do-not-disclose"}',
                    ),
                ),
            )

    def test_descriptor_rejects_unknown_fields_and_secret_literals(self) -> None:
        codec = ProductRuntimeContractCodec()
        descriptor = codec.encode(
            ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),))
            )
        )

        with self.assertRaises(ProductRuntimeContractError):
            codec.decode({**descriptor, "future": "unknown"})
        descriptor["public_environment"] = [
            {"kind": "public-static", "name": "API_TOKEN", "value": "do-not-disclose"}
        ]
        with self.assertRaises(ProductRuntimeContractError):
            codec.decode(descriptor)

    def test_descriptor_rejects_non_string_socket_names(self) -> None:
        codec = ProductRuntimeContractCodec()
        descriptor = codec.encode(ProductRuntimeContract(sockets=BlockSockets()))
        descriptor["sockets"]["providers"] = {
            1: {"protocol": Protocol.HTTP.descriptor()}
        }

        with self.assertRaises(ProductRuntimeContractError):
            codec.decode(descriptor)

    def test_retained_data_resource_is_distinct_from_configuration_artifact(self) -> None:
        with self.assertRaisesRegex(ProductRuntimeContractError, "retained data"):
            ProductRuntimeContract(
                sockets=BlockSockets(),
                configuration_artifacts=(
                    ConfigurationArtifact(
                        "orders-db",
                        "/etc/cpk/orders.json",
                        ConfigurationMediaType.JSON,
                        '{"mode":"readonly"}',
                    ),
                ),
                lifecycle=ResourceLifecycle.owned_with_retained_data("orders-db"),
            )

    def test_product_runtime_contract_module_has_no_effect_boundary(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_core"
            / "products.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))

        forbidden_import_roots = {
            "control_plane_kit",
            "docker",
            "fastapi",
            "httpx",
            "importlib",
            "mcp",
            "psycopg",
            "uvicorn",
        }
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])

        self.assertEqual(roots & forbidden_import_roots, set())

    def test_product_family_is_a_closed_descriptor_vocabulary(self) -> None:
        self.assertEqual(ProductFamily.SERVER.value, "server")
        self.assertEqual(ProductFamily.DATA_SERVICE.value, "data-service")


if __name__ == "__main__":
    unittest.main()
