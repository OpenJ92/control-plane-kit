from __future__ import annotations

import concurrent.futures
import os
import threading
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.delegation_authority import DelegationAuthorityBinding
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
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
from control_plane_kit_operations.graph_authoring import (
    GraphAuthoringError,
    GraphAuthoringService,
    SetDesiredGraphCommand,
)
from control_plane_kit_operations.planning import (
    DesiredGraphCommandService,
    SetDesiredGraph,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    RealizedGraphProjectionKind,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
QUFB
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
QkJC
-----END PUBLIC KEY-----
"""


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> str:
        return next(self._values)


class ObservedConnection:
    def __init__(
        self,
        connection,
        *,
        projection_read: threading.Event | None = None,
        release_projection: threading.Event | None = None,
        before_scope_lock: threading.Event | None = None,
        after_scope_lock: threading.Event | None = None,
    ) -> None:
        self._connection = connection
        self._projection_read = projection_read
        self._release_projection = release_projection
        self._before_scope_lock = before_scope_lock
        self._after_scope_lock = after_scope_lock

    def execute(self, query, params=()):
        normalized = " ".join(str(query).upper().split())
        scope_lock = "PG_ADVISORY_XACT_LOCK" in normalized
        projection_read = (
            "FROM CPK_DELEGATION_SIGNING_KEYS" in normalized
            and "FOR UPDATE" in normalized
            and "STATUS IN ('ACTIVE', 'VERIFY-ONLY')" in normalized
        )
        if scope_lock and self._before_scope_lock is not None:
            self._before_scope_lock.set()
        result = self._connection.execute(query, params)
        if scope_lock and self._after_scope_lock is not None:
            self._after_scope_lock.set()
        if projection_read and self._projection_read is not None:
            self._projection_read.set()
            if not self._release_projection.wait(timeout=5):
                raise RuntimeError("projection test barrier timed out")
        return result

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


class InitialDelegationProjectionTests(unittest.TestCase):
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
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def service(self) -> GraphAuthoringService:
        return GraphAuthoringService(
            self.unit_of_work,
            graph_id_factory=lambda: "graph-desired",
            clock=lambda: "2026-08-04T12:00:00Z",
        )

    def test_settled_active_key_compiles_initial_verifier_projection(self) -> None:
        self.register_key("key-a", PUBLIC_KEY_A, active=True)

        result = self.service().set_desired_graph(
            self.command(self.bound_graph())
        )

        self.assertEqual(
            result.realized_projection.projection_kind,
            RealizedGraphProjectionKind.DELEGATION_VERIFIER,
        )
        realized = DEFAULT_GRAPH_CODEC.decode(
            result.realized_projection.graph_descriptor
        )
        projection = realized.node("gateway").delegation_verifier_projection
        self.assertIsNotNone(projection)
        self.assertEqual(projection.delegate_node_id, "gateway")
        self.assertEqual(projection.purpose, DelegationKeyPurpose.GATEWAY_PROBE)
        self.assertEqual(projection.issuer, "issuer-a")
        self.assertEqual(projection.audience, "gateway:workspace-a:gateway")
        self.assertEqual(tuple(key.key_id for key in projection.public_keys), ("key-a",))
        authored_json = str(result.graph_version.graph_descriptor)
        self.assertNotIn("key-a", authored_json)
        self.assertNotIn("BEGIN PUBLIC KEY", authored_json)
        self.assertNotIn("secret://", authored_json)

    def test_graph_without_binding_keeps_identity_projection(self) -> None:
        result = self.service().set_desired_graph(
            self.command(self.unbound_graph())
        )

        self.assertEqual(
            result.realized_projection.projection_kind,
            RealizedGraphProjectionKind.IDENTITY,
        )
        self.assertEqual(
            result.realized_projection.graph_descriptor,
            result.graph_version.graph_descriptor,
        )

    def test_missing_or_mismatched_key_truth_rolls_back_all_graph_writes(self) -> None:
        for issuer in (None, "other-issuer"):
            with self.subTest(issuer=issuer):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                with self.unit_of_work() as unit_of_work:
                    unit_of_work.stores.workspaces.create(
                        WorkspaceRecord("workspace-a", "Workspace A")
                    )
                    unit_of_work.commit()
                if issuer is not None:
                    self.register_key("key-a", PUBLIC_KEY_A, active=True, issuer=issuer)

                with self.assertRaisesRegex(
                    GraphAuthoringError,
                    "exactly one settled active delegation key",
                ):
                    self.service().set_desired_graph(self.command(self.bound_graph()))

                self.assertEqual(self.row_count("cpk_graph_versions"), 0)
                self.assertEqual(self.row_count("cpk_realized_graph_projections"), 0)
                workspace = self.connection.execute(
                    "SELECT desired_graph_id, desired_realized_projection_id "
                    "FROM cpk_workspaces WHERE workspace_id = 'workspace-a'"
                ).fetchone()
                self.assertEqual(workspace, (None, None))

    def test_verify_only_overlap_cannot_bypass_rotation_program(self) -> None:
        self.register_key("key-a", PUBLIC_KEY_A, active=True)
        self.register_key("key-b", PUBLIC_KEY_B, active=False)

        with self.assertRaisesRegex(
            GraphAuthoringError,
            "exactly one settled active delegation key",
        ):
            self.service().set_desired_graph(self.command(self.bound_graph()))

        self.assertEqual(self.row_count("cpk_graph_versions"), 0)
        self.assertEqual(self.row_count("cpk_realized_graph_projections"), 0)

    def test_idempotent_replay_returns_original_projection_after_key_change(self) -> None:
        self.register_key("key-a", PUBLIC_KEY_A, active=True)
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T11:30:00Z",
            id_factory=Sequence("session-a", "action-start"),
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Initial gateway deployment",
                IdempotencyKey("start-session"),
            )
        )
        service = DesiredGraphCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T12:00:00Z",
            id_factory=Sequence("graph-desired", "action-desired"),
        )
        command = SetDesiredGraph(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="operator-a",
            graph=self.bound_graph(),
            expected_desired_graph_id=None,
            idempotency_key=IdempotencyKey("set-bound-graph"),
        )

        original = service.execute(command)
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        replay = service.execute(command)

        self.assertTrue(replay.replayed)
        self.assertEqual(
            replay.desired_realized_projection_id,
            original.desired_realized_projection_id,
        )
        self.assertEqual(self.row_count("cpk_graph_versions"), 1)
        self.assertEqual(self.row_count("cpk_realized_graph_projections"), 1)

    def test_key_activation_waits_for_projection_compilation_transaction(self) -> None:
        self.register_key("key-a", PUBLIC_KEY_A, active=True)
        projection_read = threading.Event()
        release_projection = threading.Event()
        activation_waiting = threading.Event()
        activation_locked = threading.Event()

        def projection_uow() -> PostgresUnitOfWork:
            return PostgresUnitOfWork(
                lambda: ObservedConnection(
                    psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]),
                    projection_read=projection_read,
                    release_projection=release_projection,
                )
            )

        def activation_uow() -> PostgresUnitOfWork:
            return PostgresUnitOfWork(
                lambda: ObservedConnection(
                    psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]),
                    before_scope_lock=activation_waiting,
                    after_scope_lock=activation_locked,
                )
            )

        projection_service = GraphAuthoringService(
            projection_uow,
            graph_id_factory=lambda: "graph-desired",
            clock=lambda: "2026-08-04T12:00:00Z",
        )

        def activate_b():
            public_key = DelegationPublicKey(
                "key-b",
                DelegationKeyAlgorithm.ED25519,
                PUBLIC_KEY_B,
            )
            private_reference = SecretReference(
                "secret://workspace-secrets/delegation/key-b"
            )
            with activation_uow() as unit_of_work:
                unit_of_work.stores.delegation_signing_keys.register(
                    RegisteredDelegationSigningKey(
                        registration_id=delegation_signing_key_registration_id_for(
                            workspace_id="workspace-a",
                            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                            issuer="issuer-a",
                            public_key=public_key,
                            private_key_reference=private_reference,
                        ),
                        workspace_id="workspace-a",
                        purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                        issuer="issuer-a",
                        public_key=public_key,
                        private_key_reference=private_reference,
                        admitted_by="operator-a",
                        admitted_at="2026-08-04T12:01:00Z",
                    )
                )
                result = unit_of_work.stores.delegation_signing_keys.activate(
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "issuer-a",
                    "key-b",
                    activated_by="operator-a",
                    activated_at="2026-08-04T12:01:00Z",
                )
                unit_of_work.commit()
                return result

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            projection_future = executor.submit(
                projection_service.set_desired_graph,
                self.command(self.bound_graph()),
            )
            self.assertTrue(projection_read.wait(timeout=5))
            activation_future = executor.submit(activate_b)
            self.assertTrue(activation_waiting.wait(timeout=5))
            self.assertFalse(activation_locked.is_set())
            release_projection.set()
            projection_result = projection_future.result(timeout=5)
            activated = activation_future.result(timeout=5)

        realized = DEFAULT_GRAPH_CODEC.decode(
            projection_result.realized_projection.graph_descriptor
        )
        projection = realized.node("gateway").delegation_verifier_projection
        self.assertEqual(tuple(key.key_id for key in projection.public_keys), ("key-a",))
        self.assertEqual(activated.key_id, "key-b")
        self.assertEqual(
            activated.status,
            RegisteredDelegationSigningKeyStatus.ACTIVE,
        )

    def command(self, graph: DeploymentGraph) -> SetDesiredGraphCommand:
        return SetDesiredGraphCommand(
            workspace_id="workspace-a",
            actor_id="operator-a",
            graph=graph,
            expected_desired_graph_id=None,
        )

    def bound_graph(self) -> DeploymentGraph:
        graph = self.unbound_graph()
        return DeploymentGraph(
            graph.name,
            nodes=graph.nodes,
            runtimes=graph.runtimes,
            delegation_authorities=(
                DelegationAuthorityBinding(
                    "gateway",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "issuer-a",
                ),
            ),
        )

    @staticmethod
    def unbound_graph() -> DeploymentGraph:
        node = Node(
            node_id="gateway",
            block_family=BlockFamily.PROXY,
            block_spec=BlockSpec("gateway"),
            kind="container-server",
            runtime_id="docker",
            sockets=BlockSockets(),
        )
        return DeploymentGraph(
            "gateway-island",
            nodes={node.node_id: node},
            runtimes={
                "docker": RuntimeRecord(
                    "docker",
                    RuntimeKind.DOCKER,
                    children=(node.node_id,),
                )
            },
        )

    def register_key(
        self,
        key_id: str,
        public_key_pem: str,
        *,
        active: bool,
        issuer: str = "issuer-a",
    ) -> None:
        public_key = DelegationPublicKey(
            key_id,
            DelegationKeyAlgorithm.ED25519,
            public_key_pem,
        )
        private_reference = SecretReference(
            f"secret://workspace-secrets/delegation/{key_id}"
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.delegation_signing_keys.register(
                RegisteredDelegationSigningKey(
                    registration_id=delegation_signing_key_registration_id_for(
                        workspace_id="workspace-a",
                        purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                        issuer=issuer,
                        public_key=public_key,
                        private_key_reference=private_reference,
                    ),
                    workspace_id="workspace-a",
                    purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                    issuer=issuer,
                    public_key=public_key,
                    private_key_reference=private_reference,
                    admitted_by="operator-a",
                    admitted_at="2026-08-04T11:00:00Z",
                    status=RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
                )
            )
            if active:
                unit_of_work.stores.delegation_signing_keys.activate(
                    "workspace-a",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    issuer,
                    key_id,
                    activated_by="operator-a",
                    activated_at="2026-08-04T11:01:00Z",
                )
            unit_of_work.commit()

    def row_count(self, table: str) -> int:
        if table not in {
            "cpk_graph_versions",
            "cpk_realized_graph_projections",
        }:
            raise ValueError(f"unexpected table {table!r}")
        return self.connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
