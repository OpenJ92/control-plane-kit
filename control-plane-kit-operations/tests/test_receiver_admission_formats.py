"""#1903 closed old/new command formats, original receipts and bounded actions."""

from dataclasses import replace
import hashlib
import json
import unittest

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.cpk_server import CpkServerApplicationError, CpkServerPlanningService
from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError
from control_plane_kit_operations.desired_realized_projections import DesiredRealizedProjectionPublicationError
from control_plane_kit_operations.planning import DesiredGraphCommandError
from tests.draft_catalogue_fixture import CatalogueRequest, principal
from tests.receiver_admission_fixture import ReceiverAdmissionFixture


class ReceiverAdmissionFormatTests(ReceiverAdmissionFixture, unittest.TestCase):
    def wire(self, route, payload):
        adapter = CpkServerPlanningService(None, desired_graphs=self.desired_service(),
            desired_topology_drafts=self.catalogue())
        return adapter.handle(CatalogueRequest("http", route, ControlPlaneServiceRole.PLANNING,
            {"workspace_id": "workspace-a"}, payload, principal()))

    def wire_payload(self, *, receiver=True):
        current = self.workspace()
        return dict(session_id=self.sessions["workspace-a"], title="Receiver", idempotency_key="wire",
            graph=DEFAULT_GRAPH_CODEC.encode(self.receiver_graph()[0] if receiver else self.graph()),
            expected_desired_graph_id=current.desired_graph_id,
            expected_desired_realized_projection_id=current.desired_realized_projection_id,
            expected_desired_graph_revision=current.desired_graph_revision)

    def test_wire_explicit_null_is_malformed_even_on_legacy_graph(self):
        for route in ("command.desired-graph.set", "command.desired-topology-draft.create"):
            with self.subTest(route=route):
                before = self.admission_truth()
                payload = self.wire_payload(receiver=False) | dict(
                    receiver_lifecycle=None, idempotency_key="wire-null-" + route)
                with self.assertRaises(CpkServerApplicationError) as captured:
                    self.wire(route, payload)
                self.assertEqual(captured.exception.status, 400)
                self.assertEqual(self.admission_truth(), before)

    def test_wire_closed_product_rejects_missing_extra_and_inconsistent_fields(self):
        pins = self.pins().descriptor()
        malformed = [pins | {"private-extra": "private-marker"}, []]
        malformed += [{key: value for key, value in pins.items() if key != missing} for missing in pins]
        malformed += [pins | {"desired_graph_id": None}, pins | {"desired_graph_revision": True}]
        for index, value in enumerate(malformed):
            before = self.admission_truth()
            with self.subTest(index=index), self.assertRaises(CpkServerApplicationError) as captured:
                self.wire("command.desired-topology-draft.create",
                    self.wire_payload() | {"receiver_lifecycle": value})
            self.assertEqual(captured.exception.status, 400)
            self.assertNotIn("private-marker", str(captured.exception))
            self.assertEqual(self.admission_truth(), before)

    def test_wire_desired_create_revise_select_forward_exact_product(self):
        pins = self.pins().descriptor()
        payload = self.wire_payload() | {"receiver_lifecycle": pins}
        created = self.wire("command.desired-topology-draft.create", payload)
        with self.unit_of_work() as uow:
            action = uow.stores.activity_history.action_for_idempotency(self.sessions["workspace-a"], "wire")
        self.assertEqual(action.payload["receiver_lifecycle"], pins)
        revised = self.wire("command.desired-topology-draft.revise", payload | dict(
            idempotency_key="wire-revise", draft_id=created["draft_id"], expected_head_revision=1))
        selected = self.wire("command.desired-topology-draft.select", payload | dict(
            idempotency_key="wire-select", draft_id=created["draft_id"], revision=revised["revision"]))
        next_pins = self.pins().descriptor()
        desired = self.wire("command.desired-graph.set", self.wire_payload() | dict(
            idempotency_key="wire-desired", receiver_lifecycle=next_pins))
        self.assertEqual(selected["graph_id"], revised["graph_id"])
        self.assertIn("desired_graph_id", desired)
        with self.unit_of_work() as uow:
            for key, expected in (("wire-revise", pins), ("wire-select", pins), ("wire-desired", next_pins)):
                action = uow.stores.activity_history.action_for_idempotency(self.sessions["workspace-a"], key)
                self.assertEqual(action.payload["receiver_lifecycle"], expected)

    def test_absent_legacy_draft_member_preserves_original_fingerprint_and_closed_action(self):
        command = self.create_command()
        result = self.catalogue().execute(command)
        action = self.command_action(command)
        intent = dict(kind="create-desired-topology-draft", context=command.context.descriptor(),
            session_id=command.session_id, graph=DEFAULT_GRAPH_CODEC.encode(command.graph),
            title=command.title, draft_id=None, expected_head_revision=None)
        expected = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.assertEqual(action.intent_fingerprint, expected)
        self.assertEqual(dict(action.payload), result.descriptor())
        self.assertNotIn("receiver_lifecycle", action.payload)
        self.assertEqual(self.catalogue(id_factory=self.forbid_allocation).execute(command), result)

    def test_draft_replay_accepts_only_exact_old_or_plus_member_payload(self):
        commands = [self.create_command(key="old-format"), self.receiver_create(key="new-format")]
        for command in commands:
            result = self.catalogue().execute(command)
            original = dict(self.command_action(command).payload)
            variants = [original | {"unknown": "private-marker"}, original | {"receiver_lifecycle": None}]
            if "receiver_lifecycle" in original:
                variants += [{key: value for key, value in original.items() if key != "receiver_lifecycle"},
                    original | {"receiver_lifecycle": original["receiver_lifecycle"] | {"desired_graph_revision": 99}}]
            else:
                variants += [original | {"receiver_lifecycle": self.pins().descriptor()}]
            try:
                for payload in variants:
                    self.connection.execute("UPDATE cpk_operation_actions SET payload=%s "
                        "WHERE session_id=%s AND idempotency_key=%s",
                        (Jsonb(payload), command.session_id, command.idempotency_key.value))
                    before = self.admission_truth()
                    with self.assertRaises(DesiredTopologyDraftError):
                        self.catalogue(id_factory=self.forbid_allocation).execute(command)
                    self.assertEqual(self.admission_truth(), before)
            finally:
                self.connection.execute("UPDATE cpk_operation_actions SET payload=%s "
                    "WHERE session_id=%s AND idempotency_key=%s",
                    (Jsonb(original), command.session_id, command.idempotency_key.value))
            self.assertEqual(self.catalogue(id_factory=self.forbid_allocation).execute(command), result)

    def test_desired_and_publication_replay_refuse_extra_payload_keys(self):
        command = self.desired_command()
        self.desired_service().execute(command)
        publication = self.publication()
        self.publisher().execute(publication)
        for command, service, error_type in ((command, self.desired_service(), DesiredGraphCommandError),
                (publication, self.publisher(), DesiredRealizedProjectionPublicationError)):
            original = dict(self.command_action(command).payload)
            self.connection.execute("UPDATE cpk_operation_actions SET payload=%s "
                "WHERE session_id=%s AND idempotency_key=%s",
                (Jsonb(original | {"extra": "private-marker"}), command.session_id, command.idempotency_key.value))
            before = self.admission_truth()
            try:
                with self.assertRaises(error_type):
                    service.execute(command)
                self.assertEqual(self.admission_truth(), before)
            finally:
                self.connection.execute("UPDATE cpk_operation_actions SET payload=%s "
                    "WHERE session_id=%s AND idempotency_key=%s",
                    (Jsonb(original), command.session_id, command.idempotency_key.value))

    def test_complete_publication_payload_bound_includes_expectation_before_any_write(self):
        self.desired_service().execute(self.desired_command())
        for limit in (65_536, 65_537):
            command = self.publication(key="payload-" + str(limit))
            projection = command.projection
            payload = dict(workspace_id=command.workspace_id, authored_graph_id=command.expected_authored_graph_id,
                previous_realized_projection_id=command.expected_realized_projection_id,
                desired_realized_projection_id=projection.projection_id,
                desired_realized_projection_digest=projection.projection_digest,
                desired_graph_revision=command.expected_desired_graph_revision + 1,
                projection_kind=projection.projection_kind.value, projection_key=projection.projection_key,
                source_operation_id="", source_operation_version=1,
                receiver_lifecycle=command.receiver_lifecycle.descriptor())
            base_size = len(json.dumps(payload).encode())
            command = replace(command, source_operation_id="a" * (limit - base_size))
            if limit == 65_536:
                result = self.publisher().execute(command)
                self.assertEqual(len(json.dumps(dict(result.action.payload)).encode()), limit)
            else:
                statements = []
                before = self.admission_truth()
                with self.assertRaises((ValueError, DesiredRealizedProjectionPublicationError)):
                    self.publisher(uow=self.observed_uow(statements)).execute(command)
                self.assertEqual(self.admission_truth(), before)
                self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE")) for query in statements))
