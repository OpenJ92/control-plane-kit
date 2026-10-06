"""Configuration commands retain graph-origin and accepted-history read budgets.

The existing recorded-acceptance fixture is a history premise, not deployment
evidence. No provider, runtime execution or accepted-current mutation is tested.
"""
import unittest

from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
from control_plane_kit_operations.receiver_lifecycle import ReceiverLifecycleStorageError
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_recorded_acceptance_fixture import record_accepted_current
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture


class PostgresConfigurationReceiverAccountingTests(ReceiverAdmissionFixture, unittest.TestCase):
    def test_original_receiver_material_refuses_when_command_budget_is_already_full(self):
        self.desired_service().execute(self.desired_command())
        origin = self.introduction()
        before = self.admission_truth()
        with self.unit_of_work() as uow:
            stores = uow.stores
            operations = (
                lambda: stores.graphs.get(origin.introducing_graph_id),
                lambda: stores.realized_graphs.get(origin.introducing_realized_projection_id),
                lambda: stores.graphs.receiver_introduction(origin.workspace_id, origin.receiver_id),
                lambda: stores.graphs._require_receiver_origin_action(origin),
            )
            for operation in operations:
                # These are distinct existing owner boundaries reached by a
                # receiver-bearing configuration start, not SQL shape tests.
                with _configuration_accounting("configuration-command") as accounting:
                    operation()
                    self.assertGreater(accounting.used.accounted_bytes, 0)
                with _configuration_accounting("configuration-command") as accounting:
                    accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                    with self.assertRaises(_Capacity):
                        operation()
        self.assertEqual(self.admission_truth(), before)


    def test_prior_acceptance_cannot_start_an_independent_command_budget(self):
        record_accepted_current(self)
        origin = self.introduction()
        before = self.admission_truth()
        with self.unit_of_work() as uow:
            with _configuration_accounting("configuration-command") as accounting:
                evidence = uow.stores.execution._receiver_acceptance_evidence((origin,))
                self.assertTrue(evidence)
                self.assertGreater(accounting.used.accounted_bytes, 0)
            with _configuration_accounting("configuration-command") as accounting:
                accounting.used = ConfigurationEvidenceFootprint(4096, 0, 0, 0)
                with self.assertRaises((ReceiverLifecycleStorageError, _Capacity)):
                    uow.stores.execution._receiver_acceptance_evidence((origin,))
        self.assertEqual(self.admission_truth(), before)


class PostgresConfigurationInitialReceiverFeasibilityTests(ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def test_1950_initial_receiver_feasibility_and_update_refusal(self):
        from dataclasses import replace
        from unittest import mock
        from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
        from control_plane_kit_core.products import ProductInstanceConfiguration, instantiate_product
        from control_plane_kit_core.topology import compile_topology
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_operations.approvals import ApprovalCommandService, RequestApproval, DecideApproval
        from control_plane_kit_operations.coordinator import CoordinatorStatus
        from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
        from control_plane_kit_operations.planning import ActivityPlanningCommandService, RequestActivityPlan
        from control_plane_kit_operations.records import ApprovalDecisionKind
        from control_plane_kit_operations.workflows import IdempotencyKey
        from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds, RecordingRuntimeAdapter
        from tests.configuration_cleanup_phase_read_bounds_fixture import ordinary_start_feasibility
        from tests.test_runtime_effect_translation import _configuration_product

        registered = _configuration_product()
        product = registered.descriptor_document.product
        runtime = self.canonical_receiver_graph.runtimes["docker"]
        with self.unit_of_work() as uow:
            uow.stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=registered.descriptor_document, source=registered.source,
                imported_by="operator-a", imported_at=self.now())
            uow.commit()
        block = instantiate_product(product, "diagnostic-config",
            ProductInstanceConfiguration.from_contract(product.runtime_contract))
        selected = compile_topology(DeploymentTopology("diagnostic", DockerRuntime(
            runtime_id="docker", authority_ref=runtime.authority_ref, children=(block,))))
        node = selected.nodes["diagnostic-config"]
        graph = replace(self.canonical_receiver_graph,
            nodes={**self.canonical_receiver_graph.nodes, node.node_id: node},
            runtimes={**self.canonical_receiver_graph.runtimes, "docker": replace(runtime,
                children=tuple(sorted((*runtime.children, node.node_id))))})
        self.desired_receiver("diagnostic-initial", graph=graph)
        self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        with ordinary_start_feasibility(self, "initial-managed-configuration") as reports:
            claimed, _, _ = self.retained_success("diagnostic-initial")
        self.assertTrue(reports, "initial managed plan did not reach configuration start")
        self.assertTrue(all(report["outcome"] == "NewlyStarted" for report in reports))
        self.assertTrue(all("after_revalidation" in report for report in reports))
        self.advance(claimed, "diagnostic-initial")
        self.assertIsNotNone(self.receiver_origin().first_accepted_action_id)

        changed = replace(node, configuration_artifacts=tuple(replace(artifact,
            content='{"diagnostic":"changed"}') for artifact in node.configuration_artifacts))
        self.desired_receiver("diagnostic-update", graph=replace(graph, nodes={**graph.nodes, node.node_id: changed}))
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        suffix = "diagnostic-update"
        planning = ActivityPlanningCommandService(self.unit_of_work, clock=self.now,
            id_factory=GeneratedIds("plan-" + suffix))
        planned = planning.execute(RequestActivityPlan("session-a", "workspace-a", "operator-a",
            workspace.current_graph_id, workspace.desired_graph_id, IdempotencyKey("plan-" + suffix),
            workspace.current_realized_projection_id, workspace.desired_realized_projection_id,
            workspace.desired_graph_revision))
        approvals = ApprovalCommandService(self.unit_of_work, clock=self.now,
            id_factory=GeneratedIds("approval-" + suffix))
        approval = approvals.execute(RequestApproval("session-a", planned.plan_record.plan_id,
            "operator-a", tuple(PolicyScope), IdempotencyKey("approval-" + suffix)))
        approvals.execute(DecideApproval("session-a", approval.request.request_id, "manager-a",
            tuple(PolicyScope), ApprovalDecisionKind.APPROVED, IdempotencyKey("decision-" + suffix)))
        self.admit_approved(suffix, planned.plan_record, approval)
        updated = self.ready_run(suffix)
        adapter = RecordingRuntimeAdapter()
        before = self.execution_truth()
        with mock.patch.object(EffectAttemptStartService, "execute",
                side_effect=AssertionError("unsupported managed update reached effect start")) as start:
            refused = self.coordinator(self.unit_of_work, adapter, suffix).execute(self.execution_command(updated, suffix))
        self.assertIs(refused.status, CoordinatorStatus.UNSUPPORTED)
        self.assertEqual(refused.effects_attempted, 0)
        self.assertEqual(self.execution_truth(), before)
        self.assertEqual(start.call_count, 0)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(adapter.legacy_calls, [])
