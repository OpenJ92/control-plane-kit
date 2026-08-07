from __future__ import annotations

import os
import unittest

import psycopg

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
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord
from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.types import BlockFamily, RuntimeKind
from control_plane_kit_operations.postgres import (
    PostgresUnitOfWork,
    RealizedGraphProjectionConflict,
    install_schema,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)


_PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
QUFB
-----END PUBLIC KEY-----
"""
_PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
QkJC
-----END PUBLIC KEY-----
"""


class RealizedGraphProjectionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def test_authored_graph_remains_stable_across_realized_projections(self) -> None:
        authored = self.authored_graph()
        graph_a = self.realized(authored, "projection-a", "key-a", _PUBLIC_KEY_A)
        graph_b = self.realized(authored, "projection-b", "key-b", _PUBLIC_KEY_B)

        with self.unit_of_work() as unit_of_work:
            self.seed_authored(unit_of_work, authored)
            record_a = self.record("realized-a", "rotation-a", graph_a)
            record_b = self.record("realized-b", "rotation-b", graph_b)
            unit_of_work.stores.realized_graphs.save(record_a)
            unit_of_work.stores.realized_graphs.save(record_b)
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            restored_a = unit_of_work.stores.realized_graphs.get("realized-a")
            restored_b = unit_of_work.stores.realized_graphs.get("realized-b")
            authored_record = unit_of_work.stores.graphs.get("graph-authored")

        self.assertEqual(restored_a.source_authored_graph_id, "graph-authored")
        self.assertEqual(restored_b.source_authored_graph_id, "graph-authored")
        self.assertNotEqual(restored_a.projection_digest, restored_b.projection_digest)
        self.assertEqual(authored_record.graph_descriptor, authored.descriptor())
        self.assertNotIn(
            "delegation_verifier_projection",
            authored_record.graph_descriptor["nodes"]["gateway"],
        )
        self.assertNotIn("PRIVATE KEY", str(restored_a.graph_descriptor))
        self.assertNotIn("PRIVATE KEY", str(restored_b.graph_descriptor))

    def test_save_is_idempotent_and_semantic_mismatch_fails_closed(self) -> None:
        authored = self.authored_graph()
        graph_a = self.realized(authored, "projection-a", "key-a", _PUBLIC_KEY_A)
        graph_b = self.realized(authored, "projection-b", "key-b", _PUBLIC_KEY_B)

        with self.unit_of_work() as unit_of_work:
            self.seed_authored(unit_of_work, authored)
            first = unit_of_work.stores.realized_graphs.save(
                self.record("realized-a", "rotation", graph_a)
            )
            repeated = unit_of_work.stores.realized_graphs.save(
                self.record("another-id", "rotation", graph_a)
            )
            self.assertEqual(repeated, first)
            with self.assertRaises(RealizedGraphProjectionConflict):
                unit_of_work.stores.realized_graphs.save(
                    self.record("realized-b", "rotation", graph_b)
                )
            unit_of_work.commit()

    def test_projection_store_rejects_noncanonical_timestamp_before_write(self) -> None:
        authored = self.authored_graph()
        graph = self.realized(authored, "projection-a", "key-a", _PUBLIC_KEY_A)

        with self.unit_of_work() as unit_of_work:
            self.seed_authored(unit_of_work, authored)
            with self.assertRaisesRegex(
                ValueError,
                "^postgres timestamp must be canonical UTC text$",
            ):
                unit_of_work.stores.realized_graphs.save(
                    self.record(
                        "realized-invalid-time",
                        "rotation-invalid-time",
                        graph,
                        created_at="not-a-timestamp",
                    )
                )

        self.assertEqual(self._row_count("cpk_realized_graph_projections"), 0)

    def test_cross_workspace_source_fails_closed(self) -> None:
        authored = self.authored_graph()
        graph_a = self.realized(authored, "projection-a", "key-a", _PUBLIC_KEY_A)

        with self.unit_of_work() as unit_of_work:
            self.seed_authored(unit_of_work, authored)
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(workspace_id="workspace-b", name="Other")
            )
            with self.assertRaises(RealizedGraphProjectionConflict):
                unit_of_work.stores.realized_graphs.save(
                    RealizedGraphProjectionRecord.from_graph(
                        projection_id="cross-workspace",
                        workspace_id="workspace-b",
                        source_authored_graph_id="graph-authored",
                        projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
                        projection_key="rotation-a",
                        graph=graph_a,
                        created_by="operator-a",
                        created_at="2026-08-01T20:00:00Z",
                    )
                )

    def test_identity_projection_is_deterministic_and_durable(self) -> None:
        authored = self.authored_graph()
        first = RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=self.authored_record(authored)
        )
        second = RealizedGraphProjectionRecord.identity_for_authored(
            authored_record=self.authored_record(authored)
        )
        self.assertEqual(first, second)

        with self.unit_of_work() as unit_of_work:
            self.seed_authored(unit_of_work, authored)
            stored = unit_of_work.stores.realized_graphs.save(first)
            unit_of_work.commit()
        with self.unit_of_work() as unit_of_work:
            restored = unit_of_work.stores.realized_graphs.get(stored.projection_id)

        self.assertEqual(restored, first)
        self.assertEqual(restored.projection_kind, RealizedGraphProjectionKind.IDENTITY)

    def test_authored_and_realized_writes_roll_back_together(self) -> None:
        authored = self.authored_graph()
        graph_a = self.realized(authored, "projection-a", "key-a", _PUBLIC_KEY_A)

        with self.unit_of_work() as unit_of_work:
            self.seed_authored(unit_of_work, authored)
            unit_of_work.stores.realized_graphs.save(
                self.record("realized-a", "rotation-a", graph_a)
            )

        self.assertEqual(self._row_count("cpk_workspaces"), 0)
        self.assertEqual(self._row_count("cpk_graph_versions"), 0)
        self.assertEqual(self._row_count("cpk_realized_graph_projections"), 0)

    def seed_authored(
        self,
        unit_of_work: PostgresUnitOfWork,
        graph: DeploymentGraph,
    ) -> None:
        unit_of_work.stores.workspaces.create(
            WorkspaceRecord(workspace_id="workspace-a", name="Demo")
        )
        unit_of_work.stores.graphs.save(self.authored_record(graph))

    @staticmethod
    def authored_record(graph: DeploymentGraph) -> GraphVersionRecord:
        return GraphVersionRecord.from_graph(
            graph_id="graph-authored",
            workspace_id="workspace-a",
            version=1,
            graph=graph,
            created_by="operator-a",
            created_at="2026-08-01T20:00:00Z",
        )

    @staticmethod
    def record(
        projection_id: str,
        projection_key: str,
        graph: DeploymentGraph,
        *,
        created_at: str = "2026-08-01T20:00:00.000001Z",
    ) -> RealizedGraphProjectionRecord:
        return RealizedGraphProjectionRecord.from_graph(
            projection_id=projection_id,
            workspace_id="workspace-a",
            source_authored_graph_id="graph-authored",
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key=projection_key,
            graph=graph,
            created_by="operator-a",
            created_at=created_at,
        )

    @staticmethod
    def authored_graph() -> DeploymentGraph:
        binding = DelegationAuthorityBinding(
            delegate_node_id="gateway",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server:workspace-a",
        )
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
            delegation_authorities=(binding,),
        )

    @staticmethod
    def realized(
        authored: DeploymentGraph,
        projection_id: str,
        key_id: str,
        pem: str,
    ) -> DeploymentGraph:
        projection = DelegationVerifierProjection(
            delegate_node_id="gateway",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server:workspace-a",
            audience="gateway:workspace-a:gateway",
            projection_id=projection_id,
            public_keys=(
                DelegationPublicKey(
                    key_id=key_id,
                    algorithm=DelegationKeyAlgorithm.ED25519,
                    public_key_pem=pem,
                ),
            ),
        )
        return materialize_delegation_verifiers(authored, (projection,))

    def _row_count(self, table: str) -> int:
        if table not in {
            "cpk_workspaces",
            "cpk_graph_versions",
            "cpk_realized_graph_projections",
        }:
            raise ValueError(f"unexpected table {table!r}")
        return self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
