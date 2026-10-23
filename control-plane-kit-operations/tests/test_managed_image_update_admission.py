"""Registered immutable-image admission through the existing managed profile."""
from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import DeploymentTopology, ExternalRuntime
from control_plane_kit_core.planning import (
    ActivityPlan, ReconcileNode, compile_retained_image_ingress_activity_plan,
)
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.topology import DeploymentGraph, compile_topology, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import (
    PlanDerivationProfile, derive_activity_plan, decode_stored_activity_plan, encode_stored_activity_plan,
)
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct, RegisteredProductStatus
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from control_plane_kit_operations.runtime_management_admission import (
    runtime_management_execution_is_unsupported, runtime_management_plan_profile,
)
from tests.managed_update_fixture import managed_update_graphs
from tests.test_managed_configuration_update_admission import APPLICATION
from tests.test_runtime_effect_translation import _context


PROFILE = PlanDerivationProfile.MANAGED_UPDATE_V1


def retained_image_profile(test):
    profile = getattr(PlanDerivationProfile, "RETAINED_IMAGE_INGRESS_V1", None)
    test.assertIsNotNone(profile, "retained-image ingress derivation profile is missing")
    test.assertEqual(profile.value, "retained-image-ingress-v1")
    return profile


def registered_successor(predecessor, *, contract=None):
    product = predecessor.descriptor_document.product
    successor = replace(product, identity=replace(product.identity, contract_revision=2),
        image=replace(product.image, digest="sha256:" + "b" * 64),
        runtime_contract=product.runtime_contract if contract is None else contract)
    return RegisteredProduct.from_document(workspace_id=predecessor.workspace_id,
        descriptor_document=ProductDescriptorCodec().encode_document(successor),
        source=InlineDescriptorSource(), imported_by="operator-a", imported_at=predecessor.imported_at)


def selected_metadata(product):
    return {"product_identity": product.reference.identity.key,
        "product_descriptor_digest": product.reference.descriptor_sha256.value,
        "oci_image": product.descriptor_document.product.image.execution_reference}


class ManagedImageUpdateAdmissionTests(unittest.TestCase):
    def pair(self):
        graph, _, _, participants = managed_update_graphs(self, application_artifacts=(APPLICATION,))
        before, = (value for value in participants
            if value.reference.identity.key == graph.node("api-x").metadata["product_identity"])
        after = registered_successor(before)
        current = graph.update_node(replace(graph.node("api-x"), metadata=selected_metadata(before)))
        desired = current.update_node(replace(current.node("api-x"), metadata=selected_metadata(after)))
        return current, desired, (*participants, after), before, after

    def transition(self, current, desired):
        left, right = validate_graph(current), validate_graph(desired)
        left.require_valid()
        right.require_valid()
        return Deploy(left, right)

    def refused(self, current, desired, products):
        transition = self.transition(current, desired)
        plan = derive_activity_plan(transition, profile=PROFILE)
        self.assertTrue(plan.ready_for_execution, "counterexample must retain the valid Core image relation")
        self.assertIsNone(runtime_management_plan_profile(transition, registered_products=products))
        self.assertTrue(runtime_management_execution_is_unsupported(current, desired, plan,
            registered_products=products, derivation_profile=PROFILE))

    def test_exact_active_pair_selects_profile_and_successor_material(self):
        current, desired, products, before, after = self.pair()
        transition = self.transition(current, desired)
        # Fresh preparation advances; explicitly recorded V1 still means V1.
        self.assertIs(runtime_management_plan_profile(transition, registered_products=products),
            retained_image_profile(self))
        plan = derive_activity_plan(transition, profile=PROFILE)
        self.assertTrue(plan.ready_for_execution)
        self.assertEqual(decode_stored_activity_plan(encode_stored_activity_plan(plan, profile=PROFILE)), (plan, PROFILE))
        self.assertFalse(runtime_management_execution_is_unsupported(current, desired, plan,
            registered_products=products, derivation_profile=PROFILE))
        activity, = (value for value in plan.activities if type(value.operation) is ReconcileNode)
        context = _context(activity=replace(activity, dependencies=()), base_graph=current,
            desired_graph=desired, registered_products=products)
        context = replace(context, activity=activity, plan_record=replace(context.plan_record,
            plan=plan, derivation_profile=PROFILE))
        material, = runtime_effect_request_for_context(context).products
        self.assertEqual(material.reference, after.reference)
        self.assertEqual(material.product.image, after.descriptor_document.product.image)
        self.assertNotEqual(material.reference, before.reference)
        self.assertEqual(material.product.runtime_contract.configuration_artifacts,
            tuple(sorted(current.node("api-x").configuration_artifacts)))
        self.assertIsNone(material.pull_authority)
        for candidate in (ActivityPlan(()), replace(plan, activities=plan.activities[:-1])):
            self.assertTrue(runtime_management_execution_is_unsupported(current, desired, candidate,
                registered_products=products, derivation_profile=PROFILE))
        noop = self.transition(current, current)
        self.assertIs(runtime_management_plan_profile(noop, registered_products=products),
            PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)

    def test_both_exact_registrations_must_be_available_and_active(self):
        current, desired, products, before, after = self.pair()
        for selected in (before, after):
            with self.subTest(selected=selected.reference.identity.key):
                self.refused(current, desired, tuple(value for value in products if value != selected))
                self.refused(current, desired, tuple(replace(value, status=RegisteredProductStatus.REVOKED)
                    if value == selected else value for value in products))
        node = desired.node("api-x")
        self.refused(current, desired.update_node(replace(node,
            metadata={**node.metadata, "product_descriptor_digest": "c" * 64})), products)

    def test_each_graph_image_must_match_its_selected_registered_document(self):
        current, desired, products, _, _ = self.pair()
        for side in (0, 1):
            with self.subTest(side=side):
                pair = [current, desired]
                node = pair[side].node("api-x")
                pair[side] = pair[side].update_node(replace(node, metadata={**node.metadata,
                    "oci_image": "ghcr.io/test/test-api-x@sha256:" + "c" * 64}))
                self.refused(*pair, products)

    def test_full_registered_contract_must_agree_beyond_selected_graph_slots(self):
        current, desired, products, before, after = self.pair()
        contract = before.descriptor_document.product.runtime_contract
        changed = replace(contract, configuration_artifacts=tuple(
            replace(value, content='{"mode":"different-default"}', source_digest=None)
            if value.artifact_id == "application" else value for value in contract.configuration_artifacts))
        incompatible = registered_successor(before, contract=changed)
        self.assertNotEqual(incompatible.reference, after.reference)
        self.assertNotEqual(incompatible.descriptor_document.product.runtime_contract, contract)
        self.assertEqual(current.node("api-x").configuration_artifacts, desired.node("api-x").configuration_artifacts)
        desired = desired.update_node(replace(desired.node("api-x"), metadata=selected_metadata(incompatible)))
        self.refused(current, desired, tuple(incompatible if value == after else value for value in products))


class RetainedImageIngressAdmissionTests(unittest.TestCase):
    pair = ManagedImageUpdateAdmissionTests.pair
    transition = ManagedImageUpdateAdmissionTests.transition

    def refused(self, current, desired, products):
        profile = retained_image_profile(self)
        transition = self.transition(current, desired)
        plan = derive_activity_plan(transition, profile=profile)
        self.assertTrue(plan.ready_for_execution,
            "registered counterexample must retain the valid Core image relation")
        self.assertIsNone(runtime_management_plan_profile(transition, registered_products=products))
        self.assertTrue(runtime_management_execution_is_unsupported(current, desired, plan,
            registered_products=products, derivation_profile=profile))

    def test_exact_new_profile_derivation_wire_and_registered_material(self):
        profile = retained_image_profile(self)
        current, desired, products, before, after = self.pair()
        for left, right, selected in ((current, desired, after), (desired, current, before)):
            with self.subTest(selected=selected.reference.identity.key):
                transition = self.transition(left, right)
                self.assertIs(runtime_management_plan_profile(transition,
                    registered_products=products), profile)
                plan = derive_activity_plan(transition, profile=profile)
                self.assertEqual(plan, compile_retained_image_ingress_activity_plan(
                    transition.current, transition.desired))
                self.assertTrue(plan.activities)
                self.assertTrue(plan.ready_for_execution)
                self.assertNotEqual(plan, derive_activity_plan(transition, profile=PROFILE))
                wire = encode_stored_activity_plan(plan, profile=profile)
                self.assertEqual(wire["version"], 1)
                self.assertEqual(wire["derivation_profile"], profile.value)
                self.assertEqual(decode_stored_activity_plan(wire), (plan, profile))
                self.assertFalse(runtime_management_execution_is_unsupported(left, right, plan,
                    registered_products=products, derivation_profile=profile))
                activity, = (value for value in plan.activities if type(value.operation) is ReconcileNode)
                context = _context(activity=replace(activity, dependencies=()), base_graph=left,
                    desired_graph=right, registered_products=products)
                context = replace(context, activity=activity, plan_record=replace(context.plan_record,
                    plan=plan, derivation_profile=profile))
                material, = runtime_effect_request_for_context(context).products
                self.assertEqual(material.reference, selected.reference)
                self.assertEqual(material.product, selected.descriptor_document.product)
                self.assertIsNone(material.pull_authority)

    def test_exact_active_registration_image_and_full_contract_negative_laws(self):
        retained_image_profile(self)
        ManagedImageUpdateAdmissionTests.test_both_exact_registrations_must_be_available_and_active(self)
        ManagedImageUpdateAdmissionTests.test_each_graph_image_must_match_its_selected_registered_document(self)
        ManagedImageUpdateAdmissionTests.test_full_registered_contract_must_agree_beyond_selected_graph_slots(self)

    def test_recorded_profile_cannot_inherit_empty_fresh_teardown_or_unmanaged_exemptions(self):
        profile = retained_image_profile(self)
        current, desired, products, _, _ = self.pair()
        empty = DeploymentGraph("empty")
        unmanaged = compile_topology(DeploymentTopology("external-runtime", ExternalRuntime()))
        cases = ((current, current), (empty, desired), (current, empty),
            (empty, empty), (empty, unmanaged), (unmanaged, unmanaged))
        for left, right in cases:
            with self.subTest(left=left.name, right=right.name):
                transition = self.transition(left, right)
                for plan in (None, ActivityPlan(()), derive_activity_plan(transition, profile=profile)):
                    self.assertTrue(runtime_management_execution_is_unsupported(left, right, plan,
                        registered_products=products, derivation_profile=profile))
        self.assertIs(runtime_management_plan_profile(self.transition(current, current),
            registered_products=products), PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)
        self.assertFalse(runtime_management_execution_is_unsupported(empty, empty, ActivityPlan(())))

    def test_new_profile_wire_remains_strict_and_action_marker_is_exact(self):
        from control_plane_kit_operations.plan_derivation import planning_derivation_matches_action
        profile = retained_image_profile(self)
        current, desired, _, _, _ = self.pair()
        plan = derive_activity_plan(self.transition(current, desired), profile=profile)
        wire = encode_stored_activity_plan(plan, profile=profile)
        for changed in ({**wire, "version": True}, {**wire, "version": 2},
                {**wire, "derivation_profile": profile}, {**wire, "derivation_profile": "unknown"},
                {**wire, "extra": "CANARY"}, {**wire, "plan": wire}):
            with self.subTest(keys=tuple(changed)), self.assertRaises(ValueError):
                decode_stored_activity_plan(changed)
        self.assertTrue(planning_derivation_matches_action(profile, {"derivation_profile": profile.value}))
        for evidence in ({}, {"derivation_profile": None}, {"derivation_profile": PROFILE.value},
                {"derivation_profile": profile}):
            self.assertFalse(planning_derivation_matches_action(profile, evidence))

    def test_recorded_profile_and_plan_substitution_refuse_without_reinterpreting_v1(self):
        profile = retained_image_profile(self)
        current, desired, products, _, _ = self.pair()
        transition = self.transition(current, desired)
        old = derive_activity_plan(transition, profile=PROFILE)
        new = derive_activity_plan(transition, profile=profile)
        self.assertNotEqual(old, new)
        for recorded, canonical, foreign in ((PROFILE, old, new), (profile, new, old)):
            with self.subTest(recorded=recorded):
                self.assertFalse(runtime_management_execution_is_unsupported(current, desired, canonical,
                    registered_products=products, derivation_profile=recorded))
                for candidate in (foreign, ActivityPlan(()),
                        replace(canonical, activities=canonical.activities[:-1])):
                    self.assertTrue(runtime_management_execution_is_unsupported(current, desired, candidate,
                        registered_products=products, derivation_profile=recorded))
