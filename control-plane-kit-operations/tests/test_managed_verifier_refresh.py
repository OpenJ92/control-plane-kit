"""Fresh verifier authority over the existing real Operations update fixture."""

from dataclasses import replace
from contextlib import nullcontext
from unittest import mock

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome
from control_plane_kit_core.planning import ObserveNodeHealth, ReconcileNode, StartNode
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.cpk_server import CpkServerApplicationError
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey, delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import derive_activity_plan, PlanDerivationProfile
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests import test_managed_application_chain as chain
from tests import test_managed_configuration_update_execution as content
from tests.test_managed_configuration_update_admission import APPLICATION
from tests.health_receiver_trust_fixture import PUBLIC_KEY_C


class ManagedVerifierRefreshTests(chain.ManagedApplicationFixture):
    application_artifacts = (APPLICATION,)
    complete_initial = chain.ManagedUpdateExecutionTests.complete_initial
    prepare_content = content.ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = content.ManagedConfigurationUpdateExecutionTests.execute_content

    def selected(self, graph):
        node = graph.node("api")
        artifact = select_receiver_node_control_configuration_artifact(
            artifacts=node.configuration_artifacts,
            environment=node.public_environment + node.socket_environment,
            control_surfaces=node.block_spec.control_surfaces)
        return artifact, ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode())

    def register_public(self, purpose, key, *, active=False):
        original = self.keys["workload"]
        fields = dict(workspace_id="workspace-a", purpose=purpose, issuer=original.issuer,
            public_key=key, private_key_reference=original.private_key_reference)
        record = RegisteredDelegationSigningKey(
            registration_id=delegation_signing_key_registration_id_for(**fields),
            **fields, admitted_by="operator-a", admitted_at=chain.now())
        with self.unit_of_work() as uow:
            uow.stores.delegation_signing_keys.register(record)
            if active:
                record = uow.stores.delegation_signing_keys.activate("workspace-a", purpose,
                    original.issuer, key.key_id, activated_by="operator-a", activated_at=chain.now())
            uow.commit()
        return record

    async def desired_update(self):
        self.origin_order_count = len(self.effect_order)
        artifact, configured = self.selected(self.graph)
        surface, = (family for family in configured.verifiers
            if family.purpose is DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ)
        self.register_public(surface.purpose, surface.public_keys[0], active=True)
        extra = replace(self.keys["workload"].public_key, key_id="refresh-overlap", public_key_pem=PUBLIC_KEY_C)
        self.overlap = self.register_public(self.keys["workload"].purpose, extra)
        families = tuple(replace(family, public_keys=(*family.public_keys, extra))
            if family.purpose is self.overlap.purpose else family for family in configured.verifiers)
        self.refresh_graph = self.with_families(self.graph, families)
        return self.refresh_graph

    def with_families(self, graph, families):
        artifact, configured = self.selected(graph)
        changed = replace(artifact, content=ReceiverNodeControlConfigurationCodec().encode_bytes(
            replace(configured, verifiers=families)).decode(), source_digest=None)
        node = graph.node("api")
        return graph.update_node(replace(node, configuration_artifacts=tuple(
            changed if value.artifact_id == artifact.artifact_id else value
            for value in node.configuration_artifacts)))

    def prepare_payload(self, desired, key):
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        return {"desired_graph": DEFAULT_GRAPH_CODEC.encode(desired),
            "expected_current": {"authored_graph_id": workspace.current_graph_id,
                "realized_projection_id": workspace.current_realized_projection_id},
            "expected_desired": {"authored_graph_id": workspace.desired_graph_id,
                "realized_projection_id": workspace.desired_realized_projection_id},
            "expected_desired_graph_revision": workspace.desired_graph_revision,
            "title": "Verifier refresh", "idempotency_key": key}

    async def test_non_authoritative_material_refuses_fresh_plan_before_plan_writes(self):
        await self.complete_initial()
        desired = await self.desired_update()
        _, configured = self.selected(desired)
        for case in ("issuer", "surface-key", "health-key", "missing-active"):
            families = []
            for family in configured.verifiers:
                if case == "issuer":
                    family = replace(family, issuer="foreign-issuer")
                elif ((case == "surface-key" and family.purpose is DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ)
                        or (case == "health-key" and family.purpose is self.overlap.purpose)):
                    family = replace(family, public_keys=tuple(replace(key, key_id="foreign-" + key.key_id)
                        for key in family.public_keys))
                elif case == "missing-active" and family.purpose is self.overlap.purpose:
                    family = replace(family, public_keys=(self.overlap.public_key,))
                families.append(family)
            candidate = self.with_families(desired, tuple(families))
            with self.subTest(case=case):
                await self.assert_plan_refused(candidate, "bad-" + case)

    async def assert_plan_refused(self, candidate, key):
        plan = derive_activity_plan(Deploy(validate_graph(self.graph), validate_graph(candidate)),
            profile=PlanDerivationProfile.MANAGED_UPDATE_V1)
        self.assertTrue(plan.ready_for_execution, "negative must reach authority, not Core refusal")
        before = self.rows("cpk_activity_plans"), self.effects()
        with self.assertRaises(CpkServerApplicationError):
            await self.invoke("command.deployment.prepare", ControlPlaneServiceRole.PLANNING,
                path={"workspace_id": "workspace-a"}, payload=self.prepare_payload(candidate, key))
        self.assertTrue((self.rows("cpk_activity_plans"), self.effects()) == before,
            "refused refresh changed plans or effects")

    async def test_same_key_id_with_different_public_material_refuses(self):
        await self.complete_initial()
        desired = await self.desired_update()
        _, configured = self.selected(desired)
        families = tuple(replace(family, public_keys=tuple(
            replace(key, public_key_pem=self.keys["transit"].public_key.public_key_pem)
            if key.key_id == self.keys["workload"].key_id else key for key in family.public_keys))
            if family.purpose is self.overlap.purpose else family for family in configured.verifiers)
        await self.assert_plan_refused(self.with_families(desired, families), "wrong-public-material")

    async def test_revoked_verify_only_member_refuses_with_current_signer_unchanged(self):
        await self.complete_initial()
        desired = await self.desired_update()
        with self.unit_of_work() as uow:
            store = uow.stores.delegation_signing_keys
            store.revoke("workspace-a", self.overlap.purpose, self.overlap.issuer,
                self.overlap.key_id, revoked_by="operator-a", revoked_at=chain.now())
            self.assertEqual(store.require_unambiguous_active("workspace-a", self.overlap.purpose),
                self.keys["workload"])
            uow.commit()
        await self.assert_plan_refused(desired, "revoked-overlap")

    async def test_changed_snapshot_before_start_refuses_without_replacement(self):
        await self.assert_changed_snapshot_refused(at_start=False)

    async def test_drift_after_proposal_is_rechecked_by_locked_first_start(self):
        await self.assert_changed_snapshot_refused(at_start=True)

    async def assert_changed_snapshot_refused(self, *, at_start):
        await self.prepare_content()
        # The extra member is compatible with the current signer, but changes
        # the complete snapshot approved for this not-yet-started installation.
        def change_snapshot():
            self.assertEqual(self.tracker.active, 0)
            self.register_public(self.overlap.purpose,
                replace(self.overlap.public_key, key_id="added-after-approval",
                    public_key_pem=self.keys["transit"].public_key.public_key_pem))
        original_start = chain.EffectAttemptStartService.execute
        proposals = []
        def start_after_drift(service, command):
            if type(command.intent.operation) is ReconcileNode:
                self.assertFalse(proposals)
                proposals.append(command.intent)
                change_snapshot()
            return original_start(service, command)
        if not at_start:
            change_snapshot()
        before = self.rows("cpk_effect_configuration_refs"), self.rows("cpk_configuration_claims")
        # The hook changes real owner truth only after the genuine optimistic
        # proposal exists. The ordinary public first-start owner still runs.
        hook = mock.patch.object(chain.EffectAttemptStartService, "execute", start_after_drift) if at_start else nullcontext()
        with hook, self.assertRaises((CpkServerApplicationError, InvalidOperationCommand, ExecutionCoordinatorConflict)):
            await self.execute_content()
        if at_start:
            self.assertEqual(len(proposals), 1)
        self.assertEqual(tuple(self.runtime.calls), self.origin_calls)
        self.assertEqual((self.rows("cpk_effect_configuration_refs"), self.rows("cpk_configuration_claims")), before)
        # Births live in the same complete ref rows under is_birth, not in a
        # separate allocation table; full equality includes every birth.
        reconcile, = (item for item in self.record.plan.activities if type(item.operation) is ReconcileNode)
        self.assertFalse(any(row[0] == self.update_run and row[1] == reconcile.activity_id.value
            for row in self.rows("cpk_effect_attempts")))
        self.assertEqual(self.history(), self.origin_history)

    async def test_current_refresh_installs_exact_wrapper_then_health_and_advances(self):
        await self.prepare_content()
        result = await self.execute_content()
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        update, = self.runtime.calls[len(self.origin_calls):]
        self.assertIs(type(update.operation), ReconcileNode)
        original, = (item for item in self.origin_calls if type(item.operation) is StartNode
            and item.operation.target.node_id == "api")
        before = {ref.artifact_id: ref for ref in original.configuration_instances.instances}
        after = {ref.artifact_id: ref for ref in update.configuration_instances.instances}
        artifact, configured = self.selected(self.refresh_graph)
        self.assertEqual(after["application"], before["application"])
        self.assertNotEqual(after[artifact.artifact_id].allocation_id, before[artifact.artifact_id].allocation_id)
        self.assertEqual(after[artifact.artifact_id].content_digest, artifact.content_digest)
        material, = update.products
        self.assertIn(artifact, material.product.runtime_contract.configuration_artifacts)
        desired_reads = [(request, prep) for request, prep, _ in self.health.signed_reads
            if type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api"
            and request.source.run_id.value == self.update_run]
        self.assertTrue(desired_reads)
        for request, prep in desired_reads:
            self.assertEqual(prep.request.target, configured.target)
            order = self.effect_order[self.origin_order_count:]
            self.assertLess(order.index(update.activity_id.value), order.index(request.activity_id.value))
        self.assertEqual(self.history(), self.origin_history)
        advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"plan_id": self.record.plan_id,
                "expected_current_graph_id": self.before_update.current_graph_id,
                "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
                "desired_graph_id": self.update_plan["desired_graph_id"],
                "desired_realized_projection_id": self.update_plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": self.update_plan["desired_graph_revision"],
                "claim_generation": self.update_generation, "idempotency_key": "advance-refresh"})
        self.assertEqual(advanced["to_graph_id"], self.update_plan["desired_graph_id"])

    test_missing_refresh_receipt_preserves_uncertainty = (
        content.ManagedConfigurationUpdateExecutionTests.test_missing_replacement_receipt_preserves_uncertainty_and_original_retry)

    async def test_failed_health_preserves_actual_replacement_without_advancement(self):
        await self.prepare_content()
        original_read = self.health.observe_signed
        async def failed_desired_health(realization, request, authority):
            result = await original_read(realization, request, authority)
            if (type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api"
                    and request.source.run_id.value == self.update_run):
                result = replace(result, outcome=NodeHealthReadOutcome.UNHEALTHY)
                self.health.signed_reads[-1] = (request, authority.preparation, result)
            return result
        self.health.observe_signed = failed_desired_health
        for number in range(len(self.record.plan.activities) + 2):
            payload = {"claim_generation": self.update_generation,
                "idempotency_key": f"failed-refresh-{number}", "max_effects": 1}
            result = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker, payload=payload)
            if result["coordinator_status"] == "failed":
                break
            self.assertEqual(result["coordinator_status"], "progressed", result)
        else:
            self.fail("desired health did not fail")
        update, = self.runtime.calls[len(self.origin_calls):]
        records = self.mutation_records((update,))
        _, _, outcome = records[0]
        self.assertEqual(outcome.outcome.result.evidence["configuration_installation_receipt"]["disposition"], "replaced")
        before = self.durable_snapshot(), self.effects()
        replay = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload=payload, app=self.application())
        self.assertEqual(replay, result)
        self.assertTrue((self.durable_snapshot(), self.effects()) == before)
        self.assertEqual(self.mutation_records((update,)), records)
        self.assertEqual(self.history(), self.origin_history)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)
        with self.assertRaises(CpkServerApplicationError):
            await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
                path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
                payload={"plan_id": self.record.plan_id,
                    "expected_current_graph_id": self.before_update.current_graph_id,
                    "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
                    "desired_graph_id": self.update_plan["desired_graph_id"],
                    "desired_realized_projection_id": self.update_plan["desired_realized_projection_id"],
                    "expected_desired_graph_revision": self.update_plan["desired_graph_revision"],
                    "claim_generation": self.update_generation, "idempotency_key": "reject-failed-refresh"})
