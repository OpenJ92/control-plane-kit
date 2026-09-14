"""#1834: fresh planning completeness, distinct from execution authority."""

from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import BlockSpec
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.planning import InvalidActivityPlan, ReviewChange, compile_graph_activity_plan
from control_plane_kit_core.products import ProductDescriptorDigest, ProductIdentity, ProductReference
from control_plane_kit_core.topology import DeploymentGraph, RuntimeRecord, validate_graph
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations import graph_authoring, runtime_management_admission
from control_plane_kit_operations.deployment_transitions import Deploy
from tests.runtime_management_fixtures import (
    bootstrap_management_graph, cyclic_bootstrap_graph, management_graph,
    registered_management_product, sdk_health_graph, sdk_variable_graph,
)


def require_planning_policy(test_case):
    policy = getattr(runtime_management_admission, "runtime_management_planning_is_unsupported", None)
    test_case.assertIsNotNone(policy, "fresh management planning policy is missing")
    return policy


class ManagementPlanningAdmissionTests(unittest.TestCase):
    def transition(self, current, desired):
        return Deploy(validate_graph(current), validate_graph(desired))

    def pin(self, graph, product, *, node_id="api", alias=False):
        reference = product.reference
        identity = reference.identity.key
        if alias:
            namespace, name, revision = identity.split("/")
            identity = f"{namespace}/{name}/00{revision}"
        node = replace(graph.node(node_id), metadata={
            "product_identity": identity,
            "product_descriptor_digest": reference.descriptor_sha256.value,
        })
        return replace(graph, nodes={**graph.nodes, node_id: node})

    def test_authoritative_node_parser_preserves_normalized_unique_aggregate(self):
        parser = getattr(graph_authoring, "product_reference_in_node", None)
        self.assertIsNotNone(parser, "per-node authoritative product reference extraction is missing")
        product = registered_management_product()
        graph = self.pin(bootstrap_management_graph(self), product, alias=True)
        sibling = replace(graph.node("api"), node_id="sibling", metadata={
            "product_identity": product.reference.identity.key,
            "product_descriptor_digest": product.reference.descriptor_sha256.value,
        })
        runtime = graph.runtimes["docker"]
        graph = replace(graph, nodes={**graph.nodes, "sibling": sibling},
                        runtimes={"docker": replace(runtime, children=runtime.children + ("sibling",))})
        other_reference = ProductReference(ProductIdentity("aaa", "second", 2), ProductDescriptorDigest("c" * 64))
        other = replace(sibling, node_id="another", metadata={
            "product_identity": "aaa/second/02", "product_descriptor_digest": "c" * 64,
        })
        runtime = graph.runtimes["docker"]
        graph = replace(graph, nodes={**graph.nodes, "another": other},
                        runtimes={"docker": replace(runtime, children=runtime.children + ("another",))})
        validate_graph(graph).require_valid()
        self.assertEqual(parser(graph.node("api")), product.reference)
        self.assertEqual(parser(sibling), product.reference)
        self.assertIsNone(parser(graph.node("connector")))
        self.assertEqual(parser(other), other_reference)
        self.assertEqual(graph_authoring.product_references_in_graph(graph), (other_reference, product.reference))
        for metadata in ({"product_identity": "CANARY"}, {
            "product_identity": "test/managed-contract/CANARY",
            "product_descriptor_digest": product.reference.descriptor_sha256.value,
        }):
            with self.subTest(metadata=metadata):
                bad = replace(sibling, metadata=metadata)
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    parser(bad)
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    graph_authoring.product_references_in_graph(replace(graph, nodes={**graph.nodes, "sibling": bad}))

    def test_complete_selected_pairs_allow_ready_review_teardown_and_cutover_values(self):
        policy = require_planning_policy(self)
        ready = bootstrap_management_graph(self)
        review = management_graph(self)
        runtime = ready.runtimes["docker"]
        changed = replace(ready, public_ingresses=ready.public_ingresses + (
            replace(ready.public_ingresses[0], ingress_id="retarget", hostname="changed.example.invalid"),
        ), runtimes={"docker": replace(runtime, management=replace(runtime.management, management_ingress_id="retarget"))})
        for current, desired, expected_ready in (
            (DeploymentGraph("empty"), ready, True), (DeploymentGraph("empty"), review, False),
            (ready, DeploymentGraph("empty"), True), (ready, changed, False), (ready, ready, True),
        ):
            with self.subTest(current=current.name, desired=desired.name, changed=desired is changed):
                transition = self.transition(current, desired)
                self.assertFalse(policy(transition))
                plan = compile_graph_activity_plan(transition.current, transition.desired)
                self.assertEqual(plan.ready_for_execution, expected_ready)
        review_plan = compile_graph_activity_plan(validate_graph(DeploymentGraph("empty")), validate_graph(review))
        self.assertTrue(any(isinstance(value.operation, ReviewChange) for value in review_plan.activities))
        self.assertFalse(review_plan.ready_for_execution)

    def test_unselected_material_on_either_side_and_selection_repair_stay_refused(self):
        policy = require_planning_policy(self)
        complete = bootstrap_management_graph(self)
        for graph in (sdk_health_graph(), sdk_variable_graph(), management_graph(self, selected=False)):
            for current, desired in ((DeploymentGraph("empty"), graph), (graph, DeploymentGraph("empty")), (graph, complete)):
                with self.subTest(graph=graph.name, current=current.name, desired=desired.name):
                    self.assertTrue(policy(self.transition(current, desired)))

    def test_one_selected_runtime_cannot_cover_an_unselected_sdk_runtime(self):
        policy = require_planning_policy(self)
        graph = bootstrap_management_graph(self)
        other = replace(sdk_health_graph().node("api"), node_id="other-api", runtime_id="other")
        graph = replace(graph, nodes={**graph.nodes, other.node_id: other}, runtimes={
            **graph.runtimes, "other": RuntimeRecord("other", RuntimeKind.DOCKER, (other.node_id,)),
        })
        for current, desired in ((DeploymentGraph("empty"), graph), (graph, DeploymentGraph("empty"))):
            self.assertTrue(policy(self.transition(current, desired)))

    def test_selected_variable_only_is_review_work_and_mixed_surface_keeps_health_scope(self):
        policy = require_planning_policy(self)
        base = bootstrap_management_graph(self)
        for health_reads in ((), (NodeHealthReadKind.LIVENESS,)):
            workload = sdk_variable_graph(health_reads=health_reads).node("api")
            desired = replace(base, nodes={**base.nodes, "api": workload})
            transition = self.transition(DeploymentGraph("empty"), desired)
            self.assertFalse(policy(transition))
            plan = compile_graph_activity_plan(transition.current, transition.desired)
            if not health_reads:
                self.assertFalse(plan.ready_for_execution)
                self.assertTrue(any(isinstance(value.operation, ReviewChange) for value in plan.activities))
            self.assertTrue(runtime_management_admission.runtime_management_execution_is_unsupported(
                transition.current.graph, transition.desired.graph, plan,
            ))

    def test_exact_normalized_product_projection_is_checked_per_node_on_both_sides(self):
        policy = require_planning_policy(self)
        for transit in (False, True):
            product = registered_management_product(transit=transit)
            graph = management_graph(self) if transit else bootstrap_management_graph(self)
            contract = product.descriptor_document.product.runtime_contract
            node = replace(graph.node("api"), block_spec=replace(
                graph.node("api").block_spec, capabilities=contract.capabilities,
                control_surfaces=contract.control_surfaces, gateway_transit=contract.gateway_transit,
            ))
            faithful = self.pin(replace(graph, nodes={**graph.nodes, "api": node}), product, alias=True)
            omitted = replace(faithful, nodes={**faithful.nodes, "api": replace(node,
                metadata=faithful.node("api").metadata, block_spec=BlockSpec("api"))})
            for current, desired in ((DeploymentGraph("empty"), faithful), (faithful, DeploymentGraph("empty"))):
                self.assertFalse(policy(self.transition(current, desired), registered_products=(product,)))
            for current, desired in ((DeploymentGraph("empty"), omitted), (omitted, DeploymentGraph("empty"))):
                self.assertTrue(policy(self.transition(current, desired), registered_products=(product,)))

    def test_same_product_faithful_sibling_cannot_hide_an_omitted_node(self):
        policy = require_planning_policy(self)
        product = registered_management_product()
        graph = self.pin(bootstrap_management_graph(self), product)
        omitted = replace(graph.node("api"), node_id="sibling", block_spec=BlockSpec("sibling"))
        runtime = graph.runtimes["docker"]
        graph = replace(graph, nodes={**graph.nodes, omitted.node_id: omitted},
                        runtimes={"docker": replace(runtime, children=runtime.children + (omitted.node_id,))})
        self.assertEqual(graph_authoring.product_references_in_graph(graph), (product.reference,))
        for current, desired in ((DeploymentGraph("empty"), graph), (graph, DeploymentGraph("empty"))):
            self.assertTrue(policy(self.transition(current, desired), registered_products=(product,)))

    def test_projection_values_not_just_presence_must_match_selected_product(self):
        policy = require_planning_policy(self)
        product = registered_management_product()
        graph = self.pin(bootstrap_management_graph(self), product)
        node = graph.node("api")
        changed_health = replace(node.block_spec.control_surfaces[0], health_reads=(NodeHealthReadKind.READINESS,))
        changed_variables = sdk_variable_graph(health_reads=(NodeHealthReadKind.LIVENESS,)).node("api").block_spec.control_surfaces[0]
        for surface in (changed_health, changed_variables):
            changed = replace(graph, nodes={**graph.nodes, "api": replace(node,
                block_spec=replace(node.block_spec, control_surfaces=(surface,)))})
            for current, desired in ((DeploymentGraph("empty"), changed), (changed, DeploymentGraph("empty"))):
                self.assertTrue(policy(self.transition(current, desired), registered_products=(product,)))

    def test_unrelated_and_different_digest_catalog_entries_do_not_taint_plain_work(self):
        policy = require_planning_policy(self)
        product = registered_management_product()
        graph = management_graph(self)
        other_pin = self.pin(graph, product)
        other_pin = replace(other_pin, nodes={**other_pin.nodes, "api": replace(other_pin.node("api"), metadata={
            **other_pin.node("api").metadata, "product_descriptor_digest": "0" * 64,
        })})
        for desired in (graph, other_pin):
            self.assertFalse(policy(self.transition(DeploymentGraph("empty"), desired), registered_products=(product,)))

    def test_malformed_reference_precedes_empty_exception_but_valid_omission_preserves_it(self):
        policy = require_planning_policy(self)
        product = registered_management_product()
        graph = self.pin(management_graph(self), product)
        self.assertFalse(policy(self.transition(graph, graph), registered_products=(product,)))
        self.assertFalse(policy(self.transition(graph, replace(graph, name="renamed")), registered_products=(product,)))
        bad = replace(graph, nodes={**graph.nodes, "api": replace(graph.node("api"), metadata={
            "product_identity": "test/managed-contract/REFERENCE-CANARY",
            "product_descriptor_digest": product.reference.descriptor_sha256.value,
        })})
        for registrations in ((), (product,)):
            self.assertTrue(policy(self.transition(bad, bad), registered_products=registrations))

    def test_real_cycle_is_typed_core_refusal_not_a_successful_policy_result(self):
        policy = require_planning_policy(self)
        desired = cyclic_bootstrap_graph(self)
        transition = self.transition(DeploymentGraph("empty"), desired)
        with self.assertRaises(InvalidActivityPlan):
            compile_graph_activity_plan(transition.current, transition.desired)
        with self.assertRaises(InvalidActivityPlan):
            policy(transition)
