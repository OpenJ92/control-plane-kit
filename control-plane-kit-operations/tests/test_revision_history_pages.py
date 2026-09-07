"""#1773 bounded independent traversal and representative real query evidence."""
import json
import unittest

from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.workflows import IdempotencyKey, StartOperationSession
from revision_history_fixture import RevisionHistoryFixture
from draft_catalogue_fixture import NOW


class RevisionHistoryPageTests(RevisionHistoryFixture, unittest.TestCase):
    def test_oversized_hidden_candidate_identity_fails_the_whole_page_without_leak(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        oversized = "\U0001f680" * 513
        self.connection.execute("INSERT INTO cpk_operation_sessions "
            "(session_id,workspace_id,actor_id,title,status,created_at) "
            "VALUES (%s,'workspace-a','operator-a','Hidden','open',%s)", (oversized, NOW))
        self.clone_plan(plan, plan_id="hidden-plan", session_id=oversized)
        before = self.history_truth()
        with self.assertRaises(ValueError) as captured:
            self.page(draft, "preparations", limit=1)
        self.assertLess(len(str(captured.exception)), 256)
        self.assertNotIn(oversized, str(captured.exception))
        self.assertEqual(self.history_truth(), before)

    def test_both_collections_traverse_201_tied_rows_without_duplicates_or_false_absence(self):
        draft = self.selected()
        original = self.plan(self.program().prepare(self.prepare_command(draft)))
        session_ids = [original.session_id]
        run_ids = [self.add_attempt(original, "000").run_id]
        for index in range(1, 201):
            session_id = self.start_session("workspace-a")
            plan = self.clone_plan(original, plan_id=f"page-plan-{index:03}", session_id=session_id)
            session_ids.append(session_id)
            run_ids.append(self.add_attempt(plan, f"{index:03}").run_id)
        for kind, identity, expected in (("preparations", "session_id", session_ids), ("attempts", "run_id", run_ids)):
            cursor, actual = None, []
            sizes = []
            while True:
                page = self.page(draft, kind, cursor=cursor)
                actual.extend(item[identity] for item in page.items)
                sizes.append(len(page.items))
                if page.next_cursor is None:
                    break
                self.assertEqual(page.next_cursor.item_id, page.items[-1][identity])
                self.assertEqual(page.next_cursor.scope, self.scope_type("workspace-a", draft.draft_id, 1))
                cursor = page.next_cursor
            self.assertEqual(sizes, [10] * 20 + [1])
            self.assertEqual(actual, sorted(expected))
            self.assertEqual(len(actual), len(set(actual)))
            # A later empty page cannot undo the complete relation's existence facts.
            last = type(cursor)(cursor.collection, cursor.scope, cursor.instant, actual[-1])
            self.assertEqual(self.page(draft, kind, cursor=last).items, ())
            history = self.detail(draft)["history"]
            self.assertTrue(history["preparations_present"])
            self.assertTrue(history["attempts_present"])
        before = self.history_truth()
        for kind in ("preparations", "attempts"):
            direct = self.page(draft, kind).descriptor()
            self.assertEqual(self.service_page(draft, kind), direct)
            self.assertEqual(self.wire_page(draft, kind), direct)
            self.assertEqual(self.wire_page(draft, kind, surface="mcp"), direct)
        self.assertEqual(self.history_truth(), before)

    def test_later_commits_follow_cursor_membership_without_snapshot_claim(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        self.add_attempt(plan, "a")
        self.add_attempt(plan, "c")
        first = self.page(draft, "attempts", limit=1)
        self.add_attempt(plan, "0")
        self.add_attempt(plan, "b")
        continued = self.page(draft, "attempts", cursor=first.next_cursor)
        self.assertEqual([item["run_id"] for item in continued.items], ["run-b", "run-c"])
        self.assertEqual([item["run_id"] for item in self.page(draft, "attempts").items],
                         ["run-0", "run-a", "run-b", "run-c"])
        # Preparation chronology has the same fresh-traversal limitation.
        second_session = self.start_session("workspace-a")
        self.clone_plan(plan, plan_id="second-plan", session_id=second_session)
        first = self.page(draft, "preparations", limit=1)
        for label, instant in (("before", "2000-01-01T00:00:00Z"), ("after", "2099-01-01T00:00:00Z")):
            identifiers = iter(("session-" + label, "action-" + label))
            session = self.operations(clock=lambda instant=instant: instant, id_factory=lambda: next(identifiers)).execute(
                StartOperationSession("workspace-a", "operator-a", label, IdempotencyKey(label))).session
            self.clone_plan(plan, plan_id="plan-" + label, session_id=session.session_id)
        continued_ids = [item["session_id"] for item in self.page(draft, "preparations", cursor=first.next_cursor).items]
        self.assertIn("session-after", continued_ids)
        self.assertNotIn("session-before", continued_ids)
        self.assertIn("session-before", [item["session_id"] for item in self.page(draft, "preparations").items])

    def test_pages_use_bounded_set_queries_and_explain_at_representative_fanout(self):
        draft = self.selected()
        original = self.plan(self.program().prepare(self.prepare_command(draft)))
        for index in range(32):
            session = self.start_session("workspace-a")
            plan = self.clone_plan(original, plan_id=f"fanout-{index}", session_id=session)
            for attempt in range(3):
                self.add_attempt(plan, f"fanout-{index}-{attempt}")
        captured = []
        class ObservedConnection:
            def __init__(self, connection):
                self.connection = connection
            def execute(self, query, params=()):
                captured.append((query, params))
                return self.connection.execute(query, params)
            def __getattr__(self, name):
                return getattr(self.connection, name)
        for kind in ("preparations", "attempts"):
            counts = []
            with self.unit_of_work() as uow:
                connection = uow.stores.connection
                store = PostgresStoreBundle(ObservedConnection(connection)).revision_history
                for limit in (1, 10):
                    captured.clear()
                    page = store.page(self.request_page(draft, kind, limit=limit))
                    self.assertEqual(len(page.items), limit)
                    counts.append(len(captured))
                self.assertEqual(counts[0], counts[1], "query count grows with page items")
                self.assertLessEqual(counts[1], 2, "one page statement plus optional parent admission")
                summaries = []
                for query, params in captured:
                    explanation = connection.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, params).fetchone()[0][0]
                    top = explanation["Plan"]
                    self.assertLessEqual(top["Actual Rows"], 11)
                    summaries.append({"node": top["Node Type"], "rows": top["Actual Rows"],
                                      "execution_ms": explanation["Execution Time"]})
                self.assertTrue(summaries)
                print("revision-history-explain", kind, json.dumps(summaries, sort_keys=True))
