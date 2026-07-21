from __future__ import annotations

from dataclasses import dataclass, replace
import json
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
)
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
    environment_binding_from_descriptor,
)
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.secrets import (
    LocalDevelopmentSecretResolver,
    SecretDenied,
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretFileMode,
    SecretFilePathBinding,
    SecretMissing,
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
    SecretReferenceEnvironmentDelivery,
    SecretResolutionCode,
    SecretResolutionError,
    SecretResolved,
    require_resolved_secret,
    secret_delivery_from_descriptor,
)
from control_plane_kit_core.topology import (
    Endpoint,
    FieldSubject,
    GraphDescriptorCodec,
    ModifiedChange,
    Node,
    StructuralField,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import BlockFamily, Protocol


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
class EnvironmentImplementation:
    public_environment: tuple[object, ...] = ()
    secret_deliveries: tuple[object, ...] = ()

    def materialize(
        self,
        block_id: str,
        sockets: BlockSockets,
        runtime: object,
    ) -> MaterializedBlock:
        return MaterializedBlock(
            "environment-fixture",
            {},
            self.public_environment,
            secret_deliveries=self.secret_deliveries,
        )


class EnvironmentBindingTests(unittest.TestCase):
    def test_public_binding_is_closed_bounded_and_deterministic(self) -> None:
        binding = PublicStaticEnvironmentBinding("WORKER_COUNT", "4")

        self.assertEqual(
            binding.descriptor(),
            {"kind": "public-static", "name": "WORKER_COUNT", "value": "4"},
        )
        self.assertEqual(environment_binding_from_descriptor(binding.descriptor()), binding)

        for name in ("", "lowercase", "1_INVALID", "A-B"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                PublicStaticEnvironmentBinding(name, "value")
        with self.assertRaises(TypeError):
            PublicStaticEnvironmentBinding("WORKER_COUNT", 4)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "malformed or unbounded"):
            PublicStaticEnvironmentBinding("VALUE", "contains\x00nul")
        with self.assertRaisesRegex(ValueError, "malformed or unbounded"):
            PublicStaticEnvironmentBinding("VALUE", "x" * 16_385)
        with self.assertRaisesRegex(ValueError, "SecretEnvironmentDelivery") as caught:
            PublicStaticEnvironmentBinding("API_TOKEN", "do-not-disclose")
        self.assertNotIn("do-not-disclose", str(caught.exception))

    def test_inline_passwords_and_public_secret_references_fail_without_disclosure(self) -> None:
        supplied = (
            "postgresql://app:do-not-disclose@database:5432/app",
            "secret://local/workspace/do-not-disclose",
        )
        for value in supplied:
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                PublicStaticEnvironmentBinding("DATABASE_URL", value)
            self.assertNotIn("do-not-disclose", str(caught.exception))

        self.assertEqual(
            PublicStaticEnvironmentBinding(
                "DATABASE_URL",
                "postgresql://app@database:5432/app",
            ).value,
            "postgresql://app@database:5432/app",
        )

        with self.assertRaises(ValueError) as caught:
            SocketDerivedEnvironmentBinding(
                "DATABASE_URL",
                "postgresql://app:do-not-disclose@database:5432/app",
                "database.internal-to-api.database",
            )
        self.assertNotIn("do-not-disclose", str(caught.exception))

    def test_unknown_or_extra_environment_descriptors_fail_closed(self) -> None:
        socket_binding = SocketDerivedEnvironmentBinding(
            "DATABASE_URL",
            "postgresql://app@database:5432/app",
            "database.internal-to-api.database",
        )

        self.assertEqual(
            environment_binding_from_descriptor(socket_binding.descriptor()),
            socket_binding,
        )

        malformed = (
            {"kind": "future", "name": "VALUE", "value": "x"},
            {"kind": "public-static", "name": "VALUE", "value": "x", "extra": "no"},
            {"kind": "socket-derived", "name": "VALUE", "value": "x"},
        )
        for descriptor in malformed:
            with self.subTest(descriptor=descriptor), self.assertRaises(ValueError):
                environment_binding_from_descriptor(descriptor)

    def test_node_rejects_open_environment_metadata_and_untyped_bindings(self) -> None:
        node = bare_node()

        with self.assertRaisesRegex(ValueError, "must not contain environment"):
            replace(node, metadata={"environment": {"API_TOKEN": "do-not-disclose"}})
        with self.assertRaisesRegex(TypeError, "typed bindings"):
            replace(
                node,
                public_environment={"WORKER_COUNT": "4"},  # type: ignore[arg-type]
            )

    def test_public_socket_and_secret_environment_names_remain_disjoint(self) -> None:
        node = bare_node()
        reference = SecretReference("secret://local/workspace-a/database")

        with self.assertRaisesRegex(ValueError, "unique across sources"):
            replace(
                node,
                public_environment=(
                    PublicStaticEnvironmentBinding("DATABASE_URL", "public"),
                ),
                secret_deliveries=(
                    SecretEnvironmentDelivery("DATABASE_URL", reference),
                ),
            )
        with self.assertRaisesRegex(ValueError, "unique across sources"):
            replace(
                node,
                socket_environment=(
                    SocketDerivedEnvironmentBinding(
                        "POSTGRES_PASSWORD_FILE",
                        "/run/secrets/database-password",
                        "database.internal-to-api.database",
                    ),
                ),
                secret_deliveries=(
                    SecretFileDelivery(
                        "/run/secrets/database-password",
                        reference,
                        path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
                    ),
                ),
            )


class SecretContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = SecretProviderAuthority(
            SecretProviderId("local"),
            (("workspace-a",),),
        )
        self.resolver = LocalDevelopmentSecretResolver(
            self.authority,
            {"secret://local/workspace-a/database": "never-persist-this"},
        )

    def test_reference_has_provider_and_path_identity(self) -> None:
        reference = SecretReference("secret://local/workspace-a/database")

        self.assertEqual(reference.provider_id, SecretProviderId("local"))
        self.assertEqual(reference.path, ("workspace-a", "database"))

    def test_malformed_references_fail_at_construction(self) -> None:
        for value in (
            "",
            "token",
            "secret://",
            "secret://LOCAL/key",
            "secret://local/a/../b",
        ):
            with self.subTest(value=value), self.assertRaises(SecretResolutionError):
                SecretReference(value)

    def test_local_resolver_distinguishes_resolved_missing_and_denied(self) -> None:
        resolved = self.resolver.resolve(
            SecretReference("secret://local/workspace-a/database")
        )
        missing = self.resolver.resolve(
            SecretReference("secret://local/workspace-a/missing")
        )
        denied = self.resolver.resolve(
            SecretReference("secret://local/workspace-b/database")
        )

        self.assertIsInstance(resolved, SecretResolved)
        self.assertIsInstance(missing, SecretMissing)
        self.assertIsInstance(denied, SecretDenied)

    def test_values_are_released_only_by_explicit_runtime_interpretation(self) -> None:
        value = require_resolved_secret(
            self.resolver,
            SecretReference("secret://local/workspace-a/database"),
        )

        self.assertEqual(value.reveal(), "never-persist-this")
        self.assertNotIn("never-persist-this", repr(value))
        self.assertNotIn("never-persist-this", repr(self.resolver))

    def test_denied_and_missing_errors_are_closed_and_redacted(self) -> None:
        cases = (
            ("secret://local/workspace-a/missing", SecretResolutionCode.MISSING),
            ("secret://local/workspace-b/database", SecretResolutionCode.DENIED),
        )
        for reference_id, expected in cases:
            with self.subTest(reference_id=reference_id):
                with self.assertRaises(SecretResolutionError) as raised:
                    require_resolved_secret(self.resolver, SecretReference(reference_id))
                self.assertIs(raised.exception.code, expected)
                self.assertNotIn(reference_id, str(raised.exception))
                self.assertNotIn("never-persist-this", str(raised.exception))

    def test_bootstrap_configuration_cannot_exceed_authority(self) -> None:
        with self.assertRaises(SecretResolutionError) as raised:
            LocalDevelopmentSecretResolver(
                self.authority,
                {"secret://local/workspace-b/database": "forbidden"},
            )

        self.assertIs(raised.exception.code, SecretResolutionCode.DENIED)
        self.assertNotIn("forbidden", str(raised.exception))

    def test_closed_delivery_variants_round_trip(self) -> None:
        values = (
            SecretEnvironmentDelivery(
                "DATABASE_URL",
                SecretReference("secret://local/workspace-a/database"),
            ),
            SecretReferenceEnvironmentDelivery(
                "DATABASE_REFERENCE",
                SecretReference("secret://local/workspace-a/database"),
            ),
            SecretFileDelivery(
                "/run/secrets/database-password",
                SecretReference("secret://local/workspace-a/database-password"),
                SecretFileMode.OWNER_READ_ONLY,
                SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
            ),
        )

        self.assertEqual(
            tuple(secret_delivery_from_descriptor(value.descriptor()) for value in values),
            values,
        )

    def test_secret_file_targets_are_closed_to_protected_namespace(self) -> None:
        for target in (
            "/etc/service/password",
            "/run/secrets/../password",
            "/run/secrets/",
            "run/secrets/password",
        ):
            with self.subTest(target=target), self.assertRaises(SecretResolutionError):
                SecretFileDelivery(
                    target,
                    SecretReference("secret://local/workspace-a/password"),
                )

    def test_unknown_or_extra_delivery_fields_fail_closed(self) -> None:
        malformed = (
            {"kind": "pipe", "reference_id": "secret://local/workspace-a/key"},
            {
                "kind": "environment",
                "environment_name": "TOKEN",
                "reference_id": "secret://local/workspace-a/key",
                "value": "must-not-enter",
            },
            {
                "kind": "file",
                "target_path": "/run/secrets/password",
                "reference_id": "secret://local/workspace-a/key",
                "file_mode": "0400",
                "path_binding": {"environment_name": "PASSWORD_FILE", "extra": True},
            },
        )
        for descriptor in malformed:
            with self.subTest(descriptor=descriptor), self.assertRaises(
                SecretResolutionError,
            ):
                secret_delivery_from_descriptor(descriptor)


class SecretDeliveryTopologyTests(unittest.TestCase):
    def test_graph_codec_preserves_closed_deliveries_without_values(self) -> None:
        graph = secret_graph("database-a")
        descriptor = GraphDescriptorCodec().encode(graph)

        reconstructed = GraphDescriptorCodec().decode(descriptor)

        self.assertEqual(
            reconstructed.node("service").secret_deliveries,
            graph.node("service").secret_deliveries,
        )
        self.assertIn(
            "environment-reference",
            {
                value["kind"]
                for value in descriptor["nodes"]["service"]["secret_deliveries"]
            },
        )
        rendered = json.dumps(descriptor, sort_keys=True)
        self.assertIn("secret://local/workspace-a/database-a", rendered)
        self.assertNotIn("resolved-password", rendered)

    def test_delivery_change_is_explicit_diff_without_exposing_values(self) -> None:
        current = validate_graph(secret_graph("database-a"))
        desired = validate_graph(secret_graph("database-b"))

        diff = diff_graphs(current, desired)
        change = next(
            item
            for item in diff.changes
            if isinstance(item, ModifiedChange)
            and isinstance(item.subject, FieldSubject)
            and item.subject.field is StructuralField.SECRET_DELIVERIES
        )
        rendered = json.dumps(diff.descriptor(), sort_keys=True)

        self.assertNotEqual(change.before.descriptor(), change.after.descriptor())
        self.assertIn("<redacted>", rendered)
        self.assertNotIn("database-a", rendered)
        self.assertNotIn("database-b", rendered)

    def test_exact_deliveries_remain_pinned_in_desired_graph_language(self) -> None:
        graph = secret_graph("database-a")
        service = graph.node("service")

        environment = next(
            value
            for value in service.secret_deliveries
            if isinstance(value, SecretEnvironmentDelivery)
        )
        reference_identity = next(
            value
            for value in service.secret_deliveries
            if isinstance(value, SecretReferenceEnvironmentDelivery)
        )
        file_delivery = next(
            value
            for value in service.secret_deliveries
            if isinstance(value, SecretFileDelivery)
        )

        self.assertEqual(
            environment.reference.reference_id,
            "secret://local/workspace-a/database-a",
        )
        self.assertEqual(
            reference_identity.reference.reference_id,
            "secret://local/workspace-a/database-a",
        )
        self.assertEqual(file_delivery.target_path, "/run/secrets/database-password")
        self.assertEqual(
            file_delivery.path_binding,
            SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
        )


def bare_node() -> Node:
    return Node(
        node_id="service",
        block_family=BlockFamily.APPLICATION,
        block_spec=BlockSpec("service"),
        kind="fixture",
        runtime_id="docker",
        sockets=BlockSockets(),
    )


def secret_graph(reference_name: str) -> object:
    reference = SecretReference(f"secret://local/workspace-a/{reference_name}")
    service = ApplicationBlock(
        BlockSpec("service"),
        EnvironmentImplementation(
            secret_deliveries=(
                SecretEnvironmentDelivery("DATABASE_URL", reference),
                SecretReferenceEnvironmentDelivery("DATABASE_REFERENCE", reference),
                SecretFileDelivery(
                    "/run/secrets/database-password",
                    reference,
                    path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
                ),
            ),
        ),
        BlockSockets(),
    )
    return compile_topology(
        DeploymentTopology(
            "secret-delivery",
            DockerRuntime(children=(service,)),
        )
    )


if __name__ == "__main__":
    unittest.main()
