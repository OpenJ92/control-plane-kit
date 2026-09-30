"""#1903 publication remains one caller-owned semantic transaction."""

from dataclasses import replace
import unittest

from control_plane_kit_operations.desired_realized_projections import (
    DesiredRealizedProjectionPublicationError, prepare_desired_realized_projection_publication,
    publish_desired_realized_projection_in_unit_of_work,
)
from tests.draft_catalogue_fixture import NOW
from tests.receiver_admission_fixture import ReceiverAdmissionFixture


class ReceiverPublicationAdmissionTests(ReceiverAdmissionFixture, unittest.TestCase):
    def test_publication_keeps_exact_pins_and_complete_original_bindings(self):
        initial = self.desired_service().execute(self.desired_command())
        origin = self.introduction()
        original_binding, = self.bindings_for_graph(initial.graph_version_id)
        graph, artifact, declaration = self.receiver_graph(pretty=True)
        command = self.publication(graph=graph)
        self.assertNotEqual(command.projection.projection_id, initial.desired_realized_projection_id)
        result = self.publisher().execute(command)
        self.assertEqual(result.action.payload["receiver_lifecycle"], command.receiver_lifecycle.descriptor())
        self.assertEqual(self.introduction(), origin)
        self.assertIsNone(origin.first_accepted_action_id)
        with self.unit_of_work() as uow:
            stored = uow.stores.realized_graphs.get(result.desired_realized_projection_id)
            bindings = uow.stores.graphs.receiver_bindings("workspace-a", result.authored_graph_id,
                result.desired_realized_projection_id)
        self.assertEqual(stored, command.projection)
        self.assertEqual(stored.projection_digest, result.projection_digest)
        binding, = bindings
        self.assertEqual((binding.receiver_id, binding.realized_projection_id,
            binding.selected_configuration_digest, binding.declaration_identity),
            (origin.receiver_id, result.desired_realized_projection_id,
             artifact.content_digest, declaration.identity().value))
        self.assertNotEqual(binding.selected_configuration_digest, original_binding.selected_configuration_digest)
        self.assertEqual(self.bindings_for_graph(initial.graph_version_id), (original_binding,))

    def test_publication_requires_current_expectation_even_with_matching_desired(self):
        self.desired_service().execute(self.desired_command())
        command = self.publication()
        for pins in (None, replace(command.receiver_lifecycle, current_graph_id="stale-current")):
            before = self.admission_truth()
            with self.subTest(pins=pins), self.assertRaises(DesiredRealizedProjectionPublicationError):
                self.publisher().execute(replace(command, receiver_lifecycle=pins))
            self.assertEqual(self.admission_truth(), before)

    def test_caller_rollback_undoes_publication_action_and_desired_generation(self):
        self.desired_service().execute(self.desired_command())
        command = self.publication(graph=self.receiver_graph(pretty=True)[0])
        before = self.admission_truth()
        with self.unit_of_work() as uow:
            prepared = prepare_desired_realized_projection_publication(uow,
                command.workspace_id, command.session_id, command.idempotency_key.value)
            result = publish_desired_realized_projection_in_unit_of_work(uow, command,
                prepared=prepared, created_at=NOW, action_id="rolled-back-publication")
            self.assertEqual(result.desired_graph_revision, command.expected_desired_graph_revision + 1)
            self.assertEqual(uow.stores.realized_graphs.get(result.desired_realized_projection_id), command.projection)
            bindings = uow.stores.graphs.receiver_bindings("workspace-a", result.authored_graph_id,
                result.desired_realized_projection_id)
            self.assertEqual(tuple(binding.realized_projection_id for binding in bindings),
                             (command.projection.projection_id,))
            # Deliberately no commit: the semantic operation does not own it.
        self.assertEqual(self.admission_truth(), before)

    def test_late_nonoriginal_continuation_membership_change_refuses_with_pins_unchanged(self):
        original = self.desired_service().execute(self.desired_command())
        selected = self.desired_service().execute(self.desired_command(
            graph=self.receiver_graph(pretty=True)[0], key="selected-continuation"))
        self.assertNotEqual(original.graph_version_id, selected.graph_version_id)
        self.assertEqual(self.introduction().introducing_graph_id, original.graph_version_id)
        command = self.publication(graph=self.receiver_graph()[0], key="late-source-change")
        before = self.admission_truth()
        pins = self.workspace()
        # The selected continuation is NOT the original binding required by
        # the introduction FK. The trigger leaves both workspace pins intact,
        # so only the semantic owner's final source check can catch this loss.
        self.connection.execute("CREATE FUNCTION remove_continuation_source() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.idempotency_key='late-source-change' THEN DELETE FROM cpk_graph_receiver_bindings "
            "WHERE workspace_id=NEW.payload->>'workspace_id' AND graph_id=NEW.payload->>'authored_graph_id' "
            "AND realized_projection_id=NEW.payload->>'previous_realized_projection_id'; "
            "END IF; RETURN NEW; END $$")
        self.connection.execute("CREATE TRIGGER remove_continuation_source AFTER INSERT ON cpk_operation_actions "
            "FOR EACH ROW EXECUTE FUNCTION remove_continuation_source()")
        try:
            with self.assertRaises(DesiredRealizedProjectionPublicationError):
                self.publisher().execute(command)
            self.assertEqual(self.admission_truth(), before)
            self.assertEqual(self.workspace(), pins)
        finally:
            self.connection.execute("DROP TRIGGER remove_continuation_source ON cpk_operation_actions")
            self.connection.execute("DROP FUNCTION remove_continuation_source()")

    def test_prepared_prefix_cannot_authorize_same_uow_changed_pins(self):
        self.desired_service().execute(self.desired_command())
        command = self.publication()
        before = self.admission_truth()
        with self.assertRaises(DesiredRealizedProjectionPublicationError):
            with self.unit_of_work() as uow:
                prepared = prepare_desired_realized_projection_publication(uow,
                    command.workspace_id, command.session_id, command.idempotency_key.value)
                # Deliberately changed durable truth within the owning UoW;
                # prepared is a prefix witness, not a reusable validation token.
                uow.stores.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision="
                    "desired_graph_revision+1 WHERE workspace_id='workspace-a'")
                publish_desired_realized_projection_in_unit_of_work(uow, command,
                    prepared=prepared, created_at=NOW, action_id="stale-publication")
                uow.commit()
        self.assertEqual(self.admission_truth(), before)
