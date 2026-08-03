from __future__ import annotations

from dataclasses import replace
import json
import os
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.delegation_authority import (
    DelegationAuthorityBinding,
    DelegationVerifierProjection,
    materialize_delegation_verifiers,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    DeploymentGraph,
    Node,
    RuntimeRecord,
)
from control_plane_kit_core.types import BlockFamily, RuntimeKind
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.gateway_key_rotation_overlap import (
    GatewayKeyRotationOverlapProjectionAuthorizationDenied,
    GatewayKeyRotationOverlapProjectionConflict,
    GatewayKeyRotationOverlapProjectionService,
    PublishGatewayKeyRotationOverlapProjection,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import IdempotencyKey


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
QUFB
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
QkJC
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_OTHER = """-----BEGIN PUBLIC KEY-----
T1RIRVI=
-----END PUBLIC KEY-----
"""


class GatewayKeyRotationOverlapProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self, action_id: str = "action-overlap"):
        return GatewayKeyRotationOverlapProjectionService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T02:00:00Z",
            action_id_factory=lambda: action_id,
        )

    def command(self, **changes) -> PublishGatewayKeyRotationOverlapProjection:
        values = {
            "rotation_id": "rotation-a",
            "session_id": "session-a",
            "actor_id": "operator-a",
            "expected_rotation_version": 5,
            "expected_authored_graph_id": "graph-a",
            "expected_current_realized_projection_id": "projection-a",
            "expected_desired_realized_projection_id": "projection-a",
            "expected_desired_graph_revision": 1,
            "actor_scopes": (PolicyScope.DELEGATION_KEY_ROTATE,),
            "idempotency_key": IdempotencyKey("publish-overlap"),
        }
        values.update(changes)
        return PublishGatewayKeyRotationOverlapProjection(**values)

    def test_publishes_exact_a_plus_b_without_changing_authored_graph(self) -> None:
        result = self.service().execute(self.command())

        self.assertFalse(result.publication.replayed)
        self.assertEqual(
            result.publication.action.action_type,
            OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION,
        )
        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            authored = unit_of_work.stores.graphs.get("graph-a")
            projection = unit_of_work.stores.realized_graphs.get(
                result.publication.desired_realized_projection_id
            )
            actions = unit_of_work.stores.activity_history.actions_for_session(
                "session-a"
            )

        realized = DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor)
        target = realized.node("gateway-a").delegation_verifier_projection
        other = realized.node("gateway-other").delegation_verifier_projection
        self.assertEqual(tuple(key.key_id for key in target.public_keys), ("key-a", "key-b"))
        self.assertEqual(tuple(key.key_id for key in other.public_keys), ("key-other",))
        self.assertEqual(workspace.current_graph_id, "graph-a")
        self.assertEqual(workspace.desired_graph_id, "graph-a")
        self.assertEqual(workspace.current_realized_projection_id, "projection-a")
        self.assertEqual(
            workspace.desired_realized_projection_id,
            result.publication.desired_realized_projection_id,
        )
        self.assertEqual(workspace.desired_graph_revision, 2)
        self.assertEqual(authored.graph_descriptor, self.authored_graph().descriptor())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].ordinal, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_graph_versions WHERE workspace_id='workspace-a'"
            ).fetchone()[0],
            1,
        )
        self.assertNotIn("PRIVATE KEY", repr(projection.graph_descriptor))

    def test_replay_is_deterministic_and_changed_intent_conflicts(self) -> None:
        first = self.service().execute(self.command())
        replay = self.service("unused-action").execute(self.command())

        self.assertTrue(replay.publication.replayed)
        self.assertEqual(replay.publication.action, first.publication.action)
        self.assertEqual(
            replay.publication.desired_realized_projection_id,
            first.publication.desired_realized_projection_id,
        )
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(
                self.command(expected_rotation_version=6)
            )

    def test_stale_workspace_or_key_truth_fails_before_pointer_mutation(self) -> None:
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(
                self.command(expected_rotation_version=6)
            )
        self.connection.execute(
            "UPDATE cpk_delegation_signing_keys SET status='verify-only' "
            "WHERE key_id='key-a'"
        )
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

        workspace = self.connection.execute(
            "SELECT desired_realized_projection_id, desired_graph_revision "
            "FROM cpk_workspaces WHERE workspace_id='workspace-a'"
        ).fetchone()
        self.assertEqual(workspace, ("projection-a", 1))

    def test_extra_verification_key_fails_closed(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.delegation_signing_keys.register(
                self.signing_key("key-extra", PUBLIC_KEY_OTHER)
            )
            unit_of_work.commit()

        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

    def test_missing_key_or_authored_binding_fails_closed(self) -> None:
        self.connection.execute(
            "DELETE FROM cpk_delegation_signing_keys WHERE key_id='key-b'"
        )
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.delegation_signing_keys.register(
                self.signing_key("key-b", PUBLIC_KEY_B)
            )
            unit_of_work.commit()
        self.connection.execute(
            "UPDATE cpk_gateway_key_rotations SET gateway_node_id='gateway-missing' "
            "WHERE rotation_id='rotation-a'"
        )
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

    def test_wrong_issuer_and_malformed_authored_binding_fail_closed(self) -> None:
        self.connection.execute(
            "UPDATE cpk_gateway_key_rotations SET issuer='other-issuer' "
            "WHERE rotation_id='rotation-a'"
        )
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

        self.connection.execute(
            "UPDATE cpk_gateway_key_rotations SET issuer='cpk-server' "
            "WHERE rotation_id='rotation-a'"
        )
        malformed = replace(self.authored_graph(), delegation_authorities=())
        malformed_record = GraphVersionRecord.from_graph(
            graph_id="graph-a",
            workspace_id="workspace-a",
            version=1,
            graph=malformed,
            created_by="operator-a",
            created_at="2026-08-02T01:00:00Z",
        )
        self.connection.execute(
            "UPDATE cpk_graph_versions SET graph_descriptor=%s::jsonb "
            "WHERE graph_id='graph-a'",
            (json.dumps(malformed_record.graph_descriptor),),
        )
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

    def test_stale_desired_revision_and_missing_scope_fail_closed(self) -> None:
        stale_commands = (
            self.command(expected_rotation_version=6),
            self.command(expected_authored_graph_id="graph-stale"),
            self.command(expected_current_realized_projection_id="projection-stale"),
            self.command(expected_desired_realized_projection_id="projection-stale"),
            self.command(expected_desired_graph_revision=2),
        )
        for command in stale_commands:
            with self.subTest(command=command):
                with self.assertRaises(
                    GatewayKeyRotationOverlapProjectionConflict
                ):
                    self.service().execute(command)

        self.connection.execute(
            "UPDATE cpk_workspaces SET desired_graph_revision=2 "
            "WHERE workspace_id='workspace-a'"
        )
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

        with self.assertRaises(
            GatewayKeyRotationOverlapProjectionAuthorizationDenied
        ):
            self.service().execute(self.command(actor_scopes=()))

    def test_closed_session_fails_before_projection_mutation(self) -> None:
        self.connection.execute(
            "UPDATE cpk_operation_sessions SET status='closed', "
            "closed_at='2026-08-02T01:30:00Z' WHERE session_id='session-a'"
        )

        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

        workspace = self.connection.execute(
            "SELECT desired_realized_projection_id, desired_graph_revision "
            "FROM cpk_workspaces WHERE workspace_id='workspace-a'"
        ).fetchone()
        self.assertEqual(workspace, ("projection-a", 1))

    def test_changed_material_under_deterministic_projection_identity_conflicts(self) -> None:
        authored = self.authored_graph()
        incompatible = materialize_delegation_verifiers(
            authored,
            (
                self.projection(
                    "gateway-a",
                    "incompatible-target",
                    self.public_key("key-a", PUBLIC_KEY_A),
                ),
                self.projection(
                    "gateway-other",
                    "projection-other",
                    self.public_key("key-other", PUBLIC_KEY_OTHER),
                ),
            ),
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.realized_graphs.save(
                RealizedGraphProjectionRecord.from_graph(
                    projection_id="gateway-rotation-rotation-a-overlap",
                    workspace_id="workspace-a",
                    source_authored_graph_id="graph-a",
                    projection_kind=(
                        RealizedGraphProjectionKind.DELEGATION_VERIFIER
                    ),
                    projection_key="gateway-rotation:rotation-a:overlap",
                    graph=incompatible,
                    created_by="operator-a",
                    created_at="2026-08-02T01:30:00Z",
                )
            )
            unit_of_work.commit()

        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(self.command())

    def test_late_action_failure_rolls_back_projection_and_pointer(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_session(
                OperationSessionRecord(
                    "session-other",
                    "workspace-a",
                    "operator-a",
                    "Existing",
                    OperationSessionStatus.OPEN,
                    "2026-08-02T01:59:00Z",
                )
            )
            unit_of_work.stores.activity_history.add_action(
                OperationActionRecord(
                    "action-collision",
                    "session-other",
                    1,
                    OperatorCommandKind.SET_DESIRED_GRAPH,
                    "operator-a",
                    {},
                    "2026-08-02T01:59:00Z",
                )
            )
            unit_of_work.commit()

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service("action-collision").execute(self.command())

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            with self.assertRaises(KeyError):
                unit_of_work.stores.realized_graphs.get(
                    "gateway-rotation-rotation-a-overlap"
                )
        self.assertEqual(workspace.desired_realized_projection_id, "projection-a")
        self.assertEqual(workspace.desired_graph_revision, 1)

    def seed(self) -> None:
        authored = self.authored_graph()
        realized_a = materialize_delegation_verifiers(
            authored,
            (
                self.projection("gateway-a", "projection-key-a", self.public_key("key-a", PUBLIC_KEY_A)),
                self.projection("gateway-other", "projection-other", self.public_key("key-other", PUBLIC_KEY_OTHER)),
            ),
        )
        authored_record = GraphVersionRecord.from_graph(
            graph_id="graph-a",
            workspace_id="workspace-a",
            version=1,
            graph=authored,
            created_by="operator-a",
            created_at="2026-08-02T01:00:00Z",
        )
        projection_a = RealizedGraphProjectionRecord.from_graph(
            projection_id="projection-a",
            workspace_id="workspace-a",
            source_authored_graph_id="graph-a",
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="seed-a",
            graph=realized_a,
            created_by="operator-a",
            created_at="2026-08-02T01:00:00Z",
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            stores.graphs.save(authored_record)
            stores.realized_graphs.save(projection_a)
            stores.workspaces.set_current_graph("workspace-a", "graph-a", "projection-a")
            stores.workspaces.set_desired_graph("workspace-a", "graph-a", "projection-a")
            stores.activity_history.add_session(
                OperationSessionRecord(
                    "session-a",
                    "workspace-a",
                    "operator-a",
                    "Rotate gateway key",
                    OperationSessionStatus.OPEN,
                    "2026-08-02T01:00:00Z",
                )
            )
            stores.delegation_signing_keys.register(
                self.signing_key("key-a", PUBLIC_KEY_A)
            )
            stores.delegation_signing_keys.activate(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
                "key-a",
                activated_by="operator-a",
                activated_at="2026-08-02T01:00:01Z",
            )
            stores.delegation_signing_keys.register(
                self.signing_key("key-b", PUBLIC_KEY_B)
            )
            stores.gateway_key_rotations.add(self.rotation())
            unit_of_work.commit()

    @staticmethod
    def authored_graph() -> DeploymentGraph:
        bindings = tuple(
            DelegationAuthorityBinding(
                node_id,
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
            )
            for node_id in ("gateway-a", "gateway-other")
        )
        nodes = {
            node_id: Node(
                node_id=node_id,
                block_family=BlockFamily.PROXY,
                block_spec=BlockSpec(node_id),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(),
            )
            for node_id in ("gateway-a", "gateway-other")
        }
        return DeploymentGraph(
            "gateway-island",
            nodes=nodes,
            runtimes={
                "docker": RuntimeRecord(
                    "docker",
                    RuntimeKind.DOCKER,
                    children=tuple(nodes),
                )
            },
            delegation_authorities=bindings,
        )

    @staticmethod
    def public_key(key_id: str, pem: str) -> DelegationPublicKey:
        return DelegationPublicKey(key_id, DelegationKeyAlgorithm.ED25519, pem)

    @classmethod
    def projection(
        cls,
        node_id: str,
        projection_id: str,
        key: DelegationPublicKey,
    ) -> DelegationVerifierProjection:
        return DelegationVerifierProjection(
            node_id,
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
            f"gateway:workspace-a:{node_id}",
            projection_id,
            (key,),
        )

    @classmethod
    def signing_key(
        cls,
        key_id: str,
        pem: str,
    ) -> RegisteredDelegationSigningKey:
        public_key = cls.public_key(key_id, pem)
        private_reference = SecretReference(
            f"secret://workspace-secrets/keys/{key_id}"
        )
        return RegisteredDelegationSigningKey(
            registration_id=delegation_signing_key_registration_id_for(
                workspace_id="workspace-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                public_key=public_key,
                private_key_reference=private_reference,
            ),
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            public_key=public_key,
            private_key_reference=private_reference,
            admitted_by="operator-a",
            admitted_at="2026-08-02T01:00:00Z",
            status=RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
        )

    @staticmethod
    def rotation() -> GatewayKeyRotation:
        return GatewayKeyRotation(
            rotation_id="rotation-a",
            workspace_id="workspace-a",
            gateway_node_id="gateway-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            old_key_id="key-a",
            new_secret_reference=SecretReference(
                "secret://workspace-secrets/keys/key-b"
            ),
            key_generation_correlation="generation-a",
            maximum_grant_lifetime_seconds=60,
            clock_skew_seconds=5,
            correlation_id="correlation-a",
            requested_by="operator-a",
            requested_at="2026-08-02T01:00:00Z",
            intent_fingerprint="a" * 64,
            status=GatewayKeyRotationStatus.KEY_GENERATED,
            version=5,
            approval_request_id="approval-request-a",
            approval_decision_id="approval-decision-a",
            generation_provider_registration_id="provider-a",
            generation_action_digest="b" * 64,
            new_key_id="key-b",
            new_secret_version_id="version-b",
            new_secret_version_number=1,
            updated_by="operator-a",
            updated_at="2026-08-02T01:00:02Z",
        )


if __name__ == "__main__":
    unittest.main()
