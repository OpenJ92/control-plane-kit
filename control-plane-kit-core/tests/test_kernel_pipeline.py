from __future__ import annotations

from dataclasses import dataclass
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DeploymentTopology,
    DockerRuntime,
    RequirementSocket,
    ProviderSocket,
    SocketConnection,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.planning import (
    AddSocketConnection,
    ReconcileNode,
    StartNode,
    StartRuntime,
    WaitForHealthy,
    compile_activity_plan,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    EdgeSubject,
    GraphDescriptorCodec,
    GraphDiff,
    GraphValidationError,
    LiteralAddress,
    LossyGraphDescriptor,
    ModifiedChange,
    UnknownGraphVariant,
    ValidationCode,
    compile_recipe,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.topology.graph import Endpoint
from control_plane_kit_core.types import Protocol, SocketBinding


@dataclass(frozen=True)
class MaterializedBlock:
    kind: str
    endpoints: dict[str, Endpoint]
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
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

    def materialize(self, block_id: str, sockets: BlockSockets, runtime: object) -> MaterializedBlock:
        return MaterializedBlock(
            kind=self.kind,
            endpoints={
                socket_name: Endpoint(LiteralAddress(address), Protocol.parse(protocol_name))
                for socket_name, address in self.endpoints.items()
                for protocol_name in (sockets.provider(socket_name).protocol.value,)
            },
        )


def app_with_database_topology() -> DeploymentTopology:
    api = ApplicationBlock(
        BlockSpec("api"),
        PureImplementation("application", {"internal": "http://api"}),
        BlockSockets(
            requirements=(RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    database = DataBlock(
        BlockSpec("postgres"),
        PureImplementation("data", {"internal": "postgresql://postgres:5432/app"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    return DeploymentTopology(
        "orders",
        DockerRuntime(
            children=(
                api,
                database,
                SocketConnection("postgres", "internal", "api", "database"),
            )
        ),
    )


def split_service_topology() -> DeploymentTopology:
    api = ApplicationBlock(
        BlockSpec("api"),
        PureImplementation("application", {"public": "http://api"}),
        BlockSockets(
            requirements=(
                RequirementSocket(
                    "inventory-service",
                    Protocol.HTTP,
                    ("INVENTORY_SERVICE_URL",),
                ),
            ),
            providers=(ProviderSocket("public", Protocol.HTTP),),
        ),
    )
    inventory = ApplicationBlock(
        BlockSpec("inventory-service"),
        PureImplementation("application", {"internal": "http://inventory"}),
        BlockSockets(
            requirements=(RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        ),
    )
    database = DataBlock(
        BlockSpec("postgres"),
        PureImplementation("data", {"internal": "postgresql://postgres:5432/app"}),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    return DeploymentTopology(
        "split-service",
        DockerRuntime(
            children=(
                api,
                inventory,
                database,
                SocketConnection("inventory-service", "internal", "api", "inventory-service"),
                SocketConnection("postgres", "internal", "inventory-service", "database"),
            )
        ),
    )


class PureKernelPipelineTests(unittest.TestCase):
    def test_socket_names_are_accessible_and_binding_laws_are_structural(self) -> None:
        sockets = BlockSockets(
            requirements=(RequirementSocket("database", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        )

        self.assertEqual(sockets.requirement("database").protocol, Protocol.POSTGRES)
        self.assertEqual(sockets.provider("internal").protocol, Protocol.HTTP)

        with self.assertRaisesRegex(ValueError, "needs at least one env binding"):
            RequirementSocket("database", Protocol.POSTGRES, ())
        runtime_socket = RequirementSocket(
            "active",
            Protocol.HTTP,
            (),
            binding=SocketBinding.RUNTIME_CONTROL,
        )
        self.assertIs(runtime_socket.binding, SocketBinding.RUNTIME_CONTROL)
        with self.assertRaisesRegex(ValueError, "runtime-controlled"):
            RequirementSocket(
                "active",
                Protocol.HTTP,
                ("ACTIVE_URL",),
                binding=SocketBinding.RUNTIME_CONTROL,
            )

    def test_topology_compilation_wires_provider_to_requirement_environment(self) -> None:
        graph = compile_topology(app_with_database_topology())

        api = graph.node("api")
        postgres = graph.node("postgres")
        edge = graph.edges["postgres.internal-to-api.database"]

        self.assertEqual(api.non_secret_environment()["DATABASE_URL"], postgres.endpoint("internal").url)
        self.assertEqual(edge.protocol, Protocol.POSTGRES)
        self.assertEqual(
            edge.descriptor()["provider"],
            {"role": "postgres", "socket": "internal"},
        )
        self.assertEqual(
            edge.descriptor()["consumer"],
            {"role": "api", "requirement": "database"},
        )

    def test_protocol_mismatch_fails_at_pure_compile_boundary(self) -> None:
        source = app_with_database_topology()
        bad = SocketConnection(
            "postgres",
            "internal",
            "api",
            "database",
            protocol=Protocol.HTTP,
        )
        broken = DeploymentTopology(
            source.name,
            DockerRuntime(children=(*source.root.children[:-1], bad)),
        )

        with self.assertRaises(ValueError):
            compile_topology(broken)

    def test_split_service_wires_http_and_postgres_requirements(self) -> None:
        graph = compile_topology(split_service_topology())

        api = graph.node("api")
        inventory = graph.node("inventory-service")
        postgres = graph.node("postgres")

        self.assertEqual(
            api.non_secret_environment()["INVENTORY_SERVICE_URL"],
            inventory.endpoint("internal").url,
        )
        self.assertEqual(
            inventory.non_secret_environment()["DATABASE_URL"],
            postgres.endpoint("internal").url,
        )
        self.assertEqual(
            graph.edges["inventory-service.internal-to-api.inventory-service"].protocol,
            Protocol.HTTP,
        )
        self.assertEqual(
            graph.edges["postgres.internal-to-inventory-service.database"].protocol,
            Protocol.POSTGRES,
        )

    def test_graph_descriptor_round_trip_preserves_typed_identity(self) -> None:
        graph = compile_topology(app_with_database_topology())
        codec = GraphDescriptorCodec()

        descriptor = codec.encode(graph)
        restored = codec.decode(descriptor)

        self.assertEqual(restored, graph)
        self.assertEqual(codec.encode(restored), descriptor)
        descriptor["nodes"]["api"]["block_spec"]["variant"] = "future"
        with self.assertRaisesRegex(UnknownGraphVariant, "block spec variant"):
            codec.decode(descriptor)

    def test_unknown_descriptor_fields_and_literal_credentials_fail_closed(self) -> None:
        descriptor = GraphDescriptorCodec().encode(compile_topology(app_with_database_topology()))
        descriptor["future"] = {"meaning": "unknown"}

        with self.assertRaises(LossyGraphDescriptor):
            GraphDescriptorCodec().decode(descriptor)
        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            LiteralAddress("postgresql://operator:secret@database/app")

    def test_validation_reports_missing_required_connection_as_structured_data(self) -> None:
        source = app_with_database_topology()
        disconnected = DeploymentTopology(
            source.name,
            DockerRuntime(children=source.root.children[:-1]),
        )

        result = validate_graph(compile_topology(disconnected))

        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0].code, ValidationCode.MISSING_REQUIRED_CONNECTION)
        self.assertEqual(result.errors[0].subject.descriptor()["kind"], "socket")
        with self.assertRaises(GraphValidationError):
            result.require_valid()

    def test_diff_and_plan_for_initial_deployment_are_pure_and_typed(self) -> None:
        desired = validate_graph(compile_topology(app_with_database_topology()))
        current = validate_graph(DeploymentGraph(desired.graph.name))

        diff = diff_graphs(current, desired)
        plan = compile_activity_plan(diff)

        self.assertIsInstance(diff, GraphDiff)
        self.assertTrue(any(isinstance(activity.operation, StartRuntime) for activity in plan.activities))
        self.assertTrue(any(isinstance(activity.operation, StartNode) for activity in plan.activities))
        self.assertTrue(any(isinstance(activity.operation, WaitForHealthy) for activity in plan.activities))
        self.assertFalse(any(isinstance(activity.operation, AddSocketConnection) for activity in plan.activities))

    def test_runtime_control_socket_switch_becomes_explicit_activity(self) -> None:
        def graph(target: str) -> DeploymentGraph:
            route = ApplicationBlock(
                BlockSpec("router"),
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
                    "router-switch",
                    DockerRuntime(
                        children=(
                            route,
                            backend,
                            SocketConnection(target, "internal", "router", "active", edge_id="router.active"),
                        )
                    ),
                )
            )

        diff = diff_graphs(validate_graph(graph("blue")), validate_graph(graph("green")))
        plan = compile_activity_plan(diff)

        changed_edges = {
            change.subject.edge_id
            for change in diff.changes
            if isinstance(change, ModifiedChange) and isinstance(change.subject, EdgeSubject)
        }
        self.assertIn("router.active", changed_edges)
        self.assertTrue(
            any(activity.operation.__class__.__name__ == "SwitchSocketConnection" for activity in plan.activities)
        )
        self.assertFalse(any(isinstance(activity.operation, ReconcileNode) for activity in plan.activities))

    def test_recipe_names_remain_compatibility_aliases_during_rollout(self) -> None:
        self.assertIs(DeploymentRecipe, DeploymentTopology)
        self.assertIs(compile_recipe, compile_topology)
        self.assertEqual(
            compile_recipe(app_with_database_topology()),
            compile_topology(app_with_database_topology()),
        )


if __name__ == "__main__":
    unittest.main()
