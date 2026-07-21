from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
)
from control_plane_kit_core.topology import (
    Endpoint,
    GraphDescriptorCodec,
    GraphValidationError,
    LiteralAddress,
    ValidationCode,
    ValidationSeverity,
    compile_topology,
    validate_graph,
)
from control_plane_kit_core.topology.graph import DeploymentGraph
from control_plane_kit_core.types import Protocol, SocketBinding
from control_plane_kit_core.verification import (
    HttpCheck,
    PostgresQueryCheck,
    VerificationContract,
)

from tests.test_graph_codec import PureImplementation


@dataclass(frozen=True)
class ExtendedBlockSpec(BlockSpec):
    topology_role: str = "instance"


class ExtendedBlockSpecCodec:
    variant = "extended"
    spec_type = ExtendedBlockSpec

    def encode(self, spec: BlockSpec) -> Mapping[str, object]:
        if not isinstance(spec, ExtendedBlockSpec):
            raise TypeError("expected ExtendedBlockSpec")
        return {
            "variant": self.variant,
            "role_id": spec.role_id,
            "display_name": spec.display_name,
            "health_path": spec.health_path,
            "capabilities": [],
            "verification": spec.verification.descriptor(),
            "metadata": dict(spec.metadata),
            "topology_role": spec.topology_role,
        }

    def decode(self, descriptor: Mapping[str, object]) -> BlockSpec:
        metadata = descriptor.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        return ExtendedBlockSpec(
            role_id=str(descriptor["role_id"]),
            display_name=_optional_text(descriptor.get("display_name")),
            health_path=_optional_text(descriptor.get("health_path")),
            metadata={str(key): str(value) for key, value in metadata.items()},
            topology_role=str(descriptor["topology_role"]),
        )


class GraphValidationTests(unittest.TestCase):
    def test_valid_graph_returns_original_typed_value_without_effects(self) -> None:
        graph = graph_with_requirement()

        result = validate_graph(graph)

        self.assertTrue(result.valid)
        self.assertIs(result.require_valid(), graph)
        self.assertEqual(result.findings, ())

    def test_required_optional_and_rendered_findings_are_structured(self) -> None:
        required = validate_graph(graph_with_requirement(connected=False))
        optional = validate_graph(
            graph_with_requirement(required=False, connected=False)
        )

        self.assertFalse(required.valid)
        self.assertEqual(
            required.errors[0].code,
            ValidationCode.MISSING_REQUIRED_CONNECTION,
        )
        self.assertEqual(required.errors[0].subject.descriptor()["kind"], "socket")
        self.assertTrue(optional.valid)
        self.assertEqual(optional.warnings[0].severity, ValidationSeverity.WARNING)
        self.assertEqual(required.descriptor(), validate_graph(required.graph).descriptor())
        self.assertEqual(
            required.descriptor()["findings"][0]["code"],
            "missing-required-connection",
        )

        with self.assertRaises(GraphValidationError) as raised:
            required.require_valid()
        self.assertIs(raised.exception.result, required)

    def test_runtime_endpoint_and_undeclared_endpoint_invariants_fail_closed(self) -> None:
        graph = graph_with_requirement()
        provider = graph.node("provider")
        missing_runtime = graph.update_node(
            replace(provider, runtime_id="missing", endpoints={})
        )
        undeclared_endpoint = graph.update_node(
            replace(
                provider,
                endpoints={
                    **provider.endpoints,
                    "orphan": Endpoint(
                        LiteralAddress("http://orphan"),
                        Protocol.HTTP,
                    ),
                },
            )
        )

        missing_result = validate_graph(missing_runtime)
        orphan_result = validate_graph(undeclared_endpoint)

        self.assertIn(
            ValidationCode.MISSING_RUNTIME,
            {finding.code for finding in missing_result.errors},
        )
        self.assertIn(
            ValidationCode.MISSING_PROVIDER_ENDPOINT,
            {finding.code for finding in missing_result.errors},
        )
        self.assertIn(
            ValidationCode.INVALID_DESCRIPTOR,
            {finding.code for finding in missing_result.errors},
        )
        self.assertIn(
            ValidationCode.UNDECLARED_PROVIDER_ENDPOINT,
            {finding.code for finding in orphan_result.errors},
        )

    def test_registered_block_spec_codec_is_retained_as_validation_evidence(self) -> None:
        app = ApplicationBlock(
            ExtendedBlockSpec("instance-a", topology_role="control-plane"),
            PureImplementation("instance", {"operator": "http://instance"}),
            BlockSockets(providers=(ProviderSocket("operator", Protocol.HTTP),)),
        )
        graph = compile_topology(
            DeploymentTopology("extended", DockerRuntime(children=(app,)))
        )
        codec = GraphDescriptorCodec(spec_codecs=(ExtendedBlockSpecCodec(),))

        rejected_by_default = validate_graph(graph)
        accepted = validate_graph(graph, codec=codec)

        self.assertFalse(rejected_by_default.valid)
        self.assertTrue(accepted.valid)
        self.assertIs(accepted.codec, codec)
        self.assertEqual(
            codec.encode_block_spec(accepted.graph.node("instance-a").block_spec)[
                "variant"
            ],
            "extended",
        )

    def test_unregistered_block_spec_variant_fails_closed(self) -> None:
        class UnsupportedBlockSpec(BlockSpec):
            pass

        graph = graph_with_requirement()
        provider = graph.node("provider")
        malformed = graph.update_node(
            replace(provider, block_spec=UnsupportedBlockSpec("provider"))
        )

        result = validate_graph(malformed)

        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0].code, ValidationCode.INVALID_DESCRIPTOR)

    def test_duplicate_sockets_and_multiple_requirement_connections_are_rejected(self) -> None:
        graph = graph_with_requirement()
        edge = next(iter(graph.edges.values()))
        duplicated_edge = graph.add_edge(replace(edge, edge_id="duplicate"))
        provider = graph.node("provider")
        consumer = graph.node("consumer")
        provider_socket = provider.sockets.providers[0]
        requirement_socket = consumer.sockets.requirements[0]
        duplicated_sockets = graph.update_node(
            replace(
                provider,
                sockets=BlockSockets(providers=(provider_socket, provider_socket)),
            )
        ).update_node(
            replace(
                consumer,
                sockets=BlockSockets(
                    requirements=(requirement_socket, requirement_socket),
                ),
            )
        )

        self.assertIn(
            ValidationCode.MULTIPLE_REQUIREMENT_CONNECTIONS,
            {finding.code for finding in validate_graph(duplicated_edge).errors},
        )
        socket_codes = {finding.code for finding in validate_graph(duplicated_sockets).errors}
        self.assertIn(ValidationCode.DUPLICATE_PROVIDER_SOCKET, socket_codes)
        self.assertIn(ValidationCode.DUPLICATE_REQUIREMENT_SOCKET, socket_codes)

    def test_edge_assignments_binding_and_consumer_environment_must_match(self) -> None:
        graph = graph_with_requirement()
        edge_id, edge = next(iter(graph.edges.items()))
        wrong_key = replace(edge, env_assignments={"WRONG_URL": "http://provider"})
        wrong_value = replace(edge, env_assignments={"UPSTREAM_URL": "http://wrong"})
        wrong_binding = replace(edge, binding=SocketBinding.RUNTIME_CONTROL)
        missing_environment = graph.update_node(
            replace(graph.node("consumer"), socket_environment=())
        )

        cases = (
            (
                replace(graph, edges={edge_id: wrong_key}),
                ValidationCode.EDGE_ENV_BINDINGS,
            ),
            (
                replace(graph, edges={edge_id: wrong_value}),
                ValidationCode.EDGE_ENV_BINDINGS,
            ),
            (
                replace(graph, edges={edge_id: wrong_binding}),
                ValidationCode.EDGE_BINDING,
            ),
            (missing_environment, ValidationCode.CONSUMER_ENVIRONMENT),
        )
        for candidate, code in cases:
            with self.subTest(code=code.value):
                self.assertIn(
                    code,
                    {finding.code for finding in validate_graph(candidate).errors},
                )

    def test_verification_targets_require_declared_compatible_provider_sockets(self) -> None:
        graph = graph_with_requirement()
        provider = graph.node("provider")
        missing = graph.update_node(
            replace(
                provider,
                block_spec=replace(
                    provider.block_spec,
                    verification=VerificationContract(
                        (
                            HttpCheck(
                                check_id="missing",
                                provider_socket="missing",
                                path="/verify",
                            ),
                        )
                    ),
                ),
            )
        )
        incompatible = graph.update_node(
            replace(
                provider,
                block_spec=replace(
                    provider.block_spec,
                    verification=VerificationContract(
                        (
                            PostgresQueryCheck(
                                check_id="wrong-protocol",
                                provider_socket="internal",
                            ),
                        )
                    ),
                ),
            )
        )

        self.assertIn(
            ValidationCode.VERIFICATION_PROVIDER,
            {finding.code for finding in validate_graph(missing).errors},
        )
        self.assertIn(
            ValidationCode.VERIFICATION_PROTOCOL,
            {finding.code for finding in validate_graph(incompatible).errors},
        )


def graph_with_requirement(
    *,
    required: bool = True,
    connected: bool = True,
) -> DeploymentGraph:
    provider = ApplicationBlock(
        BlockSpec("provider"),
        PureImplementation("provider", {"internal": "http://provider"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    consumer = ApplicationBlock(
        BlockSpec("consumer"),
        PureImplementation("consumer", {}),
        BlockSockets(
            requirements=(
                RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",), required),
            )
        ),
    )
    children: tuple[object, ...] = (provider, consumer)
    if connected:
        children += (SocketConnection("provider", "internal", "consumer", "upstream"),)
    return compile_topology(
        DeploymentTopology("validation", DockerRuntime(children=children))
    )


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


if __name__ == "__main__":
    unittest.main()
