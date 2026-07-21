from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Mapping
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
    RequirementSocket,
    SocketConnection,
)
from control_plane_kit_core.configuration import (
    ConfigurationArtifact,
    ConfigurationMediaType,
)
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
)
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.secrets import SecretEnvironmentDelivery, SecretReference
from control_plane_kit_core.topology import (
    AddedChange,
    AmbiguityReason,
    AmbiguousChange,
    DeploymentGraph,
    EdgeSubject,
    Endpoint,
    FieldSubject,
    GraphDescriptorCodec,
    GraphDiff,
    GraphValidationError,
    LiteralAddress,
    ModifiedChange,
    NodeSubject,
    RemovedChange,
    RuntimeRecord,
    RuntimeSubject,
    SecretReferenceAddress,
    StructuralField,
    UnsupportedChange,
    UnsupportedReason,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind, SocketBinding
from control_plane_kit_core.verification import HttpCheck, VerificationContract

from tests.test_graph_codec import PureImplementation


@dataclass(frozen=True)
class TypedInstanceSpec(BlockSpec):
    public_socket: str = "operator"


class TypedInstanceSpecCodec:
    variant = "typed-instance"
    spec_type = TypedInstanceSpec

    def encode(self, spec: BlockSpec) -> Mapping[str, object]:
        if not isinstance(spec, TypedInstanceSpec):
            raise TypeError("expected TypedInstanceSpec")
        return {
            "variant": self.variant,
            "role_id": spec.role_id,
            "display_name": spec.display_name,
            "health_path": spec.health_path,
            "capabilities": [],
            "verification": spec.verification.descriptor(),
            "metadata": dict(spec.metadata),
            "public_socket": spec.public_socket,
        }

    def decode(self, descriptor: Mapping[str, object]) -> BlockSpec:
        metadata = descriptor.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        return TypedInstanceSpec(
            role_id=str(descriptor["role_id"]),
            display_name=_optional_text(descriptor.get("display_name")),
            health_path=_optional_text(descriptor.get("health_path")),
            metadata={str(key): str(value) for key, value in metadata.items()},
            public_socket=str(descriptor["public_socket"]),
        )


class GraphDiffTests(unittest.TestCase):
    def test_change_algebra_and_interpreter_have_separate_module_boundaries(self) -> None:
        self.assertEqual(
            GraphDiff.__module__,
            "control_plane_kit_core.topology.changes",
        )
        self.assertEqual(
            diff_graphs.__module__,
            "control_plane_kit_core.topology.diff",
        )

    def test_identical_validated_graphs_have_deterministic_empty_diff(self) -> None:
        graph = validate_graph(simple_graph())

        first = diff_graphs(graph, graph)
        second = diff_graphs(graph, graph)

        self.assertTrue(first.empty)
        self.assertEqual(first.descriptor(), second.descriptor())
        self.assertEqual(first.summary(), "no changes")

    def test_added_and_removed_runtime_node_and_edge_forms_are_explicit(self) -> None:
        populated = validate_graph(router_graph("api-v1"))
        empty = validate_graph(DeploymentGraph(populated.graph.name))

        additions = diff_graphs(empty, populated)
        removals = diff_graphs(populated, empty)

        self.assertTrue(
            any(
                isinstance(change, AddedChange)
                and isinstance(change.subject, RuntimeSubject)
                for change in additions.changes
            )
        )
        self.assertTrue(
            any(
                isinstance(change, AddedChange)
                and isinstance(change.subject, NodeSubject)
                for change in additions.changes
            )
        )
        self.assertTrue(
            any(
                isinstance(change, AddedChange)
                and isinstance(change.subject, EdgeSubject)
                for change in additions.changes
            )
        )
        self.assertTrue(
            any(
                isinstance(change, RemovedChange)
                and isinstance(change.subject, RuntimeSubject | NodeSubject | EdgeSubject)
                for change in removals.changes
            )
        )

    def test_router_swap_is_a_typed_socket_connection_change(self) -> None:
        result = diff_graphs(
            validate_graph(router_graph("api-v1")),
            validate_graph(router_graph("api-v2")),
        )

        changed_edges = {
            change.subject.edge_id
            for change in result.changes
            if isinstance(change, ModifiedChange)
            and isinstance(change.subject, EdgeSubject)
        }

        self.assertIn("api-router.active", changed_edges)

    def test_structural_subjects_are_separate_for_node_fields(self) -> None:
        current = simple_graph(display_name="Version A", endpoint="http://a")
        node = current.node("application")
        desired = current.update_node(
            replace(
                node,
                block_spec=replace(
                    node.block_spec,
                    display_name="Version B",
                    verification=VerificationContract(
                        (
                            HttpCheck(
                                check_id="application-response",
                                provider_socket="public",
                                path="/verify",
                            ),
                        )
                    ),
                ),
                endpoints={
                    "public": Endpoint(LiteralAddress("http://b"), Protocol.HTTP)
                },
                metadata={"owner": "platform"},
            )
        )

        fields = modified_fields(diff_graphs(validate_graph(current), validate_graph(desired)))

        self.assertIn(StructuralField.BLOCK_SPECIFICATION, fields)
        self.assertIn(StructuralField.ENDPOINT, fields)
        self.assertIn(StructuralField.NODE_METADATA, fields)

    def test_socket_protocol_public_and_socket_environment_changes_are_distinct(self) -> None:
        current = router_graph("api-v1")
        router = current.node("api-router")
        public_desired = current.update_node(
            replace(
                router,
                public_environment=(
                    PublicStaticEnvironmentBinding("MODE", "desired"),
                ),
            )
        )
        protocol_desired = simple_graph(protocol=Protocol.TCP, endpoint="tcp://application:8000")

        public_fields = modified_fields(
            diff_graphs(validate_graph(current), validate_graph(public_desired))
        )
        socket_fields = modified_fields(
            diff_graphs(
                validate_graph(service_graph("http://provider-a")),
                validate_graph(service_graph("http://provider-b")),
            )
        )
        protocol_fields = modified_fields(
            diff_graphs(validate_graph(simple_graph()), validate_graph(protocol_desired))
        )

        self.assertIn(StructuralField.PUBLIC_ENVIRONMENT, public_fields)
        self.assertNotIn(StructuralField.SOCKET_ENVIRONMENT, public_fields)
        self.assertIn(StructuralField.SOCKET_ENVIRONMENT, socket_fields)
        self.assertIn(StructuralField.SOCKET_CONTRACT, protocol_fields)
        self.assertIn(StructuralField.ENDPOINT, protocol_fields)

    def test_runtime_and_implementation_kind_transitions_are_unsupported_data(self) -> None:
        current = simple_graph(runtime_kind=RuntimeKind.DOCKER)
        desired = simple_graph(runtime_kind=RuntimeKind.EXTERNAL)
        desired_node = desired.node("application")
        desired = desired.update_node(replace(desired_node, kind="external-application"))

        result = diff_graphs(validate_graph(current), validate_graph(desired))

        self.assertEqual(
            {
                change.reason
                for change in result.changes
                if isinstance(change, UnsupportedChange)
            },
            {
                UnsupportedReason.RUNTIME_KIND_TRANSITION,
                UnsupportedReason.IMPLEMENTATION_KIND_TRANSITION,
            },
        )

    def test_node_identity_reuse_and_codec_language_mismatch_are_ambiguous(self) -> None:
        current = validate_graph(simple_graph())
        desired = validate_graph(simple_graph(data_block=True))
        reused = diff_graphs(current, desired)

        self.assertEqual(
            [
                change.reason
                for change in reused.changes
                if isinstance(change, AmbiguousChange)
            ],
            [AmbiguityReason.NODE_IDENTITY_REUSED],
        )

        extended_codec = GraphDescriptorCodec(spec_codecs=(TypedInstanceSpecCodec(),))
        mismatch = diff_graphs(current, validate_graph(simple_graph(), codec=extended_codec))

        self.assertEqual(len(mismatch.changes), 1)
        self.assertIsInstance(mismatch.changes[0], AmbiguousChange)
        self.assertIs(
            mismatch.changes[0].reason,
            AmbiguityReason.BLOCK_SPEC_LANGUAGE_MISMATCH,
        )

    def test_custom_block_spec_variant_survives_before_and_after(self) -> None:
        codec = GraphDescriptorCodec(spec_codecs=(TypedInstanceSpecCodec(),))

        result = diff_graphs(
            validate_graph(typed_instance_graph("operator"), codec=codec),
            validate_graph(typed_instance_graph("public"), codec=codec),
        )

        spec_change = next(
            change
            for change in result.changes
            if isinstance(change, ModifiedChange)
            and isinstance(change.subject, FieldSubject)
            and change.subject.field is StructuralField.BLOCK_SPECIFICATION
        )
        self.assertIsInstance(spec_change.before.spec, TypedInstanceSpec)
        self.assertEqual(spec_change.before.descriptor()["variant"], "typed-instance")
        self.assertEqual(spec_change.after.descriptor()["public_socket"], "public")

    def test_configuration_secret_and_redacted_material_are_typed_diff_fields(self) -> None:
        current = secret_graph()
        provider = current.node("provider")
        edge_id, edge = next(iter(current.edges.items()))
        desired_endpoint = Endpoint(
            SecretReferenceAddress("secret://workspace/new-provider"),
            Protocol.HTTP,
        )
        desired = current.update_node(
            replace(
                provider,
                endpoints={"internal": desired_endpoint},
                metadata={"api_token": "new-secret"},
                configuration_artifacts=(
                    ConfigurationArtifact(
                        "settings",
                        "/etc/app/settings.json",
                        ConfigurationMediaType.JSON,
                        '{"mode":"green"}',
                    ),
                ),
                secret_deliveries=(
                    SecretEnvironmentDelivery(
                        "API_TOKEN",
                        SecretReference("secret://workspace/api-token"),
                    ),
                ),
            )
        )
        desired = desired.update_node(
            replace(
                desired.node("consumer"),
                socket_environment=(
                    SocketDerivedEnvironmentBinding(
                        "UPSTREAM_URL",
                        desired_endpoint.url,
                        edge_id,
                    ),
                ),
            )
        )
        desired = replace(
            desired,
            edges={
                edge_id: replace(
                    edge,
                    env_assignments={"UPSTREAM_URL": desired_endpoint.url},
                )
            },
        )

        diff = diff_graphs(validate_graph(current), validate_graph(desired))
        descriptor = diff.descriptor()
        rendered = json.dumps(descriptor, sort_keys=True)
        fields = modified_fields(diff)

        self.assertIn(StructuralField.CONFIGURATION_ARTIFACTS, fields)
        self.assertIn(StructuralField.SECRET_DELIVERIES, fields)
        self.assertNotIn("secret://workspace/provider", rendered)
        self.assertNotIn("secret://workspace/new-provider", rendered)
        self.assertNotIn("new-secret", rendered)
        self.assertIn("<redacted>", rendered)

    def test_invalid_graphs_and_raw_graphs_are_refused(self) -> None:
        invalid = validate_graph(
            compile_topology(
                DeploymentTopology(
                    "invalid",
                    DockerRuntime(
                        children=(
                            ApplicationBlock(
                                BlockSpec("consumer"),
                                PureImplementation("consumer", {}),
                                BlockSockets(
                                    requirements=(
                                        RequirementSocket(
                                            "required",
                                            Protocol.HTTP,
                                            ("URL",),
                                        ),
                                    )
                                ),
                            ),
                        )
                    ),
                )
            )
        )
        valid = validate_graph(simple_graph())

        with self.assertRaises(GraphValidationError):
            diff_graphs(invalid, valid)
        with self.assertRaises(TypeError):
            diff_graphs(simple_graph(), valid)  # type: ignore[arg-type]


def simple_graph(
    *,
    display_name: str = "Application",
    endpoint: str = "http://application",
    protocol: Protocol = Protocol.HTTP,
    runtime_kind: RuntimeKind = RuntimeKind.DOCKER,
    data_block: bool = False,
) -> DeploymentGraph:
    block_type = DataBlock if data_block else ApplicationBlock
    block = block_type(
        BlockSpec("application", display_name=display_name),
        PureImplementation("application", {"public": endpoint}),
        BlockSockets(providers=(ProviderSocket("public", protocol),)),
    )
    graph = compile_topology(
        DeploymentTopology(
            "topology",
            DockerRuntime(children=(block,)),
        )
    )
    if runtime_kind is RuntimeKind.DOCKER:
        return graph
    runtime = RuntimeRecord(
        "docker",
        runtime_kind,
        graph.runtimes["docker"].children,
        graph.runtimes["docker"].metadata,
        graph.runtimes["docker"].lifecycle,
    )
    return replace(graph, runtimes={"docker": runtime})


def router_graph(target: str) -> DeploymentGraph:
    router = ApplicationBlock(
        BlockSpec("api-router"),
        PureImplementation("router", {"public": "http://router"}),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "active",
                    Protocol.HTTP,
                    (),
                    binding=SocketBinding.RUNTIME_CONTROL,
                ),
            ),
            providers=(ProviderSocket("public", Protocol.HTTP),),
        ),
    )
    backend = ApplicationBlock(
        BlockSpec(target),
        PureImplementation("application", {"internal": f"http://{target}"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    return compile_topology(
        DeploymentTopology(
            "router",
            DockerRuntime(
                children=(
                    router,
                    backend,
                    SocketConnection(
                        target,
                        "internal",
                        "api-router",
                        "active",
                        edge_id="api-router.active",
                    ),
                )
            ),
        )
    )


def service_graph(provider_endpoint: str) -> DeploymentGraph:
    provider = ApplicationBlock(
        BlockSpec("provider"),
        PureImplementation("provider", {"internal": provider_endpoint}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    consumer = ApplicationBlock(
        BlockSpec("consumer"),
        PureImplementation("consumer", {}),
        BlockSockets(
            requirements=(
                RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",)),
            )
        ),
    )
    return compile_topology(
        DeploymentTopology(
            "service",
            DockerRuntime(
                children=(
                    provider,
                    consumer,
                    SocketConnection("provider", "internal", "consumer", "upstream"),
                )
            ),
        )
    )


def typed_instance_graph(public_socket: str) -> DeploymentGraph:
    block = ApplicationBlock(
        TypedInstanceSpec("instance", public_socket=public_socket),
        PureImplementation("instance", {"operator": "http://instance"}),
        BlockSockets(providers=(ProviderSocket("operator", Protocol.HTTP),)),
    )
    return compile_topology(
        DeploymentTopology("instance", DockerRuntime(children=(block,)))
    )


def secret_graph() -> DeploymentGraph:
    provider = ApplicationBlock(
        BlockSpec("provider", metadata={"api_token": "do-not-render"}),
        PureImplementation("provider", {"internal": "http://provider"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    consumer = ApplicationBlock(
        BlockSpec("consumer"),
        PureImplementation("consumer", {}),
        BlockSockets(
            requirements=(
                RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",)),
            )
        ),
    )
    graph = compile_topology(
        DeploymentTopology(
            "secret",
            DockerRuntime(
                children=(
                    provider,
                    consumer,
                    SocketConnection("provider", "internal", "consumer", "upstream"),
                )
            ),
        )
    )
    provider_node = graph.node("provider")
    secret_endpoint = Endpoint(
        SecretReferenceAddress("secret://workspace/provider"),
        Protocol.HTTP,
    )
    graph = graph.update_node(
        replace(provider_node, endpoints={"internal": secret_endpoint})
    )
    edge_id, edge = next(iter(graph.edges.items()))
    graph = graph.update_node(
        replace(
            graph.node("consumer"),
            socket_environment=(
                SocketDerivedEnvironmentBinding(
                    "UPSTREAM_URL",
                    secret_endpoint.url,
                    edge_id,
                ),
            ),
        )
    )
    return replace(
        graph,
        edges={
            edge_id: replace(
                edge,
                env_assignments={"UPSTREAM_URL": secret_endpoint.url},
            )
        },
    )


def modified_fields(diff: GraphDiff) -> set[StructuralField]:
    return {
        change.subject.field
        for change in diff.changes
        if isinstance(change, ModifiedChange)
        and isinstance(change.subject, FieldSubject)
    }


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


if __name__ == "__main__":
    unittest.main()
