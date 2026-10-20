"""Coupled declaration installations use real Operations/PostgreSQL history."""
from dataclasses import replace
import json
from unittest import mock

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.planning import ObserveNodeHealth, ReconcileNode, StartNode
from control_plane_kit_core.products import ProductReference
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.cpk_server import CpkServerApplicationError
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import derive_activity_plan, PlanDerivationProfile
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests import test_managed_application_chain as chain
from tests import test_managed_configuration_update_execution as content
from tests import test_managed_verifier_refresh as refresh
from tests.test_managed_configuration_update_admission import APPLICATION
from tests.test_managed_declaration_update_admission import declaration_successor, selected_wrapper
from tests.health_receiver_trust_fixture import PUBLIC_KEY_C


class ManagedDeclarationUpdateExecutionTests(chain.ManagedApplicationFixture):
    application_artifacts = (APPLICATION,)
    complete_initial = chain.ManagedUpdateExecutionTests.complete_initial
    prepare_content = content.ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = content.ManagedConfigurationUpdateExecutionTests.execute_content
    register_public = refresh.ManagedVerifierRefreshTests.register_public
    prepare_payload = refresh.ManagedVerifierRefreshTests.prepare_payload

    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        node = graph.node("api")
        graph = graph.update_node(replace(node, metadata={**node.metadata,
            "oci_image": documents["api"].product.image.execution_reference}))
        return DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(graph)), documents, registry

    async def desired_update(self):
        _, configured = selected_wrapper(self.graph.node("api"))
        family, = [value for value in configured.verifiers
            if value.purpose is DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ]
        self.register_public(family.purpose, family.public_keys[0], active=True)
        self.successor_graph, self.successor_document = declaration_successor(self.graph,
            self.documents["api"].product, node_id="api",
            change_default=not self.documents["api"].product.runtime_contract.configuration_requirements)
        await self.invoke("command.product.import", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={
                "descriptor_document": json.loads(self.successor_document.content),
                "imported_at": chain.now(), "idempotency_key": "product-api-declaration-successor"})
        self.documents["api-declaration-successor"] = self.successor_document
        return self.successor_graph

    def drift_public_snapshot(self):
        self.assertEqual(self.tracker.active, 0)
        key = self.keys["workload"]
        self.register_public(key.purpose, replace(key.public_key,
            key_id="declaration-overlap", public_key_pem=PUBLIC_KEY_C))

    def advancement_payload(self, key):
        return {"plan_id": self.record.plan_id,
            "expected_current_graph_id": self.before_update.current_graph_id,
            "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
            "desired_graph_id": self.update_plan["desired_graph_id"],
            "desired_realized_projection_id": self.update_plan["desired_realized_projection_id"],
            "expected_desired_graph_revision": self.update_plan["desired_graph_revision"],
            "claim_generation": self.update_generation, "idempotency_key": key}

    async def assert_not_advanced(self, key):
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)
        with self.assertRaises(chain.CurrentGraphAdvancementIncomplete):
            await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
                path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
                payload=self.advancement_payload(key))

    def desired_health(self):
        return [(request, preparation, outcome) for request, preparation, outcome in self.health.signed_reads
            if type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api"
            and request.source.run_id.value == self.update_run]

    def use_required_configuration(self):
        from tests.required_configuration_fixture import required_document, bind_document
        wrapper, _ = selected_wrapper(self.graph.node("api"))
        document = required_document(self.documents["api"], {wrapper.artifact_id})
        self.documents["api"] = document
        self.graph = DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(
            self.graph.update_node(bind_document(self.graph.node("api"), document))))

    async def test_both_exact_installations_then_desired_health_replay_and_advance(self):
        await self.assert_both_exact_installations_then_desired_health_replay_and_advance()

    async def test_required_configuration_installations_health_replay_and_advance(self):
        self.use_required_configuration()
        await self.assert_both_exact_installations_then_desired_health_replay_and_advance()
        self.assertTrue(self.documents["api"].product.runtime_contract.configuration_requirements)
        self.assertTrue(self.successor_document.product.runtime_contract.configuration_requirements)
        for request in self.runtime.calls:
            for material in request.products:
                self.assertEqual(material.product.runtime_contract.configuration_requirements, ())

    async def assert_both_exact_installations_then_desired_health_replay_and_advance(self):
        await self.prepare_content()
        result = await self.execute_content()
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        updates = tuple(self.runtime.calls[len(self.origin_calls):])
        self.assertEqual([(type(value.operation), value.operation.target.node_id) for value in updates],
            [(ReconcileNode, "api"), (ReconcileNode, "gateway")])
        records = self.mutation_records(updates)
        for request, (_, _, outcome) in zip(updates, records, strict=True):
            self.assertEqual(outcome.outcome.result.evidence["configuration_installation_receipt"]["disposition"], "replaced")
            original, = [value for value in self.origin_calls if type(value.operation) is StartNode
                and value.operation.target.node_id == request.operation.target.node_id]
            old = {value.artifact_id: value for value in original.configuration_instances.instances}
            new = {value.artifact_id: value for value in request.configuration_instances.instances}
            expected_node = self.successor_graph.node(request.operation.target.node_id)
            self.assertEqual(old.keys(), new.keys())
            changed = "gateway-health-targets" if request.operation.target.node_id == "gateway" else selected_wrapper(expected_node)[0].artifact_id
            for identity in old:
                if identity == changed:
                    self.assertNotEqual(new[identity].allocation_id, old[identity].allocation_id)
                    artifact, = [value for value in expected_node.configuration_artifacts if value.artifact_id == identity]
                    self.assertEqual(new[identity].content_digest, artifact.content_digest)
                else:
                    self.assertEqual(new[identity], old[identity])
        workload, = updates[0].products
        self.assertEqual(workload.reference, ProductReference.from_document(self.successor_document))
        desired_reads = self.desired_health()
        self.assertTrue(desired_reads)
        _, configured = selected_wrapper(self.successor_graph.node("api"))
        for request, preparation, _ in desired_reads:
            self.assertEqual(preparation.request.target, configured.target)
            self.assertEqual(preparation.request.declaration_identity, configured.declaration.identity())
            self.assertLess(self.effect_order.index(updates[1].activity_id.value), self.effect_order.index(request.activity_id.value))
        self.assertEqual(self.history(), self.origin_history)
        advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload=self.advancement_payload("advance-declaration"))
        self.assertEqual(advanced["to_graph_id"], self.update_plan["desired_graph_id"])
        self.assertEqual(self.mutation_records(updates), records)

    async def test_unchanged_but_noncurrent_public_snapshot_refuses_before_plan_write(self):
        await self.complete_initial()
        desired = await self.desired_update()
        self.assertTrue(derive_activity_plan(Deploy(validate_graph(self.graph), validate_graph(desired)),
            profile=PlanDerivationProfile.MANAGED_UPDATE_V1).ready_for_execution)
        self.drift_public_snapshot()
        before = self.rows("cpk_activity_plans"), self.effects()
        with self.assertRaises(CpkServerApplicationError):
            await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
                path={"workspace_id": "workspace-a"}, payload=self.prepare_payload(desired, "stale-declaration-authority"))
        self.assertEqual((self.rows("cpk_activity_plans"), self.effects()), before)

    async def assert_locked_drift_refuses(self, node_id):
        await self.prepare_content()
        original_start = chain.EffectAttemptStartService.execute
        proposals = []
        retained = []
        snapshots = []
        def drift_after_proposal(service, command):
            if type(command.intent.operation) is ReconcileNode and command.intent.operation.target.node_id == node_id:
                self.assertFalse(proposals)
                proposals.append(command.intent)
                retained.extend(self.mutation_records(tuple(self.runtime.calls[len(self.origin_calls):])))
                snapshots.append((self.rows("cpk_effect_attempts"), self.rows("cpk_effect_configuration_refs"),
                    self.rows("cpk_configuration_claims")))
                self.drift_public_snapshot()
            return original_start(service, command)
        with mock.patch.object(chain.EffectAttemptStartService, "execute", drift_after_proposal), self.assertRaises(
                (CpkServerApplicationError, InvalidOperationCommand, ExecutionCoordinatorConflict)):
            await self.execute_content()
        self.assertEqual(len(proposals), 1)
        self.assertEqual((self.rows("cpk_effect_attempts"), self.rows("cpk_effect_configuration_refs"),
            self.rows("cpk_configuration_claims")), snapshots[0])
        updates = tuple(self.runtime.calls[len(self.origin_calls):])
        self.assertEqual([value.operation.target.node_id for value in updates], ["api"] if node_id == "gateway" else [])
        self.assertEqual(self.mutation_records(updates), tuple(retained))
        self.assertEqual(self.desired_health(), [])
        self.assertEqual(self.history(), self.origin_history)
        await self.assert_not_advanced("no-advance-drift-" + node_id)

    async def test_authority_drift_after_workload_proposal_refuses_locked_first_start(self):
        await self.assert_locked_drift_refuses("api")

    async def test_authority_drift_after_gateway_proposal_preserves_workload_outcome(self):
        await self.assert_locked_drift_refuses("gateway")

    async def test_required_configuration_authority_drift_before_workload_start_refuses(self):
        self.use_required_configuration()
        await self.assert_locked_drift_refuses("api")

    async def test_required_configuration_authority_drift_before_gateway_preserves_workload(self):
        self.use_required_configuration()
        await self.assert_locked_drift_refuses("gateway")

    async def assert_bad_receipt(self, node_id, *, mismatch=False):
        await self.prepare_content()
        original_execute = self.runtime.execute_with_authority
        def bad_receipt(request, authority):
            if type(request.operation) is ReconcileNode and request.operation.target.node_id == node_id:
                if mismatch:
                    self.runtime.installation_selection_fingerprint = "f" * 64
                else:
                    self.runtime.installation_disposition = None
            return original_execute(request, authority)
        self.runtime.execute_with_authority = bad_receipt
        result = await self.execute_content()
        self.assertEqual(result["coordinator_status"], "uncertain")
        updates = tuple(self.runtime.calls[len(self.origin_calls):])
        self.assertEqual([value.operation.target.node_id for value in updates],
            ["api", "gateway"] if node_id == "gateway" else ["api"])
        if node_id == "gateway":
            self.mutation_records(updates[:1])
        self.assertEqual(self.desired_health(), [])
        self.assertEqual(self.history(), self.origin_history)
        await self.assert_not_advanced("no-advance-receipt-" + node_id)

    async def test_missing_workload_receipt_prevents_gateway_and_health(self):
        await self.assert_bad_receipt("api")

    async def test_mismatched_gateway_receipt_retains_workload_and_prevents_health(self):
        await self.assert_bad_receipt("gateway", mismatch=True)

    async def test_failed_desired_health_retains_both_installations_and_original_replay(self):
        await self.prepare_content()
        original_read = self.health.observe_signed
        async def failed_health(realization, request, authority):
            result = await original_read(realization, request, authority)
            if type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api":
                result = replace(result, outcome=NodeHealthReadOutcome.UNHEALTHY)
                self.health.signed_reads[-1] = (request, authority.preparation, result)
            return result
        self.health.observe_signed = failed_health
        for number in range(len(self.record.plan.activities) + 2):
            payload = {"claim_generation": self.update_generation,
                "idempotency_key": f"declaration-failed-health-{number}", "max_effects": 1}
            result = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker, payload=payload)
            if result["coordinator_status"] == "failed":
                break
            self.assertEqual(result["coordinator_status"], "progressed", result)
        else:
            self.fail("desired health did not fail")
        updates = tuple(self.runtime.calls[len(self.origin_calls):])
        self.assertEqual([value.operation.target.node_id for value in updates], ["api", "gateway"])
        records = self.mutation_records(updates)
        before = self.durable_snapshot(), self.effects()
        replay = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload=payload, app=self.application())
        self.assertEqual(replay, result)
        self.assertEqual((self.durable_snapshot(), self.effects()), before)
        self.assertEqual(self.mutation_records(updates), records)
        self.assertEqual(self.history(), self.origin_history)
        await self.assert_not_advanced("no-advance-declaration-health")
