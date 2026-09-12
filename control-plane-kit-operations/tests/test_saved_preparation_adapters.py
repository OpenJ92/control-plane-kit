"""#1764 existing public factory, authenticated modes and bounded overview."""
from dataclasses import replace
import unittest

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.cpk_server import CpkServerApplicationError, cpk_server_services
from draft_catalogue_fixture import CatalogueRequest, principal
from saved_preparation_fixture import SavedPreparationFixture, SAVED_ONLY_KEYS, expected_saved_metadata


class SavedPreparationAdapterTests(SavedPreparationFixture, unittest.TestCase):
    def services(self, *, uow=None, program=None):
        operations, desired, planning, approval = self.components()
        return cpk_server_services(unit_of_work_factory=uow or self.unit_of_work,
            planning=planning, approval=approval, admission=None, lifecycle=None, execution=None,
            operations=operations, desired_graphs=desired, deployment_program=program)

    def request(self, surface, payload, *, route="command.deployment.prepare", actor="default"):
        path = {"workspace_id": "workspace-a"}
        if surface == "mcp":
            payload, path = {**path, **payload}, {}
        role = ControlPlaneServiceRole.READS if route.startswith("read.") else ControlPlaneServiceRole.PLANNING
        return CatalogueRequest(surface, route, role, path, payload, principal() if actor == "default" else actor)

    def payload(self, command):
        return {"draft_id": command.desired.draft_id, "revision": command.desired.revision,
                "expected_current": vars(command.expected_current),
                "expected_desired": vars(command.expected_desired),
                "expected_desired_graph_revision": command.expected_desired_graph_revision,
                "title": command.title, "idempotency_key": command.idempotency_key.value,
                "approval_comment": command.approval_comment}

    def overview(self, *, services=None):
        service = (services or self.services())[ControlPlaneServiceRole.READS]
        responses = [service.handle(self.request(surface, {}, route="read.operator-overview"))
                     for surface in ("http", "mcp")]
        self.assertEqual(responses[0], responses[1])
        return responses[0]

    def test_real_factory_saved_prepare_http_mcp_replay_preserves_exact_plan_and_approval(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        service = self.services()[ControlPlaneServiceRole.PLANNING]
        frozen = self.frozen_truth()
        payload = {**self.payload(command), "actor_id": "forged", "actor_scopes": []}
        first = service.handle(self.request("http", payload))
        before = self.all_truth()
        self.assertEqual(service.handle(self.request("mcp", payload)), first)
        self.assertEqual(self.all_truth(), before)
        self.assertEqual(self.frozen_truth(), frozen)
        self.assertIn("approval_request_id", first)
        self.assertNotIn("forged", repr(first))

    def test_mode_exclusivity_scalar_bounds_and_stale_errors_have_transport_parity(self):
        draft = self.selected()
        payload = self.payload(self.prepare_command(draft))
        cases = [({**payload, "desired_graph": None}, 400),
                 ({key: value for key, value in payload.items() if key != "draft_id"}, 400),
                 ({key: value for key, value in payload.items() if key != "revision"}, 400),
                 ({key: value for key, value in payload.items() if key not in {"draft_id", "revision"}}, 400),
                 ({**payload, "draft_id": "private-canary" * 100}, 400),
                 ({**payload, "revision": True}, 400),
                 ({**payload, "expected_desired": None}, 400),
                 ({**payload, "expected_desired_graph_revision": 9223372036854775808}, 400),
                 ({**payload, "expected_desired_graph_revision": payload["expected_desired_graph_revision"] + 1}, 409),
                 ({**payload, "idempotency_key": "private-canary" * 100}, 400)]
        service = self.services()[ControlPlaneServiceRole.PLANNING]
        for candidate, status in cases:
            before = self.all_truth()
            errors = []
            for surface in ("http", "mcp"):
                with self.subTest(surface=surface, status=status), self.assertRaises(CpkServerApplicationError) as error:
                    service.handle(self.request(surface, candidate))
                self.assertEqual(error.exception.status, status)
                self.assertNotIn("private-canary", str(error.exception))
                errors.append(str(error.exception))
            self.assertEqual(errors[0], errors[1])
            self.assertEqual(self.all_truth(), before)

    def test_authentication_and_both_scopes_precede_saved_program_or_store_access(self):
        test = self
        class ForbiddenProgram:
            def prepare(self, command):
                test.fail("unauthorized request reached preparation")
        def forbidden_uow():
            self.fail("unauthorized request opened UoW")
        service = self.services(uow=forbidden_uow, program=ForbiddenProgram())[ControlPlaneServiceRole.PLANNING]
        for surface in ("http", "mcp"):
            for label, actor in (("missing", None), ("foreign", principal("workspace-b")),
                                 ("edit-only", principal(scopes=(PolicyScope.INSTANCE_WORKSPACE_EDIT,))),
                                 ("plan-only", principal(scopes=(PolicyScope.PLAN_REQUEST,))),
                                 ("none", principal(scopes=()))):
                with self.subTest(surface=surface, actor=label), self.assertRaises(CpkServerApplicationError) as error:
                    service.handle(self.request(surface, {"actor_scopes": [scope.value for scope in PolicyScope]}, actor=actor))
                self.assertEqual(error.exception.status, 403)

    def test_overview_projects_prepared_old_revision_from_existing_workflow_without_cursor_change(self):
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        self.catalogue().execute(self.revise_command(draft))
        overview = self.overview()
        self.assertEqual(overview["workflow"]["plan"]["plan_id"], result.reference.plan_id)
        self.assertEqual(overview["workflow"]["prepared_draft"], {
            "state": "prepared", "revision": {"draft_id": draft.draft_id, "revision": 1, "graph_id": draft.graph_id}})
        self.assertEqual(overview["graphs"]["desired"]["draft"]["head"]["revision"], 2)
        self.assertEqual(overview["history"], {"state": "none", "run_id": None, "items": [], "next_cursor": None})
        self.assertEqual(overview["next_action"]["operation_id"], "command.approval.decide")
        self.assertNotIn("Prepare saved draft", repr(overview))
        self.assertNotIn("deployment_prepare_intent_sha256", repr(overview))

    def test_overview_requires_congruent_relational_saved_source_without_repair(self):
        self.assertIsNotNone(self.connection.execute(
            "SELECT to_regclass('cpk_saved_preparation_sources')").fetchone()[0],
            "missing saved-preparation source relation")
        draft = self.selected()
        result = self.program().prepare(self.prepare_command(draft))
        session = self.prepared_session(result)
        later = self.catalogue().execute(self.revise_command(draft))
        self.assertEqual(self.overview()["workflow"]["prepared_draft"]["state"], "prepared")
        self.connection.execute("UPDATE cpk_saved_preparation_sources SET revision=%s WHERE session_id=%s",
                                (later.revision, session.session_id))
        for state in ("wrong-revision", "missing"):
            if state == "missing":
                self.connection.execute("DELETE FROM cpk_saved_preparation_sources WHERE session_id=%s",
                                        (session.session_id,))
            before = self.all_truth(), self.rows("cpk_saved_preparation_sources")
            with self.subTest(state=state):
                self.assertEqual(self.overview()["workflow"]["prepared_draft"],
                                 {"state": "unavailable", "revision": None})
            self.assertEqual((self.all_truth(), self.rows("cpk_saved_preparation_sources")), before)

    def test_overview_closed_saved_discriminator_rejects_partial_false_or_extra_evidence(self):
        draft = self.selected()
        command = self.prepare_command(draft)
        result = self.program().prepare(command)
        session = self.prepared_session(result)
        original = dict(session.metadata)
        self.assertEqual(original, expected_saved_metadata(command))
        cases = [("wrong-marker", {**original, "deployment_prepare_source": "unknown"}),
                 ("marker-only", {"deployment_prepare_source": "saved-revision.v1"}),
                 ("missing-field", {key: value for key, value in original.items() if key != "deployment_prepare_saved_revision"}),
                 ("extra-field", {**original, "deployment_prepare_saved_unknown": "private-canary"}),
                 ("false-revision", {**original, "deployment_prepare_saved_revision": "999"}),
                 ("orphan-saved-key", {"deployment_prepare_saved_draft_id": draft.draft_id}),
                 ("missing-marker", {key: value for key, value in original.items() if key != "deployment_prepare_source"})]
        cases.extend(("orphan-" + key, {key: original[key]}) for key in sorted(SAVED_ONLY_KEYS))
        cases.extend(("missing-" + key, {name: value for name, value in original.items() if name != key})
                     for key in sorted(SAVED_ONLY_KEYS | {"deployment_prepare_intent_sha256"}))
        cases.extend((label, {**original, key: value}) for label, key, value in (
            ("digest-format", "deployment_prepare_intent_sha256", "invalid"),
            ("noncanonical-revision", "deployment_prepare_saved_revision", "01"),
            ("noncanonical-generation", "deployment_prepare_saved_desired_generation", "01"),
            ("false-current-graph", "deployment_prepare_saved_current_graph_id", "workspace-b-current"),
            ("false-current-projection", "deployment_prepare_saved_current_projection_id", "missing"),
            ("false-desired-projection", "deployment_prepare_saved_desired_projection_id", "missing"),
            ("false-generation", "deployment_prepare_saved_desired_generation", "999"),
            ("unreserved-extra", "extra", "private-canary")))
        for label, metadata in cases:
            self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s", (Jsonb(metadata), session.session_id))
            with self.subTest(case=label):
                projection = self.overview()["workflow"]["prepared_draft"]
                self.assertEqual(projection, {"state": "unavailable", "revision": None})
                self.assertNotIn("private-canary", repr(projection))
        # A retained source still identifies missing saved commitment evidence.
        legacy = {key: value for key, value in original.items()
                  if key not in SAVED_ONLY_KEYS and key != "deployment_prepare_source"}
        self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s", (Jsonb(legacy), session.session_id))
        before = self.all_truth(), self.rows("cpk_saved_preparation_sources")
        self.assertEqual(self.overview()["workflow"]["prepared_draft"], {"state": "unavailable", "revision": None})
        self.assertEqual((self.all_truth(), self.rows("cpk_saved_preparation_sources")), before)
        # Complete erasure of both kinds of evidence remains indistinguishable.
        self.connection.execute("DELETE FROM cpk_saved_preparation_sources WHERE session_id=%s", (session.session_id,))
        before = self.all_truth(), self.rows("cpk_saved_preparation_sources")
        self.assertEqual(self.overview()["workflow"]["prepared_draft"], {"state": "none", "revision": None})
        self.assertEqual((self.all_truth(), self.rows("cpk_saved_preparation_sources")), before)
        self.connection.execute("UPDATE cpk_operation_sessions SET metadata=%s WHERE session_id=%s", (Jsonb(original), session.session_id))
