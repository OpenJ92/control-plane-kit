from __future__ import annotations

import concurrent.futures
from dataclasses import replace
import os
import threading
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
from control_plane_kit_operations.desired_realized_projections import (
    DesiredRealizedProjectionCommandService,
    DesiredRealizedProjectionPublicationConflict,
    PublishDesiredRealizedProjection,
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
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
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
        before_workspace_lock: threading.Event | None = None,
        after_workspace_lock: threading.Event | None = None,
        release_workspace_lock: threading.Event | None = None,
    ) -> None:
        self._connection = connection
        self._projection_read = projection_read
        self._release_projection = release_projection
        self._before_scope_lock = before_scope_lock
        self._after_scope_lock = after_scope_lock
        self._before_workspace_lock = before_workspace_lock
        self._after_workspace_lock = after_workspace_lock
        self._release_workspace_lock = release_workspace_lock

    def execute(self, query, params=()):
        normalized = " ".join(str(query).upper().split())
        scope_lock = "PG_ADVISORY_XACT_LOCK" in normalized
        projection_read = (
            "FROM CPK_DELEGATION_SIGNING_KEYS" in normalized
            and "FOR UPDATE" in normalized
            and "STATUS IN ('ACTIVE', 'VERIFY-ONLY')" in normalized
        )
        workspace_lock = (
            "FROM CPK_WORKSPACES" in normalized and "FOR UPDATE" in normalized
        )
        if scope_lock and self._before_scope_lock is not None:
            self._before_scope_lock.set()
        if workspace_lock and self._before_workspace_lock is not None:
            self._before_workspace_lock.set()
        result = self._connection.execute(query, params)
        if scope_lock and self._after_scope_lock is not None:
            self._after_scope_lock.set()
        if workspace_lock and self._after_workspace_lock is not None:
            self._after_workspace_lock.set()
        if workspace_lock and self._release_workspace_lock is not None:
            if not self._release_workspace_lock.wait(timeout=5):
                raise RuntimeError("workspace lock test barrier timed out")
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

    def test_lifecycle_projection_carries_across_additive_graph_change(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        self.seed_desired(self.bound_graph(), projection_b)
        desired = self.with_node(self.bound_graph(), "worker")

        result = self.service().set_desired_graph(
            self.command(
                desired,
                expected_graph_id="graph-current",
                expected_projection_id="projection-current",
                expected_revision=1,
            )
        )

        realized = DEFAULT_GRAPH_CODEC.decode(
            result.realized_projection.graph_descriptor
        )
        self.assertEqual(
            realized.node("gateway").delegation_verifier_projection,
            projection_b,
        )
        self.assertIsNone(desired.node("gateway").delegation_verifier_projection)

    def test_lifecycle_projection_carries_when_overlay_node_is_removed(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        self.seed_desired(
            self.with_node(self.bound_graph(), "cloudflared"),
            projection_b,
        )

        result = self.service().set_desired_graph(
            self.command(
                self.bound_graph(),
                expected_graph_id="graph-current",
                expected_projection_id="projection-current",
                expected_revision=1,
            )
        )

        realized = DEFAULT_GRAPH_CODEC.decode(
            result.realized_projection.graph_descriptor
        )
        self.assertEqual(
            realized.node("gateway").delegation_verifier_projection,
            projection_b,
        )
        self.assertNotIn("cloudflared", realized.nodes)

    def test_changed_delegate_truth_compiles_fresh_projection(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        self.seed_desired(self.bound_graph(), projection_b)
        desired = self.bound_graph().update_node(
            replace(self.bound_graph().node("gateway"), metadata={"release": "next"})
        )

        result = self.service().set_desired_graph(
            self.command(
                desired,
                expected_graph_id="graph-current",
                expected_projection_id="projection-current",
                expected_revision=1,
            )
        )

        realized = DEFAULT_GRAPH_CODEC.decode(
            result.realized_projection.graph_descriptor
        )
        projection = realized.node("gateway").delegation_verifier_projection
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertNotEqual(projection.projection_id, projection_b.projection_id)
        self.assertEqual(projection.public_keys, projection_b.public_keys)

    def test_removed_binding_does_not_inherit_projection(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        self.seed_desired(self.bound_graph(), projection_b)

        result = self.service().set_desired_graph(
            self.command(
                self.unbound_graph(),
                expected_graph_id="graph-current",
                expected_projection_id="projection-current",
                expected_revision=1,
            )
        )

        self.assertEqual(
            result.realized_projection.projection_kind,
            RealizedGraphProjectionKind.IDENTITY,
        )
        realized = DEFAULT_GRAPH_CODEC.decode(
            result.realized_projection.graph_descriptor
        )
        self.assertIsNone(realized.node("gateway").delegation_verifier_projection)

    def test_new_binding_failure_rolls_back_carried_projection_and_graph(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        self.seed_desired(self.bound_graph(), projection_b)
        gateway_other = replace(
            self.bound_graph().node("gateway"),
            node_id="gateway-other",
            block_spec=BlockSpec("gateway-other"),
        )
        desired = DeploymentGraph(
            "two-gateways",
            nodes={
                "gateway": self.bound_graph().node("gateway"),
                "gateway-other": gateway_other,
            },
            runtimes={
                "docker": RuntimeRecord(
                    "docker",
                    RuntimeKind.DOCKER,
                    children=("gateway", "gateway-other"),
                )
            },
            delegation_authorities=(
                *self.bound_graph().delegation_authorities,
                DelegationAuthorityBinding(
                    "gateway-other",
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "missing-issuer",
                ),
            ),
        )

        with self.assertRaisesRegex(
            GraphAuthoringError,
            "exactly one settled active delegation key",
        ):
            self.service().set_desired_graph(
                self.command(
                    desired,
                    expected_graph_id="graph-current",
                    expected_projection_id="projection-current",
                    expected_revision=1,
                )
            )

        self.assertEqual(self.row_count("cpk_graph_versions"), 1)
        self.assertEqual(self.row_count("cpk_realized_graph_projections"), 1)
        workspace = self.connection.execute(
            "SELECT desired_graph_id, desired_realized_projection_id, "
            "desired_graph_revision FROM cpk_workspaces "
            "WHERE workspace_id = 'workspace-a'"
        ).fetchone()
        self.assertEqual(workspace, ("graph-current", "projection-current", 1))

    def test_multiple_bindings_carry_exact_projection_identity(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        self.register_key(
            "key-other",
            PUBLIC_KEY_A,
            active=True,
            issuer="issuer-other",
        )
        graph = self.two_binding_graph()
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        projection_other = self.projection(
            "gateway-rotation-rotation-other-retirement-verifier",
            "key-other",
            PUBLIC_KEY_A,
            node_id="gateway-other",
            issuer="issuer-other",
        )
        self.seed_desired(graph, projection_b, projection_other)

        result = self.service().set_desired_graph(
            self.command(
                self.with_node(graph, "worker"),
                expected_graph_id="graph-current",
                expected_projection_id="projection-current",
                expected_revision=1,
            )
        )

        realized = DEFAULT_GRAPH_CODEC.decode(
            result.realized_projection.graph_descriptor
        )
        self.assertEqual(
            realized.node("gateway").delegation_verifier_projection,
            projection_b,
        )
        self.assertEqual(
            realized.node("gateway-other").delegation_verifier_projection,
            projection_other,
        )

    def test_carried_projection_does_not_bypass_unsettled_key_scope(self) -> None:
        self.register_key("key-a", PUBLIC_KEY_A, active=True)
        projection_a = self.projection(
            "gateway-rotation-rotation-a-overlap-source",
            "key-a",
            PUBLIC_KEY_A,
        )
        self.seed_desired(self.bound_graph(), projection_a)
        self.register_key("key-b", PUBLIC_KEY_B, active=False)

        with self.assertRaisesRegex(
            GraphAuthoringError,
            "exactly one settled active delegation key",
        ):
            self.service().set_desired_graph(
                self.command(
                    self.with_node(self.bound_graph(), "worker"),
                    expected_graph_id="graph-current",
                    expected_projection_id="projection-current",
                    expected_revision=1,
                )
            )

        self.assertEqual(self.row_count("cpk_graph_versions"), 1)
        self.assertEqual(self.row_count("cpk_realized_graph_projections"), 1)

    def test_stale_realized_pointer_rejects_without_writes(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        self.seed_desired(
            self.bound_graph(),
            self.projection(
                "gateway-rotation-rotation-a-retirement-verifier",
                "key-b",
                PUBLIC_KEY_B,
            ),
        )

        with self.assertRaisesRegex(GraphAuthoringError, "stale desired graph pointer"):
            self.service().set_desired_graph(
                self.command(
                    self.with_node(self.bound_graph(), "worker"),
                    expected_graph_id="graph-current",
                    expected_projection_id="projection-stale",
                    expected_revision=1,
                )
            )

        self.assertEqual(self.row_count("cpk_graph_versions"), 1)
        self.assertEqual(self.row_count("cpk_realized_graph_projections"), 1)

    def test_graph_authoring_serializes_against_projection_publication(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        authored = self.bound_graph()
        self.seed_desired(authored, projection_b)
        self.start_session("session-author", "action-start-author")
        self.start_session("session-projection", "action-start-projection")
        author_locked = threading.Event()
        release_author = threading.Event()
        publication_waiting = threading.Event()
        publication_locked = threading.Event()

        def author_uow() -> PostgresUnitOfWork:
            return PostgresUnitOfWork(
                lambda: ObservedConnection(
                    psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]),
                    after_workspace_lock=author_locked,
                    release_workspace_lock=release_author,
                )
            )

        def publication_uow() -> PostgresUnitOfWork:
            return PostgresUnitOfWork(
                lambda: ObservedConnection(
                    psycopg.connect(os.environ["CPK_OPERATIONS_TEST_DATABASE_URL"]),
                    before_workspace_lock=publication_waiting,
                    after_workspace_lock=publication_locked,
                )
            )

        author_service = DesiredGraphCommandService(
            author_uow,
            clock=lambda: "2026-08-04T12:00:00Z",
            id_factory=Sequence("graph-next", "action-author"),
        )
        author_command = SetDesiredGraph(
            session_id="session-author",
            workspace_id="workspace-a",
            actor_id="operator-a",
            graph=self.with_node(authored, "worker"),
            expected_desired_graph_id="graph-current",
            expected_desired_realized_projection_id="projection-current",
            expected_desired_graph_revision=1,
            idempotency_key=IdempotencyKey("author-next"),
        )
        replacement = RealizedGraphProjectionRecord.from_graph(
            projection_id="projection-rotation-next",
            workspace_id="workspace-a",
            source_authored_graph_id="graph-current",
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="gateway-rotation:rotation-next:retirement",
            graph=materialize_delegation_verifiers(authored, (projection_b,)),
            created_by="operator-a",
            created_at="2026-08-04T12:00:00Z",
        )
        publication_service = DesiredRealizedProjectionCommandService(
            publication_uow,
            clock=lambda: "2026-08-04T12:00:00Z",
            action_id_factory=lambda: "action-projection",
        )
        publication_command = PublishDesiredRealizedProjection(
            session_id="session-projection",
            workspace_id="workspace-a",
            actor_id="operator-a",
            expected_authored_graph_id="graph-current",
            expected_realized_projection_id="projection-current",
            expected_desired_graph_revision=1,
            projection=replacement,
            source_operation_id="rotation-next",
            source_operation_version=1,
            idempotency_key=IdempotencyKey("publish-rotation-next"),
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            author_future = executor.submit(author_service.execute, author_command)
            self.assertTrue(author_locked.wait(timeout=5))
            publication_future = executor.submit(
                publication_service.execute,
                publication_command,
            )
            self.assertTrue(publication_waiting.wait(timeout=5))
            self.assertFalse(publication_locked.is_set())
            release_author.set()
            authored_result = author_future.result(timeout=5)
            with self.assertRaises(DesiredRealizedProjectionPublicationConflict):
                publication_future.result(timeout=5)

        workspace = self.connection.execute(
            "SELECT desired_graph_id, desired_realized_projection_id, "
            "desired_graph_revision FROM cpk_workspaces "
            "WHERE workspace_id = 'workspace-a'"
        ).fetchone()
        self.assertEqual(
            workspace,
            (
                authored_result.graph_version_id,
                authored_result.desired_realized_projection_id,
                authored_result.desired_graph_revision,
            ),
        )
        self.assertEqual(self.row_count("cpk_graph_versions"), 2)
        self.assertEqual(self.row_count("cpk_realized_graph_projections"), 2)

    def test_carried_projection_replay_survives_later_pointer_change(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        authored = self.bound_graph()
        self.seed_desired(authored, projection_b)
        self.start_session("session-author", "action-start-author")
        service = DesiredGraphCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T12:00:00Z",
            id_factory=Sequence("graph-next", "action-author"),
        )
        command = SetDesiredGraph(
            session_id="session-author",
            workspace_id="workspace-a",
            actor_id="operator-a",
            graph=self.with_node(authored, "worker"),
            expected_desired_graph_id="graph-current",
            expected_desired_realized_projection_id="projection-current",
            expected_desired_graph_revision=1,
            idempotency_key=IdempotencyKey("author-next"),
        )
        original = service.execute(command)
        later_graph = self.with_node(command.graph, "worker-later")
        later_record = GraphVersionRecord.from_graph(
            graph_id="graph-later",
            workspace_id="workspace-a",
            version=3,
            graph=later_graph,
            created_by="operator-a",
            created_at="2026-08-04T12:30:00Z",
        )
        later_projection = RealizedGraphProjectionRecord.from_graph(
            projection_id="projection-later",
            workspace_id="workspace-a",
            source_authored_graph_id=later_record.graph_id,
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="authored-delegation-verifier",
            graph=materialize_delegation_verifiers(later_graph, (projection_b,)),
            created_by="operator-a",
            created_at="2026-08-04T12:30:00Z",
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.graphs.save(later_record)
            unit_of_work.stores.realized_graphs.save(later_projection)
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                later_record.graph_id,
                later_projection.projection_id,
            )
            unit_of_work.commit()

        replay = service.execute(command)

        self.assertTrue(replay.replayed)
        self.assertEqual(
            replay.desired_realized_projection_id,
            original.desired_realized_projection_id,
        )
        workspace = self.connection.execute(
            "SELECT desired_graph_id, desired_realized_projection_id "
            "FROM cpk_workspaces WHERE workspace_id = 'workspace-a'"
        ).fetchone()
        self.assertEqual(workspace, ("graph-later", "projection-later"))

    def test_malformed_prior_projection_graph_fails_closed_before_writes(self) -> None:
        self.register_key("key-b", PUBLIC_KEY_B, active=True)
        authored = self.bound_graph()
        projection_b = self.projection(
            "gateway-rotation-rotation-a-retirement-verifier",
            "key-b",
            PUBLIC_KEY_B,
        )
        malformed_realized = materialize_delegation_verifiers(
            self.with_node(authored, "unowned-node"),
            (projection_b,),
        )
        self.seed_desired(
            authored,
            projection_b,
            realized_graph=malformed_realized,
        )

        with self.assertRaisesRegex(
            GraphAuthoringError,
            "does not match authored graph truth",
        ):
            self.service().set_desired_graph(
                self.command(
                    self.with_node(authored, "worker"),
                    expected_graph_id="graph-current",
                    expected_projection_id="projection-current",
                    expected_revision=1,
                )
            )

        self.assertEqual(self.row_count("cpk_graph_versions"), 1)
        self.assertEqual(self.row_count("cpk_realized_graph_projections"), 1)

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

    def command(
        self,
        graph: DeploymentGraph,
        *,
        expected_graph_id: str | None = None,
        expected_projection_id: str | None = None,
        expected_revision: int = 0,
    ) -> SetDesiredGraphCommand:
        return SetDesiredGraphCommand(
            workspace_id="workspace-a",
            actor_id="operator-a",
            graph=graph,
            expected_desired_graph_id=expected_graph_id,
            expected_desired_realized_projection_id=expected_projection_id,
            expected_desired_graph_revision=expected_revision,
        )

    def seed_desired(
        self,
        authored: DeploymentGraph,
        *projections: DelegationVerifierProjection,
        realized_graph: DeploymentGraph | None = None,
    ) -> None:
        graph_record = GraphVersionRecord.from_graph(
            graph_id="graph-current",
            workspace_id="workspace-a",
            version=1,
            graph=authored,
            created_by="operator-a",
            created_at="2026-08-04T11:00:00Z",
        )
        projection_record = RealizedGraphProjectionRecord.from_graph(
            projection_id="projection-current",
            workspace_id="workspace-a",
            source_authored_graph_id=graph_record.graph_id,
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="gateway-rotation:rotation-a:retirement",
            graph=(
                materialize_delegation_verifiers(authored, projections)
                if realized_graph is None
                else realized_graph
            ),
            created_by="operator-a",
            created_at="2026-08-04T11:00:00Z",
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.graphs.save(graph_record)
            unit_of_work.stores.realized_graphs.save(projection_record)
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a",
                graph_record.graph_id,
                projection_record.projection_id,
            )
            unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a",
                graph_record.graph_id,
                projection_record.projection_id,
            )
            unit_of_work.commit()

    def start_session(self, session_id: str, action_id: str) -> None:
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T11:30:00Z",
            id_factory=Sequence(session_id, action_id),
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                f"Session {session_id}",
                IdempotencyKey(f"start-{session_id}"),
            )
        )

    @staticmethod
    def projection(
        projection_id: str,
        key_id: str,
        public_key_pem: str,
        *,
        node_id: str = "gateway",
        issuer: str = "issuer-a",
    ) -> DelegationVerifierProjection:
        return DelegationVerifierProjection(
            delegate_node_id=node_id,
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer=issuer,
            audience=f"gateway:workspace-a:{node_id}",
            projection_id=projection_id,
            public_keys=(
                DelegationPublicKey(
                    key_id,
                    DelegationKeyAlgorithm.ED25519,
                    public_key_pem,
                ),
            ),
        )

    @staticmethod
    def with_node(graph: DeploymentGraph, node_id: str) -> DeploymentGraph:
        node = Node(
            node_id=node_id,
            block_family=BlockFamily.APPLICATION,
            block_spec=BlockSpec(node_id),
            kind="container-server",
            runtime_id="docker",
            sockets=BlockSockets(),
        )
        return replace(
            graph.add_node(node),
            runtimes={
                "docker": replace(
                    graph.runtimes["docker"],
                    children=tuple(
                        sorted((*graph.runtimes["docker"].children, node_id))
                    ),
                )
            },
        )

    def two_binding_graph(self) -> DeploymentGraph:
        gateway = self.bound_graph().node("gateway")
        gateway_other = replace(
            gateway,
            node_id="gateway-other",
            block_spec=BlockSpec("gateway-other"),
        )
        return DeploymentGraph(
            "two-gateways",
            nodes={
                gateway.node_id: gateway,
                gateway_other.node_id: gateway_other,
            },
            runtimes={
                "docker": RuntimeRecord(
                    "docker",
                    RuntimeKind.DOCKER,
                    children=(gateway.node_id, gateway_other.node_id),
                )
            },
            delegation_authorities=(
                *self.bound_graph().delegation_authorities,
                DelegationAuthorityBinding(
                    gateway_other.node_id,
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    "issuer-other",
                ),
            ),
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
