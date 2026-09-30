"""N8 exact source association, closed authority, provenance and whole refusal."""

from dataclasses import replace
import hashlib
import json
import unittest

from psycopg.types.json import Jsonb

from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.cpk_server import CpkServerApplicationError, CpkServerReadService
from control_plane_kit_operations.read_services import ReadModelError
from control_plane_kit_operations.records import WorkspaceRecord
from tests.draft_catalogue_fixture import principal
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_authoring_context_fixture import ReceiverAuthoringContextFixture, READ_SCOPES, body_bytes
from tests.receiver_storage_fixture import RECEIVER


class ReceiverAuthoringContextTests(ReceiverAuthoringContextFixture, ReceiverAdmissionFixture, unittest.TestCase):
    def test_exact_selected_artifact_and_origin_agree_across_direct_http_and_mcp_reads(self):
        graph, artifact, declaration = self.receiver_graph(pretty=True)
        command = self.desired_command(graph=graph)
        introduced = self.desired_service().execute(command)
        pins = self.pins()
        before = self.admission_truth()
        descriptor = self.direct_context(expected=pins).descriptor()
        digest = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
        expected = {
            "profile": "receiver-authoring-context.v1", "workspace_id": "workspace-a",
            "expectation": pins.descriptor(),
            "current": {"graph_id": pins.current_graph_id,
                "realized_projection_id": pins.current_realized_projection_id, "receivers": []},
            "desired": {"graph_id": introduced.graph_version_id,
                "realized_projection_id": introduced.desired_realized_projection_id,
                "receivers": [{
                    "binding": {"workspace_id": "workspace-a", "graph_id": introduced.graph_version_id,
                        "realized_projection_id": introduced.desired_realized_projection_id,
                        "runtime_id": "docker", "node_id": "api", "provider_socket_name": "http",
                        "receiver_id": RECEIVER, "selected_configuration_digest": digest,
                        "declaration_identity": declaration.identity().value},
                    "configuration_artifact": {"artifact_id": "selected", "target_path": "/etc/test/receiver.json",
                        "media_type": "application/json", "file_mode": "0444", "content": artifact.content,
                        "content_digest": digest, "source_digest": digest},
                    "origin": {"introducing_graph_id": introduced.graph_version_id,
                        "introducing_realized_projection_id": introduced.desired_realized_projection_id,
                        "introducing_action_id": introduced.action.action_id,
                        "introducing_session_id": command.session_id, "introducing_draft_id": None,
                        "first_accepted_action_id": None, "first_accepted_session_id": None},
                    "lifecycle": "pending"}]},
            "pending_draft": None,
        }
        self.assertEqual(descriptor, expected)
        for surface in ("http", "mcp"):
            factory, observations = self.measured_factory()
            self.assertEqual(self.context_read(surface=surface, expected=pins.descriptor(), factory=factory), expected)
            self.assert_measured_snapshot(observations)
        self.assertEqual(self.admission_truth(), before)
        encoded = body_bytes(descriptor)
        for unrelated in (b"application.txt", b"not-a-receiver-configuration", b"public_environment", b"metadata"):
            self.assertNotIn(unrelated, encoded)
        # The established redacted projection must not turn into the new exact read.
        redacted = self.read(route="read.desired-graph")
        self.assertNotIn(json.dumps(artifact.content), json.dumps(redacted))

    def test_same_draft_revision_preserves_original_origin_and_distinct_selected_bytes(self):
        original_graph, original_artifact, _ = self.receiver_graph()
        command = self.receiver_create(graph=original_graph)
        first = self.catalogue().execute(command)
        first_action = self.command_action(command)
        self.catalogue().execute(self.receiver_select(first))
        changed_graph, changed_artifact, _ = self.receiver_graph(pretty=True)
        revised = self.catalogue().execute(self.receiver_revise(first, graph=changed_graph))
        before = self.admission_truth()
        result = self.context_read(pending_draft={"draft_id": first.draft_id, "expected_head_revision": 2})
        desired = result["desired"]["receivers"][0]
        pending = result["pending_draft"]["receivers"][0]
        self.assertEqual(result["pending_draft"]["head_revision"], revised.revision)
        self.assertEqual(result["pending_draft"]["graph_id"], revised.graph_id)
        self.assertEqual(desired["configuration_artifact"]["content"], original_artifact.content)
        self.assertEqual(pending["configuration_artifact"]["content"], changed_artifact.content)
        self.assertNotEqual(desired["configuration_artifact"], pending["configuration_artifact"])
        self.assertEqual(desired["origin"], pending["origin"])
        self.assertEqual(pending["origin"]["introducing_action_id"], first_action.action_id)
        self.assertEqual(pending["origin"]["introducing_graph_id"], first.graph_id)
        self.assertEqual(pending["origin"]["introducing_draft_id"], first.draft_id)
        self.assertEqual((desired["lifecycle"], pending["lifecycle"]), ("pending", "pending"))
        self.assertEqual(self.admission_truth(), before)
        self.assert_context_refused(409, pending_draft={"draft_id": first.draft_id, "expected_head_revision": 1})
        self.catalogue().execute(self.delete_command(revised))
        self.assert_context_refused(404, pending_draft={"draft_id": first.draft_id, "expected_head_revision": 2})

    def test_legacy_receiver_free_live_head_has_explicit_absent_projection_without_minting(self):
        draft = self.create()
        before = self.admission_truth()
        result = self.context_read(pending_draft={"draft_id": draft.draft_id, "expected_head_revision": 1})
        self.assertEqual(result["pending_draft"], {"draft_id": draft.draft_id, "head_revision": 1,
            "graph_id": draft.graph_id, "realized_projection_id": None, "receivers": []})
        self.assertEqual(self.admission_truth(), before)

    def test_absent_desired_is_distinct_from_assigned_empty_and_unassigned_current_refuses(self):
        initial = self.context_read()
        self.assertEqual(initial["desired"]["receivers"], [])
        # Storage corruption/premise below command owners, never admission evidence.
        self.connection.execute("UPDATE cpk_workspaces SET desired_graph_id=NULL, "
            "desired_realized_projection_id=NULL,desired_graph_revision=0 WHERE workspace_id='workspace-a'")
        absent = self.context_read()
        self.assertIsNone(absent["desired"])
        self.assertEqual(absent["expectation"]["desired_graph_revision"], 0)
        with self.unit_of_work() as uow:
            uow.stores.workspaces.create(WorkspaceRecord("unassigned", "Unassigned"))
            uow.commit()
        self.assert_context_refused(409, workspace="unassigned")

    def test_both_read_grants_and_operator_kind_precede_uow_even_for_forged_input(self):
        factory_calls = []

        def forbidden():
            factory_calls.append(True)
            self.fail("unauthorized request constructed a UoW")
        actors = [principal(scopes=scopes) for scopes in (
            (), (PolicyScope.INSTANCE_WORKSPACE_READ,), (PolicyScope.DELEGATION_KEY_READ,),
            (PolicyScope.INSTANCE_WORKSPACE_EDIT,), (PolicyScope.DELEGATION_KEY_REGISTER,))]
        actors.append(principal("workspace-b", READ_SCOPES))
        for kind in (PrincipalKind.WORKER, PrincipalKind.SERVICE):
            actors.append(AuthenticatedPrincipal(PrincipalIdentity("urn:test:context", "nonoperator", kind),
                (WorkspaceGrant("workspace-a", READ_SCOPES),)))
        for actor in actors:
            with self.subTest(actor=actor):
                self.assert_context_refused(403, factory=forbidden, actor=actor,
                    scopes=[scope.value for scope in READ_SCOPES], expected={"submitted-marker": True})
                self.assertEqual(factory_calls, [])
        request = replace(self.context_request(), principal=None)
        with self.assertRaises(CpkServerApplicationError) as caught:
            CpkServerReadService(forbidden).handle(request)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(factory_calls, [])
        # Dedicated owner must enforce the same rule without the adapter.
        with self.assertRaises(ReadModelError) as caught:
            self.direct_context(factory=forbidden, actor=principal(scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,)))
        self.assertEqual((caught.exception.status, caught.exception.category), (403, "forbidden"))
        self.assertEqual(factory_calls, [])
        api = self.authoring_api()
        service = api.ReceiverAuthoringContextReadService(forbidden)
        query = api.ReceiverAuthoringContextQuery(workspace_id="workspace-a")
        for actor in actors:
            context = actor.command_context(actor.workspace_grants[0].workspace_id)
            with self.subTest(direct_actor=actor):
                with self.assertRaises(ReadModelError) as caught:
                    service.read(query, context=context)
                self.assertEqual((caught.exception.status, caught.exception.category), (403, "forbidden"))
                self.assertEqual(factory_calls, [])
        with self.assertRaises(ReadModelError) as caught:
            service.read(query, context=None)
        self.assertEqual((caught.exception.status, caught.exception.category), (403, "forbidden"))
        self.assertEqual(factory_calls, [])

    def test_closed_query_invalid_types_conflicts_and_missing_scoped_objects(self):
        pins = self.pins().descriptor()
        malformed = (
            {"limit": 1}, {"after": "submitted-marker"}, {"graph_id": "submitted-marker"},
            {"workspace_id": "workspace-b"}, {"expected": {**pins, "extra": True}},
            {"expected": {"desired_graph_revision": 1}}, {"expected": {**pins, "desired_graph_revision": True}},
            {"pending_draft": {"draft_id": "missing", "expected_head_revision": 0}},
            {"pending_draft": {"draft_id": "missing", "expected_head_revision": 1, "history": True}},
            {"pending_draft": {"draft_id": "submitted-marker" * 2000, "expected_head_revision": 1}},
        )
        for values in malformed:
            with self.subTest(values=values):
                self.assert_context_refused(400, **values)
        self.assert_context_refused(404, pending_draft={"draft_id": "missing", "expected_head_revision": 1})
        foreign = self.create(workspace="workspace-b", key="foreign-head")
        self.assert_context_refused(404,
            pending_draft={"draft_id": foreign.draft_id, "expected_head_revision": 1})
        self.assert_context_refused(404, workspace="missing")
        self.assert_context_refused(409, expected={**pins, "current_graph_id": "stale"})

    def test_mismatched_original_action_or_binding_is_one_safe_unavailable_category(self):
        command = self.desired_command()
        first = self.desired_service().execute(command)
        # Move selection forward so provenance must read the ORIGINAL source too.
        self.desired_service().execute(self.desired_command(graph=self.receiver_graph(pretty=True)[0], key="next"))
        self.context_read()  # Establish a valid control before corruption.
        payload = first.action.payload
        messages = []
        self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
            (Jsonb({**payload, "desired_graph_id": "submitted-marker"}), first.action.action_id))
        try:
            messages.append(self.assert_context_refused(409))
        finally:
            self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                (Jsonb(dict(payload)), first.action.action_id))
        binding = self.bindings_for_graph(first.graph_version_id)[0]
        self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s "
            "WHERE graph_id=%s", ("f" * 64, first.graph_version_id))
        try:
            messages.append(self.assert_context_refused(409))
        finally:
            self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s "
                "WHERE graph_id=%s", (binding.selected_configuration_digest, first.graph_version_id))
        self.assertEqual(len(set(messages)), 1)
