"""N8 one real PostgreSQL snapshot, in both owner/read commit orders."""

from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

import psycopg

from control_plane_kit_operations.planning import DesiredGraphCommandError
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.unit_of_work import UnitOfWorkStateError
from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleExpectation
from control_plane_kit_core.topology import DeploymentGraph
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_authoring_context_fixture import ReceiverAuthoringContextFixture, ContextObserver
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture


class SnapshotSchedule:
    def read_over_commit(self, writer, **query):
        before = self.context_read(**query)
        captured, release = threading.Event(), threading.Event()

        def after_first_data():
            captured.set()
            if not release.wait(10):
                raise AssertionError("test did not release snapshot reader")

        factory, observed = self.measured_factory(after_data=after_first_data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(self.context_read, factory=factory, **query)
            try:
                self.assertTrue(captured.wait(10), "reader did not reach a real data result")
                # Completion before releasing the reader also proves the read
                # holds no lifecycle/workspace lock that excludes this owner.
                written = pool.submit(writer).result(timeout=8)
            finally:
                release.set()
            actual = reader.result(timeout=10)
        self.assertEqual(actual, before, "the response mixed snapshots or rechecked live pins")
        self.assert_measured_snapshot(observed)
        return before, written


class ReceiverAuthoringSnapshotTests(SnapshotSchedule, ReceiverAuthoringContextFixture,
                                     ReceiverAdmissionFixture, unittest.TestCase):
    def test_desired_commit_during_read_keeps_old_snapshot_and_later_admission_rechecks_pins(self):
        self.desired_service().execute(self.desired_command())
        pins = self.pins()
        before, written = self.read_over_commit(
            lambda: self.desired_service().execute(self.desired_command(
                graph=self.receiver_graph(pretty=True)[0], key="concurrent-desired")),
            expected=pins.descriptor())
        # Writer-before-reader is the reverse ordering: every field is new.
        after = self.context_read()
        self.assertEqual(after["desired"]["graph_id"], written.graph_version_id)
        self.assertNotEqual(after["expectation"], before["expectation"])
        self.assert_context_refused(409, expected=before["expectation"])
        old = ReceiverLifecycleExpectation(**before["expectation"])
        truth = self.admission_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(self.desired_command(key="stale-author", pins=old))
        self.assertEqual(self.admission_truth(), truth)

    def test_live_draft_head_is_wholly_old_then_old_head_refuses_and_new_head_succeeds(self):
        first = self.catalogue().execute(self.receiver_create())
        before, revised = self.read_over_commit(lambda: self.catalogue().execute(
            self.receiver_revise(first, graph=self.receiver_graph(pretty=True)[0])),
            pending_draft={"draft_id": first.draft_id, "expected_head_revision": 1})
        self.assert_context_refused(409, pending_draft={"draft_id": first.draft_id, "expected_head_revision": 1})
        after = self.context_read(pending_draft={"draft_id": first.draft_id, "expected_head_revision": 2})
        self.assertEqual(after["pending_draft"]["graph_id"], revised.graph_id)
        self.assertNotEqual(after["pending_draft"]["graph_id"], before["pending_draft"]["graph_id"])
        self.assertEqual(after["pending_draft"]["receivers"][0]["origin"],
                         before["pending_draft"]["receivers"][0]["origin"])

    def snapshot_entry(self, uow):
        entry = getattr(uow, "read_snapshot", None)
        self.assertTrue(callable(entry), "#1899 missing explicit snapshot UoW entry")
        return entry()

    def test_snapshot_mode_precedes_data_and_postgres_itself_refuses_writes(self):
        factory, observed = self.measured_factory()
        uow = factory()
        with self.snapshot_entry(uow):
            observer = observed[0]
            observer.execute("SELECT workspace_id FROM cpk_workspaces LIMIT 1").fetchone()
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                observer.execute("UPDATE cpk_workspaces SET name=name WHERE workspace_id='workspace-a'")
        self.assert_measured_snapshot(observed)
        self.assertEqual(observed[0].rollbacks, 1)

    def test_used_connection_is_refused_and_closed_during_entry(self):
        connection = psycopg.connect(self.database_url, options="-c search_path=" + self.schema)
        self.addCleanup(connection.close)
        connection.execute("SELECT workspace_id FROM cpk_workspaces LIMIT 1").fetchone()
        observer = ContextObserver(connection)
        uow = PostgresUnitOfWork(lambda: observer)
        entry = self.snapshot_entry(uow)
        with self.assertRaises(UnitOfWorkStateError):
            with entry:
                self.fail("an already-acquired data snapshot was accepted")
        self.assertTrue(connection.closed)
        self.assertEqual((observer.rollbacks, observer.closes), (1, 1))

    def test_mode_setup_failure_rolls_back_and_closes_even_when_enter_does_not_finish(self):
        class RejectMode(ContextObserver):
            def execute(self, query, params=()):
                text = str(query).lower()
                if "read only" in text or "repeatable read" in text:
                    raise psycopg.OperationalError("test-only mode setup failure")
                return super().execute(query, params)

        observer = RejectMode(psycopg.connect(self.database_url))
        self.addCleanup(observer.connection.close)
        uow = PostgresUnitOfWork(lambda: observer)
        with self.assertRaises(psycopg.OperationalError):
            with self.snapshot_entry(uow):
                self.fail("snapshot yielded after setup failed")
        self.assertTrue(observer.connection.closed)
        self.assertEqual((observer.rollbacks, observer.closes), (1, 1))

    def test_ordinary_uow_retains_default_write_and_rollback_behavior(self):
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            self.assertEqual(connection.execute("SHOW transaction_isolation").fetchone(), ("read committed",))
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone(), ("off",))
            connection.execute("UPDATE cpk_workspaces SET name='rolled-back' WHERE workspace_id='workspace-a'")
        self.assertNotEqual(self.workspace().name, "rolled-back")


class ReceiverAuthoringAcceptanceSnapshotTests(SnapshotSchedule, ReceiverAuthoringContextFixture,
                                               ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def test_current_and_first_acceptance_change_atomically_across_the_read_snapshot(self):
        introduced = self.desired_receiver("authoring", graph=self.canonical_receiver_graph)
        # Existing fixture explicitly ASSUMES upstream completion inputs. Only
        # the real advancement owner below earns current/acceptance evidence.
        claimed, _, _ = self.retained_success("authoring")
        before, accepted = self.read_over_commit(lambda: self.advance(claimed, "authoring"))
        self.assertEqual(before["current"]["receivers"], [])
        self.assertIsNone(before["desired"]["receivers"][0]["origin"]["first_accepted_action_id"])
        after = self.context_read()
        receiver = after["current"]["receivers"][0]
        self.assertEqual(receiver["lifecycle"], "current")
        self.assertEqual(receiver["origin"]["introducing_action_id"], introduced.action.action_id)
        self.assertEqual(receiver["origin"]["first_accepted_action_id"], accepted.action.action_id)
        self.assertEqual(after["expectation"]["current_graph_id"], introduced.graph_version_id)
        self.assertEqual(after["current"], after["desired"])
        # A detached value must still serialize after the snapshot connection
        # closes, and an identical subsequent read does not create history.
        truth = self.acceptance_truth()
        factory, observed = self.measured_factory()
        model = self.direct_context(factory=factory)
        self.assert_measured_snapshot(observed)
        self.assertEqual(model.descriptor(), after)
        self.assertEqual(self.acceptance_truth(), truth)

    def test_retired_reservations_are_not_catalogued_and_a_corrupt_retired_selection_refuses(self):
        introduced = self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("first")
        self.advance(claimed, "first")
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        removal, _, _ = self.retained_success("remove")
        self.advance(removal, "remove")
        self.assertIsNotNone(self.receiver_origin().retired_action_id)
        descriptor = self.context_read()
        self.assertEqual(descriptor["current"]["receivers"], [])
        self.assertEqual(descriptor["desired"]["receivers"], [])
        # Deliberate corruption below the command owner; never lawful adoption.
        self.connection.execute("UPDATE cpk_workspaces SET desired_graph_id=%s, "
            "desired_realized_projection_id=%s WHERE workspace_id='workspace-a'",
            (introduced.graph_version_id, introduced.desired_realized_projection_id))
        self.assert_context_refused(409)
