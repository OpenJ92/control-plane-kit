import json
import unittest
from dataclasses import dataclass, replace
from typing import Mapping

from control_plane_kit import (
    AmbiguityReason,
    AmbiguousChange,
    ApplicationBlock,
    AddedChange,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    EdgeSubject,
    Endpoint,
    FieldSubject,
    GraphDescriptorCodec,
    GraphDiff,
    GraphValidationError,
    LiteralAddress,
    ModifiedChange,
    NodeSubject,
    PlanOnlyImplementation,
    Protocol,
    ProviderSocket,
    RequirementSocket,
    RemovedChange,
    RuntimeContext,
    RuntimeKind,
    RuntimeSubject,
    SecretReferenceAddress,
    SocketConnection,
    StructuralField,
    UnsupportedChange,
    UnsupportedReason,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from examples.router_swap import recipe as router_recipe


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


def simple_graph(
    *,
    display_name: str = "Application",
    endpoint: str = "http://application",
    runtime_kind: RuntimeKind = RuntimeKind.DOCKER,
    data_block: bool = False,
) -> DeploymentGraph:
    block_type = DataBlock if data_block else ApplicationBlock
    block = block_type(
        BlockSpec("application", display_name=display_name),
        PlanOnlyImplementation("application", {"public": endpoint}),
        BlockSockets(providers=(ProviderSocket("public", Protocol.HTTP),)),
    )
    runtime = RuntimeContext("runtime", runtime_kind, children=(block,))
    return compile_recipe(DeploymentRecipe("topology", runtime))


class GraphDiffTests(unittest.TestCase):
    def test_change_algebra_and_interpreter_have_separate_module_boundaries(self):
        self.assertEqual(GraphDiff.__module__, "control_plane_kit.topology.changes")
        self.assertEqual(diff_graphs.__module__, "control_plane_kit.topology.diff")

    def test_identical_validated_graphs_have_deterministic_empty_diff(self):
        graph = validate_graph(simple_graph())

        first = diff_graphs(graph, graph)
        second = diff_graphs(graph, graph)

        self.assertTrue(first.empty)
        self.assertEqual(first.descriptor(), second.descriptor())
        self.assertEqual(first.summary(), "no changes")

    def test_router_swap_is_a_typed_socket_connection_change(self):
        current = validate_graph(compile_recipe(router_recipe("api-v1")))
        desired = validate_graph(compile_recipe(router_recipe("api-v2")))

        result = diff_graphs(current, desired)

        changed_edges = {
            change.subject.edge_id
            for change in result.changes
            if isinstance(change, ModifiedChange)
            and isinstance(change.subject, EdgeSubject)
        }
        self.assertIn("api-router.active", changed_edges)

    def test_added_and_removed_runtime_node_and_edge_forms_are_explicit(self):
        populated = validate_graph(compile_recipe(router_recipe("api-v1")))
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

    def test_socket_contract_and_endpoint_addition_are_separate_changes(self):
        current = simple_graph()
        desired_node = current.node("application")
        desired = current.update_node(
            replace(
                desired_node,
                sockets=BlockSockets(
                    providers=(
                        *desired_node.sockets.providers,
                        ProviderSocket("admin", Protocol.HTTP),
                    )
                ),
                endpoints={
                    **desired_node.endpoints,
                    "admin": Endpoint(
                        LiteralAddress("http://application/admin"),
                        Protocol.HTTP,
                    ),
                },
            )
        )

        result = diff_graphs(validate_graph(current), validate_graph(desired))

        self.assertTrue(
            any(
                isinstance(change, ModifiedChange)
                and isinstance(change.subject, FieldSubject)
                and change.subject.field is StructuralField.SOCKET_CONTRACT
                for change in result.changes
            )
        )
        self.assertTrue(
            any(
                isinstance(change, AddedChange)
                and isinstance(change.subject, FieldSubject)
                and change.subject.field is StructuralField.ENDPOINT
                and change.subject.key == "admin"
                for change in result.changes
            )
        )

    def test_protocol_product_change_is_an_explicit_socket_and_endpoint_change(self):
        current = simple_graph()
        node = current.node("application")
        desired = current.update_node(
            replace(
                node,
                sockets=BlockSockets(
                    providers=(ProviderSocket("public", Protocol.TCP),)
                ),
                endpoints={
                    "public": Endpoint(
                        LiteralAddress("tcp://application:8000"),
                        Protocol.TCP,
                    )
                },
            )
        )

        result = diff_graphs(validate_graph(current), validate_graph(desired))

        fields = {
            change.subject.field
            for change in result.changes
            if isinstance(change, ModifiedChange)
            and isinstance(change.subject, FieldSubject)
        }
        self.assertIn(StructuralField.SOCKET_CONTRACT, fields)
        self.assertIn(StructuralField.ENDPOINT, fields)

    def test_block_spec_endpoint_and_metadata_changes_are_separate_fields(self):
        current = simple_graph(display_name="Version A", endpoint="http://a")
        desired = simple_graph(display_name="Version B", endpoint="http://b")
        desired_node = desired.node("application")
        desired = desired.update_node(
            replace(desired_node, metadata={**desired_node.metadata, "owner": "platform"})
        )

        result = diff_graphs(validate_graph(current), validate_graph(desired))

        fields = {
            change.subject.field
            for change in result.changes
            if isinstance(change, ModifiedChange)
            and isinstance(change.subject, FieldSubject)
        }
        self.assertIn(StructuralField.BLOCK_SPECIFICATION, fields)
        self.assertIn(StructuralField.ENDPOINT, fields)
        self.assertIn(StructuralField.NODE_METADATA, fields)

    def test_runtime_and_implementation_kind_transitions_are_unsupported_data(self):
        current = simple_graph(runtime_kind=RuntimeKind.DOCKER)
        desired = simple_graph(runtime_kind=RuntimeKind.EXTERNAL)
        desired_node = desired.node("application")
        desired = desired.update_node(replace(desired_node, kind="external-application"))

        result = diff_graphs(validate_graph(current), validate_graph(desired))

        reasons = {
            change.reason
            for change in result.changes
            if isinstance(change, UnsupportedChange)
        }
        self.assertEqual(
            reasons,
            {
                UnsupportedReason.RUNTIME_KIND_TRANSITION,
                UnsupportedReason.IMPLEMENTATION_KIND_TRANSITION,
            },
        )

    def test_reusing_node_identity_for_another_block_family_is_ambiguous(self):
        current = validate_graph(simple_graph())
        desired = validate_graph(simple_graph(data_block=True))

        result = diff_graphs(current, desired)

        ambiguities = [
            change
            for change in result.changes
            if isinstance(change, AmbiguousChange)
        ]
        self.assertEqual(len(ambiguities), 1)
        self.assertIs(ambiguities[0].reason, AmbiguityReason.NODE_IDENTITY_REUSED)

    def test_custom_block_spec_variant_survives_before_and_after(self):
        codec = GraphDescriptorCodec(spec_codecs=(TypedInstanceSpecCodec(),))

        def graph(public_socket: str) -> DeploymentGraph:
            block = ApplicationBlock(
                TypedInstanceSpec("instance", public_socket=public_socket),
                PlanOnlyImplementation("instance", {"operator": "http://instance"}),
                BlockSockets(providers=(ProviderSocket("operator", Protocol.HTTP),)),
            )
            return compile_recipe(
                DeploymentRecipe("instance", DockerRuntime(children=(block,)))
            )

        result = diff_graphs(
            validate_graph(graph("operator"), codec=codec),
            validate_graph(graph("public"), codec=codec),
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

    def test_incompatible_spec_languages_are_explicitly_ambiguous(self):
        graph = simple_graph()
        default = validate_graph(graph)
        extended_codec = GraphDescriptorCodec(spec_codecs=(TypedInstanceSpecCodec(),))
        extended = validate_graph(graph, codec=extended_codec)

        result = diff_graphs(default, extended)

        self.assertEqual(len(result.changes), 1)
        self.assertIsInstance(result.changes[0], AmbiguousChange)
        self.assertIs(
            result.changes[0].reason,
            AmbiguityReason.BLOCK_SPEC_LANGUAGE_MISMATCH,
        )

    def test_descriptors_redact_secret_references_environment_and_metadata(self):
        provider = ApplicationBlock(
            BlockSpec("provider", metadata={"api_token": "do-not-render"}),
            PlanOnlyImplementation("provider", {"internal": "http://provider"}),
            BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
        )
        consumer = ApplicationBlock(
            BlockSpec("consumer"),
            PlanOnlyImplementation("consumer"),
            BlockSockets(
                requirements=(
                    RequirementSocket("upstream", Protocol.HTTP, ("UPSTREAM_URL",)),
                )
            ),
        )
        recipe = DeploymentRecipe(
            "secret",
            DockerRuntime(
                children=(
                    provider,
                    consumer,
                    SocketConnection(
                        "provider",
                        "internal",
                        "consumer",
                        "upstream",
                    ),
                )
            ),
        )
        current = compile_recipe(recipe)
        provider_node = current.node("provider")
        secret_endpoint = Endpoint(
            SecretReferenceAddress("secret://workspace/provider"),
            Protocol.HTTP,
        )
        current = current.update_node(
            replace(provider_node, endpoints={"internal": secret_endpoint})
        )
        edge_id, edge = next(iter(current.edges.items()))
        secret_assignment = {"UPSTREAM_URL": secret_endpoint.url}
        current = replace(
            current.update_node(
                replace(current.node("consumer"), environment=secret_assignment)
            ),
            edges={edge_id: replace(edge, env_assignments=secret_assignment)},
        )
        desired_endpoint = Endpoint(
            SecretReferenceAddress("secret://workspace/new-provider"),
            Protocol.HTTP,
        )
        desired_assignment = {"UPSTREAM_URL": desired_endpoint.url}
        desired = current.update_node(
            replace(
                current.node("provider"),
                endpoints={"internal": desired_endpoint},
                metadata={"api_token": "new-secret"},
            )
        )
        desired = desired.update_node(
            replace(desired.node("consumer"), environment=desired_assignment)
        )
        desired = replace(
            desired,
            edges={edge_id: replace(edge, env_assignments=desired_assignment)},
        )

        descriptor = diff_graphs(
            validate_graph(current),
            validate_graph(desired),
        ).descriptor()
        rendered = json.dumps(descriptor, sort_keys=True)

        self.assertNotIn("secret://workspace/provider", rendered)
        self.assertNotIn("secret://workspace/new-provider", rendered)
        self.assertNotIn("do-not-render", rendered)
        self.assertNotIn("new-secret", rendered)
        self.assertIn("<redacted>", rendered)

    def test_invalid_graphs_and_raw_graphs_are_refused(self):
        invalid = validate_graph(
            compile_recipe(
                DeploymentRecipe(
                    "invalid",
                    DockerRuntime(
                        children=(
                            ApplicationBlock(
                                BlockSpec("consumer"),
                                PlanOnlyImplementation("consumer"),
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


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


if __name__ == "__main__":
    unittest.main()
