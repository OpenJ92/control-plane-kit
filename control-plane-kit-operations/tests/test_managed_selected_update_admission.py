"""#1991 registered selected consumers share the canonical managed planner."""
from dataclasses import replace
import unittest

from control_plane_kit_core.planning import ActivityPlan, ReconcileNode
from control_plane_kit_core.secret_selections import SecretVersionSelection, UnselectedSecretUse
from control_plane_kit_core.secrets import SecretEnvironmentDelivery, SecretReference, SecretUseIntent
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import (
    PlanDerivationProfile, derive_activity_plan, decode_stored_activity_plan, encode_stored_activity_plan,
)
from control_plane_kit_operations.products import RegisteredProductStatus
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from control_plane_kit_operations.runtime_management_admission import (
    runtime_management_execution_is_unsupported, runtime_management_plan_profile,
)
from tests.managed_update_fixture import managed_update_graphs
from tests.test_managed_configuration_update_admission import APPLICATION
from tests.test_managed_image_update_admission import registered_successor, selected_metadata
from tests.test_runtime_effect_translation import _context


PROFILE = PlanDerivationProfile.MANAGED_UPDATE_V1
DELIVERY = SecretEnvironmentDelivery("APPLICATION_TOKEN",
    SecretReference("secret://workspace-secrets/workspace-a/token"), SecretUseIntent.APPLICATION_CONTROL_TOKEN)


def selected_update_graphs(test, family="version"):
    graph, _, _, participants = managed_update_graphs(test,
        application_artifacts=(APPLICATION,), application_deliveries=(DELIVERY,))
    predecessor, = (value for value in participants
        if value.reference.identity.key == graph.node("api-x").metadata["product_identity"])
    selection = SecretVersionSelection(DELIVERY.reference, DELIVERY.intent, "version-1")
    current = graph.update_node(replace(graph.node("api-x"),
        metadata=selected_metadata(predecessor), secret_selections=(selection,)))
    node = current.node("api-x")
    if family == "version":
        desired = current.update_node(replace(node,
            secret_selections=(replace(selection, version_id="version-2"),)))
    elif family == "image":
        successor = registered_successor(predecessor)
        desired = current.update_node(replace(node, metadata=selected_metadata(successor)))
        participants = (*participants, successor)
    else:
        desired = current.update_node(replace(node, configuration_artifacts=tuple(
            replace(value, content='{"mode":"green"}', source_digest=None)
            if value.artifact_id == "application" else value for value in node.configuration_artifacts)))
    return current, desired, participants


class ManagedSelectedUpdateAdmissionTests(unittest.TestCase):
    def transition(self, current, desired):
        pair = validate_graph(current), validate_graph(desired)
        for value in pair:
            value.require_valid()
        return Deploy(*pair)

    def test_three_separate_families_preserve_selected_material_and_canonical_profile(self):
        for family in ("version", "image", "content"):
            with self.subTest(family=family):
                current, desired, products = selected_update_graphs(self, family)
                transition = self.transition(current, desired)
                expected = (PlanDerivationProfile.RETAINED_IMAGE_INGRESS_V1
                    if family == "image" else PROFILE)
                self.assertIs(runtime_management_plan_profile(transition, registered_products=products), expected)
                # Fresh image selection changes; explicitly recorded V1 image plans stay valid.
                profiles = (expected, PROFILE) if family == "image" else (expected,)
                for profile in profiles:
                    with self.subTest(profile=profile):
                        plan = derive_activity_plan(transition, profile=profile)
                        self.assertTrue(plan.ready_for_execution)
                        self.assertFalse(runtime_management_execution_is_unsupported(current, desired, plan,
                            registered_products=products, derivation_profile=profile))
                        self.assertEqual(decode_stored_activity_plan(encode_stored_activity_plan(plan, profile=profile)),
                            (plan, profile))
                        activity, = (value for value in plan.activities if type(value.operation) is ReconcileNode)
                        context = _context(activity=replace(activity, dependencies=()), base_graph=current,
                            desired_graph=desired, registered_products=products)
                        context = replace(context, activity=activity, plan_record=replace(context.plan_record,
                            plan=plan, derivation_profile=profile))
                        material, = runtime_effect_request_for_context(context).products
                        self.assertEqual(material.secret_selections, desired.node("api-x").secret_selections)
                self.assertIs(runtime_management_plan_profile(self.transition(desired, desired),
                    registered_products=products), PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)

    def test_all_active_participants_and_exact_plan_are_required_at_both_boundaries(self):
        current, desired, products = selected_update_graphs(self)
        transition = self.transition(current, desired)
        plan = derive_activity_plan(transition, profile=PROFILE)
        self.assertTrue(plan.ready_for_execution)
        for selected in products:
            for candidates in (tuple(value for value in products if value != selected),
                    tuple(replace(value, status=RegisteredProductStatus.REVOKED)
                          if value == selected else value for value in products)):
                with self.subTest(selected=selected.reference):
                    self.assertIsNone(runtime_management_plan_profile(transition, registered_products=candidates))
                    self.assertTrue(runtime_management_execution_is_unsupported(current, desired, plan,
                        registered_products=candidates, derivation_profile=PROFILE))
        for candidate in (ActivityPlan(()), replace(plan, activities=plan.activities[:-1])):
            self.assertTrue(runtime_management_execution_is_unsupported(current, desired, candidate,
                registered_products=products, derivation_profile=PROFILE))

    def test_unsupported_selection_candidates_never_fall_back_to_legacy_profile(self):
        current, desired, products = selected_update_graphs(self)
        node = desired.node("api-x")
        unselected = current.update_node(replace(current.node("api-x"),
            secret_selections=(UnselectedSecretUse(DELIVERY.reference, DELIVERY.intent),)))
        mixed = desired.update_node(replace(node, configuration_artifacts=tuple(
            replace(value, content='{"mode":"green"}', source_digest=None)
            if value.artifact_id == "application" else value for value in node.configuration_artifacts)))
        other = desired.update_node(replace(desired.node("connector"), metadata={"changed": True}))
        for before, after in ((unselected, desired), (current, unselected), (current, mixed), (current, other)):
            with self.subTest(before=before.node("api-x").secret_selections, after=after.node("api-x").secret_selections):
                transition = self.transition(before, after)
                self.assertIsNone(runtime_management_plan_profile(transition, registered_products=products))
                plan = derive_activity_plan(transition, profile=PROFILE)
                self.assertTrue(runtime_management_execution_is_unsupported(before, after, plan,
                    registered_products=products, derivation_profile=PROFILE))
