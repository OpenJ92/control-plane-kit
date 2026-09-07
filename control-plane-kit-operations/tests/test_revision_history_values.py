"""#1773 strengthened closed paging and canonical owner-descriptor laws."""
import json
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_operations.read_pages import (
    ReadCollection, ReadPage, ReadPageCandidate, ReadPageError, ReadPageRequest,
    TemporalReadCursor, WorkspaceReadScope, read_cursor_from_mapping,
)


class RevisionHistoryValueTests(unittest.TestCase):
    def setUp(self):
        self.scope_type = getattr(operations, "RevisionReadScope", None)
        self.assertIsNotNone(self.scope_type, "missing revision history scope")
        self.collections = tuple(getattr(ReadCollection, "DESIRED_TOPOLOGY_DRAFT_REVISION_" + suffix, None)
                                 for suffix in ("PREPARATIONS", "ATTEMPTS"))
        self.assertNotIn(None, self.collections, "missing revision history collections")
        self.scope = self.scope_type("workspace-a", "draft-a", 1)

    def test_revision_scope_and_cursor_are_closed_and_collection_specific(self):
        for field in ("workspace_id", "draft_id"):
            for value in (None, True, 1, "", "bad\nidentifier", "bad\x00identifier", "x" * 513, "\U0001f680" * 513):
                values = {"workspace_id": "workspace-a", "draft_id": "draft-a", "revision": 1, field: value}
                with self.subTest(field=field, value=value), self.assertRaises(ReadPageError):
                    self.scope_type(**values)
            values = {"workspace_id": "workspace-a", "draft_id": "draft-a", "revision": 1, field: "\U0001f680" * 512}
            self.assertEqual(getattr(self.scope_type(**values), field), values[field])
        for revision in (True, 0, -1, 2**63, "1"):
            with self.subTest(revision=revision), self.assertRaises(ReadPageError):
                self.scope_type("workspace-a", "draft-a", revision)
        for collection in self.collections:
            cursor = TemporalReadCursor(collection, self.scope, "2026-09-07T00:00:00.000000Z", "id-a")
            self.assertEqual(read_cursor_from_mapping(cursor.descriptor()), cursor)
            for key, value in (("workspace_id", "foreign"), ("draft_id", "other"), ("revision", 2)):
                mapping = cursor.descriptor()
                mapping["scope"][key] = value
                with self.subTest(key=key), self.assertRaises(ReadPageError):
                    ReadPageRequest(collection, self.scope, 10, read_cursor_from_mapping(mapping))
            for key in ("workspace_id", "draft_id", "revision"):
                mapping = cursor.descriptor()
                del mapping["scope"][key]
                with self.assertRaises(ReadPageError):
                    read_cursor_from_mapping(mapping)
            mapping = cursor.descriptor()
            mapping["scope"]["extra"] = "private-canary"
            with self.assertRaises(ReadPageError):
                read_cursor_from_mapping(mapping)
            other = self.collections[1] if collection is self.collections[0] else self.collections[0]
            with self.assertRaises(ReadPageError):
                ReadPageRequest(other, self.scope, 10, cursor)

    def test_new_maximum_is_ten_and_existing_hundred_limit_survives(self):
        for collection in self.collections:
            for limit in (1, 10):
                self.assertEqual(ReadPageRequest(collection, self.scope, limit).limit, limit)
            for limit in (0, True, 11, 100):
                with self.subTest(collection=collection, limit=limit), self.assertRaises(ReadPageError):
                    ReadPageRequest(collection, self.scope, limit)
        self.assertEqual(ReadPageRequest(ReadCollection.ACTIVITY_SESSIONS,
                                        WorkspaceReadScope("workspace-a"), 100).limit, 100)

    def test_lookahead_cursor_is_last_exposed_and_hidden_coordinates_are_validated(self):
        collection = self.collections[0]
        request = ReadPageRequest(collection, self.scope, 1)
        first = TemporalReadCursor(collection, self.scope, "2026-09-07T00:00:00.000000Z", "a")
        second = TemporalReadCursor(collection, self.scope, "2026-09-07T00:00:00.000000Z", "b")
        page = ReadPage.from_candidates(request, (ReadPageCandidate({"session_id": "a"}, first),
                                                  ReadPageCandidate({"session_id": "b"}, second)))
        self.assertEqual(page.next_cursor, first)
        wrong = TemporalReadCursor(collection, self.scope_type("workspace-a", "other", 1),
                                   "2026-09-07T00:00:00.000000Z", "b")
        with self.assertRaises(ReadPageError):
            ReadPage.from_candidates(request, (ReadPageCandidate({}, first), ReadPageCandidate({}, wrong)))

    def test_canonical_unicode_owner_descriptor_is_bounded_and_guarded(self):
        collection = self.collections[1]
        wide = "\U0001f680" * 512
        scope = self.scope_type(wide, wide, 2**63 - 1)
        request = ReadPageRequest(collection, scope, 10)
        item = {"session_id": wide, "plan_id": wide, "request_id": wide,
                "run_id": "r" * 200, "prior_run_id": "p" * 200, "attempt": 2**31 - 1,
                "created_at": "2026-09-07T00:00:00.000000Z", "status": "succeeded",
                "association": {"source_linked": True, "target_graph_matches": True},
                "source": {"state": "saved", "draft_id": wide, "revision": 2**63 - 1, "graph_id": wide},
                "plan": {"base_graph_id": wide, "base_realized_projection_id": wide,
                         "desired_graph_id": wide, "desired_realized_projection_id": wide,
                         "desired_graph_revision": 2**63 - 1},
                "advancement": {"state": "accepted", "receipt": {"event_id": wide, "action_id": wide,
                    "occurred_at": "2026-09-07T00:00:00.000000Z", "to_realized_projection_digest": "a" * 64}}}
        candidates = tuple(ReadPageCandidate({**item, "run_id": "r" * 197 + f"{index:03}"},
            TemporalReadCursor(collection, scope, "2026-09-07T00:00:00.000000Z", "r" * 197 + f"{index:03}"))
            for index in range(11))
        descriptor = ReadPage.from_candidates(request, candidates).descriptor()
        payload = json.dumps(descriptor, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertGreater(len(payload), 500000)
        self.assertLessEqual(len(payload), 1048576)
        oversized = ReadPage.from_candidates(request, (ReadPageCandidate({"unexpected": wide * 200},
            TemporalReadCursor(collection, scope, "2026-09-07T00:00:00.000000Z", "run-a")),))
        with self.assertRaises(ReadPageError):
            oversized.descriptor()
