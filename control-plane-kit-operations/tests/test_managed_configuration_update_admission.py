"""Current managed content interpretation binds complete registered material."""

from dataclasses import replace
import json
import unittest

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.planning import ActivityPlan, ReconcileNode
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRoutesCodec
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import (
    PlanDerivationProfile, decode_stored_activity_plan, derive_activity_plan,
    encode_stored_activity_plan,
)
from control_plane_kit_operations.runtime_management_admission import (
    runtime_management_execution_is_unsupported, runtime_management_plan_profile,
)
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from control_plane_kit_operations.workflows import InvalidOperationCommand
from tests.managed_update_fixture import managed_update_graphs
from tests.test_runtime_effect_translation import _context, _registered_pull_authority


PROFILE = PlanDerivationProfile.MANAGED_UPDATE_V1
APPLICATION = ConfigurationArtifact(
    "application", "/etc/application/settings.json", ConfigurationMediaType.JSON,
    '{"mode":"blue"}',
)


class ManagedConfigurationUpdateAdmissionTests(unittest.TestCase):
    def pair(self, *, extra=False):
        a, b, _, products = managed_update_graphs(self, application_artifacts=(APPLICATION,))
        current = b if extra else a
        node = current.node("api-x")
        desired = current.update_node(replace(node, configuration_artifacts=tuple(
            replace(value, content='{"mode":"green"}', source_digest=None)
            if value.artifact_id == "application" else value
            for value in node.configuration_artifacts
        )))
        return current, desired, products

    def admitted(self, current, desired, products):
        left, right = validate_graph(current), validate_graph(desired)
        self.assertTrue(left.valid, left.errors)
        self.assertTrue(right.valid, right.errors)
        transition = Deploy(left, right)
        self.assertIs(runtime_management_plan_profile(transition, registered_products=products), PROFILE)
        plan = derive_activity_plan(transition, profile=PROFILE)
        self.assertTrue(plan.ready_for_execution)
        self.assertFalse(runtime_management_execution_is_unsupported(
            current, desired, plan, registered_products=products, derivation_profile=PROFILE,
        ))
        return plan

    def test_current_profile_roundtrip_and_noop_selection(self):
        current, desired, products = self.pair()
        plan = self.admitted(current, desired, products)
        self.assertEqual(decode_stored_activity_plan(encode_stored_activity_plan(plan, profile=PROFILE)), (plan, PROFILE))
        transition = Deploy(validate_graph(current), validate_graph(current))
        selected = runtime_management_plan_profile(transition, registered_products=products)
        self.assertIs(selected, PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)
        empty = derive_activity_plan(transition, profile=selected)
        self.assertEqual(empty.activities, ())
        self.assertEqual(decode_stored_activity_plan(encode_stored_activity_plan(empty, profile=selected)), (empty, selected))

    def test_every_selected_participant_and_exact_plan_are_required(self):
        current, desired, products = self.pair(extra=True)
        plan = self.admitted(current, desired, products)
        for missing in products:
            with self.subTest(product=missing.reference):
                remaining = tuple(value for value in products if value != missing)
                self.assertIsNone(runtime_management_plan_profile(
                    Deploy(validate_graph(current), validate_graph(desired)), registered_products=remaining,
                ))
                self.assertTrue(runtime_management_execution_is_unsupported(
                    current, desired, plan, registered_products=remaining, derivation_profile=PROFILE,
                ))
        for candidate in (ActivityPlan(()), replace(plan, activities=plan.activities[:-1])):
            self.assertTrue(runtime_management_execution_is_unsupported(
                current, desired, candidate, registered_products=products, derivation_profile=PROFILE,
            ))

    def test_retained_extra_receiver_requires_its_complete_route(self):
        current, desired, products = self.pair(extra=True)
        self.admitted(current, desired, products)
        def missing_route(graph):
            gateway = graph.node("gateway")
            values = []
            for artifact in gateway.configuration_artifacts:
                if artifact.artifact_id == "gateway-health-targets":
                    routes = ReceiverManagementRoutesCodec().decode_bytes(artifact.content.encode())
                    routes = replace(routes, targets=tuple(value for value in routes.targets
                        if value.target.node_id.value != "api-y"))
                    artifact = replace(artifact, content=ReceiverManagementRoutesCodec().encode_bytes(routes).decode(), source_digest=None)
                values.append(artifact)
            return graph.update_node(replace(gateway, configuration_artifacts=tuple(values)))
        left, right = missing_route(current), missing_route(desired)
        validate_graph(left).require_valid()
        validate_graph(right).require_valid()
        self.assertIsNone(runtime_management_plan_profile(
            Deploy(validate_graph(left), validate_graph(right)), registered_products=products,
        ))

    def test_selected_content_material_and_image_pull_refusal(self):
        current, desired, products = self.pair()
        plan = self.admitted(current, desired, products)
        activity, = (value for value in plan.activities if type(value.operation) is ReconcileNode)
        context = _context(activity=replace(activity, dependencies=()), base_graph=current,
            desired_graph=desired, registered_products=products)
        context = replace(context, activity=activity, plan_record=replace(context.plan_record,
            plan=plan, derivation_profile=PROFILE))
        request = runtime_effect_request_for_context(context)
        material, = request.products
        self.assertEqual(material.node_id, "api-x")
        self.assertEqual(material.product.runtime_contract.configuration_artifacts,
            desired.node("api-x").configuration_artifacts)
        with self.assertRaises(InvalidOperationCommand):
            runtime_effect_request_for_context(replace(context,
                image_pull_authorities=(_registered_pull_authority(repository="test/test-api-x"),)))

    def test_unrelated_runtime_receivers_do_not_enter_selected_routes_or_effects(self):
        current, desired, products = self.pair()
        names = {name: "other-" + name for name in current.nodes}
        unrelated = {}
        for node in current.nodes.values():
            artifacts = []
            for value in node.configuration_artifacts:
                document = json.loads(value.content)
                if "target" in document:
                    document["target"].update(runtime_id="other", node_id=names[node.node_id])
                    value = replace(value, content=json.dumps(document), source_digest=None)
                artifacts.append(value)
            unrelated[names[node.node_id]] = replace(node, node_id=names[node.node_id], runtime_id="other",
                block_spec=replace(node.block_spec, role_id=names[node.node_id]),
                configuration_artifacts=tuple(artifacts), metadata={})
        runtime = current.runtimes["docker"]
        other = replace(runtime, runtime_id="other", children=tuple(unrelated),
            management=replace(runtime.management, gateway_node_id="other-gateway", management_ingress_id="other-management"))
        ingress = current.public_ingresses[0]
        other_ingress = replace(ingress, ingress_id="other-management",
            target=replace(ingress.target, node_id="other-gateway"), connector_node_id="other-connector",
            hostname="other.example.invalid")
        pair = tuple(replace(graph, nodes={**graph.nodes, **unrelated},
            runtimes={**graph.runtimes, "other": other},
            public_ingresses=(*graph.public_ingresses, other_ingress)) for graph in (current, desired))
        plan = self.admitted(*pair, products)
        self.assertFalse(any(getattr(value.operation.target, "runtime_id", None) == "other"
            for value in plan.activities if hasattr(value.operation, "target")))
