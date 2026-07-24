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
from control_plane_kit_core.environment import SocketDerivedEnvironmentBinding
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import (
    Endpoint,
    GraphDescriptorCodec,
    InvalidGraphReference,
    LiteralAddress,
    LossyGraphDescriptor,
    MalformedGraphDescriptor,
    SecretReferenceAddress,
    UnknownGraphVariant,
    compile_topology,
)
from control_plane_kit_core.types import Protocol


@dataclass(frozen=True)
class MaterializedBlock:
    kind: str
    endpoints: dict[str, Endpoint]
    public_environment: tuple[object, ...] = ()
    metadata: dict[str, object] | None = None
    lifecycle: object = OWNED_EPHEMERAL
    configuration_artifacts: tuple[object, ...] = ()
    secret_deliveries: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class PureImplementation:
    kind: str
    endpoints: dict[str, str]

    def materialize(
        self,
        block_id: str,
        sockets: BlockSockets,
        runtime: object,
    ) -> MaterializedBlock:
        return MaterializedBlock(
            self.kind,
            {
                name: Endpoint(LiteralAddress(address), sockets.provider(name).protocol)
                for name, address in self.endpoints.items()
            },
        )


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
            "verification": spec.verification.descriptor(),
            "metadata": dict(sorted(spec.metadata.items())),
            "public_provider": spec.public_provider,
        }

    def decode(self, descriptor: Mapping[str, object]) -> BlockSpec:
        return ExampleInstanceSpec(
            role_id=_text(descriptor, "role_id"),
            display_name=_optional_text(descriptor.get("display_name")),
            health_path=_optional_text(descriptor.get("health_path")),
            metadata={
                str(key): str(value)
                for key, value in _mapping(descriptor["metadata"]).items()
            },
            public_provider=_text(descriptor, "public_provider"),
        )


class GraphDescriptorCodecTests(unittest.TestCase):
    def test_registered_spec_variant_round_trips_without_string_inference(self) -> None:
        app = ApplicationBlock(
            ExampleInstanceSpec("instance-a", public_provider="operator"),
            PureImplementation("instance", {"operator": "http://instance"}),
            BlockSockets(providers=(ProviderSocket("operator", Protocol.HTTP),)),
        )
        graph = compile_topology(
            DeploymentTopology("instance", DockerRuntime(children=(app,)))
        )
        codec = GraphDescriptorCodec(spec_codecs=(ExampleInstanceSpecCodec(),))

        restored = codec.decode(codec.encode(graph))

        self.assertEqual(restored.node("instance-a").block_spec, app.spec)
        self.assertIsInstance(
            restored.node("instance-a").block_spec,
            ExampleInstanceSpec,
        )

    def test_runtime_authority_ref_round_trips_as_secret_free_graph_data(self) -> None:
        app = ApplicationBlock(
            ExampleInstanceSpec("instance-a", public_provider="operator"),
            PureImplementation("instance", {"operator": "http://instance"}),
            BlockSockets(providers=(ProviderSocket("operator", Protocol.HTTP),)),
        )
        graph = compile_topology(
            DeploymentTopology(
                "instance",
                DockerRuntime(
                    authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                    children=(app,),
                ),
            )
        )
        codec = GraphDescriptorCodec(spec_codecs=(ExampleInstanceSpecCodec(),))

        descriptor = codec.encode(graph)
        restored = codec.decode(descriptor)

        self.assertEqual(
            descriptor["runtimes"]["docker"]["authority_ref"],
            {"reference_id": "mac-mini-docker"},
        )
        self.assertEqual(
            restored.runtimes["docker"].authority_ref,
            RuntimeAuthorityReference("mac-mini-docker"),
        )

    def test_runtime_authority_ref_rejects_material_in_graph_descriptor(self) -> None:
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(app_with_database_graph())

        self.assertIsNone(descriptor["runtimes"]["docker"]["authority_ref"])
        descriptor["runtimes"]["docker"]["authority_ref"] = {
            "reference_id": "tcp://mac-mini.local:2376"
        }

        with self.assertRaises(MalformedGraphDescriptor):
            codec.decode(descriptor)

    def test_secret_reference_address_round_trips_without_secret_resolution(self) -> None:
        graph = app_with_database_graph()
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

    def test_environment_binding_variants_round_trip_and_unknown_kind_fails_closed(self) -> None:
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(app_with_database_graph())
        api = descriptor["nodes"]["api"]["environment_bindings"]

        self.assertEqual({value["kind"] for value in api}, {"socket-derived"})
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)

        api[0]["kind"] = "future-environment"
        with self.assertRaisesRegex(UnknownGraphVariant, "unknown environment"):
            codec.decode(descriptor)

    def test_environment_passwords_fail_before_raw_descriptor_reconstruction(self) -> None:
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(app_with_database_graph())
        supplied = "postgresql://app:do-not-disclose@database:5432/app"
        descriptor["nodes"]["api"]["environment_bindings"][0]["value"] = supplied

        with self.assertRaises(UnknownGraphVariant) as caught:
            codec.decode(descriptor)

        self.assertNotIn("do-not-disclose", str(caught.exception))

    def test_direct_node_and_edge_cannot_restore_inline_environment_secrets(self) -> None:
        graph = app_with_database_graph()
        node = graph.node("api")
        edge = next(iter(graph.edges.values()))

        with self.assertRaisesRegex(ValueError, "must not contain environment"):
            replace(node, metadata={"environment": {"API_TOKEN": "do-not-disclose"}})
        with self.assertRaises(ValueError) as caught:
            replace(
                edge,
                env_assignments={
                    "DATABASE_URL": "postgresql://app:do-not-disclose@database:5432/app"
                },
            )
        self.assertNotIn("do-not-disclose", str(caught.exception))

    def test_missing_runtime_and_edge_socket_fail_loudly(self) -> None:
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(app_with_database_graph())
        descriptor["runtimes"]["docker"]["children"].remove("api")

        with self.assertRaisesRegex(InvalidGraphReference, "not owned"):
            codec.decode(descriptor)

        descriptor = codec.encode(app_with_database_graph())
        edge = descriptor["edges"]["postgres.internal-to-api.database"]
        edge["consumer"]["requirement"] = "missing"
        with self.assertRaises(InvalidGraphReference):
            codec.decode(descriptor)

    def test_edge_binding_and_protocol_products_are_closed(self) -> None:
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(app_with_database_graph())
        edge = descriptor["edges"]["postgres.internal-to-api.database"]

        self.assertEqual(edge["binding"], "environment")
        self.assertEqual(
            edge["protocol"],
            {"transport": "tcp", "application": "postgres"},
        )
        self.assertEqual(codec.encode(codec.decode(descriptor)), descriptor)

        edge["binding"] = "future-binding"
        with self.assertRaises(UnknownGraphVariant):
            codec.decode(descriptor)

        descriptor = codec.encode(app_with_database_graph())
        descriptor["edges"]["postgres.internal-to-api.database"]["protocol"][
            "transport"
        ] = "future"
        with self.assertRaisesRegex(UnknownGraphVariant, "unknown protocol"):
            codec.decode(descriptor)

    def test_tuple_input_malformed_shape_and_unknown_fields_fail_closed(self) -> None:
        codec = GraphDescriptorCodec()
        descriptor = codec.encode(app_with_database_graph())
        descriptor["runtimes"]["docker"]["children"] = tuple(
            descriptor["runtimes"]["docker"]["children"]
        )

        restored = codec.decode(descriptor)

        self.assertEqual(restored.runtimes["docker"].children, ("api", "postgres"))
        with self.assertRaises(MalformedGraphDescriptor):
            codec.decode({"name": "broken", "nodes": []})

        descriptor = codec.encode(app_with_database_graph())
        descriptor["future"] = {"meaning": "unknown"}
        with self.assertRaises(LossyGraphDescriptor):
            codec.decode(descriptor)


def app_with_database_graph():
    api = ApplicationBlock(
        BlockSpec("api"),
        PureImplementation("application", {"internal": "http://api"}),
        BlockSockets(
            requirements=(
                RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),
            ),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    database = ApplicationBlock(
        BlockSpec("postgres"),
        PureImplementation("data", {"internal": "postgresql://postgres:5432/app"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    return compile_topology(
        DeploymentTopology(
            "orders",
            DockerRuntime(
                children=(
                    api,
                    database,
                    SocketConnection("postgres", "internal", "api", "database"),
                )
            ),
        )
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("expected mapping")
    return value


def _text(descriptor: Mapping[str, object], key: str) -> str:
    value = descriptor[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be text")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


if __name__ == "__main__":
    unittest.main()
