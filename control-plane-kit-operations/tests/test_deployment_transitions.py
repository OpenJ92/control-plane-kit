from __future__ import annotations

import ast
from dataclasses import dataclass, replace
import importlib
from pathlib import Path
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
    ProviderSocket,
)
from control_plane_kit_core.delegation_authority import DelegationAuthorityBinding
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressTarget,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Edge,
    Endpoint,
    GraphDiff,
    GraphValidationError,
    LiteralAddress,
    RuntimeRecord,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.topology.validation import ValidatedGraph
from control_plane_kit_core.types import Protocol, RuntimeKind, SocketBinding


EXPECTED_EXPORTS = (
    "Deploy",
    "DeploymentTransition",
    "InitialDeployment",
    "UpdateDeployment",
    "TeardownDeployment",
    "NoOpDeployment",
)


def _contract():
    import control_plane_kit_operations as operations

    missing = tuple(name for name in EXPECTED_EXPORTS if not hasattr(operations, name))
    if missing:
        raise AssertionError(
            "missing #1624 deployment-transition exports: " + ", ".join(missing)
        )
    return operations, importlib.import_module(
        "control_plane_kit_operations.deployment_transitions"
    )


def _validated(graph: DeploymentGraph) -> ValidatedGraph:
    result = validate_graph(graph)
    if not result.valid:
        raise AssertionError(f"test graph is invalid: {result.descriptor()!r}")
    return result


def _empty(name: str = "service") -> ValidatedGraph:
    return _validated(DeploymentGraph(name))


def _runtime_graph(name: str, *, owner: str) -> ValidatedGraph:
    return _validated(
        DeploymentGraph(
            name,
            runtimes={
                "runtime": RuntimeRecord(
                    "runtime",
                    RuntimeKind.DOCKER,
                    metadata={"owner": owner},
                )
            },
        )
    )


def _gateway_graph(name: str = "gateway") -> DeploymentGraph:
    gateway = ApplicationBlock(
        BlockSpec("gateway"),
        _PureImplementation("gateway", {"control": "http://gateway:8000"}),
        BlockSockets(providers=(ProviderSocket("control", Protocol.HTTP),)),
    )
    connector = ApplicationBlock(
        BlockSpec("connector"),
        _PureImplementation("connector", {}),
        BlockSockets(),
    )
    return compile_topology(
        DeploymentTopology(
            name,
            DockerRuntime(children=(gateway, connector)),
        )
    )


@dataclass(frozen=True)
class _MaterializedBlock:
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
class _PureImplementation:
    kind: str
    endpoints: dict[str, str]

    def materialize(
        self,
        block_id: str,
        sockets: BlockSockets,
        runtime: object,
    ) -> _MaterializedBlock:
        return _MaterializedBlock(
            self.kind,
            {
                name: Endpoint(
                    LiteralAddress(address),
                    sockets.provider(name).protocol,
                )
                for name, address in self.endpoints.items()
            },
        )


class DeploymentTransitionTests(unittest.TestCase):
    def test_public_module_and_root_export_one_closed_transition_family(self) -> None:
        operations, module = _contract()

        self.assertEqual(
            module.__name__,
            "control_plane_kit_operations.deployment_transitions",
        )
        for name in EXPECTED_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(getattr(operations, name), getattr(module, name))
        self.assertEqual(
            module.DeploymentTransition,
            (
                module.InitialDeployment
                | module.UpdateDeployment
                | module.TeardownDeployment
                | module.NoOpDeployment
            ),
        )

    def test_deploy_classifies_all_four_forms_and_retains_existing_diff(self) -> None:
        operations, _ = _contract()
        empty = _empty()
        blue = _runtime_graph("service", owner="blue")
        green = _runtime_graph("service", owner="green")

        cases = (
            (empty, blue, operations.InitialDeployment),
            (blue, green, operations.UpdateDeployment),
            (blue, empty, operations.TeardownDeployment),
            (empty, empty, operations.NoOpDeployment),
            (blue, blue, operations.NoOpDeployment),
        )
        for current, desired, expected in cases:
            with self.subTest(expected=expected.__name__):
                transition = operations.Deploy(current, desired)
                self.assertIsInstance(transition, expected)
                self.assertIs(transition.current, current)
                self.assertIs(transition.desired, desired)
                self.assertIsInstance(transition.diff, GraphDiff)
                self.assertEqual(
                    transition.diff.descriptor(),
                    operations.Deploy(current, desired).diff.descriptor(),
                )

    def test_direct_variant_construction_rejects_every_wrong_form(self) -> None:
        operations, _ = _contract()
        empty = _empty()
        blue = _runtime_graph("service", owner="blue")
        green = _runtime_graph("service", owner="green")
        valid = (
            (operations.InitialDeployment, empty, blue),
            (operations.UpdateDeployment, blue, green),
            (operations.TeardownDeployment, blue, empty),
            (operations.NoOpDeployment, empty, empty),
            (operations.NoOpDeployment, blue, blue),
        )
        forged = GraphDiff("forged-current", "forged-desired", ())
        for variant, current, desired in valid:
            with self.subTest(variant=variant.__name__, case="computed-diff"):
                transition = variant(current, desired)
                self.assertEqual(transition.diff, diff_graphs(current, desired))
                with self.assertRaises(TypeError):
                    variant(current, desired, diff=forged)

        invalid = (
            (operations.InitialDeployment, blue, green),
            (operations.UpdateDeployment, empty, blue),
            (operations.TeardownDeployment, empty, empty),
            (operations.NoOpDeployment, blue, green),
        )

        for variant, current, desired in invalid:
            with self.subTest(variant=variant.__name__):
                with self.assertRaisesRegex(ValueError, "deployment transition"):
                    variant(current, desired)

        invalid_graph = validate_graph(
            DeploymentGraph(
                "invalid",
                runtimes={
                    "runtime": RuntimeRecord(
                        "runtime",
                        RuntimeKind.DOCKER,
                        children=("missing-node",),
                    )
                },
            )
        )
        self.assertFalse(invalid_graph.valid)
        with self.assertRaises(GraphValidationError):
            operations.Deploy(invalid_graph, empty)
        with self.assertRaises(GraphValidationError):
            operations.UpdateDeployment(invalid_graph, empty)

    def test_distinct_structurally_empty_graphs_are_an_update(self) -> None:
        operations, _ = _contract()

        transition = operations.Deploy(_empty("before"), _empty("after"))

        self.assertIsInstance(transition, operations.UpdateDeployment)
        self.assertFalse(transition.diff.empty)
        self.assertEqual(
            transition.diff.descriptor()["changes"][0]["subject"],
            {"owner": {"kind": "graph"}, "field": "graph-name"},
        )

    def test_legacy_and_modern_graph_owned_surfaces_cross_empty_boundary(self) -> None:
        operations, _ = _contract()
        ingress = NamedPublicIngress(
            ingress_id="gateway-public",
            authority_ref=IngressAuthorityReference("public-ingress-authority"),
            target=PublicIngressTarget("gateway", "control"),
            connector_node_id="connector",
            hostname="gateway.example.test",
        )
        authority = DelegationAuthorityBinding(
            delegate_node_id="gateway",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="gateway-probe-issuer",
        )
        graphs = (
            _runtime_graph("runtime-only", owner="platform"),
            _validated(
                replace(
                    _gateway_graph("ingress-only-change"),
                    public_ingresses=(ingress,),
                )
            ),
            _validated(
                replace(
                    _gateway_graph("authority-only-change"),
                    delegation_authorities=(authority,),
                )
            ),
        )

        for desired in graphs:
            with self.subTest(graph=desired.graph.name):
                self.assertIsInstance(
                    operations.Deploy(_empty(desired.graph.name), desired),
                    operations.InitialDeployment,
                )

    def test_structural_emptiness_predicate_names_all_five_collections(self) -> None:
        _, module = _contract()
        gateway = _gateway_graph("predicate-witness")
        ingress = NamedPublicIngress(
            ingress_id="gateway-public",
            authority_ref=IngressAuthorityReference("public-ingress-authority"),
            target=PublicIngressTarget("gateway", "control"),
            connector_node_id="connector",
            hostname="gateway.example.test",
        )
        authority = DelegationAuthorityBinding(
            delegate_node_id="gateway",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="gateway-probe-issuer",
        )
        edge = Edge(
            "provider:control->consumer:control",
            "provider",
            "control",
            "consumer",
            "control",
            Protocol.HTTP,
            SocketBinding.RUNTIME_CONTROL,
        )
        witnesses = (
            DeploymentGraph("node-only", nodes=gateway.nodes),
            DeploymentGraph("edge-only", edges={edge.edge_id: edge}),
            DeploymentGraph("runtime-only", runtimes=gateway.runtimes),
            DeploymentGraph("ingress-only", public_ingresses=(ingress,)),
            DeploymentGraph("authority-only", delegation_authorities=(authority,)),
        )

        self.assertTrue(module._structurally_empty(DeploymentGraph("empty")))
        for graph in witnesses:
            with self.subTest(graph=graph.name):
                self.assertFalse(module._structurally_empty(graph))

    def test_modern_surface_only_diffs_are_updates(self) -> None:
        operations, _ = _contract()
        base = _gateway_graph()
        ingress = NamedPublicIngress(
            ingress_id="gateway-public",
            authority_ref=IngressAuthorityReference("public-ingress-authority"),
            target=PublicIngressTarget("gateway", "control"),
            connector_node_id="connector",
            hostname="gateway.example.test",
        )
        authority = DelegationAuthorityBinding(
            delegate_node_id="gateway",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="gateway-probe-issuer",
        )
        cases = (
            replace(base, public_ingresses=(ingress,)),
            replace(base, delegation_authorities=(authority,)),
        )

        for desired_graph in cases:
            with self.subTest(desired=desired_graph.descriptor()):
                transition = operations.Deploy(
                    _validated(base),
                    _validated(desired_graph),
                )
                self.assertIsInstance(transition, operations.UpdateDeployment)
                self.assertEqual(len(transition.diff.changes), 1)

    def test_transition_module_is_pure_and_has_no_program_or_effect_surface(self) -> None:
        operations, module = _contract()
        source_path = Path(module.__file__).resolve()
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertFalse(
            imported_roots
            & {
                "control_plane_kit_interpreters",
                "control_plane_kit_secrets",
                "control_plane_kit_servers",
                "docker",
                "fastapi",
                "httpx",
                "mcp",
                "psycopg",
                "requests",
                "subprocess",
            }
        )
        for forbidden in (
            "DeploymentProgram",
            "Postgres",
            "UnitOfWork",
            "commit(",
            "rollback(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
