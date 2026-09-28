"""Explicit successor configuration preserves generic, exact artifact binding."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import importlib
import importlib.util
import json
import unittest

import rfc8785
import control_plane_kit_core as core
from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.products import ProductDescriptorCodec, ProductIdentity, ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.topology import GraphDescriptorCodec, compile_topology
from control_plane_kit_core import wrapper_configuration as legacy
from tests import test_node_health_declarations as health_fixtures
from tests import test_node_control_surfaces as surface_fixtures
from tests.test_delegation_keys import PUBLIC_KEY_A
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration

PROFILE = "workload-node-control-configuration.v2"
PATH_KEY = legacy.WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT


class ReceiverConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.declaration = health_fixtures.NodeHealthDeclarationTests().declaration()
        self.product = health_fixtures.NodeHealthDeclarationTests().product(self.declaration.surface)
        self.key = DelegationPublicKey("public-a", DelegationKeyAlgorithm.ED25519, PUBLIC_KEY_A)
        module = "control_plane_kit_core.receiver_configuration"
        self.assertIsNotNone(importlib.util.find_spec(module), "receiver configuration contract is not implemented")
        self.api = importlib.import_module(module)
        self.codec = self.api.ReceiverNodeControlConfigurationCodec()

    def document(self, declaration=None):
        declaration = self.declaration if declaration is None else declaration
        purposes = {DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ}
        if declaration.surface.variables:
            purposes.add(DelegationKeyPurpose.WORKLOAD_NODE_CONTROL)
        if declaration.surface.health_reads:
            purposes.add(DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
        return {"profile": PROFILE,
                "target": {"workspace_id": "workspace-a", "runtime_id": "runtime-a", "node_id": "service-a",
                           "provider_socket_name": "control", "receiver_id": "a" * 32},
                "declaration": declaration.descriptor(),
                "verifiers": [{"purpose": purpose.value, "issuer": "issuer-" + str(index),
                               "public_keys": [{"key_id": self.key.key_id, "algorithm": "ed25519",
                                                "public_key_pem": self.key.public_key_pem}]}
                              for index, purpose in enumerate(sorted(purposes))]}

    def refusal(self, action):
        with self.assertRaises(legacy.WrapperConfigurationError) as caught:
            action()
        self.assertEqual(str(caught.exception), "wrapper configuration is invalid")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(vars(caught.exception), {})

    def artifact(self, artifact_id="receiver", path="/etc/example/control.json", document=None):
        value = self.codec.decode(self.document() if document is None else document)
        return ConfigurationArtifact(artifact_id, path, ConfigurationMediaType.JSON,
                                     self.codec.encode_bytes(value).decode())

    def select(self, artifacts, environment=None, surfaces=None, selector=None):
        environment = ((PublicStaticEnvironmentBinding(PATH_KEY, artifacts[0].target_path),)
                       if environment is None else environment)
        surfaces = (self.declaration.surface,) if surfaces is None else surfaces
        selector = self.api.select_receiver_node_control_configuration_artifact if selector is None else selector
        return selector(artifacts=artifacts, environment=environment, control_surfaces=surfaces)

    def test_closed_successor_roundtrip_has_runtime_in_target_and_no_graph_context(self):
        document = self.document()
        value = self.codec.decode(document)
        self.assertEqual(self.codec.encode(value), document)
        self.assertEqual(self.codec.encode_bytes(value), rfc8785.dumps(document))
        self.assertEqual(self.codec.decode_bytes(json.dumps(document, indent=2).encode()), value)
        self.assertEqual(value.target.runtime_id.value, "runtime-a")
        self.assertNotIn("runtime_id", self.codec.encode(value))
        self.assertNotIn("authority_context", self.codec.encode(value))
        self.assertNotIn("graph_revision", self.codec.encode(value)["target"])
        self.assertNotIn(self.key.public_key_pem, repr(value))
        with self.assertRaises(FrozenInstanceError):
            value.target = object()
        document["verifiers"].reverse()
        self.assertEqual(self.codec.decode(document), value)

    def test_variable_only_and_mixed_declarations_keep_exact_purpose_families(self):
        variable = WorkloadNodeControlSurfaceDeclaration(
            surface_fixtures.WorkloadNodeControlSurfaceTests().surface("control", "mode"))
        mixed = health_fixtures.NodeHealthDeclarationTests().declaration("mode")
        for declaration in (self.declaration, variable, mixed):
            document = self.document(declaration)
            value = self.codec.decode(document)
            self.assertEqual(value.declaration, declaration)
            for index in range(len(document["verifiers"])):
                missing = deepcopy(document)
                missing["verifiers"].pop(index)
                self.refusal(lambda: self.codec.decode(missing))
            duplicate = deepcopy(document)
            duplicate["verifiers"].append(deepcopy(duplicate["verifiers"][0]))
            self.refusal(lambda: self.codec.decode(duplicate))
        extra = self.document()
        extra["verifiers"].append({**deepcopy(extra["verifiers"][0]), "purpose": DelegationKeyPurpose.WORKLOAD_NODE_CONTROL.value})
        self.refusal(lambda: self.codec.decode(extra))

    def test_config_refuses_profile_scope_family_and_public_key_substitution(self):
        document = self.document()
        for update in ({"profile": "workload-node-control-configuration.v1"}, {"profile": "unknown"},
                       {"runtime_id": "runtime-a"}, {"authority_context": {}}, {"private": "secret"}):
            self.refusal(lambda: self.codec.decode({**document, **update}))
        for update in ({"receiver_id": "A" * 32}, {"graph_revision": "graph-A"}, {"runtime_id": "https://private.invalid"},
                       {"provider_socket_name": "other"}):
            bad = deepcopy(document)
            bad["target"].update(update)
            self.refusal(lambda: self.codec.decode(bad))
        for edit in ("issuer", "duplicate-key", "duplicate-fingerprint", "algorithm", "pem", "purpose"):
            bad = deepcopy(document)
            family = bad["verifiers"][0]
            key = family["public_keys"][0]
            if edit == "issuer":
                family["issuer"] = "Bearer private"
            elif edit == "duplicate-key":
                family["public_keys"].append(deepcopy(key))
            elif edit == "duplicate-fingerprint":
                family["public_keys"].append({**key, "key_id": "another-id"})
            elif edit == "algorithm":
                key["algorithm"] = "rsa"
            elif edit == "pem":
                key["public_key_pem"] = "private-key-material"
            else:
                family["purpose"] = "unknown"
            self.refusal(lambda: self.codec.decode(bad))

    def test_forged_and_non_nominal_values_are_revalidated_without_leaks(self):
        class Text(str):
            pass
        value = self.codec.decode(self.document())
        target = deepcopy(value.target)
        object.__setattr__(target, "receiver_id", Text("a" * 32))
        self.refusal(lambda: replace(value, target=target))
        family = deepcopy(value.verifiers[0])
        object.__setattr__(family, "issuer", "secret=private")
        self.refusal(lambda: replace(value, verifiers=(family,) + value.verifiers[1:]))
        forged = deepcopy(value)
        object.__setattr__(forged, "verifiers", [*value.verifiers])
        self.refusal(lambda: self.codec.encode(forged))
        self.refusal(lambda: self.codec.encode(object()))

    def test_raw_configuration_is_bounded_duplicate_safe_and_cause_free(self):
        raw = self.codec.encode_bytes(self.codec.decode(self.document()))
        maximum = raw + b" " * (legacy.MAX_WRAPPER_CONFIGURATION_BYTES - len(raw))
        self.assertEqual(self.codec.decode_bytes(maximum), self.codec.decode_bytes(raw))
        duplicate = b'{"profile":"' + PROFILE.encode() + b'",' + raw[1:]
        for bad in (maximum + b" ", duplicate, b"NaN", b"{", b"\xff", bytearray(raw), raw.decode()):
            self.refusal(lambda: self.codec.decode_bytes(bad))
        nested = b"[" * 1500 + b"0" + b"]" * 1500
        self.refusal(lambda: self.codec.decode_bytes(nested))

    def test_exact_generic_slot_selection_preserves_all_ambiguity_refusals(self):
        artifact = self.artifact()
        other = ConfigurationArtifact("application", "/etc/application.txt", ConfigurationMediaType.TEXT, "workers=2")
        environment = (PublicStaticEnvironmentBinding("APP_MODE", "ready"), PublicStaticEnvironmentBinding(PATH_KEY, artifact.target_path))
        self.assertIs(self.select((other, artifact), environment), artifact)
        cases = (((), environment, (self.declaration.surface,)),
                 ((artifact,), (), (self.declaration.surface,)),
                 ((artifact,), environment + (environment[-1],), (self.declaration.surface,)),
                 ((artifact,), (PublicStaticEnvironmentBinding(PATH_KEY, other.target_path),), (self.declaration.surface,)),
                 ((artifact,), environment, ()), ((artifact,), environment, (self.declaration.surface,) * 2),
                 ((artifact, artifact), environment, (self.declaration.surface,)))
        for artifacts, env, surfaces in cases:
            self.refusal(lambda: self.select(artifacts, env, surfaces))
        for changed in (replace(artifact, file_mode=ConfigurationFileMode.OWNER_READ_ONLY),
                        replace(artifact, media_type=ConfigurationMediaType.TEXT)):
            self.refusal(lambda: self.select((changed,)))
        derived = SocketDerivedEnvironmentBinding(PATH_KEY, artifact.target_path, "edge-a")
        self.refusal(lambda: self.select((artifact,), (derived,)))
        self.refusal(lambda: self.select((artifact,), environment + (derived,)))
        surface = replace(self.declaration.surface, provider_socket_name=replace(self.declaration.surface.provider_socket_name, value="other"))
        self.refusal(lambda: self.select((artifact,), surfaces=(surface,)))

    def test_old_and_successor_selectors_never_probe_each_others_profiles(self):
        successor = self.artifact()
        self.refusal(lambda: self.select((successor,), selector=legacy.select_workload_node_control_configuration_artifact))
        old = deepcopy(self.document())
        old["profile"] = "workload-node-control-configuration.v1"
        old["runtime_id"] = old["target"].pop("runtime_id")
        old["target"].pop("receiver_id")
        old["target"]["graph_revision"] = "graph-A"
        old_codec = legacy.WorkloadNodeControlConfigurationCodec()
        original = old_codec.encode_bytes(old_codec.decode(old))
        artifact = ConfigurationArtifact("old", successor.target_path, ConfigurationMediaType.JSON, original.decode())
        self.assertIs(self.select((artifact,), selector=legacy.select_workload_node_control_configuration_artifact), artifact)
        self.refusal(lambda: self.select((artifact,)))
        self.assertEqual(old_codec.encode_bytes(old_codec.decode_bytes(original)), original)

    def test_independent_products_paths_and_graph_roundtrip_use_the_same_contract(self):
        for name, path in (("weather", "/opt/weather/receiver.json"), ("inventory", "/etc/inventory/management.json")):
            artifact = self.artifact(name + "-settings", path)
            environment = (PublicStaticEnvironmentBinding(PATH_KEY, path),)
            contract = replace(self.product.runtime_contract, configuration_artifacts=(artifact,), public_environment=environment)
            product = replace(self.product, identity=ProductIdentity("independent", name, 1), runtime_contract=contract)
            codec = ProductDescriptorCodec()
            restored = codec.decode_document(codec.encode_document(product).content).product
            self.assertEqual(restored.runtime_contract.configuration_artifacts, (artifact,))
            block = instantiate_product(product, "service-a", ProductInstanceConfiguration.from_contract(contract))
            graph = compile_topology(DeploymentTopology(name, DockerRuntime(children=(block,))))
            node = GraphDescriptorCodec().decode(GraphDescriptorCodec().encode(graph)).node("service-a")
            self.assertEqual(self.select(node.configuration_artifacts, node.public_environment, node.block_spec.control_surfaces), artifact)
            self.assertEqual(self.codec.decode_bytes(artifact.content.encode()).target.receiver_id, "a" * 32)
        for name in ("ReceiverNodeControlConfiguration", "ReceiverNodeControlConfigurationCodec",
                     "select_receiver_node_control_configuration_artifact"):
            self.assertIs(getattr(core, name), getattr(self.api, name))
            self.assertIn(name, core.__all__)


if __name__ == "__main__":
    unittest.main()
