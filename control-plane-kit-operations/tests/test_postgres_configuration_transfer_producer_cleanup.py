"""Genuine target transfer/departure; companion receiver completion is assumed."""
from hashlib import sha256
import unittest

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRefCodec, ConfigurationCleanupOutcome,
    ConfigurationCleanupOutcomeSet, ConfigurationCleanupStatus,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus
from control_plane_kit_core.runtime_effects import configuration_cleanup_result
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_cleanup import (
    ConfigurationCleanupExpectedContext, ConfigurationCleanupSourceSelector,
)
from control_plane_kit_operations.configuration_cleanup_planning import (
    ConfigurationCleanupPlanningService, InspectConfigurationCleanup,
)
from control_plane_kit_operations.coordinator import CoordinatorStatus
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from tests.configuration_cleanup_execution_fixture import ConfigurationCleanupExecutionFixture
from tests.configuration_cleanup_postgres_fixture import command_context
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter


class PostgresConfigurationTransferProducerCleanupTests(ConfigurationCleanupExecutionFixture, unittest.TestCase):
    cleanup_profile = PlanDerivationProfile.CONFIGURATION_CLEANUP_V2

    def accept_configuration_target(self, claimed, suffix):
        # C evidence uses the actual producer; no legacy disposition premise.
        return self.advance(claimed, suffix)

    def transfer_rows(self):
        return self.connection.execute("SELECT run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id,"
            "runtime_id,node_id,ref_digest,request_fingerprint,selection_fingerprint,outcome_fingerprint,"
            "acceptance_revision FROM cpk_configuration_claim_transfers ORDER BY run_id,activity_id,attempt,artifact_id").fetchall()

    def assert_produced(self, accepted):
        identity, completion = self.source_identity, self.completion
        self.assertEqual(self.transfer_rows(), [(
            identity.run_id.value, identity.activity_id, identity.attempt, ref.artifact_id,
            ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id,
            sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest(),
            completion.request_fingerprint, completion.selection_fingerprint, completion.outcome_fingerprint,
            accepted.desired_graph_revision) for ref in self.selected_refs])
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT artifact_id,protective,accepted_revision,disposition_kind "
                "FROM " + table + " WHERE (run_id,activity_id,attempt)=(%s,%s,%s) ORDER BY artifact_id",
                (identity.run_id.value, identity.activity_id, identity.attempt)).fetchall(),
                [(ref.artifact_id, False, accepted.desired_graph_revision, "accepted-current")
                    for ref in self.selected_refs])

    def advance(self, claimed, suffix):
        accepted = super().advance(claimed, suffix)
        if suffix == "ceilings-install":
            self.assert_produced(accepted)
            with self.unit_of_work() as uow:
                workspace = uow.stores.workspaces.get("workspace-a")
            pins = ConfigurationCleanupExpectedContext(workspace.current_graph_id,
                workspace.current_realized_projection_id, workspace.desired_graph_id,
                workspace.desired_realized_projection_id, workspace.desired_graph_revision)
            query = InspectConfigurationCleanup("session-a", "workspace-a", pins,
                tuple(ConfigurationCleanupSourceSelector(self.source_identity, ref.artifact_id, ref)
                    for ref in self.selected_refs), profile=self.cleanup_profile)
            forbidden = lambda: self.fail("read-only inspection sampled time or allocated an ID")
            inspected = ConfigurationCleanupPlanningService(self.unit_of_work,
                clock=forbidden, id_factory=forbidden).inspect(query, context=command_context())
            self.assertEqual(inspected.state, "complete")
            rows = inspected.inspection.descriptor()["candidates"]
            self.assertTrue(rows)
            self.assertTrue(all(row["blockers"] == ["current-selected-use"] and row["invocations"] == []
                for row in rows))
            self.current_protection_checked = True
        return accepted

    def test_real_transfer_remains_current_protection_then_allows_zero_use_cleanup_after_departure(self):
        # Target StartNode/D1, acceptance and teardown are real public commands.
        # The inherited companion receiver uses retained_success as a premise;
        # it is not target completion or genuine receiver-execution evidence.
        self.prepare_cleanup_execution()
        self.assertTrue(self.current_protection_checked)
        self.assert_produced(self.configuration_acceptance)
        proposal = self.plan.cleanup_proposal.descriptor()
        self.assertEqual(proposal["invocations"], [])
        self.assertEqual(len(proposal["accepted_transfers"]), len(self.selected_refs))
        self.assertTrue(all(row["proposed_closures"] == [] for row in proposal["candidates"]))
        self.assertTrue(self.approval.destructive)
        def unrelated_pairs():
            identity = self.source_identity
            return tuple((table, self.connection.execute("SELECT * FROM " + table
                + " WHERE (run_id,activity_id,attempt)<>(%s,%s,%s) ORDER BY run_id,activity_id,attempt,artifact_id",
                (identity.run_id.value, identity.activity_id, identity.attempt)).fetchall())
                for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"))
        unrelated = unrelated_pairs()
        self.assertTrue(all(rows for _, rows in unrelated), "the companion owns unrelated claims")
        self.admit_cleanup()
        claimed = self.ready_run("cleanup-execution")
        expected = ConfigurationCleanupOutcomeSet(tuple(ConfigurationCleanupOutcome(
            ref, ConfigurationCleanupStatus.REMOVED, None) for ref in self.selected_refs))

        def assert_reservation(identity, status):
            with _configuration_accounting(("producer-cleanup-observer", identity)), self.unit_of_work() as uow:
                reservation = uow.stores.configuration_cleanup_ownership.get(identity)
                self.assertIs(reservation.status, status)
                self.assertEqual(tuple(member.ref for member in reservation.members), self.selected_refs)
                self.assertEqual(reservation.claims, ())
                self.assertEqual(reservation.completions, ())
                self.assertEqual({value.ref for value in reservation.accepted_transfers}, set(self.selected_refs))
                if status is EffectAttemptStatus.SUCCEEDED:
                    self.assertEqual(reservation.outcomes, expected)

        def removed(_context, request):
            self.assertEqual(request.operation.instances, self.selected_refs)
            self.assertEqual(request.authority_ref, self.registration.authority_ref)
            assert_reservation(EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1),
                EffectAttemptStatus.STARTED)
            return configuration_cleanup_result(request, expected)

        adapter = RecordingRuntimeAdapter(removed)
        coordinator = self.coordinator(self.unit_of_work, adapter, "cleanup-execution")
        command = self.execution_command(claimed, "cleanup-execution")
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual(adapter.legacy_calls, [])
        request = adapter.runtime_calls[0][1]
        assert_reservation(EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1),
            EffectAttemptStatus.SUCCEEDED)
        self.assert_produced(self.configuration_acceptance)
        self.assertEqual(unrelated_pairs(), unrelated)
        before = self.ceiling_truth(), self.transfer_rows()
        self.assertIs(coordinator.execute(command).status, CoordinatorStatus.COMPLETED)
        self.assertEqual(len(adapter.runtime_calls), 1)
        self.assertEqual((self.ceiling_truth(), self.transfer_rows()), before)
        for table in ("cpk_configuration_invocation_closures", "cpk_configuration_claim_closures"):
            self.assertEqual(self.connection.execute("SELECT count(*) FROM " + table).fetchone(), (0,))
