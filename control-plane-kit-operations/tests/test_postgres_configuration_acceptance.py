"""#1924 original zero-slot acceptance via real Operations command owners."""
from dataclasses import replace
from hashlib import sha256
import os
import unittest

import psycopg
import rfc8785

from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_core.planning import ActivityId, ActivityPlan, PlannedActivity, RuntimeTarget, StartRuntime
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_core.topology import DeploymentGraph, RuntimeRecord
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph, CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun
from control_plane_kit_operations.postgres import PostgresUnitOfWork, SchemaInstallationError, install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, GraphVersionRecord,
    OperationSessionRecord, OperationSessionStatus, OperationsRecordError,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleStorageError
from tests import test_execution_coordinator as coordinator_fixture
from tests.test_postgres_configuration_evidence import _ObservedConnection


class PostgresConfigurationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker PostgreSQL")
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.addCleanup(self.connection.execute, "TRUNCATE TABLE cpk_workspaces CASCADE")
        self.create_command = CreateWorkspace("workspace-a", "Workspace A", "operator-a", IdempotencyKey("create-a"))
        self.created = WorkspaceCommandService(self.unit_of_work,
            clock=lambda: "2026-07-22T12:00:00Z", id_factory=lambda: "graph-current").create(self.create_command)
        plan = ActivityPlan((PlannedActivity(ActivityId("start-runtime"), StartRuntime(RuntimeTarget("runtime-a"))),))
        desired = DeploymentGraph("desired", runtimes={
            "runtime-a": RuntimeRecord("runtime-a", RuntimeKind.DOCKER, children=())})
        with self.unit_of_work() as uow:
            stores = uow.stores
            stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-desired", workspace_id="workspace-a",
                version=2, graph=desired, created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
            self.workspace = stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
            self.projection = stores.realized_graphs.get(self.workspace.desired_realized_projection_id)
            stores.activity_history.add_session(OperationSessionRecord("session-a", "workspace-a", "operator-a",
                "Deploy", OperationSessionStatus.OPEN, "2026-07-22T12:01:00Z"))
            stores.activity_history.add_plan(ActivityPlanRecord("plan-a", "session-a", "graph-current", "graph-desired",
                ActivityPlanStatus.PLANNED, "2026-07-22T12:02:00Z", plan,
                base_realized_projection_id=self.workspace.current_realized_projection_id,
                desired_realized_projection_id=self.workspace.desired_realized_projection_id,
                desired_graph_revision=self.workspace.desired_graph_revision))
            uow.commit()
        # Reuse existing composition only. Do not call its synthetic bootstrap,
        # inherit its test cases, or seed a claimed/succeeded run.
        self.engine = coordinator_fixture.ExecutionCoordinatorTests()
        self.engine.database_url = self.database_url
        self.engine.connection = self.connection
        self.engine.ids = coordinator_fixture.Sequence()
        self.engine.tracker = coordinator_fixture.TrackingUnitOfWorkFactory(self.database_url)
        self.engine.admit_prepared_execution(plan)
        opened = self.engine.lifecycle_with_ids("run-a", "event-open", "action-claim").execute(
            ClaimAndOpenActivityRun("request-a", self.engine.authority(), ExecutionLeaseDuration(600), IdempotencyKey("claim-a")))
        self.fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        self.engine.lifecycle_with_ids("event-start", "action-start").execute(
            StartActivityRun("run-a", self.engine.authority(), self.fence, IdempotencyKey("start-a")))
        # Explicit simulated effect premise: correlated acknowledgement only;
        # this adapter does not create a provider runtime or call Docker.
        adapter = coordinator_fixture.RecordingAdapter(self.engine.tracker, lambda _context, request:
            RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "test"}))
        result = self.engine.coordinator(adapter).execute(self.engine.command(generation=self.fence.generation))
        self.assertIs(result.status, CoordinatorStatus.COMPLETED)
        self.assertEqual(adapter.calls, ["start-runtime"])
        self.assertEqual(adapter.active_during_calls, [0])
        with self.unit_of_work() as uow:
            self.assertIs(uow.stores.execution.get_run("run-a").status, ActivityRunStatus.SUCCEEDED)

    def unit_of_work(self):
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def command(self):
        return AdvanceCurrentGraph("workspace-a", "run-a", "plan-a", "graph-current",
            self.workspace.current_realized_projection_id, "graph-desired", self.projection.projection_id,
            self.workspace.desired_graph_revision, self.engine.authority(), self.fence, IdempotencyKey("advance-a"))

    def advance(self, factory=None):
        ids = iter(("event-advance", "action-advance"))
        return CurrentGraphAdvancementCommandService(factory or self.unit_of_work,
            clock=lambda: "2026-07-22T13:05:00Z", id_factory=lambda: next(ids)).execute(self.command())

    def receipt_snapshot(self):
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        counts = tuple(self.connection.execute(f"SELECT count(*) FROM {relation}").fetchone()[0]
            for relation in ("cpk_activity_events", "cpk_operation_actions"))
        return workspace, counts

    def retained_snapshot(self, connection=None):
        # Observe the actual retained fixture, including deliberate corruption,
        # on this connection. This is a no-repair witness, not a receipt decoder.
        connection = connection or self.connection
        return tuple((table, connection.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall())
            for table, key in (
                ("cpk_workspaces", "workspace_id"),
                ("cpk_graph_versions", "graph_id"),
                ("cpk_realized_graph_projections", "projection_id"),
                ("cpk_workspace_initializations", "workspace_id"),
                ("cpk_operation_actions", "action_id"),
                ("cpk_activity_events", "event_id"),
                ("cpk_configuration_acceptances", "workspace_id,pinned_revision"),
                ("cpk_configuration_accepted_slots", "workspace_id,pinned_revision,runtime_id,node_id,artifact_id")))

    def test_current_schema_reentry_verifies_retained_original_acceptance(self):
        self.advance()
        before = self.retained_snapshot()
        install_schema(self.connection)
        self.assertEqual(self.retained_snapshot(), before)

    def test_late_commit_failure_rolls_back_pointer_pair_and_header(self):
        before, tentative = self.retained_snapshot(), []

        class CommitFailure:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                tentative.append((
                    self.connection.execute("SELECT current_graph_id,current_realized_projection_id "
                        "FROM cpk_workspaces WHERE workspace_id='workspace-a'").fetchone(),
                    self.connection.execute("SELECT action_id,event_id,slot_count FROM "
                        "cpk_configuration_acceptances WHERE workspace_id='workspace-a'").fetchall(),
                    self.connection.execute("SELECT count(*) FROM cpk_operation_actions "
                        "WHERE action_id='action-advance'").fetchone(),
                    self.connection.execute("SELECT count(*) FROM cpk_activity_events "
                        "WHERE event_id='event-advance'").fetchone()))
                raise RuntimeError("injected acceptance commit failure")

        with self.assertRaisesRegex(RuntimeError, "^injected acceptance commit failure$"):
            self.advance(lambda: PostgresUnitOfWork(lambda: CommitFailure(psycopg.connect(self.database_url))))
        self.assertEqual(tentative, [(("graph-desired", self.projection.projection_id),
            [("action-advance", "event-advance", 0)], (1,), (1,))])
        self.assertEqual(self.retained_snapshot(), before)

    def test_oversized_workspace_metadata_refuses_before_transport_or_mutation(self):
        marker = "private-advancement-canary"
        self.connection.execute("UPDATE cpk_workspaces SET metadata=jsonb_build_object('canary',%s::text) "
            "WHERE workspace_id='workspace-a'", (marker * 4000,))
        before, writes = self.retained_snapshot(), []
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)

        class ObservedWrites(_ObservedConnection):
            def execute(self, query, parameters=None):
                if str(query).lstrip().split()[0].upper() in ("INSERT", "UPDATE", "DELETE"):
                    writes.append(str(query))
                return super().execute(query, parameters)

        with self.assertRaises(CurrentGraphAdvancementConflict) as caught:
            self.advance(lambda: PostgresUnitOfWork(lambda:
                ObservedWrites(psycopg.connect(self.database_url), observed)))
        self.assertIs(type(caught.exception), CurrentGraphAdvancementConflict)
        self.assertLessEqual(len(str(caught.exception)), 512)
        self.assertNotIn(marker, str(caught.exception))
        self.assertLessEqual(observed["largest_cell"], 65536)
        self.assertGreater(observed["statements"], 0)
        self.assertEqual(writes, [])
        self.assertEqual(self.retained_snapshot(), before)

    def test_actual_cas_miss_is_accounted_and_publishes_no_receipt(self):
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        # Disposable PostgreSQL fault injection: the actual UPDATE returns zero
        # rows. No cursor result, source method or successful history is faked.
        self.connection.execute("CREATE FUNCTION cpk_test_1924_skip_current_cas() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN IF OLD.workspace_id='workspace-a' "
            "AND NEW.current_graph_id IS DISTINCT FROM OLD.current_graph_id "
            "THEN RETURN NULL; END IF; RETURN NEW; END $$")
        self.addCleanup(self.connection.execute, "DROP FUNCTION cpk_test_1924_skip_current_cas()")
        self.connection.execute("CREATE TRIGGER cpk_test_1924_skip_current_cas BEFORE UPDATE ON cpk_workspaces "
            "FOR EACH ROW EXECUTE FUNCTION cpk_test_1924_skip_current_cas()")
        self.addCleanup(self.connection.execute, "DROP TRIGGER cpk_test_1924_skip_current_cas ON cpk_workspaces")
        before, cas_rows, ledgers = self.retained_snapshot(), [], []
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)

        class ObservedCas(_ObservedConnection):
            def execute(self, query, parameters=None):
                ledgers.append(_ACCOUNTING.get())
                result = super().execute(query, parameters)
                if str(query).lstrip().startswith("UPDATE cpk_workspaces") and "current_graph_id" in str(query):
                    cas_rows.append(result.rowcount)
                return result

        with self.assertRaisesRegex(CurrentGraphAdvancementConflict, "^workspace current graph changed concurrently$"):
            self.advance(lambda: PostgresUnitOfWork(lambda:
                ObservedCas(psycopg.connect(self.database_url), observed)))
        self.assertEqual(cas_rows, [0])
        self.assertTrue(ledgers and all(ledger is not None for ledger in ledgers))
        self.assertEqual(len({id(ledger) for ledger in ledgers}), 1)
        self.assertEqual(ledgers[0].used.statements, observed["statements"])
        self.assertGreaterEqual(ledgers[0].used.records, observed["rows"])
        self.assertGreaterEqual(ledgers[0].used.accounted_bytes, observed["bytes"])
        self.assertEqual(self.retained_snapshot(), before)

    def test_issued_binding_invalidates_predecessor_copies_and_expired_transaction(self):
        captured, identities = {}, iter(("event-advance", "action-advance"))
        raw = psycopg.connect(self.database_url)
        self.addCleanup(raw.close)

        def probe_live_binding():
            stores, predecessor = captured["stores"], captured["unbound"]
            bound = stores.configuration_acceptance._issued
            self.assertIsNotNone(predecessor)
            self.assertIsNot(bound, predecessor)
            bound.require(stores.connection, "workspace-a", after_cas=True)
            captured["live_txid"] = raw.execute("SELECT txid_current()").fetchone()[0]
            before = self.retained_snapshot(raw)
            for candidate in (predecessor, replace(bound)):
                for writer in ("event", "action"):
                    with self.assertRaises(OperationsRecordError) as caught:
                        if writer == "event":
                            stores.execution._add_advancement_event(bound.event, candidate)
                        else:
                            stores.activity_history._add_advancement_action(bound.action, candidate)
                    self.assertIs(type(caught.exception), OperationsRecordError)
                    self.assertLessEqual(len(str(caught.exception)), 512)
                    self.assertEqual(self.retained_snapshot(raw), before)
                with self.assertRaises(OperationsRecordError):
                    candidate.with_records(bound.event, bound.action)
                self.assertEqual(self.retained_snapshot(raw), before)
            with self.assertRaises(OperationsRecordError):
                bound.with_records(bound.event, bound.action)
            self.assertEqual(self.retained_snapshot(raw), before)
            self.assertIs(stores.configuration_acceptance._issued, bound)

        class RetainedConnection:
            def __getattr__(self, name):
                return getattr(raw, name)

            def commit(self):
                # Probe the live completed publication before real physical
                # commit, outside the command's accounting observation scope.
                probe_live_binding()
                captured["probed"] = True
                raw.commit()
                captured["physical_commit"] = True

            def close(self):
                # Keep the real connection solely to probe the already-ended
                # physical transaction; commit/rollback are never replaced.
                captured["uow_closed"] = True

        class CapturedUnitOfWork(PostgresUnitOfWork):
            def __enter__(self):
                super().__enter__()
                captured["stores"] = self.stores
                return self

        def identity():
            captured.setdefault("unbound", captured["stores"].configuration_acceptance._issued)
            return next(identities)

        accepted = CurrentGraphAdvancementCommandService(
            lambda: CapturedUnitOfWork(RetainedConnection), clock=lambda: "2026-07-22T13:05:00Z",
            id_factory=identity).execute(self.command())
        self.assertTrue(captured["uow_closed"])
        self.assertTrue(captured["probed"])
        self.assertTrue(captured["physical_commit"])
        stores = captured["stores"]
        bound = stores.configuration_acceptance._issued
        self.assertEqual((bound.event, bound.action), (accepted.event, accepted.action))
        self.assertEqual(self.connection.execute("SELECT current_graph_id,current_realized_projection_id "
            "FROM cpk_workspaces WHERE workspace_id='workspace-a'").fetchone(),
            (accepted.to_authored_graph_id, accepted.to_realized_projection_id))
        self.assertEqual(self.connection.execute("SELECT action_id,event_id FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a'").fetchall(), [(accepted.action.action_id, accepted.event.event_id)])
        new_txid = raw.execute("SELECT txid_current()").fetchone()[0]
        self.assertNotEqual(new_txid, captured["live_txid"])
        before = self.retained_snapshot(raw)
        for writer in ("event", "action"):
            with self.subTest(expired_writer=writer):
                with self.assertRaises(ReceiverLifecycleStorageError) as caught:
                    if writer == "event":
                        stores.execution._add_advancement_event(accepted.event, bound)
                    else:
                        stores.activity_history._add_advancement_action(accepted.action, bound)
                self.assertIs(type(caught.exception), ReceiverLifecycleStorageError)
                self.assertLessEqual(len(str(caught.exception)), 512)
                self.assertEqual(self.retained_snapshot(raw), before)
        raw.rollback()
        self.assertEqual(self.retained_snapshot(), before)

    def test_private_writers_refuse_reconstructed_unissued_advancement_owner(self):
        from control_plane_kit_operations._configuration_acceptance import _PreparedAdvancementReceipt
        with self.unit_of_work() as uow:
            original_workspace = uow.stores.workspaces.get("workspace-a")
        accepted = self.advance()
        original = self.retained_snapshot()
        for writer in ("event", "action"):
            with self.subTest(writer=writer):
                with self.unit_of_work() as uow:
                    stores = uow.stores
                    guard = stores.graphs.lock_receiver_lifecycle("workspace-a")
                    request = stores.execution.get_request("request-a")
                    run = stores.execution.get_run("run-a")
                    plan = stores.activity_history.get_plan("plan-a")
                    event = replace(accepted.event, event_id="event-unissued",
                        ordinal=stores.execution.next_event_ordinal("run-a"))
                    action = replace(accepted.action, action_id="action-unissued",
                        ordinal=stores.activity_history.next_action_ordinal("session-a"),
                        idempotency_key="unissued-advance",
                        payload={**accepted.action.payload, "event_id": event.event_id})
                    # Attack premise: congruent copied data and real fresh L are
                    # not an owner-issued prepared advancement transaction.
                    reconstructed = _PreparedAdvancementReceipt(stores, guard, original_workspace,
                        request, run, plan,
                        stores.realized_graphs.get(plan.base_realized_projection_id),
                        stores.realized_graphs.get(plan.desired_realized_projection_id), event, action)
                    before = self.retained_snapshot(stores.connection)
                    with self.assertRaises(OperationsRecordError) as caught:
                        if writer == "event":
                            stores.execution._add_advancement_event(event, reconstructed)
                        else:
                            stores.activity_history._add_advancement_action(action, reconstructed)
                    self.assertIs(type(caught.exception), OperationsRecordError)
                    self.assertLessEqual(len(str(caught.exception)), 512)
                    self.assertEqual(self.retained_snapshot(stores.connection), before)
                self.assertEqual(self.retained_snapshot(), original)

    def test_current_schema_refuses_corrupt_original_acceptance_without_repair(self):
        accepted = self.advance()
        original = self.retained_snapshot()
        cases = (
            ("missing-header", "DELETE FROM cpk_configuration_acceptances WHERE action_id=%s",
                (accepted.action.action_id,)),
            ("extra-event-field", "UPDATE cpk_activity_events SET payload=payload || "
                "jsonb_build_object('unexpected',%s::text) WHERE event_id=%s",
                ("untrusted-acceptance-marker", accepted.event.event_id)),
            ("wrong-original-scope", "UPDATE cpk_operation_actions SET payload=jsonb_set(payload,"
                "'{workspace_id}',to_jsonb(%s::text)) WHERE action_id=%s",
                ("untrusted-acceptance-marker", accepted.action.action_id)),
        )

        class RestoreFixture(Exception):
            pass

        for name, query, parameters in cases:
            with self.subTest(case=name):
                with self.assertRaises(RestoreFixture):
                    with self.connection.transaction():
                        self.connection.execute(query, parameters)
                        corrupted = self.retained_snapshot()
                        self.assertNotEqual(corrupted, original)
                        with self.assertRaises(SchemaInstallationError) as caught:
                            install_schema(self.connection)
                        self.assertEqual(str(caught.exception), "operations schema reset is required")
                        self.assertNotIn("untrusted-acceptance-marker", str(caught.exception))
                        self.assertEqual(self.retained_snapshot(), corrupted)
                        raise RestoreFixture
                self.assertEqual(self.retained_snapshot(), original)

    def test_zero_slot_advancement_retains_typed_original_pair(self):
        result = self.advance()
        expected = ("workspace-a", "request-a", "plan-a", "run-a", self.workspace.desired_graph_revision)
        for relation, key, run_field, identity in (
                ("cpk_operation_actions", "action_id", "advancement_run_id", result.action.action_id),
                ("cpk_activity_events", "event_id", "run_id", result.event.event_id)):
            with self.subTest(relation=relation):
                fields = ("advancement_workspace_id", "advancement_request_id", "advancement_plan_id", run_field, "advancement_revision")
                columns = {row[0] for row in self.connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s",
                    (relation,)).fetchall()}
                self.assertTrue(set(fields) <= columns, "original advancement lacks typed owner coordinates")
                self.assertEqual(self.connection.execute(
                    f"SELECT {','.join(fields)} FROM {relation} WHERE {key}=%s", (identity,)).fetchone(), expected)

    def test_zero_slot_advancement_retains_complete_header(self):
        result = self.advance()
        for relation in ("cpk_configuration_acceptances", "cpk_configuration_accepted_slots"):
            self.assertIsNotNone(self.connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0],
                "original advancement did not retain complete configuration acceptance")
        row = self.connection.execute("SELECT workspace_id,pinned_revision,graph_id,projection_id,projection_digest,"
            "action_id,event_id,run_id,request_id,plan_id,slot_count,slot_digest FROM cpk_configuration_acceptances "
            "WHERE workspace_id='workspace-a'").fetchall()
        self.assertEqual(row, [("workspace-a", self.workspace.desired_graph_revision, "graph-desired",
            self.projection.projection_id, self.projection.projection_digest, result.action.action_id,
            result.event.event_id, "run-a", "request-a", "plan-a", 0, sha256(rfc8785.dumps([])).hexdigest())])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_accepted_slots "
            "WHERE workspace_id='workspace-a'").fetchone()[0], 0)

    def test_public_advancement_writers_cannot_publish_outside_owner(self):
        result = self.advance()
        for kind in ("event", "action"):
            with self.subTest(kind=kind):
                before = self.receipt_snapshot()
                with self.assertRaises(OperationsRecordError) as caught:
                    with self.unit_of_work() as uow:
                        if kind == "event":
                            uow.stores.execution.add_event(replace(result.event, event_id="event-copy",
                                ordinal=uow.stores.execution.next_event_ordinal("run-a")))
                        else:
                            uow.stores.activity_history.add_action(replace(result.action, action_id="action-copy",
                                ordinal=uow.stores.activity_history.next_action_ordinal("session-a"),
                                idempotency_key="fresh-copy-key"))
                        uow.commit()
                self.assertIs(type(caught.exception), OperationsRecordError)
                self.assertLessEqual(len(str(caught.exception)), 512)
                self.assertEqual(self.receipt_snapshot(), before)

    def test_advancement_and_replay_account_every_owner_statement_and_return(self):
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        for replayed in (False, True):
            with self.subTest(replayed=replayed):
                observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
                ledgers = []

                class CommandObservation(_ObservedConnection):
                    def execute(self, *args, **kwargs):
                        ledgers.append(_ACCOUNTING.get())
                        return super().execute(*args, **kwargs)

                result = self.advance(lambda: PostgresUnitOfWork(lambda:
                    CommandObservation(psycopg.connect(self.database_url), observed)))
                self.assertIs(result.replayed, replayed)
                self.assertTrue(ledgers)
                self.assertTrue(all(ledger is not None for ledger in ledgers),
                    "advancement evidence escaped its command ledger")
                self.assertEqual(len({id(ledger) for ledger in ledgers}), 1)
                self.assertGreaterEqual(ledgers[0].used.records, observed["rows"])
                self.assertGreaterEqual(ledgers[0].used.accounted_bytes, observed["bytes"])
                self.assertEqual(ledgers[0].used.statements, observed["statements"])

    def test_creation_replay_after_real_advancement_preserves_original_receipt(self):
        original = self.connection.execute("SELECT * FROM cpk_workspace_initializations WHERE workspace_id='workspace-a'").fetchone()
        accepted = self.advance()
        replay = WorkspaceCommandService(self.unit_of_work,
            clock=lambda: self.fail("creation replay sampled time"),
            id_factory=lambda: self.fail("creation replay allocated an identity")).create(
                replace(self.create_command, actor_id="later-actor", idempotency_key=IdempotencyKey("later-key")))
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.current_graph.graph_id, accepted.to_authored_graph_id)
        self.assertEqual(replay.workspace.current_realized_projection_id, accepted.to_realized_projection_id)
        self.assertEqual(self.connection.execute("SELECT * FROM cpk_workspace_initializations WHERE workspace_id='workspace-a'").fetchone(), original)
