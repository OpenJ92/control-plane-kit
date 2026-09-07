"""#1773 owner laws: association is not source attribution or advancement."""
from dataclasses import replace
import unittest

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import ActivityRunStatus
from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_operations.records import RetryIdentity, SavedPreparationSourceRecord
from control_plane_kit_operations.workflows import IdempotencyKey, StartOperationSession
from saved_preparation_fixture import InterruptedPreparation
from revision_history_fixture import RevisionHistoryFixture


UNAVAILABLE = {"state": "unavailable", "draft_id": None, "revision": None, "graph_id": None}
HISTORY_SCOPE = "source-or-target-sessions-and-exact-target-attempts"


class RevisionHistoryTests(RevisionHistoryFixture, unittest.TestCase):
    def test_new_revision_has_complete_absence_and_no_read_writes(self):
        draft = self.create()
        before = self.history_truth()
        self.assertEqual(self.page(draft, "preparations").items, ())
        self.assertEqual(self.page(draft, "attempts").items, ())
        self.assertEqual(self.detail(draft)["history"], {"scope": HISTORY_SCOPE,
            "preparations_present": False, "attempts_present": False, "completeness": "association-records-only"})
        self.assertEqual(self.history_truth(), before)

    def test_source_only_target_only_and_both_union_are_deduplicated(self):
        draft = self.selected()
        with self.assertRaises(InterruptedPreparation):
            self.program(stop="admission").prepare(self.prepare_command(draft, key="source-only"))
        result = self.program().prepare(self.prepare_command(draft, key="both"))
        plan = self.plan(result)
        target_session = self.start_session("workspace-a")
        self.clone_plan(plan, plan_id="target-only", session_id=target_session)
        before = self.history_truth()
        items = self.page(draft, "preparations").items
        self.assertEqual(len(items), 3)
        self.assertEqual(len({item["session_id"] for item in items}), 3)
        self.assertEqual({(item["association"]["source_linked"], item["association"]["has_target_plan"]) for item in items},
                         {(True, False), (False, True), (True, True)})
        for item in items:
            self.assertEqual(set(item), {"session_id", "created_at", "session_status", "association", "source",
                "target_plans_present", "target_execution_requests_present", "target_attempts_present"})
            self.assertEqual(item["target_plans_present"], item["association"]["has_target_plan"])
            self.assertFalse(item["target_attempts_present"])
            self.assertFalse(item["target_execution_requests_present"])
            self.assertEqual(item["source"], UNAVAILABLE if item["session_id"] == target_session else
                {"state": "saved", "draft_id": draft.draft_id, "revision": draft.revision, "graph_id": draft.graph_id})
        self.assertEqual(self.history_truth(), before)

    def test_each_attempt_qualifies_its_own_plan_and_presence_ignores_sibling_targets(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        workspace = self.workspace()
        sibling = self.clone_plan(plan, plan_id="unrelated-plan", desired_graph_id=workspace.current_graph_id,
            desired_realized_projection_id=workspace.current_realized_projection_id, plan=ActivityPlan(()))
        self.add_attempt(sibling, "unrelated")
        self.assertEqual(self.page(draft, "attempts").items, ())
        item = self.page(draft, "preparations").items[0]
        self.assertFalse(item["target_attempts_present"])
        self.assertFalse(item["target_execution_requests_present"])
        self.assertFalse(self.detail(draft)["history"]["attempts_present"])
        target = self.add_attempt(plan, "target")
        attempts = self.page(draft, "attempts").items
        self.assertEqual([item["run_id"] for item in attempts], [target.run_id])
        self.assertEqual(set(attempts[0]), {"session_id", "plan_id", "request_id", "run_id", "prior_run_id",
            "attempt", "created_at", "status", "association", "source", "plan", "advancement"})
        self.assertEqual(set(attempts[0]["plan"]), {"base_graph_id", "base_realized_projection_id",
            "desired_graph_id", "desired_realized_projection_id", "desired_graph_revision"})
        self.assertEqual(attempts[0]["association"], {"source_linked": True, "target_graph_matches": True})
        self.assertEqual(attempts[0]["source"]["state"], "saved")
        self.assertEqual(attempts[0]["advancement"], {"state": "none-recorded", "receipt": None})
        self.assertTrue(self.detail(draft)["history"]["attempts_present"])

    def test_exact_target_with_other_revision_source_stays_visible_but_unavailable(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        later = self.catalogue().execute(self.revise_command(draft))
        self.connection.execute("UPDATE cpk_saved_preparation_sources SET revision=%s WHERE session_id=%s",
                                (later.revision, plan.session_id))
        self.add_attempt(plan, "target")
        row = self.page(draft, "attempts").items[0]
        self.assertEqual(row["association"], {"source_linked": False, "target_graph_matches": True})
        self.assertEqual(row["source"], UNAVAILABLE)
        self.assertEqual(self.page(later, "attempts").items, ())
        self.assertTrue(self.detail(later)["history"]["preparations_present"])
        self.assertFalse(self.detail(later)["history"]["attempts_present"])

    def test_valid_session_start_commitment_still_requires_every_actual_plan_fence(self):
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        original_plan = self.plan(result)
        metadata = dict(self.prepared_session(result).metadata)
        changes = {"current_graph_id": "different-graph", "current_projection_id": "different-current",
                   "desired_projection_id": "different-desired", "desired_generation": "999"}
        for field, value in changes.items():
            session = self.operations().execute(StartOperationSession("workspace-a", "operator-a", "Fence witness",
                IdempotencyKey("fence-" + field), {**metadata, "deployment_prepare_saved_" + field: value})).session
            with self.unit_of_work() as uow:
                uow.stores.saved_preparation_sources.insert(SavedPreparationSourceRecord(
                    session.session_id, "workspace-a", draft.draft_id, draft.revision))
                uow.commit()
            plan = self.clone_plan(original_plan, plan_id="fence-" + field, session_id=session.session_id)
            self.add_attempt(plan, field)
        rows = self.page(draft, "attempts").items
        self.assertEqual(len(rows), len(changes))
        for row in rows:
            self.assertEqual(row["association"], {"source_linked": True, "target_graph_matches": True})
            self.assertEqual(row["source"], UNAVAILABLE)

    def test_missing_malformed_oversized_source_and_start_evidence_are_unavailable_without_repair(self):
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        plan = self.plan(result)
        self.add_attempt(plan, "target")
        session = self.prepared_session(result)
        original = dict(session.metadata)
        for metadata in ({}, {"deployment_prepare_saved_unknown": "orphan"},
                         {**original, "extra": "private-canary"},
                         {**original, "deployment_prepare_saved_draft_id": "private-canary" * 6000}):
            self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s",
                                    (Jsonb(metadata), plan.session_id))
            before = self.history_truth()
            for kind in ("preparations", "attempts"):
                row = self.page(draft, kind).items[0]
                self.assertEqual(row["source"], UNAVAILABLE)
                self.assertNotIn("private-canary", repr(row))
            self.assertEqual(self.history_truth(), before)
        self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s",
                                (Jsonb(original), plan.session_id))
        self.connection.execute("UPDATE cpk_operation_actions SET actor_id='wrong' WHERE session_id=%s AND ordinal=1",
                                (plan.session_id,))
        self.assertEqual(self.page(draft, "attempts").items[0]["source"], UNAVAILABLE)
        self.connection.execute("DELETE FROM cpk_saved_preparation_sources WHERE session_id=%s", (plan.session_id,))
        self.assertEqual(self.page(draft, "attempts").items[0]["source"], UNAVAILABLE)

    def test_all_recorded_run_statuses_remain_visible_without_optimistic_advancement(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        for status in ActivityRunStatus:
            self.add_attempt(plan, status.value.replace("_", "-"), status=status)
        rows = self.page(draft, "attempts").items
        self.assertEqual({row["status"] for row in rows}, {status.value for status in ActivityRunStatus})
        for row in rows:
            self.assertEqual(row["advancement"], {"state": "none-recorded", "receipt": None})
            self.assertNotIn("uncertain", row)
            self.assertEqual(row["plan"]["desired_graph_revision"], plan.desired_graph_revision)

    def test_tombstoned_and_old_head_history_remains_readable(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        self.add_attempt(plan, "old")
        later = self.catalogue().execute(self.revise_command(draft))
        # Retained historical fixture; the normal delete command rejects referenced drafts.
        self.connection.execute("UPDATE cpk_desired_topology_drafts SET deleted_by='operator-a', "
            "deleted_at='2026-09-07T00:00:00Z' WHERE workspace_id='workspace-a' AND draft_id=%s", (draft.draft_id,))
        self.assertEqual(self.page(draft, "attempts").items[0]["source"]["revision"], 1)
        self.assertEqual(self.page(later, "attempts").items, ())
        self.assertTrue(self.detail(draft)["history"]["attempts_present"])

    def test_attempt_retry_identity_matches_its_existing_predecessor(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        first = self.add_attempt(plan, "first")
        second = replace(first, run_id="run-second", retry=RetryIdentity(2, first.run_id))
        with self.unit_of_work() as uow:
            uow.stores.execution.add_run(second)
            uow.commit()
        rows = self.page(draft, "attempts").items
        self.assertEqual([(row["run_id"], row["prior_run_id"], row["attempt"]) for row in rows],
                         [("run-first", None, 1), ("run-second", "run-first", 2)])
        self.connection.execute("UPDATE cpk_activity_runs SET attempt=3 WHERE run_id='run-second'")
        with self.assertRaises(ValueError):
            self.page(draft, "attempts")
