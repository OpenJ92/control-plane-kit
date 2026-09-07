"""#1773 historical receipts originate from the existing advancement owner."""
from dataclasses import replace
import unittest

from psycopg.types.json import Jsonb

import control_plane_kit_operations as operations
import test_current_graph_advancement as advancement_fixture
from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftRecord, DesiredTopologyDraftRevisionRecord
from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest


class RevisionHistoryAdvancementTests(unittest.TestCase):
    def setUp(self):
        scope_type = getattr(operations, "RevisionReadScope", None)
        self.assertIsNotNone(scope_type, "missing revision history scope")
        self.scope = scope_type("workspace-a", "history-draft", 1)
        self.collection = getattr(ReadCollection, "DESIRED_TOPOLOGY_DRAFT_REVISION_ATTEMPTS", None)
        self.assertIsNotNone(self.collection, "missing revision history attempts")
        # Reuse fixture setup and actual command; do not inherit/recollect its tests.
        self.fixture = advancement_fixture.CurrentGraphAdvancementTests()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.setUp()
        with self.fixture.unit_of_work() as uow:
            self.assertIsNotNone(getattr(uow.stores, "revision_history", None), "missing revision history owner")
            store = uow.stores.desired_topology_drafts
            store.create(DesiredTopologyDraftRecord("workspace-a", "history-draft", "History", 1,
                                                   "operator-a", "2026-07-22T12:00:00Z"))
            store.append(DesiredTopologyDraftRevisionRecord("workspace-a", "history-draft", 1,
                "graph-desired", "operator-a", "2026-07-22T12:00:00Z"), expected_head_revision=None)
            uow.commit()
        self.fixture.seed_succeeded_run()

    def page(self):
        with self.fixture.unit_of_work() as uow:
            return uow.stores.revision_history.page(ReadPageRequest(self.collection, self.scope, 10)).descriptor()

    def accepted(self):
        return self.fixture.service("event-advance", "action-advance").execute(self.fixture.command())

    def test_success_without_receipt_is_none_recorded_then_real_acceptance_survives_pointer_change(self):
        row = self.page()["items"][0]
        self.assertEqual(row["advancement"], {"state": "none-recorded", "receipt": None})
        self.assertEqual(row["source"]["state"], "unavailable")
        result = self.accepted()
        accepted = self.page()["items"][0]["advancement"]
        self.assertEqual(accepted, {"state": "accepted", "receipt": {
            "event_id": result.event.event_id, "action_id": result.action.action_id,
            "occurred_at": "2026-07-22T13:05:00.000000Z",
            "to_realized_projection_digest": self.fixture.desired_projection.projection_digest}})
        with self.fixture.unit_of_work() as uow:
            uow.stores.workspaces.set_current_graph("workspace-a", "graph-current")
            uow.commit()
        self.assertEqual(self.page()["items"][0]["advancement"], accepted)

    def test_event_without_action_is_unavailable(self):
        self.accepted()
        self.fixture.connection.execute("DELETE FROM cpk_operation_actions WHERE action_id='action-advance'")
        self.assertEqual(self.page()["items"][0]["advancement"], {"state": "unavailable", "receipt": None})

    def test_action_without_event_is_unavailable(self):
        self.accepted()
        self.fixture.connection.execute("DELETE FROM cpk_activity_events WHERE event_id='event-advance'")
        self.assertEqual(self.page()["items"][0]["advancement"], {"state": "unavailable", "receipt": None})

    def test_duplicate_candidates_on_either_side_are_not_arbitrarily_selected(self):
        result = self.accepted()
        with self.fixture.unit_of_work() as uow:
            uow.stores.execution.add_event(replace(result.event, event_id="event-duplicate",
                ordinal=uow.stores.execution.next_event_ordinal("run-a")))
            uow.commit()
        self.assertEqual(self.page()["items"][0]["advancement"]["state"], "unavailable")
        self.fixture.connection.execute("DELETE FROM cpk_activity_events WHERE event_id='event-duplicate'")
        with self.fixture.unit_of_work() as uow:
            uow.stores.activity_history.add_action(replace(result.action, action_id="action-duplicate",
                ordinal=uow.stores.activity_history.next_action_ordinal("session-a"), idempotency_key="duplicate"))
            uow.commit()
        self.assertEqual(self.page()["items"][0]["advancement"]["state"], "unavailable")

    def test_false_request_plan_transition_time_and_oversized_evidence_fail_closed_without_writes(self):
        result = self.accepted()
        original = dict(result.action.payload)
        for key, value in (("execution_request_id", "other-request"), ("plan_id", "other-plan"),
                           ("run_id", "other-run"), ("claim_generation", 0),
                           ("event_id", "other-event"), ("workspace_id", "other-workspace"),
                           ("from_authored_graph_id", "other-base"),
                           ("from_realized_projection_id", "other-base-projection"),
                           ("to_authored_graph_id", "other-desired-graph"),
                           ("to_realized_projection_id", "other-desired-projection"),
                           ("to_realized_projection_digest", "0" * 64),
                           ("desired_graph_revision", 999), ("extra", "private-canary" * 7000)):
            payload = {**original, key: value}
            self.fixture.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id='action-advance'",
                                            (Jsonb(payload),))
            with self.subTest(field=key):
                descriptor = self.page()
                self.assertEqual(descriptor["items"][0]["advancement"], {"state": "unavailable", "receipt": None})
                self.assertNotIn("private-canary", repr(descriptor))
                self.assertEqual(self.fixture.connection.execute(
                    "SELECT payload FROM cpk_operation_actions WHERE action_id='action-advance'").fetchone()[0], payload)
        self.fixture.connection.execute("UPDATE cpk_operation_actions SET payload=%s, created_at='2026-07-22T13:06:00Z' "
                                        "WHERE action_id='action-advance'", (Jsonb(original),))
        self.assertEqual(self.page()["items"][0]["advancement"]["state"], "unavailable")
        self.fixture.connection.execute("UPDATE cpk_operation_actions SET created_at='2026-07-22T13:05:00Z' "
                                        "WHERE action_id='action-advance'")
        raw = self.fixture.connection.execute("SELECT payload FROM cpk_activity_events WHERE event_id='event-advance'").fetchone()[0]
        for key, value in (("workspace_id", "other-workspace"), ("run_id", "other-run"),
                           ("plan_id", "other-plan"), ("from_authored_graph_id", "other-base"),
                           ("from_realized_projection_id", "other-base-projection"),
                           ("to_authored_graph_id", "other-desired-graph"),
                           ("to_realized_projection_id", "other-desired-projection"),
                           ("to_realized_projection_digest", "0" * 64), ("desired_graph_revision", 999)):
            payload = {**raw, "evidence": {**raw["evidence"], key: value}}
            self.fixture.connection.execute("UPDATE cpk_activity_events SET payload=%s WHERE event_id='event-advance'",
                                            (Jsonb(payload),))
            with self.subTest(event_field=key):
                self.assertEqual(self.page()["items"][0]["advancement"], {"state": "unavailable", "receipt": None})
                self.assertEqual(self.fixture.connection.execute(
                    "SELECT payload FROM cpk_activity_events WHERE event_id='event-advance'").fetchone()[0], payload)
        self.fixture.connection.execute("UPDATE cpk_activity_events SET payload=%s, occurred_at='2026-07-22T13:06:00Z' "
                                        "WHERE event_id='event-advance'", (Jsonb(raw),))
        self.assertEqual(self.page()["items"][0]["advancement"], {"state": "unavailable", "receipt": None})
        self.fixture.connection.execute("UPDATE cpk_activity_events SET occurred_at='2026-07-22T13:05:00Z' "
                                        "WHERE event_id='event-advance'")
        self.fixture.connection.execute("UPDATE cpk_activity_events SET payload=%s WHERE event_id='event-advance'",
                                        (Jsonb({**raw, "extra": "private-canary" * 7000}),))
        self.assertEqual(self.page()["items"][0]["advancement"]["state"], "unavailable")

    def test_receipt_probes_have_the_reviewed_partial_index_keys_and_predicates(self):
        definitions = dict(self.fixture.connection.execute("SELECT indexname,indexdef FROM pg_indexes "
                                                           "WHERE schemaname=current_schema()").fetchall())
        names = ("cpk_activity_events_current_graph_advancement", "cpk_operation_actions_current_graph_advancement")
        for name in names:
            self.assertIn(name, definitions, "missing bounded receipt lookup index")
        event = definitions[names[0]]
        action = definitions[names[1]]
        self.assertIn("(run_id, event_id)", event)
        self.assertIn("event_type = 'current_graph_advanced'", event)
        self.assertIn("session_id,", action)
        self.assertIn("payload ->> 'run_id'", action)
        self.assertIn("action_id)", action)
        self.assertIn("action_type = 'advance-current-graph'", action)
