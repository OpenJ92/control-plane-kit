"""Managed updates are a closed selected-product execution family."""

from dataclasses import replace
import unittest

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.configuration_installation import (
    ConfigurationInstallationDisposition,
    ConfigurationInstallationReceipt,
)
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCompletion,
    configuration_invocation_correlation_for_request,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRef,
    ConfigurationInstanceSelection,
)
from control_plane_kit_core.lifecycle import ResourceLifecycle
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.receiver_management_routes import (
    ReceiverManagementRouteBinding,
    ReceiverManagementRoutes,
    ReceiverManagementRoutesCodec,
)
from control_plane_kit_core.planning import (
    ActivityPlan,
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ReconcileNode,
    RemoveNodeResource,
    StartNode,
    StopNode,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_core.runtime_effects import (
    EffectResultKind,
    RuntimeEffectKind,
    RuntimeEffectResult,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import _admit_managed_installation_result
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.graph_authoring import product_reference_in_node
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.runtime_management_admission import (
    runtime_management_execution_is_unsupported,
    runtime_management_plan_profile,
)
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context
from tests.managed_update_fixture import managed_update_graphs
from tests.test_runtime_effect_translation import _context


PROFILE = PlanDerivationProfile.MANAGED_UPDATE_V1


class ManagedUpdateAdmissionTests(unittest.TestCase):
    def _with_routes(self, graph, routes):
        gateway = graph.node("gateway")
        encoded = ReceiverManagementRoutesCodec().encode_bytes(routes).decode("utf-8")
        return replace(graph, nodes={**graph.nodes, "gateway": replace(
            gateway,
            configuration_artifacts=tuple(
                replace(value, content=encoded)
                if value.artifact_id == "gateway-health-targets" else value
                for value in gateway.configuration_artifacts
            ),
        )})

    def test_exact_registered_add_and_remove_are_supported(self):
        graph_a, graph_b, graph_c, products = managed_update_graphs(self)
        for current, desired in ((graph_a, graph_b), (graph_b, graph_c)):
            with self.subTest(nodes=(tuple(current.nodes), tuple(desired.nodes))):
                plan = derive_activity_plan(
                    Deploy(validate_graph(current), validate_graph(desired)), profile=PROFILE
                )
                self.assertTrue(
                    plan.ready_for_execution,
                    DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan),
                )
                self.assertFalse(runtime_management_execution_is_unsupported(
                    current, desired, plan,
                    registered_products=products, derivation_profile=PROFILE,
                ))

    def test_route_admission_is_application_independent_but_preserves_same_product_xy(self):
        references = []
        for application in ("hello", "secrets"):
            graph_a, graph_b, _, products = managed_update_graphs(
                self, product_prefix=application,
            )
            plan = derive_activity_plan(
                Deploy(validate_graph(graph_a), validate_graph(graph_b)),
                profile=PROFILE,
            )
            self.assertFalse(runtime_management_execution_is_unsupported(
                graph_a, graph_b, plan,
                registered_products=products,
                derivation_profile=PROFILE,
            ))
            x = next(value.reference for value in products
                if value.reference == product_reference_in_node(graph_a.node("api-x")))
            y = product_reference_in_node(graph_b.node("api-y"))
            self.assertEqual(x, y)
            references.append(x)
        self.assertNotEqual(*references)

    def test_route_aliases_are_opaque_but_retained_aliases_are_stable(self):
        graph_a, graph_b, _, products = managed_update_graphs(self)

        def aliased(graph):
            artifact = next(
                value for value in graph.node("gateway").configuration_artifacts
                if value.artifact_id == "gateway-health-targets"
            )
            routes = ReceiverManagementRoutesCodec().decode_bytes(
                artifact.content.encode("utf-8")
            )
            aliases = {
                "self": "gateway-self",
                "api-x": "active-workload",
                "api-y": "candidate-workload",
            }
            return self._with_routes(graph, replace(routes, targets=tuple(
                replace(value, target_id=aliases[value.target_id])
                for value in routes.targets
            )))

        current, desired = aliased(graph_a), aliased(graph_b)
        transition = Deploy(validate_graph(current), validate_graph(desired))
        self.assertIs(runtime_management_plan_profile(
            transition, registered_products=products,
        ), PROFILE)
        plan = derive_activity_plan(transition, profile=PROFILE)
        self.assertFalse(runtime_management_execution_is_unsupported(
            current, desired, plan,
            registered_products=products, derivation_profile=PROFILE,
        ))

        self_current, self_desired, _, self_products = managed_update_graphs(
            self,
            workload_ids=("self", "api-y"),
            route_aliases={
                "gateway": "gateway-self",
                "self": "workload-self",
                "api-y": "candidate-workload",
            },
        )
        self_transition = Deploy(
            validate_graph(self_current), validate_graph(self_desired),
        )
        self.assertIs(runtime_management_plan_profile(
            self_transition, registered_products=self_products,
        ), PROFILE)
        self_plan = derive_activity_plan(self_transition, profile=PROFILE)
        self.assertFalse(runtime_management_execution_is_unsupported(
            self_current, self_desired, self_plan,
            registered_products=self_products, derivation_profile=PROFILE,
        ))

    def test_planning_selects_only_the_closed_managed_update_profile(self):
        graph_a, graph_b, _, products = managed_update_graphs(self)
        transition = Deploy(validate_graph(graph_a), validate_graph(graph_b))
        self.assertIs(
            runtime_management_plan_profile(
                transition, registered_products=products,
            ),
            PROFILE,
        )
        self.assertIsNone(runtime_management_plan_profile(
            transition, registered_products=products[:-1],
        ))

        gateway = graph_b.node("gateway")
        route = next(value for value in gateway.configuration_artifacts
                     if value.artifact_id == "gateway-health-targets")
        malformed = replace(graph_b, nodes={**graph_b.nodes, "gateway": replace(
            gateway, configuration_artifacts=tuple(
                replace(value, target_path="/tmp/targets.json") if value is route else value
                for value in gateway.configuration_artifacts
            ),
        )})
        self.assertIsNone(runtime_management_plan_profile(
            Deploy(validate_graph(graph_a), validate_graph(malformed)),
            registered_products=products,
        ))

    def test_exact_plan_activities_lower_with_their_pinned_graph_side_material(self):
        graph_a, graph_b, graph_c, products = managed_update_graphs(self)
        for current, desired in ((graph_a, graph_b), (graph_b, graph_c)):
            plan = derive_activity_plan(
                Deploy(validate_graph(current), validate_graph(desired)), profile=PROFILE
            )
            mutations = tuple(
                activity for activity in plan.activities
                if type(activity.operation) in (
                    ReconcileNode, StartNode, StopNode, RemoveNodeResource,
                )
            )
            self.assertTrue(mutations)
            for activity in mutations:
                context = _context(
                    activity=replace(activity, dependencies=()),
                    base_graph=current,
                    desired_graph=desired,
                    registered_products=products,
                )
                context = replace(context, activity=activity, plan_record=replace(
                    context.plan_record,
                    plan=plan,
                    derivation_profile=PROFILE,
                ))
                request = runtime_effect_request_for_context(context)
                self.assertEqual(request.operation, activity.operation)
                self.assertEqual(len(request.products), 1)
                expected_node = activity.operation.target.node_id
                self.assertEqual(request.products[0].node_id, expected_node)
                if type(activity.operation) is ReconcileNode:
                    artifacts = request.products[0].product.runtime_contract.configuration_artifacts
                    self.assertEqual(
                        {value.artifact_id for value in artifacts},
                        {"gateway-health-transit", "gateway-health-targets", "gateway-control"},
                    )

    def test_profile_plan_and_selected_material_must_be_exact(self):
        graph_a, graph_b, _, products = managed_update_graphs(self)
        plan = derive_activity_plan(Deploy(validate_graph(graph_a), validate_graph(graph_b)), profile=PROFILE)

        def refused(current=graph_a, desired=graph_b, candidate=plan, catalogue=products, profile=PROFILE):
            return runtime_management_execution_is_unsupported(
                current, desired, candidate,
                registered_products=catalogue, derivation_profile=profile,
            )

        for profile in (None, PROFILE.value, PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1):
            with self.subTest(profile=profile):
                self.assertTrue(refused(profile=profile))
        for candidate in (None, ActivityPlan(()), replace(plan, activities=plan.activities[:-1])):
            with self.subTest(candidate=candidate):
                self.assertTrue(refused(candidate=candidate))
        self.assertTrue(refused(catalogue=products[:-1]))

        gateway = graph_b.node("gateway")
        wrong = ConfigurationArtifact(
            "management-routes", "/etc/cpk/gateway/health-targets.json",
            ConfigurationMediaType.JSON, '{"targets":["api-x","api-y"]}',
        )
        artifacts = tuple(wrong if value.artifact_id == "gateway-health-targets" else value
                          for value in gateway.configuration_artifacts)
        changed = replace(graph_b, nodes={**graph_b.nodes, "gateway": replace(
            gateway, configuration_artifacts=artifacts)})
        changed_plan = derive_activity_plan(Deploy(validate_graph(graph_a), validate_graph(changed)), profile=PROFILE)
        self.assertTrue(refused(desired=changed, candidate=changed_plan))

    def test_route_admission_refuses_each_semantic_drift_and_non_docker_runtime(self):
        graph_a, graph_b, _, products = managed_update_graphs(self)
        route_artifact = next(
            value for value in graph_b.node("gateway").configuration_artifacts
            if value.artifact_id == "gateway-health-targets"
        )
        routes = ReceiverManagementRoutesCodec().decode_bytes(
            route_artifact.content.encode("utf-8")
        )
        by_id = {value.target_id: value for value in routes.targets}
        api_x = by_id["api-x"]
        api_y = by_id["api-y"]
        foreign_target = replace(api_y.target, receiver_id="d" * 32)
        cases = {
            "missing-self": replace(routes, targets=tuple(
                value for value in routes.targets if value.target_id != "self"
            )),
            "missing-retained": replace(routes, targets=tuple(
                value for value in routes.targets if value.target_id != "api-x"
            )),
            "extra": replace(routes, targets=routes.targets + (
                ReceiverManagementRouteBinding(
                    "foreign", foreign_target, api_y.declaration,
                    "http://foreign:8000",
                ),
            )),
            "wrong-gateway-target": replace(routes, gateway_target=api_y.target),
            "wrong-target-id": replace(routes, targets=tuple(
                replace(value, target_id="renamed") if value == api_x else value
                for value in routes.targets
            )),
            "wrong-receiver": replace(routes, targets=tuple(
                replace(value, target=foreign_target) if value == api_y else value
                for value in routes.targets
            )),
            "wrong-declaration": replace(routes, targets=tuple(
                replace(value, declaration=replace(
                    api_x.declaration,
                    surface=replace(
                        api_x.declaration.surface,
                        health_reads=(NodeHealthReadKind.LIVENESS,),
                    ),
                ))
                if value == api_x else value
                for value in routes.targets
            )),
            "wrong-origin-host": replace(routes, targets=tuple(
                replace(value, origin="http://foreign:8000")
                if value == api_x else value
                for value in routes.targets
            )),
            "wrong-origin-port": replace(routes, targets=tuple(
                replace(value, origin="http://api-x:8001")
                if value == api_x else value
                for value in routes.targets
            )),
        }
        for label, candidate_routes in cases.items():
            with self.subTest(case=label):
                desired = self._with_routes(graph_b, candidate_routes)
                transition = Deploy(validate_graph(graph_a), validate_graph(desired))
                self.assertIsNone(runtime_management_plan_profile(
                    transition, registered_products=products,
                ))
                plan = derive_activity_plan(transition, profile=PROFILE)
                self.assertTrue(runtime_management_execution_is_unsupported(
                    graph_a, desired, plan,
                    registered_products=products, derivation_profile=PROFILE,
                ))

        non_docker_a = replace(graph_a, runtimes={
            "docker": replace(graph_a.runtimes["docker"], kind=RuntimeKind.KUBERNETES),
        })
        non_docker_b = replace(graph_b, runtimes={
            "docker": replace(graph_b.runtimes["docker"], kind=RuntimeKind.KUBERNETES),
        })
        transition = Deploy(validate_graph(non_docker_a), validate_graph(non_docker_b))
        self.assertIsNone(runtime_management_plan_profile(
            transition, registered_products=products,
        ))

    def test_readiness_lifecycle_and_authority_delivery_are_not_relaxed(self):
        graph_a, graph_b, _, products = managed_update_graphs(self)

        def refused(desired):
            plan = derive_activity_plan(Deploy(validate_graph(graph_a), validate_graph(desired)), profile=PROFILE)
            return runtime_management_execution_is_unsupported(
                graph_a, desired, plan,
                registered_products=products, derivation_profile=PROFILE,
            )

        api_y = graph_b.node("api-y")
        surface = api_y.block_spec.control_surfaces[0]
        liveness = replace(api_y, block_spec=replace(api_y.block_spec,
            control_surfaces=(replace(surface, health_reads=(NodeHealthReadKind.LIVENESS,)),)))
        self.assertTrue(refused(replace(graph_b, nodes={**graph_b.nodes, "api-y": liveness})))

        retained = replace(api_y, lifecycle=ResourceLifecycle.owned_with_retained_data("state"))
        self.assertTrue(refused(replace(graph_b, nodes={**graph_b.nodes, "api-y": retained})))

        delivery = RuntimeAuthorityAccessDelivery(
            RuntimeAuthorityReference("foreign"),
            RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )
        delivered = replace(api_y, runtime_authority_deliveries=(delivery,))
        self.assertTrue(refused(replace(graph_b, nodes={**graph_b.nodes, "api-y": delivered})))

    def test_gateway_reconcile_success_requires_exact_replaced_installation_receipt(self):
        graph_a, graph_b, _, products = managed_update_graphs(self)
        plan = derive_activity_plan(
            Deploy(validate_graph(graph_a), validate_graph(graph_b)), profile=PROFILE,
        )
        activity = next(
            value for value in plan.activities
            if type(value.operation) is ReconcileNode
        )
        context = _context(
            activity=replace(activity, dependencies=()),
            base_graph=graph_a,
            desired_graph=graph_b,
            registered_products=products,
        )
        context = replace(context, activity=activity, plan_record=replace(
            context.plan_record, plan=plan, derivation_profile=PROFILE,
        ))
        request = runtime_effect_request_for_context(context)
        material, = request.products
        request = replace(
            request,
            kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
            configuration_instances=ConfigurationInstanceSelection(tuple(
                ConfigurationInstanceRef(
                    allocation_id=f"allocation-{index}",
                    workspace_id=request.source.workspace_id,
                    runtime_id=material.runtime_id,
                    node_id=material.node_id,
                    artifact_id=artifact.artifact_id,
                    target_path=artifact.target_path,
                    media_type=artifact.media_type,
                    file_mode=artifact.file_mode,
                    content_digest=artifact.content_digest,
                )
                for index, artifact in enumerate(
                    material.product.runtime_contract.configuration_artifacts,
                    start=1,
                )
            )),
        )
        correlation = configuration_invocation_correlation_for_request(request)
        selection = configuration_invocation_selection_fingerprint(correlation.selection)

        def result(receipt=None, *, evidence=None):
            body = {} if evidence is None else dict(evidence)
            if receipt is not None:
                body["configuration_installation_receipt"] = receipt.descriptor()
            return RuntimeEffectResult.succeeded(request.effect_id, evidence=body)

        replaced = ConfigurationInstallationReceipt(
            correlation.request_fingerprint, selection,
            ConfigurationInstallationDisposition.REPLACED,
            "1" * 64, "2" * 64,
        )
        accepted = result(replaced)
        self.assertEqual(
            _admit_managed_installation_result(context, request, accepted),
            accepted,
        )
        completion = ConfigurationInvocationCompletion(
            correlation.request_fingerprint, selection,
        )
        refused = (
            result(),
            result(evidence={"configuration_invocation_completion": completion.descriptor()}),
            result(ConfigurationInstallationReceipt(
                correlation.request_fingerprint, selection,
                ConfigurationInstallationDisposition.CREATED, None, "2" * 64,
            )),
            result(ConfigurationInstallationReceipt(
                correlation.request_fingerprint, selection,
                ConfigurationInstallationDisposition.REUSED, "2" * 64, "2" * 64,
            )),
            result(ConfigurationInstallationReceipt(
                "f" * 64, selection,
                ConfigurationInstallationDisposition.REPLACED, "1" * 64, "2" * 64,
            )),
            result(ConfigurationInstallationReceipt(
                correlation.request_fingerprint, "e" * 64,
                ConfigurationInstallationDisposition.REPLACED, "1" * 64, "2" * 64,
            )),
            result(evidence={"configuration_installation_receipt": {"profile": "wrong"}}),
        )
        for candidate in refused:
            with self.subTest(evidence=candidate.evidence):
                admitted = _admit_managed_installation_result(
                    context, request, candidate,
                )
                self.assertIs(admitted.kind, EffectResultKind.UNCERTAIN)
                self.assertEqual(admitted.effect_id, request.effect_id)


if __name__ == "__main__":
    unittest.main()
