"""Retained public content through real Operations and PostgreSQL owners."""

from dataclasses import replace

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.planning import ObserveManagementBootstrap, ObserveNodeHealth, ReconcileNode, StartNode
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effects import ImagePullAuthority
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.cpk_server import CpkServerApplicationError
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.test_cpk_server_adapters import operator_principal
from tests import test_managed_application_chain as chain
from tests.test_managed_configuration_update_admission import APPLICATION


class ManagedConfigurationUpdateExecutionTests(chain.ManagedApplicationFixture):
    application_artifacts = (APPLICATION,)
    complete_initial = chain.ManagedUpdateExecutionTests.complete_initial

    async def desired_update(self):
        node = self.graph.node("api")
        return self.graph.update_node(replace(node, configuration_artifacts=tuple(
            replace(value, content='{"mode":"green"}', source_digest=None)
            if value.artifact_id == "application" else value
            for value in node.configuration_artifacts
        )))

    async def prepare_content(self, *, prepare_only=False):
        # Image subclasses select the new profile; content retains the exact V1 law.
        expected_profile = (self.expected_derivation_profile()
            if hasattr(self, "expected_derivation_profile") else PlanDerivationProfile.MANAGED_UPDATE_V1)
        self.probe_forged_managed_fold = True
        await self.complete_initial()
        initial_execution = self.rows("cpk_execution_requests"), self.rows("cpk_effect_attempts")
        self.origin_calls = tuple(self.runtime.calls)
        self.origin_history = self.history()
        with self.unit_of_work() as uow:
            self.before_update = uow.stores.workspaces.get("workspace-a")
        desired = await self.desired_update()
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        payload = {"desired_graph": DEFAULT_GRAPH_CODEC.encode(desired),
            "expected_current": {"authored_graph_id": workspace.current_graph_id,
                "realized_projection_id": workspace.current_realized_projection_id},
            "expected_desired": {"authored_graph_id": workspace.desired_graph_id,
                "realized_projection_id": workspace.desired_realized_projection_id},
            "expected_desired_graph_revision": workspace.desired_graph_revision,
            "title": "Retained content", "idempotency_key": "prepare-content"}
        # A stale current fence cannot author a replacement plan or dispatch.
        before = self.durable_snapshot(), self.effects()
        with self.assertRaises(CpkServerApplicationError):
            await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
                path={"workspace_id": "workspace-a"}, payload={**payload,
                    "idempotency_key": "prepare-stale-content",
                    "expected_current": {**payload["expected_current"], "authored_graph_id": "foreign"}})
        self.assertEqual((self.durable_snapshot(), self.effects()), before)
        prepared = await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload=payload)
        self.assertEqual(prepared["status"], "approval-required")
        detail = await self.invoke("read.plan-detail", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a", "plan_id": prepared["plan_id"]})
        self.assertEqual(detail["plan"]["derivation_profile"], expected_profile.value)
        self.update_plan = detail["plan"]
        with self.unit_of_work() as uow:
            self.record = uow.stores.activity_history.get_plan(prepared["plan_id"])
        self.assertIs(self.record.derivation_profile, expected_profile)
        if prepare_only:
            self.assertEqual((self.rows("cpk_execution_requests"), self.rows("cpk_effect_attempts")),
                initial_execution)
            self.assertFalse(any(row[3] == self.record.plan_id for row in self.rows("cpk_execution_requests")))
            return
        await self.invoke("command.approval.decide", ControlPlaneServiceRole.APPROVAL,
            path={"workspace_id": "workspace-a", "approval_id": prepared["approval_request_id"]},
            payload={"session_id": detail["plan"]["session_id"], "decision": "approved",
                "idempotency_key": "approve-content"},
            principal=operator_principal(subject_id="manager-a", scopes=(PolicyScope.PLAN_APPROVE,)))
        admitted = await self.invoke("command.deployment.admit", ControlPlaneServiceRole.ADMISSION,
            path={"workspace_id": "workspace-a", "plan_id": prepared["plan_id"]}, payload={
                "session_id": detail["plan"]["session_id"],
                "approval_request_id": prepared["approval_request_id"], "readiness": [],
                "idempotency_key": "admit-content"})
        claimed = await self.invoke("command.run.claim", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": admitted["execution_request_id"]},
            principal=self.worker, payload={"lease_duration_seconds": 1800, "idempotency_key": "claim-content"})
        self.update_run, self.update_generation = claimed["run_id"], claimed["claim_generation"]
        await self.invoke("command.run.start", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"claim_generation": self.update_generation, "idempotency_key": "start-content"})
        self.health.responses.append(NativeConnectionOutcome.CONNECTED)
        # Reconstruct services: update execution consumes persisted facts.
        self.app = self.application()

    async def execute_content(self):
        for number in range(1, len(self.record.plan.activities) + 2):
            payload = {"claim_generation": self.update_generation,
                "idempotency_key": f"execute-content-{number}", "max_effects": 1}
            result = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                path={"workspace_id": "workspace-a", "run_id": self.update_run},
                principal=self.worker, payload=payload)
            before = self.durable_snapshot(), self.effects(), self.rows("cpk_effect_configuration_refs")
            replay = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                path={"workspace_id": "workspace-a", "run_id": self.update_run},
                principal=self.worker, payload=payload, app=self.application())
            self.assertEqual(replay, result)
            self.assertEqual((self.durable_snapshot(), self.effects(), self.rows("cpk_effect_configuration_refs")), before)
            if result["coordinator_status"] in ("completed", "uncertain"):
                return result
            self.assertEqual(result["coordinator_status"], "progressed", result)
        self.fail("content execution did not reach a terminal boundary")

    async def test_replacement_preserves_wrapper_origin_and_history_then_advances(self):
        await self.prepare_content()
        result = await self.execute_content()
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        self.assertTrue(self.forged_managed_fold_refused)
        update, = self.runtime.calls[len(self.origin_calls):]
        self.assertIs(type(update.operation), ReconcileNode)
        self.assertEqual(update.operation.target.node_id, "api")
        original, = (value for value in self.origin_calls if type(value.operation) is StartNode
            and value.operation.target.node_id == "api")
        old = {value.artifact_id: value for value in original.configuration_instances.instances}
        new = {value.artifact_id: value for value in update.configuration_instances.instances}
        self.assertEqual(old.keys(), new.keys())
        wrapper, = old.keys() - {"application"}
        self.assertEqual(new[wrapper], old[wrapper])
        self.assertNotEqual(new["application"].allocation_id, old["application"].allocation_id)
        self.assertNotEqual(new["application"].content_digest, old["application"].content_digest)
        reads = [preparation for request, preparation, _ in self.health.signed_reads
            if type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api"]
        self.assertEqual(reads[0].request.target, reads[-1].request.target)
        self.assertEqual(self.history(), self.origin_history)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)
        plan = self.update_plan
        advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"plan_id": self.record.plan_id,
                "expected_current_graph_id": self.before_update.current_graph_id,
                "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
                "desired_graph_id": plan["desired_graph_id"],
                "desired_realized_projection_id": plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": plan["desired_graph_revision"],
                "claim_generation": self.update_generation, "idempotency_key": "advance-content"})
        self.assertEqual(advanced["to_graph_id"], plan["desired_graph_id"])

    async def test_missing_replacement_receipt_preserves_uncertainty_and_original_retry(self):
        await self.prepare_content()
        self.runtime.installation_disposition = None
        def desired_health_count():
            return sum(type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api"
                for request, _, _ in self.health.signed_reads)
        before_reads = desired_health_count()
        result = await self.execute_content()
        self.assertEqual(result["coordinator_status"], "uncertain")
        self.assertEqual(desired_health_count(), before_reads)
        self.assertEqual(len(self.runtime.calls) - len(self.origin_calls), 1)
        self.assertEqual(self.history(), self.origin_history)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)

    async def test_fresh_selected_pull_authority_refuses_before_attempt_or_dispatch(self):
        await self.prepare_content()
        with self.unit_of_work() as uow:
            uow.stores.image_pull_authorities.register(workspace_id="workspace-a",
                authority=ImagePullAuthority("ghcr.io", "test/chain-api", "secret://local/workspace-a/pull"),
                admitted_by="operator-a", admitted_at=chain.now())
            uow.commit()
        reconcile, = (activity for activity in self.record.plan.activities
            if type(activity.operation) is ReconcileNode)
        def mutation_snapshot():
            attempts = tuple(row for row in self.rows("cpk_effect_attempts")
                if row[0] == self.update_run and row[1] == reconcile.activity_id.value)
            self.assertEqual(attempts, (), "no attempt number may exist for the forbidden replacement")
            return (self.effects()[:3], self.rows("cpk_effect_configuration_refs"),
                self.rows("cpk_configuration_claims"))
        before = mutation_snapshot()
        native_before, signed_before = len(self.health.native_reads), len(self.health.signed_reads)

        def assert_no_replacement():
            self.assertEqual(mutation_snapshot(), before)
            observed = (*self.health.native_reads[native_before:],
                *(request for request, _, _ in self.health.signed_reads[signed_before:]))
            for request in observed:
                self.assertIs(type(request.operation), ObserveManagementBootstrap)
                self.assertEqual(request.source.run_id.value, self.update_run)
                self.assertEqual(request.operation, self.record.plan.activity(request.activity_id).operation)
            with self.unit_of_work() as uow:
                self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                    self.before_update.current_graph_id)

        with self.assertRaisesRegex(InvalidOperationCommand,
                "^managed content update requires no image pull authority$"):
            for number in range(len(self.record.plan.activities) + 1):
                observed_before = len(self.health.native_reads) + len(self.health.signed_reads)
                result = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                    path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
                    payload={"claim_generation": self.update_generation,
                        "idempotency_key": f"execute-pull-refused-{number}", "max_effects": 1})
                self.assertEqual(result["coordinator_status"], "progressed")
                self.assertGreater(len(self.health.native_reads) + len(self.health.signed_reads), observed_before)
                assert_no_replacement()
            self.fail("ordinary preflight never reached the forbidden replacement boundary")
        assert_no_replacement()
