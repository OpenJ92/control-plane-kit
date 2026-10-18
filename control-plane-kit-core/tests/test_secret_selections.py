"""#1974 pure instance selection laws; no provider or installed truth is invented."""
from copy import deepcopy
from dataclasses import replace, fields
import importlib
import unittest

from control_plane_kit_core.planning import compile_activity_plan, ReconcileNode, StartNode, NodeTarget
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_for_request, runtime_effect_intent_fingerprint,
    runtime_effect_request_for_intent,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectContractError, RuntimeEffectKind
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent, SecretFileDelivery
from control_plane_kit_core.topology import GraphDescriptorCodec, diff_graphs, validate_graph
from control_plane_kit_core.algebra import BlockSockets, DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.products import (
    ContainerServerProduct, ProductIdentity, OciImageReference, ProductRuntimeContract,
    ProductInstanceConfiguration, ProductDescriptorCodec, instantiate_product,
)
from control_plane_kit_core.topology import compile_topology
from tests.test_environment_secrets import secret_graph, bare_node
from tests.test_configuration_effect_contract import selected_request


def language(name="secret_selections"):
    module = "control_plane_kit_core." + name
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as error:
        if error.name != module:
            raise
        raise AssertionError("missing #1974 secret selection capability") from error


def choice(version="version-1", reference="secret://local/workspace-a/database"):
    return language().SecretVersionSelection(
        SecretReference(reference), SecretUseIntent.POSTGRES_PASSWORD, version)


def selected_graph(version="version-1"):
    graph = secret_graph("database")
    return graph.update_node(replace(graph.node("service"), secret_selections=(choice(version),)))


def request_with_version(version="version-1"):
    m = language()
    request = selected_request()
    material = request.products[0]
    delivery = material.product.runtime_contract.secret_deliveries[0]
    return replace(request, products=(replace(material, secret_selections=(
        m.SecretVersionSelection(delivery.reference, delivery.intent, version),)),))


class SecretSelectionTests(unittest.TestCase):
    def declaration_pair(self, *, reverse=False):
        from control_plane_kit_core.node_control import NodeHealthReadKind
        from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
        from tests.test_management_bootstrap_planning import ManagedImageUpdateTests
        delivery = SecretFileDelivery("/run/secrets/key", choice().reference, choice().intent)
        initial, _, product, _ = ManagedImageUpdateTests().pair(secret_deliveries=(delivery,))
        codec = ReceiverNodeControlConfigurationCodec()
        wrapper = next(value for value in product.runtime_contract.configuration_artifacts
                       if value.artifact_id == "receiver")
        configured = codec.decode_bytes(wrapper.content.encode())
        readiness = (NodeHealthReadKind.READINESS,)
        both = (NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS)
        graphs, documents = [], []
        for revision, kinds in enumerate((both, readiness) if reverse else (readiness, both), 1):
            declaration = replace(configured.declaration,
                surface=replace(configured.declaration.surface, health_reads=kinds))
            artifact = replace(wrapper, content=codec.encode_bytes(
                replace(configured, declaration=declaration)).decode(), source_digest=None)
            artifacts = tuple(artifact if value.artifact_id == wrapper.artifact_id else value
                              for value in product.runtime_contract.configuration_artifacts)
            successor = replace(product, identity=replace(product.identity, contract_revision=revision),
                runtime_contract=replace(product.runtime_contract,
                    control_surfaces=(declaration.surface,), configuration_artifacts=artifacts))
            document = ProductDescriptorCodec().encode_document(successor)
            node = initial.node("workload")
            graphs.append(initial.update_node(replace(node,
                block_spec=replace(node.block_spec, control_surfaces=(declaration.surface,)),
                configuration_artifacts=artifacts, secret_selections=(choice(),),
                metadata={**node.metadata, "product_identity": successor.identity.key,
                    "product_descriptor_digest": document.content_digest})))
            documents.append(document)
        return (*graphs, tuple(documents))

    def test_health_declaration_successor_carries_exact_selected_version(self):
        authoring = language("topology.secret_selection")
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                current, desired, documents = self.declaration_pair(reverse=reverse)
                carried = authoring.carry_forward_secret_selections(
                    current, desired, {}, product_documents=documents)
                self.assertEqual(carried.node("workload").secret_selections, (choice(),))
                self.assertEqual(carried, desired)

    def assert_declaration_not_carried(self, current, desired, documents):
        carried = language("topology.secret_selection").carry_forward_secret_selections(
            current, desired, {}, product_documents=documents)
        self.assertTrue(all(type(value) is language().UnselectedSecretUse
            for value in carried.node("workload").secret_selections))

    def declaration_product(self, graph, product):
        updated = ProductDescriptorCodec().encode_document(product)
        node = graph.node("workload")
        return graph.update_node(replace(node, metadata={**node.metadata,
            "product_identity": product.identity.key, "product_descriptor_digest": updated.content_digest,
            "oci_image": product.image.execution_reference})), updated

    def test_declaration_carry_refuses_unrelated_node_and_installed_trust_changes(self):
        from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
        from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
        current, desired, documents = self.declaration_pair()
        node = desired.node("workload")
        wrapper = next(value for value in node.configuration_artifacts if value.artifact_id == "receiver")
        codec = ReceiverNodeControlConfigurationCodec()
        configured = codec.decode_bytes(wrapper.content.encode())
        materials = [replace(configured, target=replace(configured.target, receiver_id="e" * 32)),
            replace(configured, verifiers=tuple(replace(value, issuer="different-issuer")
                for value in configured.verifiers))]
        wrappers = [replace(wrapper, content=codec.encode_bytes(value).decode(), source_digest=None)
            for value in materials]
        wrappers += [replace(wrapper, target_path="/elsewhere/receiver.json"),
            replace(wrapper, content=wrapper.content.replace("workload-node-control-configuration.v2",
                "workload-node-control-configuration.v1"), source_digest=None)]
        candidates = [replace(node, metadata={**node.metadata, "unrelated": "changed"}),
            replace(node, block_spec=replace(node.block_spec, role_id="another-role")),
            replace(node, block_spec=replace(node.block_spec, control_surfaces=(
                replace(node.block_spec.control_surfaces[0], provider_socket_name="other"),))),
            replace(node, public_environment=(*node.public_environment, PublicStaticEnvironmentBinding("EXTRA", "value"))),
            replace(node, secret_deliveries=tuple(replace(value, target_path="/run/secrets/moved")
                for value in node.secret_deliveries)),
            replace(node, configuration_artifacts=(*node.configuration_artifacts,
                ConfigurationArtifact("extra", "/etc/extra.json", ConfigurationMediaType.JSON, "{}")))]
        candidates += [replace(node, configuration_artifacts=tuple(value if item.artifact_id == wrapper.artifact_id
            else item for item in node.configuration_artifacts)) for value in wrappers]
        for index, candidate in enumerate(candidates):
            with self.subTest(case=index):
                self.assert_declaration_not_carried(current, desired.update_node(candidate), documents)

    def test_declaration_carry_requires_exact_product_family_image_and_default_relation(self):
        from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
        current, desired, documents = self.declaration_pair()
        product = documents[1].product
        wrapper = next(value for value in product.runtime_contract.configuration_artifacts
                       if value.artifact_id == "receiver")
        codec = ReceiverNodeControlConfigurationCodec()
        configured = codec.decode_bytes(wrapper.content.encode())
        changed_defaults = [replace(wrapper, content="{}", source_digest=None),
            replace(wrapper, content=codec.encode_bytes(replace(configured,
                target=replace(configured.target, receiver_id="e" * 32))).decode(), source_digest=None),
            replace(wrapper, content=codec.encode_bytes(replace(configured, verifiers=tuple(
                replace(value, issuer="different-template-issuer") for value in configured.verifiers))).decode(),
                source_digest=None)]
        candidates = [replace(product, image=replace(product.image, digest="sha256:" + "c" * 64)),
            replace(product, identity=replace(product.identity, name="different-product")),
            replace(product, runtime_contract=replace(product.runtime_contract, configuration_artifacts=(
                *product.runtime_contract.configuration_artifacts,
                ConfigurationArtifact("extra", "/etc/extra.json", ConfigurationMediaType.JSON, "{}"))))]
        candidates += [replace(product, runtime_contract=replace(product.runtime_contract,
            configuration_artifacts=tuple(value if item.artifact_id == wrapper.artifact_id else item
                for item in product.runtime_contract.configuration_artifacts))) for value in changed_defaults]
        for index, candidate in enumerate(candidates):
            graph, document = self.declaration_product(desired, candidate)
            with self.subTest(case=index):
                self.assert_declaration_not_carried(current, graph, (documents[0], document))
        for evidence in ((), documents[:1], (*documents, documents[1]),
                         (documents[0], replace(documents[1], product=candidates[0]))):
            with self.subTest(evidence_count=len(evidence)), self.assertRaises(ValueError):
                language("topology.secret_selection").carry_forward_secret_selections(
                    current, desired, {}, product_documents=evidence)

    def test_declaration_carry_preserves_opaque_and_independent_registered_templates(self):
        from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
        codec = ReceiverNodeControlConfigurationCodec()
        for opaque in (False, True):
            current, desired, documents = self.declaration_pair()
            graphs, updated_documents = [], []
            for graph, document in zip((current, desired), documents, strict=True):
                product = document.product
                artifacts = []
                for artifact in product.runtime_contract.configuration_artifacts:
                    if artifact.artifact_id == "receiver":
                        configured = codec.decode_bytes(artifact.content.encode())
                        content = "{}" if opaque else codec.encode_bytes(replace(configured,
                            target=replace(configured.target, receiver_id="d" * 32))).decode()
                        artifact = replace(artifact, content=content, source_digest=None)
                    artifacts.append(artifact)
                product = replace(product, runtime_contract=replace(product.runtime_contract,
                    configuration_artifacts=tuple(artifacts)))
                graph, document = self.declaration_product(graph, product)
                graphs.append(graph)
                updated_documents.append(document)
            with self.subTest(opaque=opaque):
                carried = language("topology.secret_selection").carry_forward_secret_selections(
                    *graphs, {}, product_documents=tuple(updated_documents))
                self.assertEqual(carried, graphs[1])

    def test_closed_explicit_unselected_and_exact_version_encoding(self):
        m = language()
        selected = choice()
        unselected = m.UnselectedSecretUse(selected.reference, selected.intent)
        for value in (unselected, selected):
            document = value.descriptor()
            self.assertEqual(set(document), {"reference_id", "intent", "version_id"})
            self.assertEqual(m.secret_selection_from_descriptor(document), value)
            for bad in ({k: v for k, v in document.items() if k != "version_id"},
                        {**document, "value": "private-canary"},
                        {**document, "version_id": "x" * 201},
                        {**document, "version_id": True}):
                with self.assertRaises(ValueError) as caught:
                    m.secret_selection_from_descriptor(bad)
                self.assertNotIn("private-canary", str(caught.exception))
        self.assertIsNone(unselected.descriptor()["version_id"])
        self.assertEqual(selected.descriptor()["version_id"], "version-1")

    def test_graph_coverage_shares_one_selection_across_slots_and_rejects_omission(self):
        m = language()
        graph = secret_graph("database")
        node = graph.node("service")
        self.assertEqual(node.secret_selections, (m.UnselectedSecretUse(
            choice().reference, choice().intent),))
        graph = selected_graph()
        codec = GraphDescriptorCodec()
        document = codec.encode(graph)
        self.assertEqual(codec.decode(document).node("service").secret_selections, (choice(),))
        for selections in ((), (choice(), choice()), (choice(), choice("version-2")),
                           (choice(reference="secret://foreign/other"),)):
            with self.assertRaises(ValueError):
                replace(graph.node("service"), secret_selections=selections)
        missing = deepcopy(document)
        del missing["nodes"]["service"]["secret_selections"]
        with self.assertRaises(ValueError):
            codec.decode(missing)

    def test_socket_composition_uses_same_selection_path_without_reselecting(self):
        m = language()
        node = secret_graph("database").node("service")
        delivery = node.secret_deliveries[-1]
        socket_node = bare_node().with_connection_material((), (delivery,))
        self.assertEqual(socket_node.secret_selections,
                         (m.UnselectedSecretUse(delivery.reference, delivery.intent),))
        selected = replace(socket_node, secret_selections=(choice(),))
        self.assertEqual(selected.with_connection_material((), ()).secret_selections,
                         selected.secret_selections)

    def test_common_authoring_carries_known_versions_and_exposes_explicit_delta(self):
        authoring = language("topology.secret_selection")
        current = selected_graph()
        authored = secret_graph("database")
        carried = authoring.carry_forward_secret_selections(current, authored, {})
        self.assertEqual(carried.node("service").secret_selections, (choice(),))
        self.assertFalse(diff_graphs(validate_graph(current), validate_graph(carried)).changes)
        changed = authoring.carry_forward_secret_selections(
            current, authored, {"service": (choice("version-2"),)})
        difference = diff_graphs(validate_graph(current), validate_graph(changed))
        self.assertTrue(any(change.subject.field.value == "secret-selections"
                            for change in difference.changes))
        self.assertIn("<redacted>", repr(difference.descriptor()))
        self.assertNotIn("version-2", repr(difference.descriptor()))
        self.assertTrue(any(isinstance(activity.operation, ReconcileNode)
                            for activity in compile_activity_plan(difference).activities))
        incompatible = secret_graph("different-reference")
        fresh = authoring.carry_forward_secret_selections(current, incompatible, {})
        self.assertTrue(all(type(value) is language().UnselectedSecretUse
                            for value in fresh.node("service").secret_selections))
        with self.assertRaises(ValueError):
            authoring.carry_forward_secret_selections(current, incompatible,
                                                      {"service": (choice(),)})

    def test_real_product_image_and_configuration_changes_preserve_compatible_selection(self):
        m = language()
        authoring = language("topology.secret_selection")
        delivery = SecretFileDelivery("/run/secrets/key", choice().reference, choice().intent)
        artifact = ConfigurationArtifact("app", "/etc/app.json", ConfigurationMediaType.JSON, '{"workers":1}')
        product = ContainerServerProduct(ProductIdentity("example", "service", 1),
            OciImageReference("ghcr.io", "example/service", digest="sha256:" + "a" * 64),
            ProductRuntimeContract(sockets=BlockSockets(), configuration_artifacts=(artifact,),
                                   secret_deliveries=(delivery,)))
        successor = replace(product, identity=replace(product.identity, contract_revision=2),
                            image=replace(product.image, digest="sha256:" + "b" * 64))
        codec = ProductDescriptorCodec()

        def graph(value, configuration=artifact, selected_delivery=delivery):
            block = instantiate_product(value, "service", ProductInstanceConfiguration(
                configuration_artifacts=(configuration,), secret_deliveries=(selected_delivery,)))
            return compile_topology(DeploymentTopology("selection", DockerRuntime(children=(block,))))

        current = graph(product)
        current = current.update_node(replace(current.node("service"), secret_selections=(choice(),)))
        documents = (codec.encode_document(product), codec.encode_document(successor))
        for desired in (graph(successor), graph(product, replace(artifact, content='{"workers":2}', source_digest=None))):
            carried = authoring.carry_forward_secret_selections(
                current, desired, {}, product_documents=documents)
            self.assertEqual(carried.node("service").secret_selections, (choice(),))
            self.assertEqual(carried.node("service").configuration_artifacts,
                             desired.node("service").configuration_artifacts)
            self.assertEqual(carried.node("service").metadata["oci_image"],
                             desired.node("service").metadata["oci_image"])
        desired = graph(successor)
        mismatched_product = replace(successor, runtime_contract=replace(successor.runtime_contract,
            secret_deliveries=(replace(delivery, target_path="/run/secrets/uncommitted"),)))
        for evidence in ((), documents[:1], (*documents, documents[1]),
                         (documents[0], replace(documents[1], product=mismatched_product))):
            with self.subTest(count=len(evidence)), self.assertRaises(ValueError):
                authoring.carry_forward_secret_selections(current, desired, {}, product_documents=evidence)
        partial_node = replace(desired.node("service"), metadata={key: value
            for key, value in desired.node("service").metadata.items() if key != "product_descriptor_digest"})
        with self.assertRaises(ValueError):
            authoring.carry_forward_secret_selections(current, desired.update_node(partial_node), {},
                                                      product_documents=documents)
        for key, malformed in (("product_identity", "example/service/01"),
                               ("oci_image", "ghcr.io/example/service:latest")):
            bad_node = replace(current.node("service"), metadata={**current.node("service").metadata, key: malformed})
            unchanged_bad = current.update_node(bad_node)
            with self.subTest(key=key), self.assertRaises(ValueError):
                authoring.carry_forward_secret_selections(unchanged_bad, unchanged_bad, {})
        changed_delivery = replace(delivery, target_path="/run/secrets/another-key")
        incompatible = replace(successor, runtime_contract=replace(successor.runtime_contract,
                                                                   secret_deliveries=(changed_delivery,)))
        unresolved = authoring.carry_forward_secret_selections(current, graph(incompatible,
            selected_delivery=changed_delivery), {}, product_documents=(documents[0], codec.encode_document(incompatible)))
        self.assertEqual(unresolved.node("service").secret_selections,
                         (m.UnselectedSecretUse(choice().reference, choice().intent),))

    def test_runtime_intent_preserves_exact_versions_and_refuses_all_downgrades(self):
        m = language()
        request = request_with_version()
        intent = runtime_effect_intent_for_request(request)
        self.assertEqual(runtime_effect_intent_for_request(
            runtime_effect_request_for_intent(intent, effect_id="next-event")), intent)
        self.assertNotEqual(runtime_effect_intent_fingerprint(intent),
            runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request_with_version("version-2"))))
        material = request.products[0]
        entry = material.secret_selections[0]
        unresolved = replace(material, secret_selections=(m.UnselectedSecretUse(entry.reference, entry.intent),))
        for value in (request, intent):
            for changes in ({"products": ()}, {"products": (unresolved,)},
                            {"products": (unresolved,), "kind": RuntimeEffectKind.REALIZE_ACTIVITY,
                             "configuration_instances": None}):
                with self.subTest(changes=tuple(changes)), self.assertRaises(RuntimeEffectContractError):
                    replace(value, **changes)
        with self.assertRaises(ValueError):
            replace(material, secret_selections=())
        document = material.descriptor()
        del document["secret_selections"]
        with self.assertRaises(ValueError):
            type(material).from_descriptor(document)

    def test_plain_secret_start_and_reconcile_require_complete_target_material(self):
        m = language()
        request = request_with_version()
        material = request.products[0]
        material = replace(material, pull_authority=None, product=replace(material.product,
            runtime_contract=replace(material.product.runtime_contract, configuration_artifacts=())))
        entry = material.secret_selections[0]
        unresolved = replace(material, secret_selections=(m.UnselectedSecretUse(entry.reference, entry.intent),))
        for operation in (StartNode(NodeTarget("api")), ReconcileNode(NodeTarget("api"))):
            plain = replace(request, kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                operation=operation, configuration_instances=None, products=(material,))
            for value in (plain, runtime_effect_intent_for_request(plain)):
                for products in ((), (unresolved,), (replace(material, node_id="foreign"),)):
                    with self.subTest(operation=type(operation).__name__, products=len(products)), \
                            self.assertRaises(RuntimeEffectContractError):
                        replace(value, products=products)

    def test_non_nominal_operations_and_deliveries_cannot_bypass_selection(self):
        language()
        request = request_with_version()
        for operation in (StartNode, ReconcileNode):
            derived = type("DerivedOperation", (operation,), {})
            with self.assertRaises(RuntimeEffectContractError):
                replace(request, operation=derived(NodeTarget("api")), products=(),
                        kind=RuntimeEffectKind.REALIZE_ACTIVITY, configuration_instances=None)
        graph = secret_graph("database")
        node = graph.node("service")
        delivery = node.secret_deliveries[0]
        derived = type("DerivedDelivery", (type(delivery),), {})
        candidate = derived(**{field.name: getattr(delivery, field.name) for field in fields(delivery) if field.init})
        with self.assertRaises(ValueError):
            replace(node, secret_deliveries=(candidate,), secret_selections=())

    def test_forged_nominal_request_and_intent_refuse_at_canonical_projection(self):
        m = language()
        request = request_with_version()
        material = request.products[0]
        entry = material.secret_selections[0]
        unresolved = replace(material, secret_selections=(m.UnselectedSecretUse(entry.reference, entry.intent),))
        for value in (request, runtime_effect_intent_for_request(request)):
            for products in ((), (unresolved,)):
                forged = object.__new__(type(value))
                for field in fields(value):
                    object.__setattr__(forged, field.name, products if field.name == "products" else getattr(value, field.name))
                with self.assertRaises(RuntimeEffectContractError):
                    forged.descriptor()
                with self.assertRaises(RuntimeEffectContractError):
                    if type(value) is type(request):
                        runtime_effect_intent_for_request(forged)
                    else:
                        runtime_effect_intent_fingerprint(forged)


if __name__ == "__main__":
    unittest.main()
