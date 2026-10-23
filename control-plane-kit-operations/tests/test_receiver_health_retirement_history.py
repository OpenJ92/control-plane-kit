"""N4 history after real O1 logical retirement, with recorded removal premises.

Initial deployment uses actual Operations coordinator/health start/fold owners
and existing recording effect ports. Teardown completion uses O1's explicitly
recorded removal-effect premise. This is not provider deletion or #1912 proof.
"""

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_operations.advancement import AdvanceCurrentGraph, CurrentGraphAdvancementCommandService
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationCodec
from control_plane_kit_operations.lifecycle import CompleteActivityRun, RunLifecycleCommandService
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.receiver_health_execution_fixture import ReceiverHealthExecutionFixture
from tests.receiver_recorded_completion_fixture import retain_completion_inputs
from tests.test_cpk_server_adapters import operator_principal
from tests.test_managed_application_chain import now


class ReceiverHealthRetirementHistoryTests(ReceiverHealthExecutionFixture):
    now = staticmethod(now)

    def advance_completed(self, suffix):
        context, command = self.execution_context()
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(self.plan_id)
        return CurrentGraphAdvancementCommandService(self.unit_of_work, clock=now,
            id_factory=self.ids["advance"]).execute(AdvanceCurrentGraph(
                "workspace-a", self.run_id, self.plan_id, plan.base_graph_id,
                plan.base_realized_projection_id, plan.desired_graph_id,
                plan.desired_realized_projection_id, plan.desired_graph_revision,
                command.authority, command.fence, IdempotencyKey("advance-" + suffix)))

    async def test_exact_original_v2_preparation_survives_real_logical_retirement(self):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            if result["run_status"] == "succeeded":
                break
            self.assertEqual(result["coordinator_status"], "progressed")
        self.assertEqual(result["run_status"], "succeeded")
        self.assertEqual(len(self.health.signed_reads), 3)
        preparation = self.health.signed_reads[0][1]
        codec = HealthEffectPreparationCodec()
        original_bytes = codec.encode_canonical_bytes(preparation)
        with self.unit_of_work() as uow:
            original_attempt = uow.stores.effect_attempts.get(preparation.identity)
            original_intent = uow.stores.effect_attempt_intents.get(preparation.identity)
            self.assertEqual(original_attempt.state.status.value, "succeeded")
            self.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)
        accepted = self.advance_completed("initial")
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            for target in self.targets.values():
                origin = uow.stores.graphs.receiver_introduction("workspace-a", target.receiver_id)
                self.assertEqual(origin.first_accepted_action_id, accepted.action.action_id)
                self.assertIsNone(origin.retired_action_id)
        prepared = await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={
                "desired_graph": DEFAULT_GRAPH_CODEC.encode(DeploymentGraph("removed")),
                "expected_current": {"authored_graph_id": workspace.current_graph_id,
                    "realized_projection_id": workspace.current_realized_projection_id},
                "expected_desired": {"authored_graph_id": workspace.desired_graph_id,
                    "realized_projection_id": workspace.desired_realized_projection_id},
                "expected_desired_graph_revision": workspace.desired_graph_revision,
                "title": "Logical retirement premise", "idempotency_key": "prepare-removal"})
        self.assertEqual(prepared["status"], "approval-required")
        self.plan_id = prepared["plan_id"]
        detail = await self.invoke("read.plan-detail", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a", "plan_id": self.plan_id})
        self.plan_descriptor = detail["plan"]
        with self.unit_of_work() as uow:
            self.plan = uow.stores.activity_history.get_plan(self.plan_id).plan
            self.assertTrue(self.plan.activities)
            for target in self.targets.values():
                self.assertIsNone(uow.stores.graphs.receiver_introduction("workspace-a", target.receiver_id).retired_action_id)
        await self.invoke("command.approval.decide", ControlPlaneServiceRole.APPROVAL,
            path={"workspace_id": "workspace-a", "approval_id": prepared["approval_request_id"]},
            payload={"session_id": self.plan_descriptor["session_id"], "decision": "approved", "idempotency_key": "approve-removal"},
            principal=operator_principal(subject_id="manager-a", scopes=tuple(PolicyScope)))
        admitted = await self.invoke("command.deployment.admit", ControlPlaneServiceRole.ADMISSION,
            path={"workspace_id": "workspace-a", "plan_id": self.plan_id}, payload={
                "session_id": self.plan_descriptor["session_id"], "approval_request_id": prepared["approval_request_id"],
                "readiness": [], "idempotency_key": "admit-removal"})
        self.request_id = admitted["execution_request_id"]
        claimed = await self.invoke("command.run.claim", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.request_id}, principal=self.worker,
            payload={"lease_duration_seconds": 1800, "idempotency_key": "claim-removal"})
        self.run_id, self.generation = claimed["run_id"], claimed["claim_generation"]
        self.assertNotEqual(self.run_id, preparation.identity.run_id.value)
        await self.invoke("command.run.start", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.run_id}, principal=self.worker,
            payload={"claim_generation": self.generation, "idempotency_key": "start-removal"})
        context, command = self.execution_context()
        # ONLY the separate teardown run gets O1's completion premise. Never
        # overwrite the initial run's actual health attempt/intent/outcome.
        retain_completion_inputs(self, context)
        RunLifecycleCommandService(self.unit_of_work, clock=now, id_factory=self.ids["lifecycle"]).execute(
            CompleteActivityRun(self.run_id, command.authority, command.fence, IdempotencyKey("complete-removal")))
        retired = self.advance_completed("removal")
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            current = uow.stores.realized_graphs.get(workspace.current_realized_projection_id)
            self.assertEqual(DEFAULT_GRAPH_CODEC.decode(current.graph_descriptor).nodes, {})
            for target in self.targets.values():
                origin = uow.stores.graphs.receiver_introduction("workspace-a", target.receiver_id)
                self.assertEqual(origin.first_accepted_action_id, accepted.action.action_id)
                self.assertEqual(origin.retired_action_id, retired.action.action_id)
        before = self.durable_snapshot(), self.receiver_snapshot()
        with self.forbid_current_health():
            with self.unit_of_work() as uow:
                restored = uow.stores.health_effect_preparations.get(preparation.identity)
                self.assertEqual(uow.stores.effect_attempts.get(preparation.identity), original_attempt)
                self.assertEqual(uow.stores.effect_attempt_intents.get(preparation.identity), original_intent)
        self.assertEqual(restored, preparation)
        self.assertEqual(codec.encode_canonical_bytes(restored), original_bytes)
        self.assertEqual((self.durable_snapshot(), self.receiver_snapshot()), before)
