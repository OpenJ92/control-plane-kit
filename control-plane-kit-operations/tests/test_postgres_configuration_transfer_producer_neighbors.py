"""Real accepted reuse never discharges unrelated unresolved claims."""
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService
from control_plane_kit_operations.coordinator import CoordinatorStatus
from tests import test_configuration_cleanup_v2_planning as planning


class PostgresConfigurationTransferProducerNeighborTests(planning._V2PlanningFixture, unittest.TestCase):
    def advance(self, command, *, replay=False):
        forbidden = lambda: self.fail("original replay sampled time or allocated an ID")
        return CurrentGraphAdvancementCommandService(self.unit_of_work,
            clock=forbidden if replay else self.sample_clock,
            id_factory=forbidden if replay else self.sample_id).execute(command)

    def snapshot(self):
        return self.truth(), self.connection.execute("SELECT * FROM cpk_configuration_claim_transfers "
            "ORDER BY run_id,activity_id,attempt,artifact_id").fetchall()

    def test_own_success_transfers_preserve_unprofiled_and_unknown_shared_neighbors(self):
        birth = self.member.advance()
        unprofiled, _ = self.later_use("unprofiled-neighbor", producer=lambda request:
            RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "unprofiled"}))
        unprofiled_acceptance = self.advance(unprofiled)

        def uncertain(_request):
            raise RuntimeError("simulated ambiguous adapter outcome")

        unknown, _ = self.later_use("unknown-neighbor", producer=uncertain,
            expected_status=CoordinatorStatus.UNCERTAIN)
        own, _ = self.later_use("qualified-success")
        accepted = self.advance(own)
        for command, present in ((unprofiled, False), (unknown, False), (own, True)):
            identity = EffectAttemptIdentity(RunId(command.run_id), "activity-" + command.run_id[4:], 1)
            with self.unit_of_work() as uow:
                self.assertEqual(uow.stores.effect_attempt_intents.get(identity).intent.configuration_instances.instances,
                    self.refs)
                self.assertEqual(uow.stores.configuration_completions.get(identity) is not None, present)
        self.assertEqual(self.connection.execute("SELECT run_id,artifact_id,acceptance_revision "
            "FROM cpk_configuration_claim_transfers ORDER BY run_id,artifact_id").fetchall(), sorted(
                [("run-config", ref.artifact_id, birth.desired_graph_revision) for ref in self.refs]
                + [(own.run_id, ref.artifact_id, accepted.desired_graph_revision) for ref in self.refs]))
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT run_id,artifact_id,accepted_revision FROM " + table
                + " WHERE protective ORDER BY run_id,artifact_id").fetchall(), sorted(
                    [(command.run_id, ref.artifact_id, None)
                        for command in (unprofiled, unknown) for ref in self.refs]))
            self.assertEqual(self.connection.execute("SELECT allocation_id,count(*) FROM " + table
                + " WHERE protective GROUP BY allocation_id ORDER BY allocation_id").fetchall(),
                [(ref.allocation_id, 2) for ref in sorted(self.refs, key=lambda ref: ref.allocation_id)])
        current = self.review()
        candidates = current.inspection.descriptor()["candidates"]
        self.assertEqual(len(candidates), len(self.refs))
        for row in candidates:
            self.assertEqual(row["blockers"], ["current-selected-use", "unresolved-invocation"])
            self.assertEqual({item["source_identity"]["run_id"] for item in row["invocations"]},
                {unprofiled.run_id, unknown.run_id})
        before = self.snapshot()
        with self.assertRaises(self.commands.ConfigurationCleanupCommandError):
            self.publish(self.request(inspection=current))
        self.assertEqual(self.snapshot(), before)
        replay = self.advance(unprofiled, replay=True)
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.action, replay.event), (unprofiled_acceptance.action, unprofiled_acceptance.event))
        self.assertEqual(self.snapshot(), before, "later successful acceptance must not backfill original replay")
