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
    def test_pending_origin_with_unselected_sibling_preserves_complete_binding_proof(self):
        from dataclasses import replace
        from unittest import mock
        from control_plane_kit_core.topology import validate_graph
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _BOUND_ORDINARY_START
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable
        from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
        graph = self.canonical_receiver_graph
        sibling = self.receiver_graph(node_id="sibling", receiver="b" * 32)[0].node("sibling")
        introducing = replace(graph, nodes={**graph.nodes, "sibling": sibling}, runtimes={
            **graph.runtimes, "docker": replace(graph.runtimes["docker"],
                children=(*graph.runtimes["docker"].children, "sibling"))})
        validate_graph(introducing).require_valid()
        self.desired_receiver("introducing-two", graph=introducing)
        origin = self.receiver_origin()
        self.assertIsNone(origin.first_accepted_action_id)
        self.desired_receiver("retaining-one", graph=graph)
        self.assertEqual(self.receiver_origin(), origin)
        with self.unit_of_work() as uow:
            bindings = uow.stores.graphs.receiver_bindings("workspace-a",
                origin.introducing_graph_id, origin.introducing_realized_projection_id)
            workspace = uow.stores.workspaces.get("workspace-a")
            desired = uow.stores.graphs.receiver_bindings("workspace-a",
                workspace.desired_graph_id, workspace.desired_realized_projection_id)
        self.assertEqual({binding.receiver_id for binding in bindings}, {"a" * 32, "b" * 32})
        self.assertEqual({binding.receiver_id for binding in desired}, {"a" * 32})
        actual_prepare, captured = ConfigurationPreparationStore._prepare, []

        def prepared(store, *args, **kwargs):
            result = actual_prepare(store, *args, **kwargs)
            issued = _BOUND_ORDINARY_START.get()
            self.assertIsNotNone(issued)
            origins = {entry.identity for role, entry in issued.points if role == "introduction"}
            self.assertEqual(origins, {("workspace-a", "a" * 32)})
            introducing_bindings = next(entry for role, entry in issued.collections
                if role == "bindings" and entry.identity == ("workspace-a",
                    origin.introducing_graph_id, origin.introducing_realized_projection_id))
            self.assertEqual(set(introducing_bindings.keys), {("api", "http"), ("sibling", "http")})
            before = _ACCOUNTING.get().used
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                    "unselected origin reached SQL")), self.assertRaises(_Unavailable):
                store._ordinary_owner._stores.graphs.receiver_introduction("workspace-a", "b" * 32)
            self.assertEqual(_ACCOUNTING.get().used, before)
            captured.append(result)
            return result

        with mock.patch.object(ConfigurationPreparationStore, "_prepare", prepared):
            claimed, _, plan = self.retained_success("retaining-one")
        self.assertTrue(plan.plan.ready_for_execution)
        self.assertEqual(len(captured), 1)
        self.advance(claimed, "retaining-one")
        self.assertIsNotNone(self.receiver_origin().first_accepted_action_id)

    def test_1950_initial_receiver_feasibility_and_update_refusal(self):
        from dataclasses import replace
        from control_plane_kit_core.planning import StartNode
        from control_plane_kit_operations.planning import ActivityPlanningCommandService, RequestActivityPlan
        from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand
        from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds
        from tests.configuration_cleanup_phase_read_bounds_fixture import ordinary_start_feasibility

        graph = self.canonical_receiver_graph
        node = graph.nodes["api"]
        self.desired_receiver("diagnostic-initial", graph=graph)
        self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        with ordinary_start_feasibility(self, "initial-managed-configuration") as reports:
            claimed, _, plan = self.retained_success("diagnostic-initial")
        self.assertTrue(plan.plan.ready_for_execution)
        configured_starts = [activity for activity in plan.plan.activities
            if isinstance(activity.operation, StartNode)
            and graph.nodes[activity.operation.target.node_id].configuration_artifacts]
        self.assertEqual([activity.operation.target.node_id for activity in configured_starts], ["api"])
        self.assertTrue(reports, "initial managed plan did not reach configuration start")
        self.assertEqual(len(reports), len(configured_starts))
        self.assertEqual(reports[0]["ref_count"], len(node.configuration_artifacts))
        self.assertTrue(all(report["outcome"] == "NewlyStarted" for report in reports))
        self.assertTrue(all("after_revalidation" in report for report in reports))
        self.advance(claimed, "diagnostic-initial")
        self.assertIsNotNone(self.receiver_origin().first_accepted_action_id)

        changed = replace(node, configuration_artifacts=tuple(replace(artifact,
            content='{"diagnostic":"changed"}') if artifact.artifact_id == "application" else artifact
            for artifact in node.configuration_artifacts))
        self.desired_receiver("diagnostic-update", graph=replace(graph, nodes={**graph.nodes, node.node_id: changed}))
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        suffix = "diagnostic-update"
        planning = ActivityPlanningCommandService(self.unit_of_work, clock=self.now,
            id_factory=GeneratedIds("plan-" + suffix))
        before = self.execution_truth()
        with self.assertRaisesRegex(InvalidOperationCommand,
                "runtime management planning is unsupported"):
            planning.execute(RequestActivityPlan("session-a", "workspace-a", "operator-a",
                workspace.current_graph_id, workspace.desired_graph_id, IdempotencyKey("plan-" + suffix),
                workspace.current_realized_projection_id, workspace.desired_realized_projection_id,
                workspace.desired_graph_revision))
        self.assertEqual(self.execution_truth(), before)
