"""#1763 public factory, authorization, bounded overview and transport parity."""
import unittest

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.cpk_server import CpkServerApplicationError, cpk_server_services
from draft_catalogue_fixture import CatalogueRequest, principal
from draft_selection_fixture import DraftSelectionFixture


class DraftSelectionAdapterTests(DraftSelectionFixture, unittest.TestCase):
    def services(self, *, commands=None, unit_of_work=None):
        return cpk_server_services(unit_of_work_factory=unit_of_work or self.unit_of_work,
            planning=None, approval=None, admission=None, lifecycle=None, execution=None,
            desired_topology_drafts=commands or self.catalogue())

    def request(self, surface, route, payload, *, draft_id=None, actor="default"):
        path = {"workspace_id": "workspace-a"}
        if draft_id is not None:
            path["draft_id"] = draft_id
        if surface == "mcp":
            payload = {**path, **payload}
            path = {}
        role = ControlPlaneServiceRole.READS if route.startswith("read.") else ControlPlaneServiceRole.PLANNING
        return CatalogueRequest(surface, route, role, path, payload,
                                principal() if actor == "default" else actor)

    def payload(self, command):
        return {key: (value.value if key == "idempotency_key" else value)
                for key, value in vars(command).items() if key not in {"context", "draft_id"}}

    def overview(self, services=None):
        reads = (services or self.services())[ControlPlaneServiceRole.READS]
        http = reads.handle(self.request("http", "read.operator-overview", {}))
        mcp = reads.handle(self.request("mcp", "read.operator-overview", {}))
        self.assertEqual(http, mcp)
        return http

    def test_public_factory_select_and_delete_have_identical_http_mcp_action_results(self):
        first = self.create()
        services = self.services()
        planning = services[ControlPlaneServiceRole.PLANNING]
        selection = self.select_command(first)
        for command, verb in ((selection, "select"), (self.delete_command(first), "delete")):
            if verb == "delete":
                self.restore_legacy_desired()
            payload = {**self.payload(command), "actor_id": "forged", "actor_scopes": []}
            responses = [planning.handle(self.request(surface, f"command.desired-topology-draft.{verb}",
                         payload, draft_id=first.draft_id)) for surface in ("http", "mcp")]
            self.assertEqual(responses[0], responses[1])
            action = [row[0] for row in self.rows("cpk_operation_actions")
                      if row[0]["action_type"] == f"{verb}-desired-topology-draft"]
            self.assertEqual(len(action), 1)
            self.assertEqual(action[0]["actor_id"], "operator-a")
            self.assertEqual(action[0]["payload"], responses[0])
            self.assertNotIn("forged", repr(responses))

    def test_authorization_precedes_service_or_store_access_for_both_commands_and_overview(self):
        test = self
        class ForbiddenCommands:
            def execute(self, command):
                test.fail("denied command reached service")
        def forbidden_uow():
            self.fail("denied request opened transaction")
        services = self.services(commands=ForbiddenCommands(), unit_of_work=forbidden_uow)
        for surface in ("http", "mcp"):
            for route in ("command.desired-topology-draft.select", "command.desired-topology-draft.delete",
                          "read.operator-overview"):
                for actor_label, actor in (("missing", None), ("foreign-workspace", principal("workspace-b")),
                                           ("missing-scope", principal(scopes=()))):
                    payload = {} if route.startswith("read.") else {"actor_scopes": [s.value for s in PolicyScope]}
                    request = self.request(surface, route, payload,
                                           draft_id=None if route.startswith("read.") else "draft", actor=actor)
                    with self.subTest(surface=surface, route=route, actor=actor_label):
                        with self.assertRaises(CpkServerApplicationError) as error:
                            services[request.service_role].handle(request)
                        self.assertEqual(error.exception.status, 403)
            # Closed overview arguments reject forged authority before any transaction.
            request = self.request(surface, "read.operator-overview",
                                   {"actor_scopes": [s.value for s in PolicyScope]})
            with self.subTest(surface=surface, route="read.operator-overview", actor="forged-scopes"):
                with self.assertRaises(CpkServerApplicationError) as error:
                    services[request.service_role].handle(request)
                self.assertEqual(error.exception.status, 400)

    def test_missing_or_malformed_fences_and_stale_conflicts_match_bounded_http_mcp_errors(self):
        first = self.create()
        planning = self.services()[ControlPlaneServiceRole.PLANNING]
        select = self.payload(self.select_command(first))
        delete = self.payload(self.delete_command(first))
        cases = [
            ("select", {**select, "revision": True}, 400),
            ("select", {**select, "expected_desired_graph_revision": "1"}, 400),
            ("select", {**select, "expected_desired_graph_revision": 0}, 409),
            ("select", {key: value for key, value in select.items() if key != "expected_desired_graph_id"}, 400),
            ("select", {key: value for key, value in select.items() if key != "expected_desired_realized_projection_id"}, 400),
            ("select", {key: value for key, value in select.items() if key != "expected_desired_graph_revision"}, 400),
            ("delete", {**delete, "expected_head_revision": 2}, 409),
            ("delete", {**delete, "expected_head_revision": True}, 400),
            ("delete", {**delete, "idempotency_key": "private-marker" * 20}, 400),
        ]
        for verb, payload, status in cases:
            before = self.truth()
            errors = []
            for surface in ("http", "mcp"):
                with self.subTest(verb=verb, surface=surface), self.assertRaises(CpkServerApplicationError) as error:
                    planning.handle(self.request(surface, f"command.desired-topology-draft.{verb}",
                                                 payload, draft_id=first.draft_id))
                self.assertEqual(error.exception.status, status)
                self.assertLessEqual(len(str(error.exception)), 512)
                self.assertNotIn("private-marker", str(error.exception))
                errors.append(error.exception.descriptor())
            self.assertEqual(errors[0], errors[1])
            self.assertEqual(self.truth(), before)

    def test_overview_legacy_none_and_selected_old_revision_have_exact_distinct_head_coordinates(self):
        legacy = self.overview()
        self.assertEqual(legacy["graphs"]["desired"]["draft"],
                         {"state": "none", "selected": None, "head": None})
        first = self.create(title="private-title")
        self.catalogue().execute(self.select_command(first))
        second = self.catalogue().execute(self.revise_command(first))
        before = self.truth()
        overview = self.overview()
        self.assertEqual(overview["graphs"]["desired"]["draft"], {
            "state": "selected",
            "selected": {"draft_id": first.draft_id, "revision": 1, "graph_id": first.graph_id},
            "head": {"draft_id": first.draft_id, "revision": 2, "graph_id": second.graph_id},
        })
        self.assertEqual(set(overview), set(legacy))
        self.assertEqual(set(overview["history"]), set(legacy["history"]))
        self.assertNotIn("private-title", repr(overview))
        self.assertNotIn("graph_descriptor", repr(overview))
        self.assertEqual(self.truth(), before)
        with self.assertRaises(CpkServerApplicationError) as error:
            self.read("read.operator-overview", after={"format_version": 1,
                "collection": "desired-topology-drafts", "scope": {"workspace_id": "workspace-a"},
                "position": {"instant": "2026-09-06T18:00:00Z", "item_id": first.draft_id}})
        self.assertEqual(error.exception.status, 400)

    def test_overview_tombstoned_or_malformed_mapped_draft_is_unavailable_without_partial_coordinates(self):
        first = self.create()
        self.catalogue().execute(self.select_command(first))
        for assignment in ("deleted_by='operator-a', deleted_at='2026-09-06T18:00:00Z'",
                           "deleted_by=NULL, deleted_at=NULL, title=E'private-marker\\n'"):
            self.connection.execute("UPDATE cpk_desired_topology_drafts SET " + assignment + " WHERE draft_id=%s",
                                    (first.draft_id,))
            before = self.truth()
            overview = self.overview()
            self.assertEqual(overview["graphs"]["desired"]["draft"],
                             {"state": "unavailable", "selected": None, "head": None})
            self.assertNotIn("private-marker", repr(overview))
            self.assertEqual(self.truth(), before)

    def test_overview_rereads_draft_head_anchor_after_concurrent_revision(self):
        first = self.create()
        self.catalogue().execute(self.select_command(first))
        changed = False
        def revise_after_head_read(statement):
            nonlocal changed
            if not changed and "FROM cpk_desired_topology_drafts" in statement:
                changed = True
                self.catalogue().execute(self.revise_command(first))
        services = self.services(unit_of_work=self.observed_uow([], after_execute=revise_after_head_read))
        result = services[ControlPlaneServiceRole.READS].handle(
            self.request("http", "read.operator-overview", {}))
        self.assertTrue(changed, "overview did not resolve its selected draft head")
        self.assertEqual(result["graphs"]["desired"]["draft"],
                         {"state": "unavailable", "selected": None, "head": None})
        fresh = self.overview()
        self.assertEqual(fresh["graphs"]["desired"]["draft"]["selected"]["revision"], 1)
        self.assertEqual(fresh["graphs"]["desired"]["draft"]["head"]["revision"], 2)
