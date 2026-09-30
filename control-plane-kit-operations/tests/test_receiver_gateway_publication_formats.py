"""#1903 generic publication pins/old receipts survive the existing gateway composition."""

from dataclasses import replace
import unittest

from control_plane_kit_operations.desired_realized_projections import (
    prepare_desired_realized_projection_publication, publish_desired_realized_projection_in_unit_of_work,
)
from control_plane_kit_operations.gateway_key_rotation_overlap import GatewayKeyRotationOverlapProjectionConflict
from tests import test_gateway_key_rotation_overlap_projection as existing


class ReceiverGatewayPublicationFormatTests(unittest.TestCase):
    # Reuse established real-store setup without collecting its tests again.
    setUp = existing.GatewayKeyRotationOverlapProjectionTests.setUp
    tearDown = existing.GatewayKeyRotationOverlapProjectionTests.tearDown
    unit_of_work = existing.GatewayKeyRotationOverlapProjectionTests.unit_of_work
    seed = existing.GatewayKeyRotationOverlapProjectionTests.seed
    command = existing.GatewayKeyRotationOverlapProjectionTests.command
    service = existing.GatewayKeyRotationOverlapProjectionTests.service

    def test_fresh_gateway_publication_forwards_the_original_five_pins(self):
        command = self.command()
        result = self.service().execute(command)
        self.assertEqual(result.publication.action.payload.get("receiver_lifecycle"), dict(
            current_graph_id=command.expected_authored_graph_id,
            current_realized_projection_id=command.expected_current_realized_projection_id,
            desired_graph_id=command.expected_authored_graph_id,
            desired_realized_projection_id=command.expected_desired_realized_projection_id,
            desired_graph_revision=command.expected_desired_graph_revision))

    def test_owned_old_publication_receipt_selects_old_format_after_supersession(self):
        command = self.command()
        with self.unit_of_work() as uow:
            prepared = prepare_desired_realized_projection_publication(uow, "workspace-a",
                command.session_id, command.idempotency_key.value)
            value = self.service()._publication_command(uow, command, prepared=prepared,
                created_at="2026-08-02T02:00:00Z")
            self.assertTrue(hasattr(value, "receiver_lifecycle"), "#1903 generic publication lacks expectation")
            # A real legacy-only old child, committed before the resumed outer
            # composition. It has no new member and no caller bypass parameter.
            old = replace(value, receiver_lifecycle=None)
            original = publish_desired_realized_projection_in_unit_of_work(uow, old, prepared=prepared,
                created_at="2026-08-02T02:00:00Z", action_id="original-old-publication")
            uow.commit()
        self.assertNotIn("receiver_lifecycle", original.action.payload)
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", command.expected_authored_graph_id,
                command.expected_desired_realized_projection_id)
            uow.commit()
        replay = self.service("unused-action").execute(command)
        self.assertTrue(replay.publication.replayed)
        self.assertEqual(replay.publication.action, original.action)
        with self.assertRaises(GatewayKeyRotationOverlapProjectionConflict):
            self.service().execute(replace(command, expected_rotation_version=command.expected_rotation_version + 1))
