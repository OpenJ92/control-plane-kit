"""#1924 original workspace initialization through the existing command owner."""
from hashlib import sha256
import os
import unittest

import psycopg
import rfc8785

from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import (
    CreateWorkspace, WorkspaceCommandError, WorkspaceCommandService,
)
from tests.lifecycle_lock_fixture import LifecycleLockFixture, LIFECYCLE_LOCK
from tests.test_postgres_configuration_evidence import _ObservedConnection


class PostgresWorkspaceInitializationTests(LifecycleLockFixture, unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker PostgreSQL")
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.addCleanup(self.connection.execute, "TRUNCATE TABLE cpk_workspaces CASCADE")

    def unit_of_work(self):
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def command(self):
        return CreateWorkspace("workspace-a", "Workspace A", "operator-a",
            IdempotencyKey("create-workspace-a"))

    def service(self, *, unit_of_work=None, ids=None, clock_calls=None):
        def identity():
            if ids is not None:
                ids.append("graph-initial")
            return "graph-initial"

        def clock():
            if clock_calls is not None:
                clock_calls.append("sampled")
            return "2026-07-22T10:00:00Z"

        return WorkspaceCommandService(unit_of_work or self.unit_of_work,
            clock=clock, id_factory=identity)

    def test_real_create_retains_exact_original_graph_and_pointer_owner_projection(self):
        result = self.service().create(self.command())
        # Missing behavior is explicit after a valid existing owner command;
        # absence of a future table must not become a SQL/setup error.
        self.assertIsNotNone(self.connection.execute(
            "SELECT to_regclass('cpk_workspace_initializations')").fetchone()[0],
            "workspace creation did not retain its original initialization receipt")
        row = self.connection.execute(
            "SELECT workspace_id,profile,initial_graph_id,initial_projection_id,"
            "graph_descriptor_sha256,projection_digest,configuration_slot_count,"
            "created_by,creation_idempotency_key FROM cpk_workspace_initializations "
            "WHERE workspace_id='workspace-a'").fetchone()
        with self.unit_of_work() as uow:
            projection = uow.stores.realized_graphs.get(
                result.workspace.current_realized_projection_id)
        self.assertEqual(row, (
            "workspace-a", "workspace-initialization.v1", result.current_graph.graph_id,
            projection.projection_id,
            sha256(rfc8785.dumps(result.current_graph.graph_descriptor)).hexdigest(),
            projection.projection_digest, 0, "operator-a", "create-workspace-a"))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_realized_graph_projections "
            "WHERE workspace_id='workspace-a'").fetchone()[0], 1)
        self.assertFalse(result.replayed)

    def test_seeded_empty_pointer_and_bootstrap_metadata_cannot_supply_original_creation(self):
        # Explicit unsupported historical premise, not owner-created genesis.
        # Matching names, graph and metadata must not be adopted or backfilled.
        graph = GraphVersionRecord.from_graph(
            graph_id="graph-initial", workspace_id="workspace-a", version=1,
            graph=DeploymentGraph("empty"), created_by="operator-a",
            created_at="2026-07-22T10:00:00Z",
            metadata={"bootstrap": "empty-current-graph",
                "idempotency_key": "create-workspace-a"})
        with self.unit_of_work() as uow:
            uow.stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            uow.stores.graphs.save(graph)
            before = uow.stores.workspaces.set_current_graph("workspace-a", graph.graph_id)
            uow.commit()
        ids, clock_calls = [], []
        with self.assertRaises(WorkspaceCommandError):
            self.service(ids=ids, clock_calls=clock_calls).create(self.command())
        self.assertEqual(ids, [])
        self.assertEqual(clock_calls, [])
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a"), before)
            self.assertEqual(uow.stores.graphs.get(graph.graph_id), graph)
        if self.connection.execute(
                "SELECT to_regclass('cpk_workspace_initializations')").fetchone()[0] is not None:
            self.assertEqual(self.connection.execute(
                "SELECT count(*) FROM cpk_workspace_initializations "
                "WHERE workspace_id='workspace-a'").fetchone()[0], 0,
                "refused replay backfilled an original initialization receipt")

    def test_fresh_creation_takes_lifecycle_before_ids_and_time(self):
        ids, clock_calls = [], []

        def create(factory):
            return self.service(unit_of_work=factory, ids=ids,
                clock_calls=clock_calls).create(self.command())

        with self.blocked_command(LIFECYCLE_LOCK,
                ("receiver-lifecycle:workspace-a",), create) as future:
            self.assertEqual(ids, [], "fresh creation allocated graph identity before L")
            self.assertEqual(clock_calls, [], "fresh creation sampled time before L")
        self.assertEqual(future.result(timeout=1).current_graph.graph_id, "graph-initial")
        self.assertEqual(ids, ["graph-initial"])
        self.assertEqual(clock_calls, ["sampled"])

    def test_concurrent_creators_share_one_original_receipt_and_projection(self):
        leader_ids, follower_ids = [], []
        leader, follower = self.opposing_commands(
            lambda factory: self.service(unit_of_work=factory, ids=leader_ids).create(self.command()),
            lambda factory: self.service(unit_of_work=factory, ids=follower_ids).create(self.command()),
            lambda sql, params: sql == LIFECYCLE_LOCK
                and params == ("receiver-lifecycle:workspace-a",))
        first, replay = leader.result(), follower.result()
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.workspace, replay.workspace)
        self.assertEqual(first.current_graph.graph_id, replay.current_graph.graph_id)
        self.assertEqual(leader_ids, ["graph-initial"])
        self.assertEqual(follower_ids, [])
        for relation in ("cpk_workspace_initializations", "cpk_graph_versions",
                "cpk_realized_graph_projections"):
            self.assertEqual(self.connection.execute(
                f"SELECT count(*) FROM {relation} WHERE workspace_id='workspace-a'"
            ).fetchone()[0], 1)

    def test_late_commit_failure_rolls_back_entire_creation(self):
        class CommitFailure:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                raise RuntimeError("injected workspace commit failure")

        factory = lambda: PostgresUnitOfWork(
            lambda: CommitFailure(psycopg.connect(self.database_url)))
        with self.assertRaisesRegex(RuntimeError, "^injected workspace commit failure$"):
            self.service(unit_of_work=factory).create(self.command())
        for relation in ("cpk_workspaces", "cpk_graph_versions",
                "cpk_realized_graph_projections", "cpk_workspace_initializations"):
            self.assertEqual(self.connection.execute(
                f"SELECT count(*) FROM {relation} WHERE workspace_id='workspace-a'"
            ).fetchone()[0], 0)

    def test_original_receipt_drift_refuses_replay_without_repair(self):
        created = self.service().create(self.command())
        self.connection.execute("UPDATE cpk_workspace_initializations "
            "SET graph_descriptor_sha256=%s WHERE workspace_id='workspace-a'", ("0" * 64,))
        ids, clock_calls = [], []
        with self.assertRaises(WorkspaceCommandError):
            self.service(ids=ids, clock_calls=clock_calls).create(self.command())
        self.assertEqual(ids, [])
        self.assertEqual(clock_calls, [])
        self.assertEqual(self.connection.execute("SELECT graph_descriptor_sha256 "
            "FROM cpk_workspace_initializations WHERE workspace_id='workspace-a'"
        ).fetchone()[0], "0" * 64)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a"), created.workspace)

    def test_original_replay_preserves_creation_inputs_without_new_ids(self):
        created = self.service().create(self.command())
        before = self.connection.execute("SELECT * FROM cpk_workspace_initializations "
            "WHERE workspace_id='workspace-a'").fetchone()
        ids, clock_calls = [], []
        replay = self.service(ids=ids, clock_calls=clock_calls).create(
            CreateWorkspace("workspace-a", "Workspace A", "later-operator",
                IdempotencyKey("later-key")))
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.workspace, created.workspace)
        self.assertEqual(replay.current_graph.graph_id, created.current_graph.graph_id)
        self.assertEqual(ids, [])
        self.assertEqual(clock_calls, [])
        self.assertEqual(self.connection.execute("SELECT * FROM cpk_workspace_initializations "
            "WHERE workspace_id='workspace-a'").fetchone(), before)

    def test_replay_refuses_oversized_workspace_metadata_before_transport(self):
        self.service().create(self.command())
        self.connection.execute("UPDATE cpk_workspaces SET metadata=jsonb_build_object('canary', %s::text) "
            "WHERE workspace_id='workspace-a'", ("private-workspace-canary" * 4000,))
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        factory = lambda: PostgresUnitOfWork(lambda: _ObservedConnection(
            psycopg.connect(self.database_url), observed))
        refusal = None
        try:
            self.service(unit_of_work=factory).create(self.command())
        except WorkspaceCommandError as error:
            refusal = error
        self.assertLessEqual(observed["largest_cell"], 65536,
            "creation replay transported oversized workspace metadata before refusal")
        self.assertIsNotNone(refusal, "oversized replay must refuse with a bounded owner error")
        self.assertNotIn("private-workspace-canary", str(refusal))

    def test_create_and_replay_keep_one_ledger_for_all_owner_reads_and_returns(self):
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING

        for replayed in (False, True):
            with self.subTest(replayed=replayed):
                observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
                ledgers = []

                class CommandObservation(_ObservedConnection):
                    def execute(self, *args, **kwargs):
                        ledgers.append(_ACCOUNTING.get())
                        return super().execute(*args, **kwargs)

                factory = lambda: PostgresUnitOfWork(lambda: CommandObservation(
                    psycopg.connect(self.database_url), observed))
                result = self.service(unit_of_work=factory).create(self.command())
                self.assertIs(result.replayed, replayed)
                self.assertTrue(ledgers)
                self.assertTrue(all(ledger is not None for ledger in ledgers),
                    "workspace evidence access escaped the command ledger")
                self.assertEqual(len({id(ledger) for ledger in ledgers}), 1)
                footprint = ledgers[0].used
                self.assertGreaterEqual(footprint.records, observed["rows"])
                self.assertGreaterEqual(footprint.accounted_bytes, observed["bytes"])
                self.assertEqual(footprint.statements, observed["statements"])
