"""#1773 authenticated owner adapters; no packaged-server implementation here."""
import unittest

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_operations.cpk_server import CpkServerApplicationError, CpkServerReadService
from draft_catalogue_fixture import CatalogueRequest, principal
from revision_history_fixture import PREFIX, RevisionHistoryFixture


class RevisionHistoryAdapterTests(RevisionHistoryFixture, unittest.TestCase):
    def test_unauthorized_workspace_denial_precedes_bad_cursor_and_any_store(self):
        def forbidden():
            self.fail("unauthorized request opened unit of work")
        service = CpkServerReadService(forbidden)
        for kind in ("preparations", "attempts"):
            for surface in ("http", "mcp"):
                for actor in (None, principal("workspace-b")):
                    path = {"workspace_id": "workspace-a", "draft_id": "draft-a", "revision": "1"}
                    values = {"after": {"private-canary": "bad"}, "limit": 999}
                    if surface == "mcp":
                        values, path = {**path, **values}, {}
                    request = CatalogueRequest(surface, PREFIX + kind, ControlPlaneServiceRole.READS, path, values, actor)
                    with self.subTest(kind=kind, surface=surface), self.assertRaises(CpkServerApplicationError) as captured:
                        service.handle(request)
                    self.assertEqual(captured.exception.status, 403)
                    self.assertNotIn("private-canary", str(captured.exception))

    def test_invalid_limits_revision_and_cursor_fail_before_owner_query(self):
        def forbidden():
            self.fail("invalid paging request opened unit of work")
        service = CpkServerReadService(forbidden)
        for kind in ("preparations", "attempts"):
            for values in ({"limit": 11}, {"limit": True}, {"after": {}}, {"extra": "private-canary"}):
                request = CatalogueRequest("http", PREFIX + kind, ControlPlaneServiceRole.READS,
                    {"workspace_id": "workspace-a", "draft_id": "draft-a", "revision": "1"}, values, principal())
                with self.assertRaises(CpkServerApplicationError) as captured:
                    service.handle(request)
                self.assertEqual(captured.exception.status, 400)
            request = CatalogueRequest("http", PREFIX + kind, ControlPlaneServiceRole.READS,
                {"workspace_id": "workspace-a", "draft_id": "draft-a", "revision": "0"}, {}, principal())
            with self.assertRaises(CpkServerApplicationError) as captured:
                service.handle(request)
            self.assertEqual(captured.exception.status, 400)

    def test_default_ten_parity_and_missing_foreign_revision_are_non_disclosing(self):
        draft = self.selected()
        plan = self.plan(self.program().prepare(self.prepare_command(draft)))
        self.add_attempt(plan, "read")
        for kind in ("preparations", "attempts"):
            direct = self.page(draft, kind).descriptor()
            self.assertEqual(self.wire_page(draft, kind), direct)
            self.assertEqual(self.wire_page(draft, kind, surface="mcp"), direct)
            self.assertEqual(direct["limit"], 10)
            for workspace, revision in (("workspace-b", 1), ("workspace-a", 999)):
                with self.assertRaises(CpkServerApplicationError) as captured:
                    self.read(PREFIX + kind, workspace=workspace, draft_id=draft.draft_id, revision=revision)
                self.assertEqual(captured.exception.status, 404)
