"""#1903 strengthened G/D/S and F0 N1-N4/N6 through real command owners."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest

import psycopg

from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError
from control_plane_kit_operations.planning import DesiredGraphCommandError, SetDesiredGraph
from control_plane_kit_operations.workflows import (
    CloseOperationSession, IdempotencyKey, OperationCommandService,
)
from tests.draft_catalogue_fixture import NOW, principal
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_storage_fixture import RECEIVER


class ReceiverGraphAdmissionTests(ReceiverAdmissionFixture, unittest.TestCase):
    def test_desired_command_commits_actual_projection_action_origin_and_all_bindings(self):
        command = self.desired_command()
        result = self.desired_service().execute(command)
        bindings = self.bindings_for_graph(result.graph_version_id)
        self.assertEqual(tuple(item.receiver_id for item in bindings), (RECEIVER,))
        origin = self.introduction()
        self.assertEqual((origin.introducing_graph_id, origin.introducing_realized_projection_id,
            origin.introducing_action_id, origin.introducing_session_id),
            (result.graph_version_id, result.desired_realized_projection_id,
             result.action.action_id, command.session_id))
        self.assertIsNone(origin.first_accepted_action_id)
        self.assertIsNone(origin.retired_action_id)
        self.assertEqual(result.action.payload["receiver_lifecycle"], command.receiver_lifecycle.descriptor())
        self.assertEqual(self.workspace().desired_graph_id, result.graph_version_id)

    def test_missing_expectation_refuses_receiver_introduction_without_any_write(self):
        current = self.workspace()
        command = SetDesiredGraph(self.sessions["workspace-a"], "workspace-a", "operator-a",
            self.receiver_graph()[0], current.desired_graph_id, IdempotencyKey("absent-pins"),
            current.desired_realized_projection_id, current.desired_graph_revision)
        before = self.admission_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(command)
        self.assertEqual(self.admission_truth(), before)

    def test_matching_desired_with_stale_current_pair_refuses_atomically(self):
        pins = self.pins()
        for field in ("current_graph_id", "current_realized_projection_id"):
            before = self.admission_truth()
            command = self.desired_command(key=field, pins=replace(pins, **{field: "stale-reference"}))
            with self.subTest(field=field), self.assertRaises(DesiredGraphCommandError):
                self.desired_service().execute(command)
            self.assertEqual(self.admission_truth(), before)

    def test_duplicate_desired_fields_must_agree_with_product_before_writes(self):
        command = self.desired_command()
        before = self.admission_truth()
        for changes in (dict(expected_desired_graph_id="different"),
                        dict(expected_desired_realized_projection_id="different"),
                        dict(expected_desired_graph_revision=command.expected_desired_graph_revision + 1)):
            with self.subTest(changes=changes), self.assertRaises((ValueError, DesiredGraphCommandError)):
                self.desired_service().execute(replace(command, **changes))
            self.assertEqual(self.admission_truth(), before)

    def test_selected_pending_continuation_and_new_identity_keep_distinct_real_origins(self):
        first = self.desired_service().execute(self.desired_command())
        origin = self.introduction()
        mixed = self.mixed_graph(self.receiver_graph(pretty=True)[0],
            self.receiver_graph(node_id="other", receiver="b" * 32)[0])
        second = self.desired_service().execute(self.desired_command(graph=mixed, key="mixed"))
        self.assertEqual(self.introduction(), origin)
        new = self.introduction(receiver="b" * 32)
        self.assertEqual((new.introducing_graph_id, new.introducing_action_id),
                         (second.graph_version_id, second.action.action_id))
        self.assertNotEqual(new.introducing_graph_id, first.graph_version_id)
        self.assertEqual({item.receiver_id for item in self.bindings_for_graph(second.graph_version_id)},
                         {RECEIVER, "b" * 32})
        self.assertIsNone(new.first_accepted_action_id)

    def test_same_live_head_continues_pending_origin_and_reserves_only_new_identity(self):
        first_command = self.receiver_create()
        first = self.catalogue().execute(first_command)
        origin = self.introduction()
        self.assertEqual(origin.introducing_draft_id, first.draft_id)
        mixed = self.mixed_graph(self.receiver_graph(pretty=True)[0],
            self.receiver_graph(node_id="other", receiver="b" * 32)[0])
        revision_command = self.receiver_revise(first, graph=mixed)
        revised = self.catalogue().execute(revision_command)
        self.assertEqual(revised.revision, 2)
        self.assertEqual(self.introduction(), origin)
        new = self.introduction(receiver="b" * 32)
        action = self.command_action(revision_command)
        self.assertEqual((new.introducing_graph_id, new.introducing_action_id, new.introducing_draft_id),
                         (revised.graph_id, action.action_id, first.draft_id))
        self.assertEqual({binding.receiver_id for binding in self.bindings_for_graph(revised.graph_id)},
                         {RECEIVER, "b" * 32})
        self.assertEqual(action.payload["receiver_lifecycle"], revision_command.receiver_lifecycle.descriptor())

    def test_copied_other_draft_cannot_continue_origin_or_leave_new_identity(self):
        self.catalogue().execute(self.receiver_create())
        mixed = self.mixed_graph(self.receiver_graph()[0],
            self.receiver_graph(node_id="other", receiver="b" * 32)[0])
        before = self.admission_truth()
        with self.assertRaises(DesiredTopologyDraftError):
            self.catalogue().execute(self.receiver_create(graph=mixed, key="copy"))
        self.assertEqual(self.admission_truth(), before)
        self.assertIsNone(self.introduction(receiver="b" * 32))

    def test_pending_scope_change_cannot_be_reclassified_as_new(self):
        first = self.catalogue().execute(self.receiver_create())
        before = self.admission_truth()
        with self.assertRaises(DesiredTopologyDraftError):
            self.catalogue().execute(self.receiver_revise(first,
                graph=self.receiver_graph(node_id="moved")[0]))
        self.assertEqual(self.admission_truth(), before)

    def test_omission_then_tombstone_preserves_reservation_and_blocks_historical_copy(self):
        first = self.catalogue().execute(self.receiver_create())
        origin = self.introduction()
        omitted = self.catalogue().execute(self.receiver_revise(first, graph=DeploymentGraph("empty")))
        self.catalogue().execute(self.delete_command(omitted))
        self.assertEqual(self.introduction(), origin)
        self.assertIsNone(origin.retired_action_id)
        before = self.admission_truth()
        with self.assertRaises(DesiredTopologyDraftError):
            self.catalogue().execute(self.receiver_create(key="historical-copy"))
        self.assertEqual(self.admission_truth(), before)

    def test_selection_requires_current_live_pending_head_and_persists_exact_pins(self):
        first = self.catalogue().execute(self.receiver_create())
        revised = self.catalogue().execute(self.receiver_revise(first))
        before = self.admission_truth()
        with self.assertRaises(DesiredTopologyDraftError):
            self.catalogue().execute(self.receiver_select(first, key="old-head"))
        self.assertEqual(self.admission_truth(), before)
        command = self.receiver_select(revised)
        result = self.catalogue().execute(command)
        self.assertEqual(result.graph_id, revised.graph_id)
        self.assertEqual(self.command_action(command).payload["receiver_lifecycle"],
                         command.receiver_lifecycle.descriptor())
        self.assertIsNone(self.introduction().first_accepted_action_id)

    def test_receiver_to_empty_still_requires_original_current_and_desired_expectations(self):
        self.desired_service().execute(self.desired_command())
        command = self.desired_command(graph=DeploymentGraph("empty"), key="omit-receiver")
        before = self.admission_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(replace(command, receiver_lifecycle=None))
        self.assertEqual(self.admission_truth(), before)
        origin = self.introduction()
        self.desired_service().execute(command)
        self.assertEqual(self.introduction(), origin)

    def test_selection_rechecks_pins_after_existing_binding_insert(self):
        draft = self.catalogue().execute(self.receiver_create())
        command = self.receiver_select(draft)
        before = self.admission_truth()
        self.connection.execute("CREATE FUNCTION change_selection_pins() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN UPDATE cpk_workspaces SET desired_graph_revision=desired_graph_revision+1 "
            "WHERE workspace_id=NEW.workspace_id; RETURN NEW; END $$")
        # Existing bindings use INSERT ON CONFLICT: BEFORE INSERT still fires.
        self.connection.execute("CREATE TRIGGER change_selection_pins BEFORE INSERT ON cpk_graph_receiver_bindings "
            "FOR EACH ROW EXECUTE FUNCTION change_selection_pins()")
        try:
            with self.assertRaises(DesiredTopologyDraftError):
                self.catalogue().execute(command)
            self.assertEqual(self.admission_truth(), before)
        finally:
            self.connection.execute("DROP TRIGGER change_selection_pins ON cpk_graph_receiver_bindings")
            self.connection.execute("DROP FUNCTION change_selection_pins()")

    def test_historical_legacy_head_without_identity_projection_can_revise_and_select(self):
        for operation in ("revise", "select"):
            with self.subTest(operation=operation):
                draft = self.catalogue().execute(self.create_command(key="old-" + operation))
                # Exact pre-C2 catalogue premise: graph/revision/action, no
                # persisted identity and no receiver material or B records.
                self.connection.execute("DELETE FROM cpk_realized_graph_projections "
                    "WHERE source_authored_graph_id=%s", (draft.graph_id,))
                command = (self.revise_command(draft, key="legacy-revise", expected=draft.revision)
                           if operation == "revise" else self.select_command(draft, key="legacy-select"))
                result = self.catalogue().execute(command)
                self.assertEqual(result.draft_id, draft.draft_id)
                if operation == "revise":
                    self.assertEqual(result.revision, draft.revision + 1)
                else:
                    self.assertEqual(self.workspace().desired_graph_id, draft.graph_id)

    def test_every_pin_changes_draft_fingerprint_and_original_replay_survives_head_and_close(self):
        command = self.receiver_create()
        first = self.catalogue().execute(command)
        self.catalogue().execute(self.receiver_revise(first))
        OperationCommandService(self.unit_of_work, clock=lambda: NOW,
            id_factory=lambda: "close-action").execute(CloseOperationSession(
                command.session_id, "operator-a", IdempotencyKey("close")))
        before = self.admission_truth()
        service = self.catalogue(id_factory=self.forbid_allocation)
        self.assertEqual(service.execute(command), first)
        changes = dict(current_graph_id="other-current", current_realized_projection_id="other-projection",
            desired_graph_id="other-desired", desired_realized_projection_id="other-desired-projection",
            desired_graph_revision=command.receiver_lifecycle.desired_graph_revision + 1)
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(DesiredTopologyDraftError):
                service.execute(replace(command,
                    receiver_lifecycle=replace(command.receiver_lifecycle, **{field: value})))
        self.assertEqual(self.admission_truth(), before)

    def test_identical_receiver_create_converges_without_reminting(self):
        command = self.receiver_create()
        barrier = threading.Barrier(2)
        def execute(_):
            barrier.wait(timeout=10)
            return self.catalogue().execute(command)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(execute, range(2))
        self.assertEqual(first, second)
        self.assertEqual(len(self.rows("cpk_graph_receiver_introductions")), 1)
        self.assertEqual(len(self.rows("cpk_graph_receiver_bindings")), 1)

    def test_two_receiver_revisions_have_one_head_winner_and_no_losing_reservation(self):
        first = self.catalogue().execute(self.receiver_create())
        commands = [self.receiver_revise(first, key="competing-" + suffix,
            graph=self.mixed_graph(self.receiver_graph()[0],
                self.receiver_graph(node_id="other", receiver=suffix * 32)[0])) for suffix in ("b", "c")]
        barrier = threading.Barrier(2)
        def revise(command):
            barrier.wait(timeout=10)
            try:
                return self.catalogue().execute(command)
            except DesiredTopologyDraftError as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(revise, commands))
        winners = [result for result in outcomes if not isinstance(result, DesiredTopologyDraftError)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].revision, 2)
        self.assertEqual(len(self.rows("cpk_graph_receiver_introductions")), 2)
        self.assertEqual(len(self.rows("cpk_desired_topology_draft_revisions")), 2)
        self.assertEqual(sum(self.introduction(receiver=suffix * 32) is not None for suffix in ("b", "c")), 1)

    def test_global_identity_race_has_one_winner_and_no_foreign_material_in_error(self):
        commands = [self.receiver_create(workspace=workspace) for workspace in ("workspace-a", "workspace-b")]
        barrier = threading.Barrier(2)
        def execute(command):
            barrier.wait(timeout=10)
            try:
                return self.catalogue().execute(command)
            except DesiredTopologyDraftError as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, commands))
        failures = [result for result in results if isinstance(result, DesiredTopologyDraftError)]
        self.assertEqual(len(failures), 1)
        self.assertLessEqual(len(str(failures[0])), 512)
        for sensitive in ("workspace-a", "workspace-b", RECEIVER):
            self.assertNotIn(sensitive, str(failures[0]))
        self.assertEqual(len(self.rows("cpk_graph_receiver_introductions")), 1)
        self.assertEqual(len(self.rows("cpk_graph_receiver_bindings")), 1)
        self.assertEqual(len(self.rows("cpk_desired_topology_draft_revisions")), 1)

    def test_real_action_failure_rolls_back_graph_projection_draft_and_receiver_facts(self):
        command = self.receiver_create(key="late-action-failure")
        before = self.admission_truth()
        # Real database failure in the action write; no replacement store or
        # lifecycle return value. The disposable schema owns this trigger.
        self.connection.execute("CREATE FUNCTION reject_receiver_action() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.idempotency_key='late-action-failure' THEN RAISE EXCEPTION 'test action failure'; "
            "END IF; RETURN NEW; END $$")
        self.connection.execute("CREATE TRIGGER reject_receiver_action BEFORE INSERT ON cpk_operation_actions "
            "FOR EACH ROW EXECUTE FUNCTION reject_receiver_action()")
        try:
            with self.assertRaises(psycopg.errors.RaiseException):
                self.catalogue().execute(command)
            self.assertEqual(self.admission_truth(), before)
        finally:
            self.connection.execute("DROP TRIGGER reject_receiver_action ON cpk_operation_actions")
            self.connection.execute("DROP FUNCTION reject_receiver_action()")
        created = self.catalogue().execute(command)
        self.assertEqual(self.introduction().introducing_graph_id, created.graph_id)

    def test_same_transaction_pin_change_after_action_invalidates_final_admission(self):
        command = self.desired_command(key="intervening-change")
        before = self.admission_truth()
        self.connection.execute("CREATE FUNCTION change_receiver_pins() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.idempotency_key='intervening-change' THEN UPDATE cpk_workspaces "
            "SET desired_graph_revision=desired_graph_revision+1 WHERE workspace_id='workspace-a'; "
            "END IF; RETURN NEW; END $$")
        self.connection.execute("CREATE TRIGGER change_receiver_pins AFTER INSERT ON cpk_operation_actions "
            "FOR EACH ROW EXECUTE FUNCTION change_receiver_pins()")
        try:
            with self.assertRaises(DesiredGraphCommandError):
                self.desired_service().execute(command)
            self.assertEqual(self.admission_truth(), before)
        finally:
            self.connection.execute("DROP TRIGGER change_receiver_pins ON cpk_operation_actions")
            self.connection.execute("DROP FUNCTION change_receiver_pins()")

    def test_deferred_original_binding_failure_rolls_back_complete_semantic_operation(self):
        command = self.desired_command(key="deferred-original-binding")
        before = self.admission_truth()
        self.connection.execute("CREATE FUNCTION remove_test_original_binding() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN DELETE FROM cpk_graph_receiver_bindings WHERE workspace_id=NEW.workspace_id "
            "AND graph_id=NEW.graph_id AND realized_projection_id=NEW.realized_projection_id "
            "AND receiver_id=NEW.receiver_id; RETURN NEW; END $$")
        self.connection.execute("CREATE TRIGGER remove_test_original_binding AFTER INSERT ON cpk_graph_receiver_bindings "
            "FOR EACH ROW EXECUTE FUNCTION remove_test_original_binding()")
        try:
            # The new introduction's deferred original-membership FK must fail
            # if the final membership verifier has not already refused.
            with self.assertRaises((psycopg.errors.ForeignKeyViolation, DesiredGraphCommandError)):
                self.desired_service().execute(command)
            self.assertEqual(self.admission_truth(), before)
        finally:
            self.connection.execute("DROP TRIGGER remove_test_original_binding ON cpk_graph_receiver_bindings")
            self.connection.execute("DROP FUNCTION remove_test_original_binding()")

    def test_receiver_expectation_never_grants_edit_authority_or_foreign_session(self):
        command = self.receiver_create()
        before = self.admission_truth()
        for denied in (replace(command, context=principal(scopes=()).command_context("workspace-a")),
                       replace(command, session_id=self.sessions["workspace-b"])):
            with self.assertRaises(DesiredTopologyDraftError):
                self.catalogue(id_factory=self.forbid_allocation).execute(denied)
            self.assertEqual(self.admission_truth(), before)
