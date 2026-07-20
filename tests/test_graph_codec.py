import unittest
from dataclasses import dataclass, replace
from typing import Mapping

from control_plane_kit import (
    ApplicationBlock,
    BlockFamily,
    BlockSockets,
    BlockSpec,
    DeploymentRecipe,
    DockerRuntime,
    Endpoint,
    GraphDescriptorCodec,
    InvalidGraphReference,
    LiteralAddress,
    LossyGraphDescriptor,
    MalformedGraphDescriptor,
    PlanOnlyImplementation,
    Protocol,
    ProviderSocket,
    SecretReferenceAddress,
    UnknownGraphVariant,
    compile_recipe,
)
from examples.app_with_postgres import recipe
from examples.gate_d_live_smoke import router_recipe


@dataclass(frozen=True)
class ExampleInstanceSpec(BlockSpec):
    public_provider: str = "public"


class ExampleInstanceSpecCodec:
    variant = "example-instance"
    spec_type = ExampleInstanceSpec

    def encode(self, spec: BlockSpec) -> Mapping[str, object]:
        if not isinstance(spec, ExampleInstanceSpec):
            raise TypeError("expected ExampleInstanceSpec")
        return {
            "variant": self.variant,
            "role_id": spec.role_id,
            "display_name": spec.display_name,
            "health_path": spec.health_path,
            "capabilities": [capability.value for capability in spec.capabilities],
            "metadata": dict(sorted(spec.metadata.items())),
            "public_provider": spec.public_provider,
        }

    def decode(self, descriptor: Mapping[str, object]) -> BlockSpec:
        return ExampleInstanceSpec(
            role_id=str(descriptor["role_id"]),
            display_name=_optional_text(descriptor.get("display_name")),
            health_path=_optional_text(descriptor.get("health_path")),
            metadata={str(key): str(value) for key, value in _mapping(descriptor["metadata"]).items()},
            public_provider=str(descriptor["public_provider"]),
        )


class GraphDescriptorCodecTests(unittest.TestCase):
    def test_generic_graph_round_trip_preserves_typed_block_identity(self):
        graph = compile_recipe(recipe())
        codec = GraphDescriptorCodec()

        restored = codec.decode(codec.encode(graph))

        self.assertEqual(restored, graph)
        self.assertIs(restored.node("orders-api").block_family, BlockFamily.APPLICATION)
        self.assertEqual(restored.node("orders-api").block_spec, graph.node("orders-api").block_spec)

    def test_registered_spec_variant_round_trips_without_string_inference(self):
        app = ApplicationBlock(
            spec=ExampleInstanceSpec("instance-a", public_provider="operator"),
            implementation=PlanOnlyImplementation("instance"),
            sockets=BlockSockets(providers=(ProviderSocket("operator", Protocol.HTTP),)),
        )
        graph = compile_recipe(
            DeploymentRecipe("instance", DockerRuntime(children=(app,)))
        )
        codec = GraphDescriptorCodec(spec_codecs=(ExampleInstanceSpecCodec(),))

        restored = codec.decode(codec.encode(graph))

        self.assertEqual(restored.node("instance-a").block_spec, app.spec)
        self.assertIsInstance(restored.node("instance-a").block_spec, ExampleInstanceSpec)

    def test_secret_reference_address_round_trips_without_secret_resolution(self):
        graph = compile_recipe(recipe())
        postgres = graph.node("postgres")
        secret_endpoint = Endpoint(
            SecretReferenceAddress("secret://workspace-a/postgres-url"),
            Protocol.POSTGRES,
        )
        graph = graph.update_node(
            replace(postgres, endpoints={"internal": secret_endpoint})
        )
        codec = GraphDescriptorCodec()

        descriptor = codec.encode(graph)
        restored = codec.decode(descriptor)

        self.assertEqual(
            descriptor["nodes"]["postgres"]["endpoints"]["internal"]["address"],
            {
                "kind": "secret-reference",
                "secret_ref": "secret://workspace-a/postgres-url",
            },
        )
        self.assertEqual(restored.node("postgres").endpoint("internal"), secret_endpoint)

    def test_literal_addresses_reject_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            LiteralAddress("postgresql://operator:secret@database/app")

    def test_unknown_closed_variants_fail_loudly(self):
        descriptor = GraphDescriptorCodec().encode(compile_recipe(recipe()))
        descriptor["nodes"]["orders-api"]["block_spec"]["variant"] = "future"

        with self.assertRaisesRegex(UnknownGraphVariant, "block spec variant"):
            GraphDescriptorCodec().decode(descriptor)

    def test_environment_binding_variants_round_trip_and_unknown_kind_fails_closed(self):
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(compile_recipe(recipe()))
        postgres = descriptor["nodes"]["postgres"]["environment_bindings"]
        api = descriptor["nodes"]["orders-api"]["environment_bindings"]

        self.assertEqual({value["kind"] for value in postgres}, {"public-static"})
        self.assertEqual({value["kind"] for value in api}, {"socket-derived"})
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)

        api[0]["kind"] = "future-environment"
        with self.assertRaisesRegex(UnknownGraphVariant, "unknown environment"):
            codec.decode(descriptor)

    def test_environment_passwords_fail_before_raw_descriptor_reconstruction(self):
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(compile_recipe(recipe()))
        supplied = "postgresql://app:do-not-disclose@database:5432/app"
        descriptor["nodes"]["postgres"]["environment_bindings"][0]["value"] = supplied

        with self.assertRaises(UnknownGraphVariant) as caught:
            codec.decode(descriptor)

        self.assertNotIn("do-not-disclose", str(caught.exception))

    def test_direct_node_and_edge_cannot_restore_inline_environment_secrets(self):
        graph = compile_recipe(recipe())
        node = graph.node("orders-api")
        edge = next(iter(graph.edges.values()))

        with self.assertRaisesRegex(ValueError, "must not contain environment"):
            replace(
                node,
                metadata={"environment": {"API_TOKEN": "do-not-disclose"}},
            )
        with self.assertRaises(ValueError) as caught:
            replace(
                edge,
                env_assignments={
                    "DATABASE_URL":
                        "postgresql://app:do-not-disclose@database:5432/app"
                },
            )
        self.assertNotIn("do-not-disclose", str(caught.exception))

    def test_missing_runtime_ownership_fails_loudly(self):
        descriptor = GraphDescriptorCodec().encode(compile_recipe(recipe()))
        descriptor["runtimes"]["docker"]["children"].remove("orders-api")

        with self.assertRaisesRegex(InvalidGraphReference, "not owned"):
            GraphDescriptorCodec().decode(descriptor)

    def test_missing_edge_socket_fails_loudly(self):
        descriptor = GraphDescriptorCodec().encode(compile_recipe(recipe()))
        edge = descriptor["edges"]["postgres.internal-to-orders-api.DATABASE_URL"]
        edge["consumer"]["requirement"] = "missing"

        with self.assertRaises(InvalidGraphReference):
            GraphDescriptorCodec().decode(descriptor)

    def test_edge_binding_round_trips_as_a_closed_value(self):
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(compile_recipe(recipe()))
        edge = descriptor["edges"]["postgres.internal-to-orders-api.DATABASE_URL"]

        self.assertEqual(edge["binding"], "environment")
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)

        edge["binding"] = "future-binding"
        with self.assertRaises(UnknownGraphVariant):
            codec.decode(descriptor)

        runtime_control = codec.encode(
            compile_recipe(router_recipe("hello-blue"))
        )
        active = runtime_control["edges"]["router.active"]
        self.assertEqual(active["binding"], "runtime-control")
        self.assertEqual(
            codec.encode(codec.decode(runtime_control)),
            runtime_control,
        )

    def test_protocol_product_round_trips_and_unknown_factors_fail_closed(self):
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(compile_recipe(recipe()))
        edge = descriptor["edges"]["postgres.internal-to-orders-api.DATABASE_URL"]

        self.assertEqual(
            edge["protocol"],
            {"transport": "tcp", "application": "postgres"},
        )
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)

        edge["protocol"]["transport"] = "future"
        with self.assertRaisesRegex(UnknownGraphVariant, "unknown protocol"):
            codec.decode(descriptor)

    def test_unknown_fields_are_rejected_as_lossy(self):
        descriptor = GraphDescriptorCodec().encode(compile_recipe(recipe()))
        descriptor["future"] = {"meaning": "unknown"}

        with self.assertRaises(LossyGraphDescriptor):
            GraphDescriptorCodec().decode(descriptor)

    def test_tuple_input_is_semantically_equivalent_to_json_list(self):
        descriptor = GraphDescriptorCodec().encode(compile_recipe(recipe()))
        descriptor["runtimes"]["docker"]["children"] = tuple(
            descriptor["runtimes"]["docker"]["children"]
        )

        restored = GraphDescriptorCodec().decode(descriptor)

        self.assertEqual(restored.runtimes["docker"].children, ("orders-api", "postgres"))

    def test_malformed_top_level_shape_fails_with_typed_error(self):
        with self.assertRaises(MalformedGraphDescriptor):
            GraphDescriptorCodec().decode({"name": "broken", "nodes": []})


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("expected mapping")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


if __name__ == "__main__":
    unittest.main()
