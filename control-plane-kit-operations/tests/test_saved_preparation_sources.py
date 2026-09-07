"""#1772 relational admission integrity; real existing PostgreSQL owners."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_operations.postgres.schema import install_schema, SchemaInstallationError
from control_plane_kit_operations.deployment_program_interpreter import DeploymentProgramStateConflict
from control_plane_kit_operations.workflows import CloseOperationSession, IdempotencyKey
from saved_preparation_fixture import SavedPreparationFixture, InterruptedPreparation, SAVED_ONLY_KEYS


class SavedPreparationSourceTests(SavedPreparationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(self.connection.execute(
            "SELECT to_regclass('cpk_saved_preparation_sources')").fetchone()[0],
            "missing saved-preparation source relation")

    def sources(self):
        return self.rows("cpk_saved_preparation_sources")

    def complete_truth(self):
        return {**self.all_truth(), "cpk_saved_preparation_sources": self.sources()}

    def assert_current_rejected_without_repair(self):
        before = self.complete_truth()
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        self.assertEqual(self.complete_truth(), before)

    def test_admission_records_one_exact_source_and_many_sessions_can_share_revision(self):
        draft = self.selected()
        self.assertEqual(self.sources(), [])
        frozen = self.frozen_truth()
        results = [self.program().prepare(self.prepare_command(draft, key=key))
                   for key in ("first", "second")]
        sessions = [self.prepared_session(result) for result in results]
        self.assertNotEqual(sessions[0].session_id, sessions[1].session_id)
        self.assertEqual({tuple(row[0][key] for key in
            ("session_id", "workspace_id", "draft_id", "revision")) for row in self.sources()},
            {(session.session_id, "workspace-a", draft.draft_id, 1) for session in sessions})
        with self.unit_of_work() as uow:
            source = uow.stores.saved_preparation_sources.get("workspace-a", sessions[0].session_id)
            self.assertEqual((source.session_id, source.workspace_id, source.draft_id, source.revision),
                             (sessions[0].session_id, "workspace-a", draft.draft_id, 1))
            self.assertIsNone(uow.stores.saved_preparation_sources.get("workspace-b", sessions[0].session_id))
        self.assertEqual(self.frozen_truth(), frozen)
        before = self.complete_truth()
        install_schema(self.connection)
        self.assertEqual(self.complete_truth(), before)

    def test_source_has_exact_four_columns_and_database_membership_constraints(self):
        columns = self.connection.execute("SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='cpk_saved_preparation_sources' "
            "ORDER BY ordinal_position", (self.schema,)).fetchall()
        self.assertEqual(columns, [(key,) for key in ("session_id", "workspace_id", "draft_id", "revision")])
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        session = self.prepared_session(result)
        candidates = (
            (session.session_id, "workspace-a", draft.draft_id, 1),
            ("missing-session", "workspace-a", draft.draft_id, 1),
            (self.sessions["workspace-b"], "workspace-a", draft.draft_id, 1),
            (self.sessions["workspace-b"], "workspace-b", draft.draft_id, 1),
            (self.sessions["workspace-a"], "workspace-a", draft.draft_id, 999),
        )
        for values in candidates:
            before = self.complete_truth()
            with self.subTest(values=values), self.assertRaises(psycopg.IntegrityError):
                self.connection.execute("INSERT INTO cpk_saved_preparation_sources "
                    "(session_id,workspace_id,draft_id,revision) VALUES (%s,%s,%s,%s)", values)
            self.assertEqual(self.complete_truth(), before)

    def test_failure_after_source_insert_rolls_back_session_action_and_source(self):
        draft = self.selected()
        before = self.complete_truth()
        statements = []
        def after(query):
            if query.startswith("INSERT INTO cpk_saved_preparation_sources"):
                raise InterruptedPreparation("after actual source INSERT")
        with self.assertRaises(InterruptedPreparation):
            self.program(uow=self.observed_uow(statements, after_execute=after)).prepare(
                self.prepare_command(draft))
        self.assertTrue(any(query.startswith("INSERT INTO cpk_saved_preparation_sources")
                            for query in statements))
        self.assertEqual(self.complete_truth(), before)

    def test_admitted_source_without_plan_verifies_and_physical_restart_reuses_it(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        with self.assertRaises(InterruptedPreparation):
            self.program(stop="admission").prepare(command)
        source = self.sources()
        self.assertEqual(len(source), 1)
        self.assertEqual(self.rows("cpk_activity_plans"), [])
        before = self.complete_truth()
        install_schema(self.connection)
        self.assertEqual(self.complete_truth(), before)
        result = self.program().prepare(command)
        self.assertEqual(self.sources(), source)
        before = self.complete_truth()
        self.assertEqual(self.program().prepare(command), result)
        self.assertEqual(self.complete_truth(), before)

    def test_identical_concurrent_admissions_have_one_source(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        barrier = threading.Barrier(2)
        def prepare(_):
            barrier.wait(timeout=5)
            return self.program().prepare(command)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(prepare, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.sources()), 1)
        self.assertEqual(len(self.rows("cpk_activity_plans")), 1)

    def test_replay_survives_head_selection_session_and_product_drift_without_source_write(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        result = self.program().prepare(command)
        self.catalogue().execute(self.revise_command(draft))
        self.restore_legacy_desired()
        self.operations().execute(CloseOperationSession(self.prepared_session(result).session_id,
            "operator-a", IdempotencyKey("close")))
        self.connection.execute("UPDATE cpk_registered_products SET status='revoked' "
                                "WHERE workspace_id='workspace-a'")
        before = self.complete_truth()
        self.assertEqual(self.program().prepare(command), result)
        self.assertEqual(self.complete_truth(), before)

    def test_missing_or_false_source_fails_replay_and_current_validation_without_repair(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        result = self.program().prepare(command)
        session = self.prepared_session(result)
        later = self.catalogue().execute(self.revise_command(draft))
        self.connection.execute("UPDATE cpk_saved_preparation_sources SET revision=%s WHERE session_id=%s",
                                (later.revision, session.session_id))
        for state in ("wrong-revision", "missing"):
            if state == "missing":
                self.connection.execute("DELETE FROM cpk_saved_preparation_sources WHERE session_id=%s",
                                        (session.session_id,))
            before = self.complete_truth()
            with self.subTest(state=state), self.assertRaises(DeploymentProgramStateConflict):
                self.program().prepare(command)
            self.assertEqual(self.complete_truth(), before)
            self.assert_current_rejected_without_repair()

    def test_source_on_inline_session_fails_current_validation(self):
        draft = self.selected()
        inline = replace(self.prepare_command(draft), desired=self.graph())
        result = self.program().prepare(inline)
        session = self.prepared_session(result)
        self.assertEqual(self.sources(), [])
        self.connection.execute("INSERT INTO cpk_saved_preparation_sources "
            "(session_id,workspace_id,draft_id,revision) VALUES (%s,'workspace-a',%s,1)",
            (session.session_id, draft.draft_id))
        self.assert_current_rejected_without_repair()

    def test_current_validation_rejects_orphan_saved_keys_without_source(self):
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        session = self.prepared_session(result)
        self.connection.execute("DELETE FROM cpk_saved_preparation_sources WHERE session_id=%s",
                                (session.session_id,))
        cases = [{key: "orphan"} for key in sorted(SAVED_ONLY_KEYS)] + [
            {"deployment_prepare_source": "wrong"},
            {"deployment_prepare_saved_unknown": "orphan"},
        ]
        for metadata in cases:
            self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s",
                                    (Jsonb(metadata), session.session_id))
            with self.subTest(keys=tuple(metadata)):
                self.assert_current_rejected_without_repair()

    def test_current_validation_rejects_commitment_fingerprint_and_graph_disagreement(self):
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        session = self.prepared_session(result)
        original = dict(session.metadata)
        for change in ({"deployment_prepare_saved_graph_id": "workspace-a-current"},
                       {"deployment_prepare_intent_sha256": "0" * 64},
                       {"deployment_prepare_saved_draft_id": "x" * 70000}):
            self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s",
                                    (Jsonb({**original, **change}), session.session_id))
            with self.subTest(keys=tuple(change)):
                self.assert_current_rejected_without_repair()
        self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s, intent_fingerprint=%s "
            "WHERE session_id=%s", (Jsonb(original), "0" * 64, session.session_id))
        self.assert_current_rejected_without_repair()
        self.connection.execute("UPDATE cpk_operation_sessions SET intent_fingerprint=%s WHERE session_id=%s",
                                (session.intent_fingerprint, session.session_id))
        self.connection.execute("UPDATE cpk_desired_topology_draft_revisions SET graph_id=%s "
            "WHERE workspace_id='workspace-a' AND draft_id=%s AND revision=1",
            ("workspace-a-current", draft.draft_id))
        self.assert_current_rejected_without_repair()

    def test_current_validation_traverses_more_than_one_candidate_batch(self):
        draft = self.selected()
        for index in range(65):
            with self.assertRaises(InterruptedPreparation):
                self.program(stop="admission").prepare(self.prepare_command(draft, key=f"batch-{index}"))
        self.assertEqual(len(self.sources()), 65)
        install_schema(self.connection)
        last_session = max(row[0]["session_id"] for row in self.sources())
        self.connection.execute("DELETE FROM cpk_saved_preparation_sources WHERE session_id=%s", (last_session,))
        self.assert_current_rejected_without_repair()

    def test_total_saved_evidence_erasure_is_not_claimed_detectable(self):
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        session = self.prepared_session(result)
        self.connection.execute("DELETE FROM cpk_saved_preparation_sources WHERE session_id=%s",
                                (session.session_id,))
        self.connection.execute("UPDATE cpk_operation_sessions SET metadata='{}'::jsonb WHERE session_id=%s",
                                (session.session_id,))
        before = self.complete_truth()
        install_schema(self.connection)
        self.assertEqual(self.complete_truth(), before)

    def test_older_schema_without_source_relation_is_rejected_without_creation(self):
        before = self.all_truth()
        self.connection.execute("DROP TABLE cpk_saved_preparation_sources")
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        self.assertEqual(self.all_truth(), before)
        self.assertIsNone(self.connection.execute(
            "SELECT to_regclass('cpk_saved_preparation_sources')").fetchone()[0])
