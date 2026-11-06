"""Immutable image transitions reuse the real Operations/Postgres command flow."""
from dataclasses import replace
import hashlib
import json
from psycopg.types.json import Jsonb

from control_plane_kit_core.operations import ControlPlaneServiceRole, EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import (
    ManagementBootstrapStage, ObserveManagementBootstrap, ObserveNodeHealth, ReconcileNode, StartNode,
)
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference
from tests import test_managed_configuration_update_execution as content
from tests import test_managed_application_chain as chain
from tests.test_managed_image_update_admission import retained_image_profile
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome
from control_plane_kit_core.receiver_health_read_results import ReceiverHealthReadResult
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from tests import test_native_connector_installation_dispatch as proof
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import (
    PlanDerivationProfile, derive_activity_plan, encode_stored_activity_plan,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService, ActivityPlanningGraphStateConflict,
    DesiredGraphCommandService, RequestActivityPlan, SetDesiredGraph,
)
from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleExpectation
from control_plane_kit_operations.workflows import CloseOperationSession, IdempotencyKey, OperationCommandService


class ManagedImageUpdateExecutionTests(content.ManagedConfigurationUpdateExecutionTests):
    # Inherit the maintained missing-receipt and fresh-pull-authority controls.
    # Only selected desired material differs; command/replay/receipt owners do not.
    def expected_derivation_profile(self):
        return retained_image_profile(self)

    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        node = graph.node("api")
        graph = graph.update_node(replace(node, metadata={**node.metadata,
            "oci_image": documents["api"].product.image.execution_reference}))
        return self.native_before_path_graph(graph), documents, registry

    async def desired_update(self):
        previous = self.documents["api"].product
        successor = replace(previous, identity=replace(previous.identity, contract_revision=2),
            image=replace(previous.image, digest="sha256:" + "b" * 64))
        self.successor_document = ProductDescriptorCodec().encode_document(successor)
        await self.invoke("command.product.import", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={
                "descriptor_document": json.loads(self.successor_document.content),
                "imported_at": chain.now(), "idempotency_key": "product-api-image-successor"})
        self.documents["api-image-successor"] = self.successor_document
        reference = ProductReference.from_document(self.successor_document)
        node = self.graph.node("api")
        return self.graph.update_node(replace(node, metadata={**node.metadata,
            "product_identity": reference.identity.key,
            "product_descriptor_digest": reference.descriptor_sha256.value,
            "oci_image": successor.image.execution_reference}))

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
        old_product, = original.products
        new_product, = update.products
        self.assertEqual(new_product.reference, ProductReference.from_document(self.successor_document))
        self.assertEqual(new_product.product.image, self.successor_document.product.image)
        self.assertNotEqual(new_product.reference, old_product.reference)
        self.assertNotEqual(new_product.product.image.digest, old_product.product.image.digest)
        self.assertEqual(new_product.product.runtime_contract, old_product.product.runtime_contract)
        old = {value.artifact_id: value for value in original.configuration_instances.instances}
        new = {value.artifact_id: value for value in update.configuration_instances.instances}
        self.assertEqual(old.keys(), new.keys())
        wrapper, = old.keys() - {"application"}
        self.assertEqual(new[wrapper], old[wrapper])
        self.assertEqual(new["application"].content_digest, old["application"].content_digest)
        reads = [preparation for request, preparation, _ in self.health.signed_reads
            if type(request.operation) is ObserveNodeHealth and request.operation.node_id == "api"]
        self.assertEqual(reads[0].request.target, reads[-1].request.target)
        self.assertEqual(self.history(), self.origin_history)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)
            active = uow.stores.registered_products.list_active("workspace-a")
            self.assertTrue({old_product.reference, new_product.reference} <= {value.reference for value in active})
        plan = self.update_plan
        advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"plan_id": self.record.plan_id,
                "expected_current_graph_id": self.before_update.current_graph_id,
                "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
                "desired_graph_id": plan["desired_graph_id"],
                "desired_realized_projection_id": plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": plan["desired_graph_revision"],
                "claim_generation": self.update_generation, "idempotency_key": "advance-image"})
        self.assertEqual(advanced["to_graph_id"], plan["desired_graph_id"])

    async def assert_preflight_stops_replacement(self, *, native=None, public=None):
        await self.prepare_content()
        returned = []
        observe_native, observe_signed = self.health.observe_connection, self.health.observe_signed
        if native is not None:
            self.health.responses[:] = [native]

        async def native_outcome(realization, request, authority):
            result = await observe_native(realization, request, authority)
            returned.append((request, result))
            return result

        async def signed_outcome(realization, request, authority):
            result = await observe_signed(realization, request, authority)
            if (public is not None and type(request.operation) is ObserveManagementBootstrap
                    and request.operation.stage is ManagementBootstrapStage.GATEWAY_INGRESS_READY):
                result = ReceiverHealthReadResult(result.request, result.declaration, public)
            # Capture what the seam actually RETURNS, independently of the delegate's recording list.
            returned.append((request, result))
            return result

        self.health.observe_connection = native_outcome
        self.health.observe_signed = signed_outcome
        before = tuple(self.runtime.calls)
        terminal = None
        for number in range(len(self.record.plan.activities) + 2):
            payload = {"claim_generation": self.update_generation,
                "idempotency_key": f"preflight-refusal-{number}", "max_effects": 1}
            result = await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                path={"workspace_id": "workspace-a", "run_id": self.update_run},
                principal=self.worker, payload=payload)
            if result["coordinator_status"] != "progressed":
                terminal = result
                snapshot = self.durable_snapshot(), self.effects()
                self.assertEqual(await self.invoke("command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
                    path={"workspace_id": "workspace-a", "run_id": self.update_run},
                    principal=self.worker, payload=payload, app=self.application()), result)
                self.assertEqual((self.durable_snapshot(), self.effects()), snapshot)
                break
        self.assertIsNotNone(terminal, "preflight failure must reach a wait/failure boundary")
        expected_stages = [ManagementBootstrapStage.CONNECTOR_CONNECTED]
        if public is not None:
            expected_stages += [ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH,
                ManagementBootstrapStage.GATEWAY_INGRESS_READY]
        self.assertEqual([request.operation.stage for request, _ in returned], expected_stages)
        self.assertTrue(all(request.source.run_id.value == self.update_run for request, _ in returned))
        selected_request, selected_result = returned[-1]
        self.assertIs(selected_result.outcome, native if native is not None else public)
        self.assertEqual(terminal["coordinator_status"], "blocked" if native is not None else "failed")
        self.assertNotEqual(terminal["run_status"], "succeeded")
        self.assertEqual(tuple(self.runtime.calls), before)
        self.assertFalse(any(type(request.operation) is ObserveNodeHealth
            and request.source.run_id.value == self.update_run
            for request, _, _ in self.health.signed_reads))
        with self.unit_of_work() as uow:
            identity = EffectAttemptIdentity(RunId(self.update_run), selected_request.activity_id.value, 1)
            attempt = uow.stores.effect_attempts.get(identity)
            self.assertEqual(attempt.state.status.value, "not_ready" if native is not None else "failed")
            self.assertEqual(attempt.latest_transition_event.kind.value,
                "step_observation_not_ready" if native is not None else "step_failed")
            retained = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id).outcome
            if native is not None:
                self.assertEqual(retained.observation, selected_result)
                self.assertEqual(retained.acceptance_reason, native.value)
            else:
                self.assertIs(returned[0][1].outcome, NativeConnectionOutcome.CONNECTED)
                self.assertEqual(retained.result.failure.code, "node-health-not-healthy")
                self.assertEqual(retained.result.failure.details["health"], selected_result.descriptor())
                for predecessor, _ in returned[:-1]:
                    prior = uow.stores.effect_attempts.get(EffectAttemptIdentity(
                        RunId(self.update_run), predecessor.activity_id.value, 1))
                    self.assertEqual(prior.state.status.value, "succeeded")
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)

    async def test_disconnected_native_preflight_has_zero_reconciles(self):
        await self.assert_preflight_stops_replacement(native=NativeConnectionOutcome.DISCONNECTED)

    async def test_unknown_native_preflight_has_zero_reconciles(self):
        await self.assert_preflight_stops_replacement(native=NativeConnectionOutcome.UNKNOWN)

    async def test_unhealthy_public_preflight_has_zero_reconciles(self):
        await self.assert_preflight_stops_replacement(public=NodeHealthReadOutcome.UNHEALTHY)

    async def test_unknown_public_preflight_has_zero_reconciles(self):
        await self.assert_preflight_stops_replacement(public=NodeHealthReadOutcome.UNKNOWN)

    def prepared_planning_case(self):
        with self.unit_of_work() as uow:
            record = uow.stores.activity_history.get_plan(self.record.plan_id)
            action, = (value for value in uow.stores.activity_history.actions_for_session(record.session_id)
                if value.action_type is OperatorCommandKind.REQUEST_ACTIVITY_PLAN
                and value.payload.get("plan_id") == record.plan_id)
        command = RequestActivityPlan(record.session_id, "workspace-a", action.actor_id,
            record.base_graph_id, record.desired_graph_id, IdempotencyKey(action.idempotency_key),
            expected_current_realized_projection_id=record.base_realized_projection_id,
            expected_desired_realized_projection_id=record.desired_realized_projection_id,
            expected_desired_graph_revision=record.desired_graph_revision)
        self.assertEqual(action.intent_fingerprint, hashlib.sha256(json.dumps(
            command.descriptor(), sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        return command, record, action

    def historical_planning_case(self, profile, suffix):
        """Only pending intent is recorded; graph ownership comes from real initial execution."""
        command, prepared, original_action = self.prepared_planning_case()
        command = replace(command, idempotency_key=IdempotencyKey("historical-image-" + suffix))
        with self.unit_of_work() as uow:
            current = DEFAULT_GRAPH_CODEC.decode(uow.stores.realized_graphs.get(
                prepared.base_realized_projection_id).graph_descriptor)
            desired = DEFAULT_GRAPH_CODEC.decode(uow.stores.realized_graphs.get(
                prepared.desired_realized_projection_id).graph_descriptor)
            plan = derive_activity_plan(Deploy(validate_graph(current), validate_graph(desired)), profile=profile)
            self.assertTrue(plan.activities)
            self.assertTrue(plan.ready_for_execution)
            record = replace(prepared, plan_id="historical-image-" + suffix, plan=plan,
                derivation_profile=profile, created_at=chain.now())
            history = uow.stores.activity_history
            action = replace(original_action, action_id="historical-image-action-" + suffix,
                ordinal=history.next_action_ordinal(record.session_id), created_at=chain.now(),
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=hashlib.sha256(json.dumps(command.descriptor(),
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                payload={**original_action.payload, "plan_id": record.plan_id,
                    "activity_count": len(plan.activities), "derivation_profile": profile.value})
            history.add_plan(record)
            history.add_action(action)
            uow.commit()
        self.assertNotEqual(record.plan_id, prepared.plan_id)
        self.assertNotEqual(action.action_id, original_action.action_id)
        return command, record, action

    def replay_snapshot(self):
        return (self.durable_snapshot(), self.effects(), tuple((name, self.rows(name)) for name in (
            "cpk_operation_actions", "cpk_activity_plans", "cpk_graph_versions",
            "cpk_realized_graph_projections", "cpk_operation_sessions", "cpk_workspaces")))

    def replay_planning(self, command):
        def no_identity_allocation():
            self.fail("planning replay must not allocate a new identity")
        return ActivityPlanningCommandService(self.unit_of_work, clock=chain.now,
            id_factory=no_identity_allocation).execute(command)

    async def test_retained_image_ingress_and_original_v1_replay_keep_recorded_plan_and_fingerprint(self):
        await self.prepare_content(prepare_only=True)
        # New-profile evidence is the real prepared action; V1 is distinct historical pending intent.
        cases = (self.prepared_planning_case(),
            self.historical_planning_case(PlanDerivationProfile.MANAGED_UPDATE_V1, "v1"))
        self.assertNotEqual(cases[0][1].plan, cases[1][1].plan)
        original_payloads = {record.plan_id: self.connection.execute(
            "SELECT payload FROM cpk_activity_plans WHERE plan_id=%s", (record.plan_id,)).fetchone()[0]
            for _, record, _ in cases}
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        identities = iter(("replay-graph-moved", "replay-action-moved"))
        DesiredGraphCommandService(self.unit_of_work, clock=chain.now, id_factory=lambda: next(identities)).execute(
            SetDesiredGraph(self.record.session_id, "workspace-a", "operator-a", DeploymentGraph("later"),
                workspace.desired_graph_id, IdempotencyKey("move-image-replay"),
                expected_desired_realized_projection_id=workspace.desired_realized_projection_id,
                expected_desired_graph_revision=workspace.desired_graph_revision,
                receiver_lifecycle=ReceiverLifecycleExpectation(workspace.current_graph_id,
                    workspace.current_realized_projection_id, workspace.desired_graph_id,
                    workspace.desired_realized_projection_id, workspace.desired_graph_revision)))
        OperationCommandService(self.unit_of_work, clock=chain.now,
            id_factory=lambda: "close-image-replay").execute(CloseOperationSession(
            self.record.session_id, "operator-a", IdempotencyKey("close-image-replay")))
        before = self.replay_snapshot()
        for command, record, action in cases:
            with self.subTest(profile=record.derivation_profile):
                replay = self.replay_planning(command)
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.plan_record, record)
                self.assertEqual(replay.action, action)
                self.assertEqual(replay.action.intent_fingerprint, action.intent_fingerprint)
                self.assertIs(replay.plan_record.derivation_profile, record.derivation_profile)
                self.assertEqual(self.replay_snapshot(), before)
                self.assertEqual(self.connection.execute(
                    "SELECT payload FROM cpk_activity_plans WHERE plan_id=%s", (record.plan_id,)).fetchone()[0],
                    original_payloads[record.plan_id])

    async def test_retained_image_ingress_durable_profile_and_plan_substitution_refuse(self):
        await self.prepare_content(prepare_only=True)
        profile = self.expected_derivation_profile()
        command, record, _ = self.historical_planning_case(profile, "new-pending")
        _, old, _ = self.historical_planning_case(PlanDerivationProfile.MANAGED_UPDATE_V1, "v1")
        for foreign_plan, foreign_profile in ((old.plan, profile),
                (record.plan, PlanDerivationProfile.MANAGED_UPDATE_V1)):
            with self.subTest(profile=foreign_profile):
                # Negative-only corruption of the separate pending fixture, never the approved plan.
                self.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s",
                    (Jsonb(encode_stored_activity_plan(foreign_plan, profile=foreign_profile)), record.plan_id))
                before = self.replay_snapshot()
                with self.assertRaises(ActivityPlanningGraphStateConflict):
                    self.replay_planning(command)
                self.assertEqual(self.replay_snapshot(), before)


class RetainedImageIngressConnectorProofTests(proof.NativeConnectorInstallationDispatchTests):
    """Run the maintained origin/race/ledger laws with the new image profile."""
    expected_derivation_profile = ManagedImageUpdateExecutionTests.expected_derivation_profile
    desired_update = ManagedImageUpdateExecutionTests.desired_update

    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        node = graph.node("api")
        graph = graph.update_node(replace(node, metadata={**node.metadata,
            "oci_image": documents["api"].product.image.execution_reference}))
        return self.native_before_path_graph(graph), documents, registry
