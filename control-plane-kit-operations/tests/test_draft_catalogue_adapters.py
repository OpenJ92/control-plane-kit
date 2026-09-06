"""HTTP/MCP mapping and trusted-auth boundaries; catalogue laws live elsewhere."""

import unittest

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError, CpkServerReadService,
)

from draft_catalogue_fixture import CatalogueRequest, DraftCatalogueFixture, principal


class DraftCatalogueAdapterTests(DraftCatalogueFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.require_catalogue_interface()
        self.require_catalogue_relations()

    def test_create_revise_and_all_three_reads_share_http_mcp_results(self):
        service = self.planning_adapter(self.catalogue())
        before = self.runtime_truth()
        payload = {
            "session_id": self.sessions["workspace-a"], "title": "Draft A",
            "graph": DEFAULT_GRAPH_CODEC.encode(self.graph()), "idempotency_key": "create",
            "actor_id": "forged-actor", "actor_scopes": [],
        }
        created = []
        for surface in ("http", "mcp"):
            created.append(service.handle(self.request(surface, "command.desired-topology-draft.create", payload)))
        for field in ("workspace_id", "draft_id", "revision", "graph_id"):
            self.assertEqual(created[0][field], created[1][field])
        self.assertEqual(created[0]["revision"], 1)
        revised = []
        for surface in ("http", "mcp"):
            revised.append(service.handle(self.request(surface, "command.desired-topology-draft.revise", {
                "session_id": self.sessions["workspace-a"], "expected_head_revision": 1,
                "graph": DEFAULT_GRAPH_CODEC.encode(self.graph("revised")), "idempotency_key": "revise",
            }, draft_id=created[0]["draft_id"])))
        for field in ("workspace_id", "draft_id", "revision", "graph_id"):
            self.assertEqual(revised[0][field], revised[1][field])
        self.assertEqual(revised[0]["revision"], 2)
        for route, values in (
            ("read.desired-topology-drafts", {"limit": 1}),
            ("read.desired-topology-draft-revisions", {"draft_id": created[0]["draft_id"], "limit": 1}),
            ("read.desired-topology-draft-revision", {"draft_id": created[0]["draft_id"], "revision": 1}),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.read(route, surface="http", **values), self.read(route, surface="mcp", **values))
        self.assertEqual({row[0]["actor_id"] for row in self.rows("cpk_operation_actions")}, {"operator-a"})
        self.assertEqual(self.runtime_truth(), before)

    def test_denied_reads_and_commands_never_reach_store_or_command_service(self):
        test = self

        class ForbiddenCommands:
            def execute(self, command):
                test.fail("unauthorized request reached catalogue service")

        def forbidden_uow():
            self.fail("unauthorized request opened a read transaction")

        planning = self.planning_adapter(ForbiddenCommands())
        reads = CpkServerReadService(forbidden_uow)
        for surface in ("http", "mcp"):
            for route, values, service in (
                ("command.desired-topology-draft.create", {}, planning),
                ("command.desired-topology-draft.revise", {"draft_id": "draft-a"}, planning),
                ("read.desired-topology-drafts", {}, reads),
                ("read.desired-topology-draft-revisions", {"draft_id": "draft-a"}, reads),
                ("read.desired-topology-draft-revision", {"draft_id": "draft-a", "revision": 1}, reads),
            ):
                for actor in (None, principal("workspace-b"), principal(scopes=())):
                    with self.subTest(surface=surface, route=route, actor=actor):
                        request = self.request(surface, route, {"actor_scopes": [s.value for s in PolicyScope]},
                                               actor=actor, **values)
                        with self.assertRaises(CpkServerApplicationError) as error:
                            service.handle(request)
                        self.assertEqual(error.exception.status, 403)

    def test_malformed_graph_and_stale_revision_errors_are_bounded_and_match_surfaces(self):
        service = self.planning_adapter(self.catalogue())
        first = self.create()
        for route, payload, path, status in (
            ("command.desired-topology-draft.create", {"session_id": self.sessions["workspace-a"],
                "title": "Draft", "graph": {"private-marker": "do-not-echo"}, "idempotency_key": "invalid"}, {}, 400),
            ("command.desired-topology-draft.revise", {"session_id": self.sessions["workspace-a"],
                "expected_head_revision": 9, "graph": DEFAULT_GRAPH_CODEC.encode(self.graph()),
                "idempotency_key": "stale"}, {"draft_id": first.draft_id}, 409),
        ):
            responses = []
            before = self.catalogue_truth()
            for surface in ("http", "mcp"):
                with self.assertRaises(CpkServerApplicationError) as error:
                    service.handle(self.request(surface, route, payload, **path))
                self.assertEqual(error.exception.status, status)
                responses.append(error.exception.descriptor())
                self.assertNotIn("do-not-echo", str(error.exception))
                self.assertLessEqual(len(str(error.exception)), 512)
            self.assertEqual(responses[0], responses[1])
            self.assertEqual(self.catalogue_truth(), before)

    def request(self, surface, route, payload, *, actor="default", **path):
        path = {"workspace_id": "workspace-a", **path}
        if surface == "mcp":
            payload = {**path, **payload}
            path = {}
        else:
            path = {key: str(value) for key, value in path.items()}
        role = ControlPlaneServiceRole.READS if route.startswith("read.") else ControlPlaneServiceRole.PLANNING
        return CatalogueRequest(surface, route, role, path, payload, principal() if actor == "default" else actor)
