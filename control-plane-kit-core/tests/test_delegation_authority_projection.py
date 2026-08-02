from __future__ import annotations

from dataclasses import dataclass
import json
import unittest

from control_plane_kit_core.algebra import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentTopology,
    DockerRuntime,
)
from control_plane_kit_core.lifecycle import OWNED_EPHEMERAL
from control_plane_kit_core.planning import (
    ReconcileNode,
    WaitForHealthy,
    compile_activity_plan,
)
from control_plane_kit_core.delegation_authority import (
    DelegationAuthorityBinding,
    DelegationAuthorityError,
    DelegationVerifierProjection,
    materialize_delegation_verifiers,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    Node,
    RuntimeRecord,
    StructuralField,
    compile_topology,
    diff_graphs,
    validate_graph,
)
from control_plane_kit_core.types import BlockFamily, RuntimeKind


_PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
QUFB
-----END PUBLIC KEY-----
"""
_PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
QkJC
-----END PUBLIC KEY-----
"""


@dataclass(frozen=True)
class _MaterializedGateway:
    kind: str = "container-server"
    endpoints: dict[str, object] | None = None
    public_environment: tuple[object, ...] = ()
    metadata: dict[str, object] | None = None
    lifecycle: object = OWNED_EPHEMERAL
    configuration_artifacts: tuple[object, ...] = ()
    secret_deliveries: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.endpoints is None:
            object.__setattr__(self, "endpoints", {})
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class _GatewayImplementation:
    kind: str = "container-server"

    def materialize(
        self,
        block_id: str,
        sockets: BlockSockets,
        runtime: object,
    ) -> _MaterializedGateway:
        return _MaterializedGateway()


class DelegationAuthorityProjectionTests(unittest.TestCase):
    def test_topology_compilation_and_graph_codec_preserve_authored_binding(self) -> None:
        binding = self.binding()
        graph = compile_topology(
            DeploymentTopology(
                "gateway-island",
                DockerRuntime(
                    children=(
                        ApplicationBlock(
                            BlockSpec("gateway"),
                            _GatewayImplementation(),
                            BlockSockets(),
                        ),
                    )
                ),
                delegation_authorities=(binding,),
            )
        )

        descriptor = DEFAULT_GRAPH_CODEC.encode(graph)
        restored = DEFAULT_GRAPH_CODEC.decode(descriptor)

        self.assertEqual(restored.delegation_authorities, (binding,))
        self.assertEqual(
            descriptor["delegation_authorities"],
            [
                {
                    "delegate_node_id": "gateway",
                    "purpose": "gateway-probe",
                    "issuer": "cpk-server:workspace-a",
                }
            ],
        )
        self.assertNotIn(
            "delegation_verifier_projection",
            descriptor["nodes"]["gateway"],
        )

    def test_materialization_changes_only_realized_projection(self) -> None:
        authored = self.authored_graph()
        authored_descriptor = DEFAULT_GRAPH_CODEC.encode(authored)

        graph_a = materialize_delegation_verifiers(
            authored,
            (self.projection("projection-a", self.public_key("key-a", _PUBLIC_KEY_A)),),
        )
        graph_ab = materialize_delegation_verifiers(
            authored,
            (
                self.projection(
                    "projection-ab",
                    self.public_key("key-a", _PUBLIC_KEY_A),
                    self.public_key("key-b", _PUBLIC_KEY_B),
                ),
            ),
        )
        graph_b = materialize_delegation_verifiers(
            authored,
            (self.projection("projection-b", self.public_key("key-b", _PUBLIC_KEY_B)),),
        )

        self.assertEqual(DEFAULT_GRAPH_CODEC.encode(authored), authored_descriptor)
        self.assertNotEqual(graph_a.descriptor(), graph_ab.descriptor())
        self.assertNotEqual(graph_ab.descriptor(), graph_b.descriptor())
        self.assertEqual(
            graph_ab,
            materialize_delegation_verifiers(
                authored,
                (
                    self.projection(
                        "projection-ab",
                        self.public_key("key-b", _PUBLIC_KEY_B),
                        self.public_key("key-a", _PUBLIC_KEY_A),
                    ),
                ),
            ),
        )
        projection = graph_ab.node("gateway").delegation_verifier_projection
        self.assertIsNotNone(projection)
        assert projection is not None
        environment = {
            value.name: value.value for value in projection.public_environment()
        }
        self.assertEqual(environment["CPK_GATEWAY_PROBE_ISSUER"], "cpk-server:workspace-a")
        self.assertEqual(environment["CPK_GATEWAY_PROBE_AUDIENCE"], "gateway:workspace-a:gateway")
        self.assertEqual(environment["CPK_GATEWAY_PROBE_NODE_ID"], "gateway")
        self.assertEqual(environment["CPK_GATEWAY_PROBE_VERIFIER"], "ed25519")
        self.assertEqual(
            tuple(json.loads(environment["CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON"])),
            ("key-a", "key-b"),
        )
        self.assertNotIn("CPK_GATEWAY_PROBE_", json.dumps(authored_descriptor, sort_keys=True))
        self.assertNotIn("secret://", json.dumps(authored_descriptor, sort_keys=True))
        self.assertEqual(
            materialize_delegation_verifiers(graph_ab, (projection,)),
            graph_ab,
        )
        self.assertEqual(
            DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(graph_ab)),
            graph_ab,
        )

    def test_projection_fails_closed_for_missing_or_ambiguous_binding(self) -> None:
        authored = self.authored_graph()
        missing = self.projection("projection-a", self.public_key("key-a", _PUBLIC_KEY_A))
        missing = DelegationVerifierProjection(
            delegate_node_id="missing-gateway",
            purpose=missing.purpose,
            issuer=missing.issuer,
            audience="gateway:workspace-a:missing-gateway",
            projection_id=missing.projection_id,
            public_keys=missing.public_keys,
        )

        with self.assertRaises(ValueError):
            materialize_delegation_verifiers(authored, (missing,))
        with self.assertRaises(ValueError):
            DelegationVerifierProjection(
                delegate_node_id="gateway",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server:workspace-a",
                audience="gateway:workspace-a:gateway",
                projection_id="projection-duplicate",
                public_keys=(
                    self.public_key("key-a", _PUBLIC_KEY_A),
                    self.public_key("key-a", _PUBLIC_KEY_A),
                ),
            )
        with self.assertRaises(DelegationAuthorityError):
            DelegationVerifierProjection(
                delegate_node_id="gateway",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server:workspace-a",
                audience="gateway:workspace-a:gateway",
                projection_id="projection-untyped",
                public_keys=(object(),),  # type: ignore[arg-type]
            )

    def test_graph_codec_rejects_projection_without_authored_binding(self) -> None:
        graph = self.authored_graph()
        projection = self.projection(
            "projection-a",
            self.public_key("key-a", _PUBLIC_KEY_A),
        )
        descriptor = DEFAULT_GRAPH_CODEC.encode(
            materialize_delegation_verifiers(graph, (projection,))
        )
        descriptor.pop("delegation_authorities")

        with self.assertRaises(ValueError):
            DEFAULT_GRAPH_CODEC.decode(descriptor)

    def test_projection_change_is_a_closed_node_reconciliation_diff(self) -> None:
        authored = self.authored_graph()
        graph_a = materialize_delegation_verifiers(
            authored,
            (self.projection("projection-a", self.public_key("key-a", _PUBLIC_KEY_A)),),
        )
        graph_ab = materialize_delegation_verifiers(
            authored,
            (
                self.projection(
                    "projection-ab",
                    self.public_key("key-a", _PUBLIC_KEY_A),
                    self.public_key("key-b", _PUBLIC_KEY_B),
                ),
            ),
        )

        difference = diff_graphs(validate_graph(graph_a), validate_graph(graph_ab))

        self.assertEqual(len(difference.changes), 1)
        self.assertEqual(
            difference.changes[0].subject.field,
            StructuralField.DELEGATION_VERIFIER_PROJECTION,
        )
        rendered = json.dumps(difference.descriptor(), sort_keys=True)
        self.assertIn("key-a", rendered)
        self.assertIn("key-b", rendered)
        self.assertNotIn("BEGIN PUBLIC KEY", rendered)
        plan = compile_activity_plan(difference)
        self.assertEqual(
            tuple(type(activity.operation) for activity in plan.activities),
            (ReconcileNode, WaitForHealthy),
        )

    def test_graph_without_binding_is_unchanged(self) -> None:
        graph = DeploymentGraph("ordinary")

        self.assertIs(materialize_delegation_verifiers(graph, ()), graph)
        self.assertNotIn("delegation_authorities", DEFAULT_GRAPH_CODEC.encode(graph))

    @staticmethod
    def binding() -> DelegationAuthorityBinding:
        return DelegationAuthorityBinding(
            delegate_node_id="gateway",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server:workspace-a",
        )

    def authored_graph(self) -> DeploymentGraph:
        return DeploymentGraph(
            "gateway-island",
            nodes={
                "gateway": Node(
                    node_id="gateway",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("gateway"),
                    kind="container-server",
                    runtime_id="docker",
                    sockets=BlockSockets(),
                )
            },
            runtimes={
                "docker": RuntimeRecord(
                    runtime_id="docker",
                    kind=RuntimeKind.DOCKER,
                    children=("gateway",),
                )
            },
            delegation_authorities=(self.binding(),),
        )

    def projection(
        self,
        projection_id: str,
        *keys: DelegationPublicKey,
    ) -> DelegationVerifierProjection:
        return DelegationVerifierProjection(
            delegate_node_id="gateway",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server:workspace-a",
            audience="gateway:workspace-a:gateway",
            projection_id=projection_id,
            public_keys=keys,
        )

    @staticmethod
    def public_key(key_id: str, pem: str) -> DelegationPublicKey:
        return DelegationPublicKey(
            key_id=key_id,
            algorithm=DelegationKeyAlgorithm.ED25519,
            public_key_pem=pem,
        )


if __name__ == "__main__":
    unittest.main()
