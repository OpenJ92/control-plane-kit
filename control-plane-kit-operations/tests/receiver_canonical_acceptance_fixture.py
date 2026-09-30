"""Canonical receiver plans; retained execution is a premise, not health proof."""

from dataclasses import replace
from contextlib import contextmanager
from datetime import datetime, timezone

from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductRuntimeContract, ProviderRuntimePort,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, validate_graph
from control_plane_kit_operations.approvals import ApprovalCommandService, DecideApproval, RequestApproval
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.planning import ActivityPlanningCommandService, RequestActivityPlan
from control_plane_kit_operations.postgres.temporal import decode_postgres_timestamp
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.records import ApprovalDecisionKind, GraphVersionRecord
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
from control_plane_kit_operations.lifecycle import CompleteActivityRun
from control_plane_kit_operations.advancement import _require_complete_success
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.managed_teardown_fixture import managed_teardown, seed_owned_ingress
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds, RecordingRuntimeAdapter
from tests.receiver_recorded_completion_fixture import retain_completion_inputs
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture
from tests.test_execution_admission import Sequence


class ReceiverCanonicalAcceptanceFixture(ReceiverFreshExecutionFixture):
    def setUp(self):
        super().setUp()
        graph, _, _, products = managed_teardown(self)
        receiver_node = self.receiver_graph(node_id="api")[0].node("api")
        product = ContainerServerProduct(ProductIdentity("test", "receiver-api", 1),
            OciImageReference("ghcr.io", "test/receiver-api", "sha256:" + "b" * 64),
            ProductRuntimeContract(sockets=receiver_node.sockets,
                provider_ports=(ProviderRuntimePort("http", 8000),),
                capabilities=receiver_node.block_spec.capabilities,
                control_surfaces=receiver_node.block_spec.control_surfaces,
                gateway_transit=receiver_node.block_spec.gateway_transit,
                configuration_artifacts=receiver_node.configuration_artifacts,
                public_environment=receiver_node.public_environment))
        with self.unit_of_work() as uow:
            registered = uow.stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=ProductDescriptorCodec().encode_document(product),
                source=InlineDescriptorSource(), imported_by="operator-a", imported_at=self.now())
            for retained in products:
                if retained.reference.identity.name != "api":
                    uow.stores.registered_products.register(workspace_id="workspace-a",
                        descriptor_document=retained.descriptor_document,
                        source=InlineDescriptorSource(), imported_by="operator-a", imported_at=self.now())
            receiver_node = replace(receiver_node, metadata={
                "product_identity": registered.reference.identity.key,
                "product_descriptor_digest": registered.reference.descriptor_sha256.value})
            self.canonical_receiver_graph = replace(graph, nodes={**graph.nodes, "api": receiver_node})
            validate_graph(self.canonical_receiver_graph).require_valid()
            # Before any receiver introduction, establish structurally empty
            # current truth. This is not the target current pointer or acceptance.
            empty = GraphVersionRecord.from_graph(graph_id="canonical-empty",
                workspace_id="workspace-a", version=uow.stores.graphs.next_version_for_workspace("workspace-a"),
                graph=DeploymentGraph("empty"), created_by="operator-a", created_at=self.now())
            uow.stores.graphs.save(empty)
            uow.stores.workspaces.set_current_graph("workspace-a", empty.graph_id)
            seed_owned_ingress(uow.stores, self.canonical_receiver_graph)
            uow.commit()

    @staticmethod
    def now():
        return decode_postgres_timestamp(datetime.now(timezone.utc))

    def plan_and_approve(self, suffix):
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            base = uow.stores.realized_graphs.get(workspace.current_realized_projection_id)
            desired = uow.stores.realized_graphs.get(workspace.desired_realized_projection_id)
            products = uow.stores.registered_products.list_active("workspace-a")
        planned = ActivityPlanningCommandService(self.unit_of_work, clock=self.now,
            id_factory=Sequence("plan-" + suffix, "plan-action-" + suffix)).execute(RequestActivityPlan(
                "session-a", "workspace-a", "operator-a", workspace.current_graph_id,
                workspace.desired_graph_id, IdempotencyKey("plan-" + suffix),
                workspace.current_realized_projection_id, workspace.desired_realized_projection_id,
                workspace.desired_graph_revision))
        current_graph = DEFAULT_GRAPH_CODEC.decode(base.graph_descriptor)
        desired_graph = DEFAULT_GRAPH_CODEC.decode(desired.graph_descriptor)
        transition = Deploy(validate_graph(current_graph), validate_graph(desired_graph))
        expected = derive_activity_plan(transition, profile=PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)
        self.assertEqual(planned.plan_record.plan, expected)
        self.assertFalse(runtime_management_execution_is_unsupported(current_graph, desired_graph, expected,
            registered_products=products, derivation_profile=planned.plan_record.derivation_profile))
        service = ApprovalCommandService(self.unit_of_work, clock=self.now,
            id_factory=GeneratedIds("approval-" + suffix))
        approval = service.execute(RequestApproval("session-a", planned.plan_record.plan_id,
            "operator-a", tuple(PolicyScope), IdempotencyKey("approval-" + suffix)))
        service.execute(DecideApproval("session-a", approval.request.request_id, "manager-a",
            tuple(PolicyScope), ApprovalDecisionKind.APPROVED, IdempotencyKey("decision-" + suffix)))
        return transition, planned.plan_record, approval

    def admit_approved(self, suffix, plan, approval):
        return ExecutionAdmissionCommandService(self.unit_of_work, clock=self.now,
            id_factory=Sequence("execution-" + suffix, "action-execute-" + suffix)).execute(
            self.command(plan_id=plan.plan_id, approval_request_id=approval.request.request_id,
                scopes=tuple(PolicyScope), key="execute-" + suffix))

    def plan_and_admit(self, suffix):
        transition, plan, approval = self.plan_and_approve(suffix)
        return transition, plan, self.admit_approved(suffix, plan, approval)

    @contextmanager
    def mismatched_receiver_binding(self):
        rows = self.connection.execute("SELECT graph_id,realized_projection_id,selected_configuration_digest "
            "FROM cpk_graph_receiver_bindings WHERE workspace_id='workspace-a' AND receiver_id=%s",
            ("a" * 32,)).fetchall()
        self.assertTrue(rows)
        try:
            self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s "
                "WHERE workspace_id='workspace-a' AND receiver_id=%s", ("0" * 64, "a" * 32))
            yield
        finally:
            for graph_id, projection_id, digest in rows:
                self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s "
                    "WHERE workspace_id='workspace-a' AND graph_id=%s AND realized_projection_id=%s AND receiver_id=%s",
                    (digest, graph_id, projection_id, "a" * 32))

    def retained_success(self, suffix):
        transition, plan, admitted = self.plan_and_admit(suffix)
        claimed = self.ready_run(suffix)
        command = self.execution_command(claimed, suffix)
        context = self.coordinator(self.unit_of_work, RecordingRuntimeAdapter(), suffix)._load_context(command)
        retain_completion_inputs(self, context)
        completed = self.lifecycle("complete-event-" + suffix, "complete-action-" + suffix).execute(
            CompleteActivityRun(claimed.run.run_id, command.authority, command.fence,
                IdempotencyKey("complete-" + suffix)))
        with self.unit_of_work() as uow:
            events = uow.stores.execution.events_for_run(claimed.run.run_id)
        _require_complete_success(plan.plan, completed.run, events)
        scopes = self.require_scopes()
        if plan.plan.activities:
            # Conflict is expected before real advancement accounts for this
            # run. It also proves the native input associations are readable,
            # rather than silently accepting unavailable evidence.
            self.assertEqual(self.evidence(scopes.ExecutionReceiverScope("docker", "api")).disposition,
                "conflict")
        return claimed, transition, plan

    def acceptance_truth(self):
        return self.graph_truth(), self.execution_truth()
