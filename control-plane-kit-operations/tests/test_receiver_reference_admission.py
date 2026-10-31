"""#1903 saved-source publication and direct planning validate receiver references."""

import unittest
from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.planning import ActivityPlanningError, DesiredGraphCommandError
from control_plane_kit_operations.records import RealizedGraphProjectionRecord
from control_plane_kit_operations.saved_deployment_preparation import (
    SavedDeploymentPreparationService, SavedPreparationError,
)
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_recorded_acceptance_fixture import record_accepted_current


class ReceiverReferenceAdmissionTests(ReceiverAdmissionFixture, unittest.TestCase):
    def test_empty_derived_selected_material_cannot_hide_retained_receiver_membership(self):
        draft = self.selected_receiver_continuation()
        command = self.desired_command(graph=DeploymentGraph("new-empty"), key="empty-with-membership")
        with self.unit_of_work() as uow:
            retained = uow.stores.realized_graphs.get(self.workspace().desired_realized_projection_id)
        changed = RealizedGraphProjectionRecord.from_graph(
            projection_id=retained.projection_id, workspace_id=retained.workspace_id,
            source_authored_graph_id=retained.source_authored_graph_id,
            projection_kind=retained.projection_kind, projection_key=retained.projection_key,
            graph=DeploymentGraph("empty-corrupted-source"), created_by=retained.created_by,
            created_at=retained.created_at)
        self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s,projection_digest=%s "
            "WHERE projection_id=%s", (Jsonb(changed.graph_descriptor), changed.projection_digest, changed.projection_id))
        before = self.reference_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(command)
        self.assertEqual(self.reference_truth(), before)

    def reference_truth(self):
        return self.admission_truth() | {name: self.rows(name) for name in (
            "cpk_operation_sessions", "cpk_saved_preparation_sources", "cpk_activity_plans")}

    def selected_receiver_continuation(self):
        first = self.catalogue().execute(self.receiver_create())
        second = self.catalogue().execute(self.receiver_revise(first))
        self.catalogue().execute(self.receiver_select(second))
        self.assertNotEqual(first.graph_id, second.graph_id)
        self.assertEqual(self.introduction().introducing_graph_id, first.graph_id)
        return second

    def saved_service(self, factory=None):
        factory = factory or self.unit_of_work
        return SavedDeploymentPreparationService(factory, self.operations(uow=factory))

    def test_saved_receiver_reference_publishes_one_source_and_replays_after_later_truth(self):
        draft = self.selected_receiver_continuation()
        command = self.prepare_command(draft)
        key = IdempotencyKey("saved-receiver-session")
        origin = self.introduction()
        result = self.saved_service().start(command, session_key=key)
        with self.unit_of_work() as uow:
            source = uow.stores.saved_preparation_sources.get("workspace-a", result.session.session_id)
        self.assertEqual((source.draft_id, source.revision), (draft.draft_id, draft.revision))
        self.assertEqual(self.introduction(), origin)
        self.catalogue().execute(self.receiver_revise(draft, key="later-head"))
        self.desired_service().execute(self.desired_command(graph=DeploymentGraph("later-empty"), key="later-desired"))
        before = self.reference_truth()
        replay = self.saved_service().start(command, session_key=key)
        self.assertEqual(replay.session.session_id, result.session.session_id)
        self.assertEqual(replay.action, result.action)
        self.assertEqual(self.reference_truth(), before)

    def test_missing_selected_receiver_membership_refuses_before_saved_session_or_source(self):
        draft = self.selected_receiver_continuation()
        command = self.prepare_command(draft)
        self.connection.execute("DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s", (draft.graph_id,))
        # This is a non-original continuation; no introduction FK forces the
        # source read to reject. Original graph and reservation remain intact.
        self.assertNotEqual(self.introduction().introducing_graph_id, draft.graph_id)
        before = self.reference_truth()
        statements = []
        with self.assertRaises(SavedPreparationError):
            self.saved_service(self.observed_uow(statements)).start(command,
                session_key=IdempotencyKey("missing-membership-session"))
        self.assertEqual(self.reference_truth(), before)
        self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE")) for query in statements))

    def test_direct_receiver_noop_planning_is_lawful_and_original_replay_survives_new_desired(self):
        record_accepted_current(self)
        command = self.plan_command(key="receiver-noop")
        origin = self.introduction()
        result = self.planner().execute(command)
        self.assertEqual(result.plan_record.plan.activities, ())
        self.assertEqual(result.plan_record.base_lineage, self.workspace().current_lineage)
        self.assertEqual(result.plan_record.desired_lineage, self.workspace().desired_lineage)
        self.assertEqual(self.introduction(), origin)
        self.desired_service().execute(self.desired_command(graph=DeploymentGraph("later-empty"), key="later-desired"))
        before = self.reference_truth()
        replay = self.planner().execute(command)
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.plan_record, replay.action), (result.plan_record, result.action))
        self.assertEqual(self.reference_truth(), before)

    def test_direct_planning_checks_receiver_membership_before_plan_or_action_writes(self):
        record_accepted_current(self)
        continued = self.desired_service().execute(self.desired_command(key="continuation"))
        command = self.plan_command(key="missing-reference")
        self.assertNotEqual(self.introduction().introducing_graph_id, continued.graph_version_id)
        # The two graph values remain semantically equal and can yield a no-op
        # under the existing planning profile. Only membership is corrupted.
        self.connection.execute("DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s",
                                (continued.graph_version_id,))
        before = self.reference_truth()
        statements = []
        with self.assertRaises(ActivityPlanningError):
            self.planner(unit_of_work_factory=self.observed_uow(statements)).execute(command)
        self.assertEqual(self.reference_truth(), before)
        self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE")) for query in statements))
