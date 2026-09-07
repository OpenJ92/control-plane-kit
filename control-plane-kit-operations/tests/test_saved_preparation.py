"""#1764 admission, historical evidence and staged transaction laws."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.deployment_program_interpreter import DeploymentProgramStateConflict
from control_plane_kit_operations.deployment_program_projections import DeploymentApprovalRequired
from control_plane_kit_operations.records import GraphProjectionLineage
from control_plane_kit_operations.workflows import CloseOperationSession, IdempotencyKey, StartOperationSession
from saved_preparation_fixture import (SavedPreparationFixture, InterruptedPreparation,
                                       SAVED_METADATA_KEYS, expected_saved_metadata)
from draft_catalogue_fixture import NOW, principal


class SavedPreparationTests(SavedPreparationFixture, unittest.TestCase):
    def test_exact_old_selected_revision_prepares_existing_plan_and_approval_without_graph_writes(self):
        draft = self.selected()
        self.catalogue().execute(self.revise_command(draft))
        frozen = self.frozen_truth()
        result = self.program().prepare(self.prepare_command(draft))
        self.assertIsInstance(result, DeploymentApprovalRequired)
        session = self.prepared_session(result)
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(result.reference.plan_id)
            self.assertEqual(plan.desired_graph_id, draft.graph_id)
            self.assertEqual(plan.desired_graph_revision, self.workspace().desired_graph_revision)
        self.assertEqual(set(session.metadata), SAVED_METADATA_KEYS)
        self.assertEqual(dict(session.metadata), expected_saved_metadata(self.prepare_command(draft)))
        self.assertTrue(all(type(value) is str for value in session.metadata.values()))
        self.assertEqual(session.metadata["deployment_prepare_source"], "saved-revision.v1")
        self.assertEqual(session.metadata["deployment_prepare_saved_draft_id"], draft.draft_id)
        self.assertEqual(session.metadata["deployment_prepare_saved_revision"], "1")
        self.assertEqual(session.metadata["deployment_prepare_saved_graph_id"], draft.graph_id)
        self.assertEqual(self.frozen_truth(), frozen)
        actions = [row[0] for row in self.rows("cpk_operation_actions") if row[0]["session_id"] == session.session_id]
        self.assertEqual([row["action_type"] for row in sorted(actions, key=lambda row: row["ordinal"])],
                         ["start-operation-session", "request-activity-plan", "request-approval"])

    def test_each_lineage_coordinate_generation_and_selected_revision_is_fenced_before_evidence(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        other = self.create(key="other")
        cases = (
            {"expected_current": GraphProjectionLineage("wrong", command.expected_current.realized_projection_id)},
            {"expected_current": GraphProjectionLineage(command.expected_current.authored_graph_id, "wrong")},
            {"expected_desired": GraphProjectionLineage("wrong", command.expected_desired.realized_projection_id)},
            {"expected_desired": GraphProjectionLineage(command.expected_desired.authored_graph_id, "wrong")},
            {"expected_desired_graph_revision": command.expected_desired_graph_revision + 1},
            {"desired": self.values.SavedDesiredTopologyRevision(other.draft_id, 1)},
            {"desired": self.values.SavedDesiredTopologyRevision(draft.draft_id, 2)},
            {"context": principal("workspace-b").command_context("workspace-b")},
        )
        for changes in cases:
            before = self.all_truth()
            statements = []
            with self.subTest(changes=changes), self.assertRaises(DeploymentProgramStateConflict):
                self.program(uow=self.observed_uow(statements)).prepare(replace(command, **changes))
            self.assertEqual(self.all_truth(), before)
            self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE")) for query in statements))
        self.catalogue().execute(self.select_command(draft, key="same-graph-aba"))
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(command)
        self.assertEqual(self.all_truth(), before)

    def test_revoked_products_and_invalid_saved_graph_reject_before_session_creation(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        descriptor = self.connection.execute("SELECT graph_descriptor FROM cpk_graph_versions WHERE graph_id=%s",
                                             (draft.graph_id,)).fetchone()[0]
        self.connection.execute("UPDATE cpk_graph_versions SET graph_descriptor=%s WHERE graph_id=%s",
                                (Jsonb({"name": "private-canary", "nodes": "bad"}), draft.graph_id))
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict) as error:
            self.program().prepare(command)
        self.assertNotIn("private-canary", str(error.exception))
        self.assertEqual(self.all_truth(), before)
        self.connection.execute("UPDATE cpk_graph_versions SET graph_descriptor=%s WHERE graph_id=%s",
                                (Jsonb(descriptor), draft.graph_id))
        from control_plane_kit_core.algebra import RequirementSocket
        from control_plane_kit_core.types import Protocol
        graph = self.graph()
        node = graph.nodes["app"]
        invalid = replace(graph, nodes={"app": replace(node, sockets=replace(node.sockets,
            requirements=(RequirementSocket("missing", Protocol.HTTP, ("MISSING_URL",), True),)))})
        self.connection.execute("UPDATE cpk_graph_versions SET graph_descriptor=%s WHERE graph_id=%s",
                                (Jsonb(DEFAULT_GRAPH_CODEC.encode(invalid)), draft.graph_id))
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(command)
        self.assertEqual(self.all_truth(), before)
        self.connection.execute("UPDATE cpk_graph_versions SET graph_descriptor=%s WHERE graph_id=%s",
                                (Jsonb(descriptor), draft.graph_id))
        self.connection.execute("UPDATE cpk_registered_products SET status='revoked' WHERE workspace_id='workspace-a'")
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(command)
        self.assertEqual(self.all_truth(), before)

    def test_restart_after_each_saved_physical_commit_has_no_duplicate_or_selection_write(self):
        for stage in ("admission", "plan", "approval"):
            draft = self.selected(key="draft-" + stage)
            command = self.prepare_command(draft, key="prepare-" + stage)
            frozen = self.frozen_truth()
            with self.subTest(stage=stage), self.assertRaises(InterruptedPreparation):
                self.program(stop=stage).prepare(command)
            result = self.program().prepare(command)
            before = self.all_truth()
            self.assertEqual(self.program().prepare(command), result)
            self.assertEqual(self.all_truth(), before)
            self.assertEqual(self.frozen_truth(), frozen)
            session = self.prepared_session(result)
            self.assertEqual(len([row for row in self.rows("cpk_activity_plans") if row[0]["session_id"] == session.session_id]), 1)
            self.assertEqual(len([row for row in self.rows("cpk_approval_requests") if row[0]["session_id"] == session.session_id]), 1)

    def test_admitted_session_then_selection_drift_rejects_first_plan_but_completed_replay_is_historical(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        with self.assertRaises(InterruptedPreparation):
            self.program(stop="admission").prepare(command)
        self.restore_legacy_desired()
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(command)
        self.assertEqual(self.all_truth(), before)
        self.catalogue().execute(self.select_command(draft, key="reselect"))
        complete = self.prepare_command(draft, key="complete")
        result = self.program().prepare(complete)
        self.catalogue().execute(self.revise_command(draft))
        self.restore_legacy_desired()
        self.operations().execute(CloseOperationSession(self.prepared_session(result).session_id,
                                                       "operator-a", IdempotencyKey("close")))
        self.connection.execute("UPDATE cpk_registered_products SET status='revoked' WHERE workspace_id='workspace-a'")
        before = self.all_truth()
        statements = []
        self.assertEqual(self.program(uow=self.observed_uow(statements)).prepare(complete), result)
        self.assertEqual(self.all_truth(), before)
        self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE")) for query in statements))

    def test_same_parent_inline_saved_and_changed_saved_intents_conflict_without_writes(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        result = self.program().prepare(command)
        changes = ({"desired": self.graph()}, {"title": "changed"}, {"approval_comment": "changed"},
                   {"desired": self.values.SavedDesiredTopologyRevision(draft.draft_id, 2)},
                   {"expected_desired_graph_revision": command.expected_desired_graph_revision + 1})
        for change in changes:
            before = self.all_truth()
            with self.subTest(change=change), self.assertRaises(DeploymentProgramStateConflict):
                self.program().prepare(replace(command, **change))
            self.assertEqual(self.all_truth(), before)
        inline = replace(command, desired=self.graph(), idempotency_key=IdempotencyKey("inline-first"))
        self.program().prepare(inline)
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(replace(command, idempotency_key=inline.idempotency_key))
        self.assertEqual(self.all_truth(), before)
        self.assertEqual(self.program().prepare(command), result)

    def test_replay_validates_full_session_and_start_action_evidence(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        result = self.program().prepare(command)
        session = self.prepared_session(result)
        action = next(row[0] for row in self.rows("cpk_operation_actions")
                      if row[0]["session_id"] == session.session_id and row[0]["ordinal"] == 1)
        session_cases = (("actor_id", "other"), ("title", "other"), ("intent_fingerprint", "0" * 64),
                         ("created_at", "2026-09-06T18:01:00Z"))
        action_cases = (("actor_id", "other"), ("action_type", "close-operation-session"),
                        ("ordinal", 99), ("idempotency_key", "false-key"),
                        ("payload", Jsonb({"workspace_id": "workspace-b"})),
                        ("payload", Jsonb({"workspace_id": "workspace-a", "extra": "false"})),
                        ("intent_fingerprint", "0" * 64), ("created_at", "2026-09-06T18:01:00Z"))
        for table, identity, row_id, cases in (("cpk_operation_sessions", "session_id", session.session_id, session_cases),
                                              ("cpk_operation_actions", "action_id", action["action_id"], action_cases)):
            for field, value in cases:
                query = sql.SQL("UPDATE {} SET {}=%s WHERE {}=%s").format(sql.Identifier(table), sql.Identifier(field), sql.Identifier(identity))
                original = self.connection.execute(sql.SQL("SELECT {} FROM {} WHERE {}=%s").format(
                    sql.Identifier(field), sql.Identifier(table), sql.Identifier(identity)), (row_id,)).fetchone()[0]
                self.connection.execute(query, (value, row_id))
                try:
                    before = self.all_truth()
                    with self.subTest(table=table, field=field), self.assertRaises(DeploymentProgramStateConflict):
                        self.program().prepare(command)
                    self.assertEqual(self.all_truth(), before)
                finally:
                    self.connection.execute(query, (Jsonb(original) if field == "payload" else original, row_id))

    def test_replay_rejects_false_revision_projection_or_saved_metadata(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        result = self.program().prepare(command)
        session = self.prepared_session(result)
        original = dict(session.metadata)
        cases = ({**original, "deployment_prepare_source": "unknown"},
                 {key: value for key, value in original.items() if key != "deployment_prepare_saved_revision"},
                 {**original, "deployment_prepare_intent_sha256": "0" * 64},
                 {**original, "deployment_prepare_intent_sha256": "invalid"},
                 {**original, "deployment_prepare_saved_revision": "2"},
                 {**original, "deployment_prepare_saved_revision": "01"},
                 {**original, "deployment_prepare_saved_current_graph_id": "workspace-b-current"},
                 {**original, "deployment_prepare_saved_current_projection_id": "false-current"},
                 {**original, "deployment_prepare_saved_desired_projection_id": "false-desired"},
                 {**original, "deployment_prepare_saved_desired_generation": str(command.expected_desired_graph_revision + 1)},
                 {**original, "deployment_prepare_saved_desired_generation": "01"},
                 {**original, "deployment_prepare_saved_graph_id": "workspace-b-current"},
                 {**original, "deployment_prepare_saved_extra": "private-canary"})
        for metadata in cases:
            self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s", (Jsonb(metadata), session.session_id))
            before = self.all_truth()
            with self.subTest(metadata=metadata), self.assertRaises(DeploymentProgramStateConflict):
                self.program().prepare(command)
            self.assertEqual(self.all_truth(), before)
        self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s", (Jsonb(original), session.session_id))
        self.connection.execute("UPDATE cpk_desired_topology_draft_revisions SET graph_id=%s WHERE workspace_id='workspace-a' AND draft_id=%s AND revision=1",
                                ("workspace-a-current", draft.draft_id))
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(command)
        self.assertEqual(self.all_truth(), before)
        self.connection.execute("UPDATE cpk_desired_topology_draft_revisions SET graph_id=%s WHERE workspace_id='workspace-a' AND draft_id=%s AND revision=1",
                                (draft.graph_id, draft.draft_id))
        projection_id = command.expected_desired.realized_projection_id
        descriptor = self.connection.execute("SELECT graph_descriptor FROM cpk_realized_graph_projections WHERE projection_id=%s", (projection_id,)).fetchone()[0]
        self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s WHERE projection_id=%s",
                                (Jsonb(DEFAULT_GRAPH_CODEC.encode(DeploymentGraph("false-identity"))), projection_id))
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(command)
        self.assertEqual(self.all_truth(), before)
        self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s WHERE projection_id=%s", (Jsonb(descriptor), projection_id))

    def test_session_start_transaction_seam_does_not_commit_and_late_action_collision_rolls_back(self):
        before = self.all_truth()
        operations = self.operations()
        with self.unit_of_work() as uow:
            operations.start_in_unit_of_work(uow, StartOperationSession("workspace-a", "operator-a", "Uncommitted",
                                                                       IdempotencyKey("uncommitted")))
            self.assertEqual(self.all_truth(), before)  # A distinct connection must see no committed rows.
        self.assertEqual(self.all_truth(), before)
        draft = self.selected()
        collision = self.rows("cpk_operation_actions")[0][0]["action_id"]
        identities = iter(("fresh-session", collision))
        before = self.all_truth()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.program(operations=self.operations(id_factory=lambda: next(identities))).prepare(self.prepare_command(draft))
        self.assertEqual(self.all_truth(), before)

    def test_concurrent_identical_preparation_converges_under_session_key_lock(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        frozen = self.frozen_truth()
        barrier = threading.Barrier(2)
        def prepare(_):
            barrier.wait(timeout=5)
            return self.program().prepare(command)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(prepare, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.frozen_truth(), frozen)
        self.assertEqual(len(self.rows("cpk_activity_plans")), 1)
        self.assertEqual(len(self.rows("cpk_approval_requests")), 1)

    def test_admission_workspace_lock_excludes_selection_until_session_commit(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        selection = self.select_command(draft, key="concurrent-select", session_id=self.start_session("workspace-a"))
        clock, reached, release = self.hold_clock()
        attempted = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(self.program(operations=self.operations(clock=clock), stop="admission").prepare, command)
            try:
                self.assertTrue(reached.wait(timeout=5))
                follower = pool.submit(self.catalogue(unit_of_work_factory=self.observed_uow([], attempted)).execute, selection)
                self.assertTrue(attempted.wait(timeout=5))
                self.assertFalse(follower.done())
            finally:
                release.set()
            with self.assertRaises(InterruptedPreparation):
                leader.result(timeout=10)
            follower.result(timeout=10)
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(command)
        self.assertEqual(self.all_truth(), before)

    def test_admission_and_tombstone_serialize_in_both_orders_without_claiming_unused_revision(self):
        selected = self.selected()
        command = self.prepare_command(selected)
        clock, reached, release = self.hold_clock()
        attempted = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(self.program(operations=self.operations(clock=clock), stop="admission").prepare, command)
            try:
                self.assertTrue(reached.wait(timeout=5))
                follower = pool.submit(self.catalogue(unit_of_work_factory=self.observed_uow([], attempted)).execute,
                                       self.delete_command(selected))
                self.assertTrue(attempted.wait(timeout=5))
                self.assertFalse(follower.done())
            finally:
                release.set()
            with self.assertRaises(InterruptedPreparation):
                leader.result(timeout=10)
            with self.assertRaises(self.api.DesiredTopologyDraftConflict):
                follower.result(timeout=10)
        unused = self.create(key="unused")
        invalid = self.prepare_command(unused, key="unused-prepare")
        clock, reached, release = self.hold_clock()
        attempted = threading.Event()
        sessions = self.rows("cpk_operation_sessions")
        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(self.catalogue(clock=clock).execute, self.delete_command(unused, key="unused-delete"))
            try:
                self.assertTrue(reached.wait(timeout=5))
                follower = pool.submit(self.program(uow=self.observed_uow([], attempted)).prepare, invalid)
                self.assertTrue(attempted.wait(timeout=5))
                self.assertFalse(follower.done())
            finally:
                release.set()
            leader.result(timeout=10)
            with self.assertRaises(DeploymentProgramStateConflict):
                follower.result(timeout=10)
        self.assertEqual(self.rows("cpk_operation_sessions"), sessions)

    def test_replay_rejects_malformed_retained_current_projection_without_writes(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        self.program().prepare(command)
        projection_id = command.expected_current.realized_projection_id
        self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s WHERE projection_id=%s",
                                (Jsonb({"name": "private-current", "nodes": "bad"}), projection_id))
        before = self.all_truth()
        with self.assertRaises(DeploymentProgramStateConflict) as error:
            self.program().prepare(command)
        self.assertNotIn("private-current", str(error.exception))
        self.assertEqual(self.all_truth(), before)

    def test_saved_admission_owner_computes_canonical_digest_without_caller_digest(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        owner = self.admission.SavedDeploymentPreparationService(self.unit_of_work, self.operations())
        result = owner.start(command, session_key=IdempotencyKey("direct-saved-session"))
        self.assertEqual(dict(result.session.metadata), expected_saved_metadata(command))
        self.assertEqual(set(result.session.metadata), SAVED_METADATA_KEYS)
        before = self.all_truth()
        replay = owner.start(command, session_key=IdempotencyKey("direct-saved-session"))
        self.assertEqual(replay.session, result.session)
        self.assertEqual(self.all_truth(), before)
