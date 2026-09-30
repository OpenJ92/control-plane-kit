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
        self.desired_service().execute(self.desired_command())
        origin = self.introduction()
        command = self.publication()
        result = self.publisher().execute(command)
        self.assertEqual(result.action.payload["receiver_lifecycle"], command.receiver_lifecycle.descriptor())
        self.assertEqual(self.introduction(), origin)
        self.assertIsNone(origin.first_accepted_action_id)
        self.assertEqual(len(self.bindings_for_graph(result.authored_graph_id)), 1)

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
        command = self.publication()
        before = self.admission_truth()
        with self.unit_of_work() as uow:
            prepared = prepare_desired_realized_projection_publication(uow,
                command.workspace_id, command.session_id, command.idempotency_key.value)
            result = publish_desired_realized_projection_in_unit_of_work(uow, command,
                prepared=prepared, created_at=NOW, action_id="rolled-back-publication")
            self.assertEqual(result.desired_graph_revision, command.expected_desired_graph_revision + 1)
            # Deliberately no commit: the semantic operation does not own it.
        self.assertEqual(self.admission_truth(), before)

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
