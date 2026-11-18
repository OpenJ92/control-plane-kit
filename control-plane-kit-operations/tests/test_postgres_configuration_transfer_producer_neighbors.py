"""Real accepted reuse never discharges unrelated unresolved claims."""
import unittest
from dataclasses import replace

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import NodeTarget, ReconcileNode
from control_plane_kit_core.runtime_effects import RuntimeEffectResult, RuntimeEffectFailure
from control_plane_kit_operations.advancement import CurrentGraphAdvancementCommandService, CurrentGraphAdvancementIncomplete
from control_plane_kit_operations.coordinator import CoordinatorStatus, ExecutionCoordinatorConflict
from tests import test_configuration_cleanup_v2_planning as planning
from tests import test_execution_coordinator as execution
from tests.configuration_cleanup_postgres_fixture import completion_result


class PostgresConfigurationTransferProducerNeighborTests(planning._V2PlanningFixture, unittest.TestCase):
    def advance(self, command, *, replay=False):
        forbidden = lambda: self.fail("original replay sampled time or allocated an ID")
        return CurrentGraphAdvancementCommandService(self.unit_of_work,
            clock=forbidden if replay else self.sample_clock,
            id_factory=forbidden if replay else self.sample_id).execute(command)

    def snapshot(self):
        return self.truth(), self.connection.execute("SELECT * FROM cpk_configuration_claim_transfers "
            "ORDER BY run_id,activity_id,attempt,artifact_id").fetchall()

    def test_own_transfer_preserves_unprofiled_then_uncertainty_blocks_next_fresh_use(self):
        birth = self.member.advance()
        unprofiled, _ = self.later_use("unprofiled-neighbor", producer=lambda request:
            RuntimeEffectResult.succeeded(request.effect_id, evidence={"adapter": "unprofiled"}))
        unprofiled_acceptance = self.advance(unprofiled)
        own, _ = self.later_use("qualified-success")
        accepted = self.advance(own)
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT run_id,artifact_id,accepted_revision FROM " + table
                + " WHERE protective ORDER BY run_id,artifact_id").fetchall(),
                [(unprofiled.run_id, ref.artifact_id, None) for ref in self.refs])

        def uncertain(_request):
            raise RuntimeError("simulated ambiguous adapter outcome")

        unknown, _ = self.later_use("unknown-neighbor", producer=uncertain,
            expected_status=CoordinatorStatus.UNCERTAIN)
        # Existing start authority requires every protective neighbor's own
        # accepted successful use. Unknown history is not reuse permission.
        operator = self.carry_operator()
        refused = operator.admit("refused-after-unknown", "graph-refused-after-unknown",
            ReconcileNode(NodeTarget("api")), graph=operator.graph)
        def effect_truth():
            tables = ("cpk_effect_attempt_intents", "cpk_effect_attempts", "cpk_effect_attempt_outcomes",
                "cpk_effect_configuration_refs", "cpk_configuration_claims", "cpk_configuration_claim_transfers",
                "cpk_configuration_invocation_completions")
            return tuple((table, self.connection.execute("SELECT row_to_json(t)::text FROM " + table
                + " t ORDER BY row_to_json(t)::text").fetchall()) for table in tables)
        # Freeze after legitimate approval/admission/lifecycle setup. This is
        # effect-truth conservation, not a claim of universal command no-op.
        effects_before = effect_truth()
        engine = self.base.engine
        adapter = execution.RecordingAdapter(engine.tracker,
            lambda _context, _request: self.fail("unknown neighbor permitted adapter dispatch"))
        with self.assertRaisesRegex(ExecutionCoordinatorConflict, "^effect attempt start truth is invalid$"):
            engine.coordinator(adapter).execute(replace(engine.command(generation=refused.fence.generation,
                idempotency_key="execute-refused-after-unknown"), run_id=refused.run_id))
        self.assertEqual(adapter.calls, [])
        self.assertEqual(effect_truth(), effects_before)
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

    def test_failed_own_completion_remains_protective_and_cannot_advance_or_transfer(self):
        self.member.advance()
        def failed(request):
            completed = completion_result(request)
            return replace(RuntimeEffectResult.failed(request.effect_id,
                RuntimeEffectFailure("test.failure", "simulated failure")),
                evidence=completed.evidence, observations=completed.observations)
        command, _ = self.later_use("failed-neighbor", producer=failed, expected_status=CoordinatorStatus.FAILED)
        identity = EffectAttemptIdentity(RunId(command.run_id), "activity-failed-neighbor", 1)
        with self.unit_of_work() as uow:
            self.assertIsNotNone(uow.stores.configuration_completions.get(identity),
                "a failed own completion must not be confused with absent profile evidence")
        before = self.snapshot()
        with self.assertRaisesRegex(CurrentGraphAdvancementIncomplete, "^run is not settled as succeeded$"):
            self.advance(command, replay=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers "
            "WHERE run_id=%s", (command.run_id,)).fetchone(), (0,))
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT artifact_id,protective,accepted_revision FROM " + table
                + " WHERE run_id=%s ORDER BY artifact_id", (command.run_id,)).fetchall(),
                [(ref.artifact_id, True, None) for ref in self.refs])
