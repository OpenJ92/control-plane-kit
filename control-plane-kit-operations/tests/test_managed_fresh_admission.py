"""Canonical fresh shape supports lowering, never grants execution authority."""
from dataclasses import replace
import unittest

from control_plane_kit_core.lifecycle import ResourceLifecycle
from control_plane_kit_core.planning import ActivityPlan, ManagementBootstrapStage, ObserveManagementBootstrap, StartRuntime
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
from tests.managed_teardown_fixture import PROFILE, managed_teardown


class ManagedFreshAdmissionTests(unittest.TestCase):
    def test_exact_canonical_fresh_owned_profile_is_supported_without_removing_other_guards(self):
        desired, current, _, products = managed_teardown(self)
        plan = derive_activity_plan(Deploy(validate_graph(current), validate_graph(desired)), profile=PROFILE)
        self.assertTrue(plan.ready_for_execution)
        self.assertEqual({item.operation.stage for item in plan.activities
            if type(item.operation) is ObserveManagementBootstrap}, {
                ManagementBootstrapStage.CONNECTOR_CONNECTED,
                ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH,
                ManagementBootstrapStage.GATEWAY_INGRESS_READY,
            })

        def refused(candidate=plan, profile=PROFILE):
            return runtime_management_execution_is_unsupported(current, desired, candidate,
                derivation_profile=profile, registered_products=products)

        self.assertFalse(refused(), "canonical fresh owned management has no execution shape support")
        for profile in (None, PROFILE.value, PlanDerivationProfile.STRUCTURAL_V1):
            with self.subTest(profile=profile):
                self.assertTrue(refused(profile=profile))
        for candidate in (None, ActivityPlan(()),
                ActivityPlan((replace(plan.activities[0], dependencies=()),)),
                replace(plan, activities=tuple(replace(item, dependencies=()) for item in plan.activities))):
            with self.subTest(candidate=candidate):
                self.assertTrue(refused(candidate=candidate))

    def test_nonempty_update_does_not_acquire_fresh_execution_support(self):
        desired, _, _, products = managed_teardown(self)
        # Retain the existing management pair; only the application is new.
        runtime = desired.runtimes["docker"]
        current = replace(desired, nodes={key: value for key, value in desired.nodes.items() if key != "api"},
            runtimes={"docker": replace(runtime, children=tuple(key for key in runtime.children if key != "api"))})
        plan = derive_activity_plan(Deploy(validate_graph(current), validate_graph(desired)), profile=PROFILE)
        self.assertTrue(plan.activities)
        self.assertTrue(runtime_management_execution_is_unsupported(current, desired, plan,
            derivation_profile=PROFILE, registered_products=products))

    def test_empty_base_does_not_turn_an_attached_or_external_runtime_into_owned_bootstrap(self):
        desired, current, _, products = managed_teardown(self)
        for lifecycle in (ResourceLifecycle.attached(), ResourceLifecycle.external()):
            with self.subTest(lifecycle=lifecycle):
                candidate = replace(desired, runtimes={"docker": replace(
                    desired.runtimes["docker"], lifecycle=lifecycle)})
                plan = derive_activity_plan(Deploy(validate_graph(current), validate_graph(candidate)), profile=PROFILE)
                self.assertFalse(any(type(item.operation) is StartRuntime for item in plan.activities))
                stages = {item.operation.stage for item in plan.activities
                    if type(item.operation) is ObserveManagementBootstrap}
                self.assertIn(ManagementBootstrapStage.GATEWAY_LOCAL_READY, stages)
                self.assertNotIn(ManagementBootstrapStage.GATEWAY_INGRESS_READY, stages)
                self.assertTrue(runtime_management_execution_is_unsupported(current, candidate, plan,
                    derivation_profile=PROFILE, registered_products=products))
