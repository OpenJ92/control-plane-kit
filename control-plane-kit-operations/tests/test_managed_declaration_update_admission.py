"""Registered successor truth composes with the accepted Core declaration law."""
from dataclasses import replace
import unittest

from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.planning import ReconcileNode
from control_plane_kit_core.products import ProductDescriptorCodec, ProductReference
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRoutesCodec
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct, RegisteredProductStatus
from tests.managed_update_fixture import managed_update_graphs
from tests.test_managed_configuration_update_admission import APPLICATION
from tests import test_managed_image_update_admission as image_fixtures
from tests.test_managed_image_update_admission import selected_metadata
from tests.test_runtime_effect_translation import _context
from control_plane_kit_operations.plan_derivation import (
    PlanDerivationProfile, derive_activity_plan, decode_stored_activity_plan, encode_stored_activity_plan,
)
from control_plane_kit_operations.runtime_management_admission import (
    runtime_management_execution_is_unsupported, runtime_management_plan_profile,
)
from control_plane_kit_operations.runtime_effects import runtime_effect_request_for_context


PROFILE = PlanDerivationProfile.MANAGED_UPDATE_V1
WRAPPERS = ReceiverNodeControlConfigurationCodec()
ROUTES = ReceiverManagementRoutesCodec()


def selected_wrapper(node):
    artifact = select_receiver_node_control_configuration_artifact(artifacts=node.configuration_artifacts,
        environment=node.public_environment + node.socket_environment, control_surfaces=node.block_spec.control_surfaces)
    return artifact, WRAPPERS.decode_bytes(artifact.content.encode())


def declaration_successor(graph, product, *, node_id="api-x", change_default=False, reverse=False):
    node = graph.node(node_id)
    artifact, configured = selected_wrapper(node)
    kinds = (NodeHealthReadKind.READINESS,) if reverse else (NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS)
    declaration = replace(configured.declaration, surface=replace(configured.declaration.surface, health_reads=kinds))
    wrapper = replace(artifact, content=WRAPPERS.encode_bytes(replace(configured, declaration=declaration)).decode(),
        source_digest=None)
    contract = replace(product.runtime_contract, control_surfaces=(declaration.surface,))
    if change_default:
        default = next(value for value in contract.configuration_artifacts if value.artifact_id == artifact.artifact_id)
        material = WRAPPERS.decode_bytes(default.content.encode())
        default = replace(default, content=WRAPPERS.encode_bytes(replace(material, declaration=declaration)).decode(),
            source_digest=None)
        contract = replace(contract, configuration_artifacts=tuple(default if value.artifact_id == default.artifact_id
            else value for value in contract.configuration_artifacts))
    successor = replace(product, identity=replace(product.identity, contract_revision=product.identity.contract_revision + 1),
        runtime_contract=contract)
    document = ProductDescriptorCodec().encode_document(successor)
    reference = ProductReference.from_document(document)
    desired = graph.update_node(replace(node, block_spec=replace(node.block_spec, control_surfaces=(declaration.surface,)),
        metadata={**node.metadata, "product_identity": reference.identity.key,
            "product_descriptor_digest": reference.descriptor_sha256.value},
        configuration_artifacts=tuple(wrapper if value.artifact_id == wrapper.artifact_id else value
            for value in node.configuration_artifacts)))
    gateway = desired.node("gateway")
    route = next(value for value in gateway.configuration_artifacts if value.artifact_id == "gateway-health-targets")
    routes = ROUTES.decode_bytes(route.content.encode())
    routes = replace(routes, targets=tuple(replace(value, declaration=declaration)
        if value.target == configured.target else value for value in routes.targets))
    desired = desired.update_node(replace(gateway, configuration_artifacts=tuple(replace(value,
        content=ROUTES.encode_bytes(routes).decode(), source_digest=None) if value.artifact_id == route.artifact_id else value
        for value in gateway.configuration_artifacts)))
    return DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(desired)), document


class ManagedDeclarationUpdateAdmissionTests(unittest.TestCase):
    transition = image_fixtures.ManagedImageUpdateAdmissionTests.transition
    refused = image_fixtures.ManagedImageUpdateAdmissionTests.refused

    def registered(self, document):
        return RegisteredProduct.from_document(workspace_id="workspace-a", descriptor_document=document,
            source=InlineDescriptorSource(), imported_by="operator-a", imported_at="2026-10-17T18:00:00Z")

    def required_pair(self, *, reverse=False):
        from tests.required_configuration_fixture import required_registration, bind_document
        current, desired, products, before, after = self.pair(change_default=True, reverse=reverse)
        wrapper, _ = selected_wrapper(current.node("api-x"))
        required_before = required_registration(before, {wrapper.artifact_id})
        required_after = required_registration(after, {wrapper.artifact_id})
        products = tuple(required_before if value == before else required_after if value == after else value
            for value in products)
        return (current.update_node(bind_document(current.node("api-x"), required_before.descriptor_document)),
            desired.update_node(bind_document(desired.node("api-x"), required_after.descriptor_document)),
            products, required_before, required_after)

    def assert_required_admitted(self, current, desired, products):
        transition = self.transition(current, desired)
        plan = derive_activity_plan(transition, profile=PROFILE)
        self.assertTrue(plan.ready_for_execution)
        self.assertIs(runtime_management_plan_profile(transition, registered_products=products), PROFILE)
        self.assertFalse(runtime_management_execution_is_unsupported(current, desired, plan,
            registered_products=products, derivation_profile=PROFILE))
        for activity in plan.activities:
            if type(activity.operation) is not ReconcileNode:
                continue
            context = _context(activity=replace(activity, dependencies=()), base_graph=current,
                desired_graph=desired, registered_products=products)
            context = replace(context, activity=activity, plan_record=replace(context.plan_record,
                plan=plan, derivation_profile=PROFILE))
            material, = runtime_effect_request_for_context(context).products
            self.assertEqual(material.product.runtime_contract.configuration_requirements, ())
            self.assertEqual(material.product.runtime_contract.configuration_artifacts,
                tuple(sorted(desired.node(activity.operation.target.node_id).configuration_artifacts)))
        return plan

    def test_required_configuration_declaration_successor_admits_both_directions(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                current, desired, products, before, after = self.required_pair(reverse=reverse)
                original = before.descriptor_document.content, after.descriptor_document.content
                self.assert_required_admitted(current, desired, products)
                self.assertEqual(before.descriptor_document.product.image, after.descriptor_document.product.image)
                self.assertEqual((before.descriptor_document.content, after.descriptor_document.content), original)

    def test_required_configuration_mixed_declarations_refuse_both_directions(self):
        from tests.required_configuration_fixture import required_registration, bind_document
        self.assert_required_admitted(*self.required_pair()[:3])
        for required_side in ("before", "after"):
            with self.subTest(required_side=required_side):
                current, desired, products, before, after = self.pair(change_default=True)
                selected = before if required_side == "before" else after
                wrapper, _ = selected_wrapper(current.node("api-x"))
                required = required_registration(selected, {wrapper.artifact_id})
                products = tuple(required if value == selected else value for value in products)
                if required_side == "before":
                    current = current.update_node(bind_document(current.node("api-x"), required.descriptor_document))
                else:
                    desired = desired.update_node(bind_document(desired.node("api-x"), required.descriptor_document))
                self.refused(current, desired, products)

    def test_required_configuration_successor_preserves_complete_contract_and_registration(self):
        from control_plane_kit_core.configuration import ConfigurationFileMode, ConfigurationMediaType
        from tests.required_configuration_fixture import bind_document
        current, desired, products, before, after = self.required_pair()
        self.assert_required_admitted(current, desired, products)
        for selected in (before, after):
            with self.subTest(missing=selected.reference.identity.key):
                self.refused(current, desired, tuple(value for value in products if value != selected))
                self.refused(current, desired, tuple(replace(value, status=RegisteredProductStatus.REVOKED)
                    if value == selected else value for value in products))
        contract = after.descriptor_document.product.runtime_contract
        requirement, = contract.configuration_requirements
        changes = (
            ("id", replace(requirement, artifact_id="other-control")),
            ("path", replace(requirement, target_path="/etc/cpk/other-control.json")),
            ("media", replace(requirement, media_type=ConfigurationMediaType.TEXT)),
            ("mode", replace(requirement, file_mode=ConfigurationFileMode.OWNER_READ_ONLY
                if requirement.file_mode is ConfigurationFileMode.READ_ONLY else ConfigurationFileMode.READ_ONLY)),
        )
        contracts = [(label, replace(contract, configuration_requirements=(value,))) for label, value in changes]
        contracts.append(("default", replace(contract, configuration_artifacts=tuple(
            replace(value, content='{"mode":"changed-default"}', source_digest=None)
            for value in contract.configuration_artifacts))))
        for label, candidate in contracts:
            with self.subTest(change=label):
                incompatible = self.registered(ProductDescriptorCodec().encode_document(replace(
                    after.descriptor_document.product, runtime_contract=candidate)))
                graph = desired.update_node(bind_document(desired.node("api-x"), incompatible.descriptor_document))
                self.refused(current, graph, tuple(incompatible if value == after else value for value in products))

    def pair(self, *, change_default=False, opaque=False, reverse=False):
        graph, _, _, products = managed_update_graphs(self, application_artifacts=(APPLICATION,))
        before, = [value for value in products if value.reference.identity.key == graph.node("api-x").metadata["product_identity"]]
        product = before.descriptor_document.product
        wrapper, configured = selected_wrapper(graph.node("api-x"))
        # Registered defaults are templates; they need not carry instance identity.
        template = replace(wrapper, content="{}" if opaque else WRAPPERS.encode_bytes(replace(configured,
            target=replace(configured.target, receiver_id="d" * 32))).decode(), source_digest=None)
        product = replace(product, runtime_contract=replace(product.runtime_contract, configuration_artifacts=tuple(
            template if value.artifact_id == wrapper.artifact_id else value for value in product.runtime_contract.configuration_artifacts)))
        before = self.registered(ProductDescriptorCodec().encode_document(product))
        products = tuple(before if value.reference.identity == before.reference.identity else value for value in products)
        graph = graph.update_node(replace(graph.node("api-x"), metadata=selected_metadata(before)))
        graph = DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(graph))
        if reverse:
            graph, document = declaration_successor(graph, product, change_default=True)
            before = self.registered(document)
            products = tuple(before if value.reference.identity.name == before.reference.identity.name else value for value in products)
            product = before.descriptor_document.product
        desired, document = declaration_successor(graph, product, change_default=change_default, reverse=reverse)
        after = self.registered(document)
        return graph, desired, (*products, after), before, after

    def test_exact_registered_templates_and_selected_material_admit_both_directions(self):
        for change_default, opaque, reverse in ((False, True, False), (True, False, False), (True, False, True)):
            with self.subTest(change_default=change_default, opaque=opaque, reverse=reverse):
                current, desired, products, before, after = self.pair(
                    change_default=change_default, opaque=opaque, reverse=reverse)
                transition = self.transition(current, desired)
                plan = derive_activity_plan(transition, profile=PROFILE)
                self.assertTrue(plan.ready_for_execution)
                self.assertIs(runtime_management_plan_profile(transition, registered_products=products), PROFILE)
                self.assertFalse(runtime_management_execution_is_unsupported(current, desired, plan,
                    registered_products=products, derivation_profile=PROFILE))
                self.assertEqual(decode_stored_activity_plan(encode_stored_activity_plan(plan, profile=PROFILE)), (plan, PROFILE))
                reconciles = [value for value in plan.activities if type(value.operation) is ReconcileNode]
                self.assertEqual([value.operation.target.node_id for value in reconciles], ["api-x", "gateway"])
                for activity in reconciles:
                    context = _context(activity=replace(activity, dependencies=()), base_graph=current,
                        desired_graph=desired, registered_products=products)
                    context = replace(context, activity=activity, plan_record=replace(context.plan_record,
                        plan=plan, derivation_profile=PROFILE))
                    material, = runtime_effect_request_for_context(context).products
                    self.assertEqual(material.product.runtime_contract.configuration_artifacts,
                        tuple(sorted(desired.node(activity.operation.target.node_id).configuration_artifacts)))
                    if activity.operation.target.node_id == "api-x":
                        self.assertEqual(material.reference, after.reference)
                self.assertEqual(before.descriptor_document.product.image, after.descriptor_document.product.image)
                self.assertIs(runtime_management_plan_profile(self.transition(current, current), registered_products=products),
                    PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)

    def test_exact_active_products_and_complete_contract_default_are_required(self):
        current, desired, products, before, after = self.pair(change_default=True)
        for selected in products:
            with self.subTest(missing=selected.reference.identity.key):
                self.refused(current, desired, tuple(value for value in products if value != selected))
                self.refused(current, desired, tuple(replace(value, status=RegisteredProductStatus.REVOKED)
                    if value == selected else value for value in products))
        product = after.descriptor_document.product
        wrapper, _ = selected_wrapper(desired.node("api-x"))
        for case in ("opaque", "unrelated", "template-verifier", "template-target"):
            artifacts = []
            for value in product.runtime_contract.configuration_artifacts:
                if case == "opaque" and value.artifact_id == wrapper.artifact_id:
                    value = replace(value, content="{}", source_digest=None)
                elif case == "unrelated" and value.artifact_id == "application":
                    value = replace(value, content='{"mode":"different-default"}', source_digest=None)
                elif case == "template-verifier" and value.artifact_id == wrapper.artifact_id:
                    configured = WRAPPERS.decode_bytes(value.content.encode())
                    configured = replace(configured, verifiers=tuple(replace(family, issuer="foreign-template-issuer")
                        for family in configured.verifiers))
                    value = replace(value, content=WRAPPERS.encode_bytes(configured).decode(), source_digest=None)
                elif case == "template-target" and value.artifact_id == wrapper.artifact_id:
                    configured = WRAPPERS.decode_bytes(value.content.encode())
                    configured = replace(configured, target=replace(configured.target, receiver_id="e" * 32))
                    value = replace(value, content=WRAPPERS.encode_bytes(configured).decode(), source_digest=None)
                artifacts.append(value)
            incompatible = self.registered(ProductDescriptorCodec().encode_document(replace(product,
                runtime_contract=replace(product.runtime_contract, configuration_artifacts=tuple(artifacts)))))
            candidate = desired.update_node(replace(desired.node("api-x"), metadata=selected_metadata(incompatible)))
            self.refused(current, candidate, tuple(incompatible if value == after else value for value in products))

    def test_core_ready_same_wrong_origin_and_wrong_registered_image_refuse(self):
        current, desired, products, _, _ = self.pair()
        wrong_origins = []
        for graph in (current, desired):
            gateway = graph.node("gateway")
            artifacts = []
            for artifact in gateway.configuration_artifacts:
                if artifact.artifact_id == "gateway-health-targets":
                    routes = ROUTES.decode_bytes(artifact.content.encode())
                    routes = replace(routes, targets=tuple(replace(value, origin="http://foreign:8000")
                        if value.target.node_id.value == "api-x" else value for value in routes.targets))
                    artifact = replace(artifact, content=ROUTES.encode_bytes(routes).decode(), source_digest=None)
                artifacts.append(artifact)
            wrong_origins.append(graph.update_node(replace(gateway, configuration_artifacts=tuple(artifacts))))
        self.refused(*wrong_origins, products)
        self.refused(*(graph.update_node(replace(graph.node("api-x"), metadata={**graph.node("api-x").metadata,
            "oci_image": "ghcr.io/test/test-api-x@sha256:" + "c" * 64})) for graph in (current, desired)), products)
