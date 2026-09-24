"""Canonical teardown shape is support evidence, never execution authority."""
from dataclasses import replace
import unittest

from control_plane_kit_core.planning import ActivityPlan, NodeTarget, StopNode, StartNode, RemoveNodeResource, RiskLevel
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_core.lifecycle import ResourcePersistence
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import derive_activity_plan
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from control_plane_kit_operations.workflows import InvalidOperationCommand
from control_plane_kit_operations.ingress_authorities import OwnedIngressResourceStatus
from tests.managed_teardown_fixture import PROFILE, managed_teardown
from tests.test_runtime_effect_translation import _context, _cloudflare_resource


class ManagedTeardownAdmissionTests(unittest.TestCase):
    def test_only_complete_canonical_profiled_teardown_is_supported(self):
        current, desired, plan, products = managed_teardown(self)
        def refused(base=current, target=desired, candidate=plan, profile=PROFILE, catalogue=products):
            return runtime_management_execution_is_unsupported(base, target, candidate,
                registered_products=catalogue, derivation_profile=profile)
        self.assertFalse(refused())
        for profile in (None, PlanDerivationProfile.STRUCTURAL_V1, PROFILE.value):
            with self.subTest(profile=profile): self.assertTrue(refused(profile=profile))
        first = plan.activities[0]
        for candidate in (ActivityPlan(()), ActivityPlan((replace(first, dependencies=()),)),
                replace(plan, activities=(replace(first, operation=StartNode(NodeTarget("api"))), *plan.activities[1:]))):
            with self.subTest(candidate=candidate): self.assertTrue(refused(candidate=candidate))
        self.assertTrue(refused(target=replace(desired, runtimes=current.runtimes)))
        self.assertTrue(refused(base=desired, target=current))
        node = current.node("api")
        for changed in (replace(node, metadata={"product_identity": "private-invalid"}),
                replace(node, block_spec=replace(node.block_spec, control_surfaces=()))):
            self.assertTrue(refused(base=replace(current, nodes={**current.nodes, "api": changed})))

        retained = replace(current, nodes={**current.nodes, "api": replace(node,
            lifecycle=replace(node.lifecycle, compute=ResourcePersistence.RETAINED))})
        retained_plan = derive_activity_plan(Deploy(validate_graph(retained), validate_graph(desired)), profile=PROFILE)
        self.assertFalse(refused(base=retained, candidate=retained_plan))
        self.assertFalse(any(isinstance(value.operation, RemoveNodeResource) and value.operation.target.node_id == "api"
                             for value in retained_plan.activities))
        self.assertTrue(refused(base=retained, candidate=plan))

    def test_lowering_requires_the_complete_pinned_activity_not_only_its_id(self):
        current, desired, plan, products = managed_teardown(self)
        activity = next(value for value in plan.activities if isinstance(value.operation, StopNode)
                        and value.operation.target.node_id == "api")
        context = _context(activity=activity, base_graph=current, desired_graph=desired, registered_products=products)
        context = replace(context, plan_record=replace(context.plan_record, plan=plan, derivation_profile=PROFILE))
        request = runtime_effect_request_for_context(context)
        self.assertEqual(request.operation, activity.operation)
        self.assertEqual(request.products[0].node_id, "api")
        self.assertEqual(request.authority_ref, current.runtimes["docker"].authority_ref)
        for candidate in (replace(activity, operation=StopNode(NodeTarget("gateway"))),
                replace(activity, risk=RiskLevel.LOW), replace(activity, operation=StartNode(NodeTarget("api")))):
            with self.subTest(candidate=candidate), self.assertRaises(InvalidOperationCommand):
                runtime_effect_request_for_context(replace(context, activity=candidate))
        with self.assertRaises(InvalidOperationCommand):
            runtime_effect_request_for_context(replace(context,
                plan_record=replace(context.plan_record, derivation_profile=None)))

    def test_connector_cleanup_after_ingress_removal_needs_no_new_token_delivery(self):
        current, desired, plan, products = managed_teardown(self)
        removed = replace(_cloudflare_resource(), ingress_id="management",
            status=OwnedIngressResourceStatus.REMOVED, removed_at="2026-07-28T08:05:00Z", removed_by_run_id="run-remove")
        for kind in (StopNode, RemoveNodeResource):
            activity = next(value for value in plan.activities if isinstance(value.operation, kind)
                            and value.operation.target.node_id == "connector")
            context = _context(activity=activity, base_graph=current, desired_graph=desired,
                registered_products=products, ingress_resources=(removed,))
            context = replace(context, plan_record=replace(context.plan_record, plan=plan, derivation_profile=PROFILE))
            request = runtime_effect_request_for_context(context)
            self.assertEqual(request.products[0].product.runtime_contract.secret_deliveries,
                             current.node("connector").secret_deliveries)
            self.assertEqual(request.products[0].node_id, "connector")
