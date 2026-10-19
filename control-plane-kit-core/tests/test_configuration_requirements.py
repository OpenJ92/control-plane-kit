"""#2011 public requirement/instance laws; no effects or downstream simulation."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

import control_plane_kit_core as core
from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration import (
    ConfigurationArtifact, ConfigurationArtifactError, ConfigurationArtifactRequirement,
    ConfigurationFileMode, ConfigurationMediaType,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.lifecycle import ResourceLifecycle
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.products import (
    ContainerServerProduct, ContainerServerProductCodec, OciImageReference,
    ProductDescriptorCodec, ProductDescriptorError, ProductIdentity,
    ProductInstanceConfiguration, ProductInstantiationError, ProductReference,
    ProductRuntimeContract, ProductRuntimeContractCodec, ProductRuntimeContractError,
    RetainedDataMount, instantiate_product, product_configuration_slots,
)
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
    select_receiver_node_control_configuration_slot,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectContractError, RuntimeProductMaterial
from control_plane_kit_core.secret_selections import UnselectedSecretUse
from control_plane_kit_core.topology import GraphDescriptorCodec, compile_topology
from control_plane_kit_core.topology.secret_selection import carry_forward_secret_selections
from control_plane_kit_core.wrapper_configuration import (
    WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, WrapperConfigurationError,
    select_workload_node_control_configuration_artifact,
)
from tests import test_external_product_fixture as external_fixture
from tests import test_receiver_configuration as receiver_fixture
from tests import test_secret_selections as selection_fixture


def requirement(artifact_id="settings", path="/etc/example/settings.json"):
    return ConfigurationArtifactRequirement(artifact_id, path, ConfigurationMediaType.JSON)


def artifact(slot=None, content='{"workers":1}'):
    slot = requirement() if slot is None else slot
    return ConfigurationArtifact(slot.artifact_id, slot.target_path, slot.media_type,
                                 content, slot.file_mode)


def product(contract=None):
    return ContainerServerProduct(
        ProductIdentity("example", "service", 1),
        OciImageReference("registry.example", "service", "sha256:" + "a" * 64),
        ProductRuntimeContract(configuration_requirements=(requirement(),)) if contract is None else contract,
        display_name="Example service")


class ConfigurationRequirementTests(unittest.TestCase):
    def test_requirement_is_frozen_ordered_payload_free_and_exactly_encoded(self):
        value = requirement()
        expected = {"artifact_id": "settings", "target_path": "/etc/example/settings.json",
                    "media_type": "application/json", "file_mode": "0444"}
        self.assertEqual(value.descriptor(), expected)
        self.assertEqual(ConfigurationArtifactRequirement.from_descriptor(expected), value)
        self.assertEqual(set(vars(value)), set(expected))
        self.assertLess(requirement("a"), value)
        with self.assertRaises(FrozenInstanceError):
            value.artifact_id = "changed"
        self.assertIs(core.ConfigurationArtifactRequirement, ConfigurationArtifactRequirement)
        self.assertIs(core.product_configuration_slots, product_configuration_slots)

    def test_requirement_reuses_field_grammar_and_rejects_non_nominal_values(self):
        class Text(str):
            pass
        for field, values in (
            ("artifact_id", ("", "A", "a" * 64, "secret=canary", 42, Text("settings"))),
            ("target_path", ("relative", "/", "/etc/../canary", "/etc//canary",
                             "/etc/canary/", "/run/secrets/key", "/proc/self", "/etc/a b")),
            ("media_type", ("application/json", object())),
            ("file_mode", ("0444", object())),
        ):
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises((ConfigurationArtifactError, TypeError)) as caught:
                        replace(requirement(), **{field: value})
                    self.assertNotIn("canary", str(caught.exception))

    def test_requirement_descriptor_is_closed_and_detaches_candidate_errors(self):
        good = {"artifact_id": "settings", "target_path": "/etc/example/settings.json",
                "media_type": "application/json", "file_mode": "0444"}
        cases = [{**good, key: "private-canary"} for key in ("content", "content_digest", "source_digest", "unknown")]
        cases += [{key: value for key, value in good.items() if key != "file_mode"},
                  {**good, "media_type": "private-canary"}, {**good, "target_path": "/../private-canary"}]
        for case in cases:
            with self.subTest(keys=tuple(case)):
                with self.assertRaises(ConfigurationArtifactError) as caught:
                    ConfigurationArtifactRequirement.from_descriptor(case)
                self.assertNotIn("private-canary", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_contract_normalizes_requirements_and_projects_exact_union(self):
        first, second, concrete = requirement("a", "/etc/a"), requirement("b", "/etc/b"), artifact()
        contract = ProductRuntimeContract(configuration_requirements=(second, first),
                                          configuration_artifacts=(concrete,))
        self.assertEqual(contract.configuration_requirements, (first, second))
        self.assertEqual(product_configuration_slots(contract), (first, second, requirement()))
        self.assertEqual(ProductRuntimeContractCodec().decode(contract.descriptor()), contract)
        self.assertEqual(product_configuration_slots(replace(contract,
            configuration_artifacts=(replace(concrete, content='{"workers":2}', source_digest=None),))),
            product_configuration_slots(contract))
        self.assertEqual(product_configuration_slots(ProductRuntimeContract()), ())

    def test_contract_rejects_duplicate_ids_and_paths_across_both_slot_kinds(self):
        first = requirement()
        for duplicate in (first, replace(first, target_path="/etc/other"), replace(first, artifact_id="other")):
            for form in ("required", "mixed", "concrete"):
                with self.subTest(form=form, duplicate=duplicate):
                    with self.assertRaises(ProductRuntimeContractError):
                        ProductRuntimeContract(
                            configuration_requirements=(first, duplicate) if form == "required" else (first,) if form == "mixed" else (),
                            configuration_artifacts=(artifact(duplicate),) if form == "mixed" else
                                (artifact(first), artifact(duplicate)) if form == "concrete" else ())

    def test_requirement_admission_rejects_non_nominal_and_forged_values(self):
        class Derived(ConfigurationArtifactRequirement):
            def descriptor(self):
                raise AssertionError("subclass dispatch must not occur")
        forged = deepcopy(requirement())
        object.__setattr__(forged, "target_path", "/../private-canary")
        for values in ([requirement()], (object(),), (Derived("settings", "/etc/a", ConfigurationMediaType.JSON),), (forged,)):
            with self.subTest(value_type=type(values).__name__):
                with self.assertRaises(ProductRuntimeContractError):
                    ProductRuntimeContract(configuration_requirements=values)
        contract = ProductRuntimeContract(configuration_requirements=(requirement(),))
        object.__setattr__(contract, "configuration_requirements", (forged,))
        for action in (lambda: product_configuration_slots(contract),
                       lambda: ProductRuntimeContractCodec().encode(contract)):
            with self.assertRaises(ProductRuntimeContractError):
                action()

    def test_retained_lifecycle_ids_and_segment_overlap_refuse_for_both_kinds(self):
        for required in (False, True):
            for path in ("/data", "/data/settings"):
                slot = requirement("settings", path)
                kwargs = {"configuration_requirements": (slot,)} if required else {"configuration_artifacts": (artifact(slot),)}
                with self.subTest(required=required, path=path), self.assertRaises(ProductRuntimeContractError):
                    ProductRuntimeContract(**kwargs, retained_data_mounts=(RetainedDataMount("database", "/data"),),
                                           lifecycle=ResourceLifecycle.owned_with_retained_data("database"))
            slot = requirement("settings", "/data")
            kwargs = {"configuration_requirements": (slot,)} if required else {"configuration_artifacts": (artifact(slot),)}
            with self.subTest(required=required, relation="ancestor"), self.assertRaises(ProductRuntimeContractError):
                ProductRuntimeContract(**kwargs, retained_data_mounts=(RetainedDataMount("database", "/data/nested"),),
                                       lifecycle=ResourceLifecycle.owned_with_retained_data("database"))
            sibling = ProductRuntimeContract(**kwargs, retained_data_mounts=(RetainedDataMount("database", "/database"),),
                                             lifecycle=ResourceLifecycle.owned_with_retained_data("database"))
            self.assertEqual(product_configuration_slots(sibling), (slot,))
            conflict = requirement("database", "/etc/database.json")
            kwargs = {"configuration_requirements": (conflict,)} if required else {"configuration_artifacts": (artifact(conflict),)}
            with self.subTest(required=required, relation="identity"), self.assertRaises(ProductRuntimeContractError):
                ProductRuntimeContract(**kwargs, lifecycle=ResourceLifecycle.owned_with_retained_data("database"))

    def test_two_real_payloads_keep_registered_document_reference_and_image(self):
        declared = product()
        codec = ProductDescriptorCodec()
        document = codec.encode_document(declared)
        reference = ProductReference.from_document(document)
        nodes = []
        for content in ('{"workers":1}', '{"workers":2}'):
            supplied = artifact(content=content)
            block = instantiate_product(declared, "service", ProductInstanceConfiguration(configuration_artifacts=(supplied,)))
            graph = compile_topology(DeploymentTopology("example", DockerRuntime(children=(block,))))
            restored = GraphDescriptorCodec().decode(GraphDescriptorCodec().encode(graph))
            node = restored.node("service")
            self.assertEqual(restored, graph)
            self.assertEqual(node.configuration_artifacts[0].content, content)
            self.assertEqual(node.configuration_artifacts[0].content_digest, hashlib.sha256(content.encode()).hexdigest())
            self.assertEqual(node.metadata["product_descriptor_digest"], reference.descriptor_sha256.value)
            self.assertEqual(node.metadata["product_identity"], reference.identity.key)
            self.assertEqual(node.metadata["oci_image"], declared.image.execution_reference)
            self.assertEqual(codec.encode_document(declared).content, document.content)
            nodes.append(node)
        self.assertNotEqual(nodes[0].configuration_artifacts[0].content_digest, nodes[1].configuration_artifacts[0].content_digest)

    def test_instance_must_supply_the_exact_required_and_concrete_union(self):
        concrete = artifact(requirement("fixed", "/etc/fixed.json"))
        declared = product(ProductRuntimeContract(configuration_requirements=(requirement(),), configuration_artifacts=(concrete,)))
        good = artifact()
        instantiate_product(declared, "service", ProductInstanceConfiguration(configuration_artifacts=(good, concrete)))
        bad = [(), (good,), (concrete,), (good, concrete, artifact(requirement("extra", "/etc/extra")))]
        bad += [(replace(good, **change), concrete) for change in (
            {"artifact_id": "wrong"}, {"target_path": "/etc/wrong"},
            {"media_type": ConfigurationMediaType.TEXT}, {"file_mode": ConfigurationFileMode.OWNER_READ_ONLY})]
        for values in bad:
            with self.subTest(slots=tuple(value.artifact_id for value in values)), self.assertRaises(ProductInstantiationError):
                instantiate_product(declared, "service", ProductInstanceConfiguration(configuration_artifacts=values))

    def test_from_contract_requires_explicit_payload_for_requirements_only(self):
        with self.assertRaises(ProductInstantiationError):
            ProductInstanceConfiguration.from_contract(product().runtime_contract)
        concrete = artifact()
        self.assertEqual(ProductInstanceConfiguration.from_contract(ProductRuntimeContract(configuration_artifacts=(concrete,))).configuration_artifacts,
                         (concrete,))

    def test_v2_document_is_selected_only_for_nonempty_requirements(self):
        codec = ProductDescriptorCodec()
        declared = product()
        document = codec.encode_document(declared)
        wire = json.loads(document.content)
        self.assertEqual(wire["schema"], "control-plane-kit.product/v2")
        self.assertEqual(wire["product"]["runtime_contract"]["configuration_requirements"], [requirement().descriptor()])
        self.assertEqual(codec.decode_document(document.content), document)
        self.assertEqual(ContainerServerProductCodec().decode(wire["product"]), declared)
        historical = external_fixture.FIXTURE.read_bytes()
        self.assertEqual(codec.encode_document(codec.decode_document(historical).product).content, historical)
        old = codec.encode_document(product(ProductRuntimeContract()))
        self.assertEqual(json.loads(old.content)["schema"], "control-plane-kit.product")
        self.assertNotIn("configuration_requirements", json.loads(old.content)["product"]["runtime_contract"])

    def test_wire_refuses_empty_mismatched_unknown_and_duplicate_requirements(self):
        codec = ProductDescriptorCodec()
        wire = json.loads(codec.encode_document(product()).content)
        # This assertion separates the missing new wire shape from test setup.
        self.assertIn("configuration_requirements", wire["product"]["runtime_contract"])
        cases = []
        for schema in ("control-plane-kit.product", "control-plane-kit.product/v3"):
            cases.append({**deepcopy(wire), "schema": schema})
        for value in ([], None, "invalid", [requirement().descriptor()] * 2,
                      [{**requirement().descriptor(), "payload": "private-canary"}]):
            bad = deepcopy(wire)
            bad["product"]["runtime_contract"]["configuration_requirements"] = value
            cases.append(bad)
        missing = deepcopy(wire)
        del missing["product"]["runtime_contract"]["configuration_requirements"]
        cases.append(missing)
        for case in cases:
            with self.subTest(schema=case["schema"]), self.assertRaises(ProductDescriptorError):
                codec.decode_document(case)
        old = json.loads(codec.encode_document(product(ProductRuntimeContract())).content)
        old["product"]["runtime_contract"]["configuration_requirements"] = []
        with self.assertRaises(ProductDescriptorError):
            codec.decode_document(old)
        with self.assertRaises(ProductRuntimeContractError):
            ProductRuntimeContractCodec().decode(old["product"]["runtime_contract"])

    def test_typed_and_mapping_order_normalize_but_noncanonical_bytes_refuse(self):
        first, second = requirement("a", "/etc/a"), requirement("b", "/etc/b")
        codec = ProductDescriptorCodec()
        document = codec.encode_document(product(ProductRuntimeContract(configuration_requirements=(second, first))))
        mapping = json.loads(document.content)
        self.assertIn("configuration_requirements", mapping["product"]["runtime_contract"])
        self.assertEqual(mapping["product"]["runtime_contract"]["configuration_requirements"], [first.descriptor(), second.descriptor()])
        mapping["product"]["runtime_contract"]["configuration_requirements"].reverse()
        self.assertEqual(codec.decode_document(mapping), document)
        with self.assertRaises(ProductDescriptorError):
            codec.decode_document(json.dumps(mapping, ensure_ascii=True, separators=(",", ":")).encode())

    def test_unresolved_material_constructor_refuses(self):
        declared = product()
        reference = ProductReference.from_document(ProductDescriptorCodec().encode_document(declared))
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeProductMaterial("service", "docker", reference, declared)

    def test_unresolved_material_decoder_refuses(self):
        declared = product()
        reference = ProductReference.from_document(ProductDescriptorCodec().encode_document(declared))
        resolved = replace(declared, runtime_contract=ProductRuntimeContract(configuration_artifacts=(artifact(),)))
        wire = RuntimeProductMaterial("service", "docker", reference, resolved).descriptor()
        wire["product"] = ContainerServerProductCodec().encode(declared)
        self.assertIn("configuration_requirements", wire["product"]["runtime_contract"])
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeProductMaterial.from_descriptor(wire)

    def test_resolved_material_keeps_original_v2_reference_for_both_payloads(self):
        declared = product()
        document = ProductDescriptorCodec().encode_document(declared)
        self.assertEqual(json.loads(document.content)["schema"], "control-plane-kit.product/v2")
        reference = ProductReference.from_document(document)
        for content in ('{"workers":1}', '{"workers":2}'):
            resolved = replace(declared, runtime_contract=replace(declared.runtime_contract,
                configuration_requirements=(), configuration_artifacts=(artifact(content=content),)))
            material = RuntimeProductMaterial("service", "docker", reference, resolved)
            restored = RuntimeProductMaterial.from_descriptor(material.descriptor())
            self.assertEqual(restored.reference, reference)
            self.assertEqual(restored.product.runtime_contract.configuration_requirements, ())
            self.assertEqual(restored.product.runtime_contract.configuration_artifacts[0].content, content)
            self.assertEqual(restored.product.image, declared.image)
            self.assertNotEqual(ProductDescriptorCodec().encode_document(restored.product).content_digest, reference.descriptor_sha256.value)


class RequiredReceiverSlotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = receiver_fixture.ReceiverConfigurationTests()
        self.fixture.setUp()
        self.slot = requirement("receiver", "/etc/receiver.json")
        self.environment = (PublicStaticEnvironmentBinding(WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, self.slot.target_path),)
        self.surfaces = (self.fixture.declaration.surface,)

    def select(self, **changes):
        args = dict(artifacts=(), requirements=(self.slot,), environment=self.environment, control_surfaces=self.surfaces)
        args.update(changes)
        return select_receiver_node_control_configuration_slot(**args)

    def test_declared_requirement_selects_without_payload_and_actual_selectors_refuse(self):
        self.assertIs(self.select(), self.slot)
        concrete = artifact(self.slot, "{}")
        self.assertIs(self.select(requirements=(), artifacts=(concrete,)), concrete)
        for selector in (select_receiver_node_control_configuration_artifact, select_workload_node_control_configuration_artifact):
            self.fixture.refusal(lambda: selector(artifacts=(self.slot,), environment=self.environment, control_surfaces=self.surfaces))

    def test_required_selector_preserves_union_binding_and_nominal_refusals(self):
        forged = deepcopy(self.slot)
        object.__setattr__(forged, "target_path", "/../private-canary")
        class Derived(ConfigurationArtifactRequirement):
            def descriptor(self):
                raise AssertionError("no virtual dispatch")
        cases = [dict(requirements=()), dict(requirements=[self.slot]),
                 dict(requirements=(self.slot, self.slot)), dict(requirements=(forged,)),
                 dict(requirements=(Derived(self.slot.artifact_id, self.slot.target_path, self.slot.media_type),)),
                 dict(artifacts=(artifact(self.slot),)),
                 dict(artifacts=(artifact(replace(self.slot, target_path="/etc/other")),)),
                 dict(artifacts=(artifact(replace(self.slot, artifact_id="other")),)),
                 dict(environment=()), dict(environment=self.environment * 2),
                 dict(environment=(SocketDerivedEnvironmentBinding(WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, self.slot.target_path, "edge"),)),
                 dict(environment=(replace(self.environment[0], value="/etc/other"),)),
                 dict(control_surfaces=()), dict(control_surfaces=self.surfaces * 2),
                 dict(requirements=(replace(self.slot, media_type=ConfigurationMediaType.TEXT),)),
                 dict(requirements=(replace(self.slot, file_mode=ConfigurationFileMode.OWNER_READ_ONLY),))]
        for index, changes in enumerate(cases):
            with self.subTest(case=index):
                self.fixture.refusal(lambda: self.select(**changes))


class RequiredDeclarationCarryTests(unittest.TestCase):
    def pair(self, *, reverse=False, required=(True, True)):
        fixture = selection_fixture.SecretSelectionTests()
        current, desired, documents = fixture.declaration_pair(reverse=reverse)
        graphs, updated = [], []
        for graph, document, use_requirement in zip((current, desired), documents, required, strict=True):
            declared = document.product
            if use_requirement:
                wrapper = next(value for value in declared.runtime_contract.configuration_artifacts if value.artifact_id == "receiver")
                declared = replace(declared, runtime_contract=replace(declared.runtime_contract,
                    configuration_artifacts=tuple(value for value in declared.runtime_contract.configuration_artifacts if value is not wrapper),
                    configuration_requirements=(ConfigurationArtifactRequirement(wrapper.artifact_id, wrapper.target_path, wrapper.media_type, wrapper.file_mode),)))
            graph, new_document = fixture.declaration_product(graph, declared)
            # Never let missing wire support masquerade as a carry-law refusal.
            self.assertEqual(ProductDescriptorCodec().decode_document(new_document.content).product, declared)
            graphs.append(graph)
            updated.append(new_document)
        return *graphs, tuple(updated)

    def assert_not_carried(self, current, desired, documents):
        result = carry_forward_secret_selections(current, desired, {}, product_documents=documents)
        self.assertTrue(result.node("workload").secret_selections)
        self.assertTrue(all(type(value) is UnselectedSecretUse for value in result.node("workload").secret_selections))

    def test_equal_requirements_carry_exact_selection_for_existing_health_relation(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                current, desired, documents = self.pair(reverse=reverse)
                carried = carry_forward_secret_selections(current, desired, {}, product_documents=documents)
                self.assertEqual(carried, desired)
                self.assertEqual(carried.node("workload").secret_selections, (selection_fixture.choice(),))

    def test_both_mixed_declaration_directions_refuse(self):
        for required in ((True, False), (False, True)):
            with self.subTest(required=required):
                self.assert_not_carried(*self.pair(required=required))

    def test_changed_required_slot_fields_and_unrelated_contract_refuse(self):
        current, desired, documents = self.pair()
        declared = documents[1].product
        slot = declared.runtime_contract.configuration_requirements[0]
        contracts = [replace(declared.runtime_contract, configuration_requirements=(replace(slot, **change),))
            for change in ({"artifact_id": "different"}, {"target_path": "/etc/other"},
                           {"media_type": ConfigurationMediaType.TEXT}, {"file_mode": ConfigurationFileMode.OWNER_READ_ONLY})]
        contracts.append(replace(declared.runtime_contract, public_environment=(
            *declared.runtime_contract.public_environment, PublicStaticEnvironmentBinding("EXTRA", "value"))))
        for contract in contracts:
            graph, doc = selection_fixture.SecretSelectionTests().declaration_product(desired, replace(declared, runtime_contract=contract))
            self.assert_not_carried(current, graph, (documents[0], doc))

    def test_required_successor_still_refuses_changed_actual_target_trust_and_node(self):
        current, desired, documents = self.pair()
        node = desired.node("workload")
        wrapper = next(value for value in node.configuration_artifacts if value.artifact_id == "receiver")
        codec = ReceiverNodeControlConfigurationCodec()
        configured = codec.decode_bytes(wrapper.content.encode())
        changed = [replace(configured, target=replace(configured.target, receiver_id="e" * 32)),
                   replace(configured, verifiers=tuple(replace(value, issuer="other-issuer") for value in configured.verifiers))]
        for candidate in changed:
            modified = replace(wrapper, content=codec.encode_bytes(candidate).decode(), source_digest=None)
            graph = desired.update_node(replace(node, configuration_artifacts=tuple(
                modified if value.artifact_id == wrapper.artifact_id else value for value in node.configuration_artifacts)))
            self.assert_not_carried(current, graph, documents)
        self.assert_not_carried(current, desired.update_node(replace(node, metadata={**node.metadata, "extra": "changed"})), documents)

    def test_required_successor_does_not_admit_health_without_readiness(self):
        current, desired, documents = self.pair()
        node = desired.node("workload")
        wrapper = next(value for value in node.configuration_artifacts if value.artifact_id == "receiver")
        codec = ReceiverNodeControlConfigurationCodec()
        configured = codec.decode_bytes(wrapper.content.encode())
        surface = replace(configured.declaration.surface, health_reads=(NodeHealthReadKind.LIVENESS,))
        candidate = replace(configured, declaration=replace(configured.declaration, surface=surface))
        modified = replace(wrapper, content=codec.encode_bytes(candidate).decode(), source_digest=None)
        graph = desired.update_node(replace(node, block_spec=replace(node.block_spec, control_surfaces=(surface,)),
            configuration_artifacts=tuple(modified if value.artifact_id == wrapper.artifact_id else value for value in node.configuration_artifacts)))
        declared = replace(documents[1].product, runtime_contract=replace(documents[1].product.runtime_contract, control_surfaces=(surface,)))
        graph, doc = selection_fixture.SecretSelectionTests().declaration_product(graph, declared)
        self.assert_not_carried(current, graph, (documents[0], doc))
